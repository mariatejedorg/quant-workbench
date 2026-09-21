"""Each doctor checker against in-memory fakes: one rule, one seeded defect, one clean case."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from textwrap import dedent

from quant_workbench.application.catalog import Catalog
from quant_workbench.application.checkers import default_checkers
from quant_workbench.application.checkers.code_quality import (
    ExplainabilityChecker,
    StaticAnalysisChecker,
)
from quant_workbench.application.checkers.environment import (
    EnvironmentChecker,
    EnvironmentHazardsChecker,
)
from quant_workbench.application.checkers.git_checks import GitHygieneChecker, GitIdentityChecker
from quant_workbench.application.checkers.imports import (
    ImportCollisionChecker,
    SiblingReferencesChecker,
)
from quant_workbench.application.checkers.outputs import OutputsFreshnessChecker
from quant_workbench.application.checkers.ssl_cert import SslCertChecker
from quant_workbench.application.diagnostics import CheckContext, Checker
from quant_workbench.application.environments import EnvironmentStatus
from quant_workbench.application.settings import Settings
from quant_workbench.domain.diagnostics import Finding, Severity
from quant_workbench.domain.git import GitState
from quant_workbench.domain.graph import Dependency, DependencyGraph, EdgeOrigin
from quant_workbench.domain.project import ConfigTarget, ManifestSource, Project, ProjectSpec
from quant_workbench.domain.requirements import Requirement
from tests.fakes import FakeGit, FakeRepo, MultiFiles, make_project

EMAIL = "maria@example.org"
ROOT = Path("workspace")


class StubEnvironments:
    """EnvironmentService stand-in that reports a fixed status."""

    def __init__(self, status: EnvironmentStatus) -> None:
        self.status = status

    async def inspect(self, project: Project) -> EnvironmentStatus:
        return self.status


def healthy() -> EnvironmentStatus:
    return EnvironmentStatus(Path("py"), "3.13.1", {"numpy": "2"}, True, ())


def build(
    projects: list[Project],
    files: MultiFiles,
    *,
    git: FakeGit | None = None,
    status: EnvironmentStatus | None = None,
    settings: Settings | None = None,
    environ: dict[str, str] | None = None,
    edges: list[Dependency] | None = None,
    missing: tuple[ProjectSpec, ...] = (),
    workspace: Path = ROOT,
) -> CheckContext:
    graph = DependencyGraph([p.slug for p in projects], edges or [])
    catalog = Catalog(workspace, tuple(projects), graph, missing)
    return CheckContext(
        catalog=catalog,
        settings=settings or Settings(expected_git_email=EMAIL),
        files=files,  # type: ignore[arg-type]
        git=git or FakeGit(),  # type: ignore[arg-type]
        environments=StubEnvironments(status or healthy()),  # type: ignore[arg-type]
        runs=None,
        environ=environ or {},
    )


async def check(checker: Checker, project: Project | None, context: CheckContext) -> list[Finding]:
    return list(await checker.check(project, context))


def codes(findings: list[Finding]) -> list[str]:
    return [f.code for f in findings]


def alpha(**kwargs: object) -> Project:
    return make_project(ROOT, "alpha", **kwargs)  # type: ignore[arg-type]


# ------------------------------------------------------------------ environment
async def test_a_missing_environment_is_an_error_with_a_fix() -> None:
    project = alpha()
    status = EnvironmentStatus(None, None, {}, True, (Requirement("numpy"),))
    context = build([project], MultiFiles(), status=status)

    (finding,) = await check(EnvironmentChecker(), project, context)

    assert (finding.code, finding.severity, finding.fix) == (
        "venv-missing",
        Severity.ERROR,
        "setup-environment",
    )


async def test_missing_packages_are_listed() -> None:
    project = alpha()
    missing = tuple(Requirement(f"pkg{i}") for i in range(10))
    context = build(
        [project], MultiFiles(), status=EnvironmentStatus(Path("py"), "3.13", {}, True, missing)
    )

    (finding,) = await check(EnvironmentChecker(), project, context)

    assert finding.code == "requirements-missing"
    assert "10 required package(s)" in finding.message
    assert "and 2 more" in finding.message
    assert finding.fix == "setup-environment"


async def test_a_healthy_environment_and_a_missing_requirements_file() -> None:
    project = alpha()
    assert await check(EnvironmentChecker(), project, build([project], MultiFiles())) == []

    no_file = EnvironmentStatus(Path("py"), "3.13", {}, False, ())
    context = build([project], MultiFiles(), status=no_file)
    assert codes(await check(EnvironmentChecker(), project, context)) == [
        "requirements-file-missing"
    ]


async def test_utf8_mode_with_an_accented_workspace_is_flagged() -> None:
    accented = Path("C:/Users/x/Quant - María")
    risky = build([alpha()], MultiFiles(), environ={"PYTHONUTF8": "1"}, workspace=accented)
    safe_env = build([alpha()], MultiFiles(), environ={}, workspace=accented)
    safe_path = build([alpha()], MultiFiles(), environ={"PYTHONUTF8": "1"})

    (finding,) = await check(EnvironmentHazardsChecker(), None, risky)
    assert finding.code == "utf8-mode-with-non-ascii-path"
    assert finding.severity is Severity.WARNING
    assert "adr/0004" in finding.detail
    assert await check(EnvironmentHazardsChecker(), None, safe_env) == []
    assert await check(EnvironmentHazardsChecker(), None, safe_path) == []


async def test_pythonpath_is_noted() -> None:
    context = build([alpha()], MultiFiles(), environ={"PYTHONPATH": "/somewhere"})

    assert codes(await check(EnvironmentHazardsChecker(), None, context)) == ["pythonpath-set"]


# ---------------------------------------------------------------------- ssl cert
async def test_yfinance_without_a_ca_bundle_is_an_error() -> None:
    project = alpha()
    files = MultiFiles({"alpha": {"src/data.py": "import yfinance as yf\n"}})

    (finding,) = await check(SslCertChecker(), project, build([project], files))

    assert finding.code == "ssl-cert-missing"
    assert finding.severity is Severity.ERROR
    assert finding.fix == "copy-ca-bundle"
    assert finding.location is not None
    assert finding.location.file == "src/data.py"


async def test_the_ca_bundle_or_no_yfinance_means_no_finding() -> None:
    project = alpha()
    with_bundle = MultiFiles(
        {"alpha": {"src/data.py": "import yfinance\n", ".certs/cacert.pem": b"pem"}}
    )
    no_yfinance = MultiFiles({"alpha": {"src/data.py": "import numpy\n"}})
    broken = MultiFiles({"alpha": {"src/data.py": "def (:\n"}})

    assert await check(SslCertChecker(), project, build([project], with_bundle)) == []
    assert await check(SslCertChecker(), project, build([project], no_yfinance)) == []
    assert await check(SslCertChecker(), project, build([project], broken)) == []


# ------------------------------------------------------------------- collisions
LOADER = dedent(
    """
    import sys
    from pathlib import Path

    def load(name):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / name / "src"))
        import config
        return config
    """
)


async def test_an_uncleaned_sibling_import_is_a_warning_with_a_location() -> None:
    project = alpha()
    files = MultiFiles({"alpha": {"src/loader.py": LOADER}})

    (finding,) = await check(ImportCollisionChecker(), project, build([project], files))

    assert finding.code == "import-collision-risk"
    assert finding.severity is Severity.WARNING
    assert str(finding.location) == "src/loader.py:6"


async def test_the_own_root_insertion_and_broken_files_are_fine() -> None:
    project = alpha()
    files = MultiFiles(
        {
            "alpha": {
                "src/a.py": "import sys\nfrom pathlib import Path\n"
                "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n",
                "src/b.py": "def (:\n",
            }
        }
    )

    assert await check(ImportCollisionChecker(), project, build([project], files)) == []


# --------------------------------------------------------------------- siblings
def spec_for(folder: str) -> ProjectSpec:
    return ProjectSpec(
        slug=folder.replace("proyecto-", "p").replace("_", "-"),  # type: ignore[arg-type]
        title=folder,
        folder=folder,
        category="Test",
        order=1,
    )


async def test_a_reference_to_a_sibling_that_is_not_in_the_workspace_is_an_error() -> None:
    project = alpha()
    files = MultiFiles(
        {"alpha": {"src/x.py": 'P = "proyecto-6-opciones"\nQ = "proyecto-other"\nR = "hello"\n'}}
    )
    missing = (spec_for("proyecto-other"),)

    findings = await check(
        SiblingReferencesChecker(), project, build([project], files, missing=missing)
    )

    assert codes(findings) == ["sibling-missing", "sibling-missing"]
    assert {f.message.split("'")[1] for f in findings} == {"proyecto-6-opciones", "proyecto-other"}
    assert all(f.severity is Severity.ERROR for f in findings)


async def test_a_sibling_that_is_present_resolves() -> None:
    other = Project(spec_for("proyecto-2-x"), ROOT / "proyecto-2-x", ManifestSource.REGISTRY)
    project = alpha()
    files = MultiFiles({"alpha": {"src/x.py": 'P = "proyecto-2-x"\n'}})

    assert await check(SiblingReferencesChecker(), project, build([project, other], files)) == []


async def test_declared_and_detected_dependencies_are_compared() -> None:
    a, b, c = alpha(), make_project(ROOT, "beta"), make_project(ROOT, "gamma")
    edges = [
        Dependency(a.slug, b.slug, frozenset({EdgeOrigin.DETECTED})),
        Dependency(a.slug, c.slug, frozenset({EdgeOrigin.DECLARED})),
        Dependency(b.slug, c.slug, frozenset({EdgeOrigin.DECLARED, EdgeOrigin.DETECTED})),
    ]
    context = build([a, b, c], MultiFiles(), edges=edges)

    findings = await check(SiblingReferencesChecker(), a, context)

    assert {(f.code, f.severity) for f in findings} == {
        ("dependency-undeclared", Severity.WARNING),
        ("dependency-not-detected", Severity.INFO),
    }
    assert await check(SiblingReferencesChecker(), b, context) == []


# -------------------------------------------------------------------------- git
def repo_at(project: Project, git: FakeGit, **kwargs: object) -> FakeRepo:
    repo = FakeRepo(**kwargs)  # type: ignore[arg-type]
    git.repos[project.root] = repo
    return repo


async def test_the_wrong_git_email_is_an_error_with_a_fix() -> None:
    project = alpha()
    git = FakeGit()
    repo_at(project, git, config={"user.email": "carlos@elsewhere.com"})

    (finding,) = await check(GitIdentityChecker(), project, build([project], MultiFiles(), git=git))

    assert finding.code == "git-email-mismatch"
    assert finding.severity is Severity.ERROR
    assert finding.fix == "set-git-identity"
    assert "carlos@elsewhere.com" in finding.message


async def test_no_email_at_all_is_also_a_mismatch() -> None:
    project = alpha()
    git = FakeGit()
    repo_at(project, git)

    (finding,) = await check(GitIdentityChecker(), project, build([project], MultiFiles(), git=git))

    assert "no e-mail configured" in finding.message


async def test_the_right_identity_and_remote_are_clean() -> None:
    project = alpha()
    git = FakeGit()
    repo_at(
        project,
        git,
        config={"user.email": EMAIL, "user.name": "María"},
        remote="git@github.com-maria:owner/repo.git",
    )
    settings = Settings(expected_git_email=EMAIL, expected_git_name="María")

    assert (
        await check(
            GitIdentityChecker(),
            project,
            build([project], MultiFiles(), git=git, settings=settings),
        )
        == []
    )


async def test_a_wrong_name_or_remote_host_is_a_warning() -> None:
    project = alpha()
    git = FakeGit()
    repo_at(
        project,
        git,
        config={"user.email": EMAIL, "user.name": "Someone"},
        remote="git@github.com:owner/repo.git",
    )
    settings = Settings(expected_git_email=EMAIL, expected_git_name="María")

    findings = await check(
        GitIdentityChecker(), project, build([project], MultiFiles(), git=git, settings=settings)
    )

    assert {(f.code, f.severity) for f in findings} == {
        ("git-name-mismatch", Severity.WARNING),
        ("git-remote-host", Severity.WARNING),
    }


async def test_a_folder_that_is_not_a_repository_is_noted_not_failed() -> None:
    project = alpha()
    context = build([project], MultiFiles(), git=FakeGit())

    (finding,) = await check(GitIdentityChecker(), project, context)
    assert finding.code == "not-a-repository"
    assert finding.severity is Severity.INFO
    assert await check(GitHygieneChecker(), project, context) == []


async def test_hygiene_reports_uncommitted_and_unpushed_work() -> None:
    project = alpha()
    git = FakeGit()
    dirty = tuple(f"f{i}.py" for i in range(8))
    repo_at(project, git, state=GitState("main", dirty, ahead=2, has_upstream=True))

    findings = await check(GitHygieneChecker(), project, build([project], MultiFiles(), git=git))

    assert codes(findings) == ["uncommitted-changes", "unpushed-commits"]
    assert "8 path(s)" in findings[0].message
    assert "and 3 more" in findings[0].message
    assert all(f.severity is Severity.INFO for f in findings)


async def test_hygiene_flags_large_tracked_files() -> None:
    project = alpha()
    git = FakeGit()
    repo_at(project, git, tracked=("big.bin", "small.txt"))
    files = MultiFiles({"alpha": {"big.bin": b"x" * (2 * 1024 * 1024), "small.txt": "ok"}})
    settings = Settings(expected_git_email=EMAIL, max_tracked_file_mb=1)

    (finding,) = await check(
        GitHygieneChecker(), project, build([project], files, git=git, settings=settings)
    )

    assert finding.code == "large-file"
    assert "2.0 MB" in finding.message


async def test_hygiene_finds_secrets_without_echoing_them() -> None:
    project = alpha()
    git = FakeGit()
    secret = "AKIAABCDEFGHIJKLMNOP"
    repo_at(project, git, tracked=("config/keys.py", "image.png", ".certs/cacert.pem"))
    files = MultiFiles(
        {
            "alpha": {
                "config/keys.py": f"AWS = '{secret}'\n",
                "image.png": f"{secret}".encode(),  # not a text file: not scanned
                ".certs/cacert.pem": "-----BEGIN CERTIFICATE-----",
            }
        }
    )

    (finding,) = await check(GitHygieneChecker(), project, build([project], files, git=git))

    assert finding.code == "secret-in-repository"
    assert finding.severity is Severity.ERROR
    assert str(finding.location) == "config/keys.py:1"
    assert secret not in finding.message + finding.detail


async def test_a_heuristic_secret_match_is_only_a_warning() -> None:
    project = alpha()
    git = FakeGit()
    repo_at(project, git, tracked=("a.py",))
    files = MultiFiles({"alpha": {"a.py": 'PASSWORD = "abcdefghijklmnop1234"\n'}})

    (finding,) = await check(GitHygieneChecker(), project, build([project], files, git=git))

    assert finding.severity is Severity.WARNING


async def test_a_venv_that_is_not_ignored_is_flagged(tmp_path: Path) -> None:
    project = make_project(tmp_path, "alpha")
    project.venv_dir.mkdir(parents=True)
    git = FakeGit()
    repo_at(project, git, ignored=frozenset())
    context = build([project], MultiFiles(), git=git)

    (finding,) = await check(GitHygieneChecker(), project, context)

    assert (finding.code, finding.fix) == ("venv-not-ignored", "ignore-venv")

    repo_at(project, git, ignored=frozenset({"venv/"}))
    assert await check(GitHygieneChecker(), project, context) == []


# ---------------------------------------------------------------------- outputs
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


async def test_missing_declared_outputs_are_warnings() -> None:
    project = alpha()
    context = build([project], MultiFiles({"alpha": {"src/a.py": "x = 1\n"}}))

    (finding,) = await check(OutputsFreshnessChecker(), project, context)

    assert finding.code == "output-missing"
    assert "outputs/dashboard.html" in finding.message


async def test_a_dashboard_older_than_the_code_is_stale() -> None:
    project = alpha(config_files=["config/a.py"])
    files = MultiFiles(
        {"alpha": {"outputs/dashboard.html": "<html>", "src/a.py": "x", "config/a.py": "Y = 1\n"}}
    )
    files.mtimes[("alpha", "outputs/dashboard.html")] = NOW
    files.mtimes[("alpha", "src/a.py")] = NOW - timedelta(hours=1)
    files.mtimes[("alpha", "config/a.py")] = NOW + timedelta(minutes=5)

    (finding,) = await check(OutputsFreshnessChecker(), project, build([project], files))

    assert finding.code == "outputs-stale"
    assert finding.severity is Severity.INFO
    assert "config/a.py" in finding.message


async def test_a_fresh_dashboard_is_fine_within_the_timestamp_tolerance() -> None:
    project = alpha()
    files = MultiFiles({"alpha": {"outputs/dashboard.html": "<html>", "src/a.py": "x"}})
    files.mtimes[("alpha", "outputs/dashboard.html")] = NOW
    files.mtimes[("alpha", "src/a.py")] = NOW + timedelta(seconds=1)  # within the tolerance

    assert await check(OutputsFreshnessChecker(), project, build([project], files)) == []


# ------------------------------------------------------------------ code quality
async def test_syntax_errors_and_bug_patterns_are_reported() -> None:
    project = alpha()
    files = MultiFiles(
        {
            "alpha": {
                "src/broken.py": "def f(:\n    pass\n",
                "src/risky.py": "import os\n\n\ndef f(x=[]):\n    try:\n        pass\n    except:\n        pass\n",
            }
        }
    )

    findings = await check(StaticAnalysisChecker(), project, build([project], files))

    by_code = {f.code: f for f in findings}
    assert by_code["syntax-error"].severity is Severity.ERROR
    assert by_code["syntax-error"].location is not None
    assert by_code["syntax-error"].location.line == 1
    assert by_code["bare-except"].severity is Severity.WARNING
    assert by_code["mutable-default"].severity is Severity.WARNING
    assert by_code["unused-imports"].severity is Severity.INFO
    assert "os" in by_code["unused-imports"].message


async def test_explainability_scores_the_project_and_points_at_the_worst_functions() -> None:
    project = alpha()
    branches = "\n".join(f"    if a{i}:\n        pass" for i in range(12))
    files = MultiFiles(
        {"alpha": {"src/a.py": f"def tangled(a0):\n{branches}\n\n\ndef plain(x):\n    return x\n"}}
    )
    context = build([project], files)

    findings = await check(ExplainabilityChecker(), project, context)

    assert project.slug in context.scores
    assert context.scores[project.slug].score < 70
    assert {"explainability-low", "function-too-complex", "missing-docstrings"} <= set(
        codes(findings)
    )
    low = next(f for f in findings if f.code == "explainability-low")
    assert low.severity is Severity.WARNING


async def test_a_well_documented_project_only_gets_a_score() -> None:
    project = alpha()
    source = dedent(
        '''\
        """Module."""

        # Explain why the function exists before showing how it works.
        # A second line of explanation, since comments are cheap.
        def double(x):
            """Twice x."""
            return 2 * x
        '''
    )
    context = build([project], MultiFiles({"alpha": {"src/a.py": source}}))

    assert await check(ExplainabilityChecker(), project, context) == []
    assert context.scores[project.slug].score >= 70


async def test_a_project_without_parsable_source_has_no_score() -> None:
    project = alpha()
    context = build([project], MultiFiles({"alpha": {"src/a.py": "def (:\n"}}))

    assert await check(ExplainabilityChecker(), project, context) == []
    assert project.slug not in context.scores


# ---------------------------------------------------------------------- registry
def test_every_default_checker_has_a_unique_id_and_a_title() -> None:
    checkers = default_checkers()

    assert len({c.id for c in checkers}) == len(checkers)
    assert all(c.title for c in checkers)
    assert {c.id for c in checkers if c.opt_in} == {"determinism"}
    assert {c.id for c in checkers if c.scope == "workspace"} == {"environment-hazards"}


def test_config_targets_are_used_for_freshness_inputs() -> None:
    spec = alpha(config_files=["config/a.py"]).spec

    assert spec.config_targets == (ConfigTarget("config/a.py"),)


async def test_a_committed_venv_is_an_error_without_an_automatic_fix(tmp_path: Path) -> None:
    project = make_project(tmp_path, "alpha")
    project.venv_dir.mkdir(parents=True)
    git = FakeGit()
    repo_at(project, git, tracked=("venv/pyvenv.cfg", "src/a.py"))

    (finding,) = await check(GitHygieneChecker(), project, build([project], MultiFiles(), git=git))

    assert (finding.code, finding.severity, finding.fix) == ("venv-tracked", Severity.ERROR, None)
    assert "git rm -r --cached venv" in finding.detail
