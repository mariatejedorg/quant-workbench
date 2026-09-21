from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from quant_workbench.application.catalog import Catalog
from quant_workbench.application.diagnostics import CheckContext
from quant_workbench.application.fixes import FixError, FixService
from quant_workbench.application.settings import Settings
from quant_workbench.domain.diagnostics import Finding, Severity
from quant_workbench.domain.graph import DependencyGraph
from quant_workbench.domain.runs import RunStatus
from tests.fakes import FakeGit, FakeRepo, MultiFiles, make_project

ROOT = Path("workspace")
EMAIL = "maria@example.org"


class StubEnvironments:
    def __init__(self, status: RunStatus = RunStatus.SUCCEEDED) -> None:
        self.status = status
        self.set_up: list[str] = []

    async def setup(self, project: object) -> SimpleNamespace:
        self.set_up.append(project.slug)  # type: ignore[attr-defined]
        return SimpleNamespace(status=self.status, id="run-1", failure="pip exploded")


def context(
    *slugs: str,
    files: MultiFiles | None = None,
    git: FakeGit | None = None,
    environments: StubEnvironments | None = None,
    settings: Settings | None = None,
) -> CheckContext:
    projects = tuple(make_project(ROOT, s) for s in slugs)
    return CheckContext(
        catalog=Catalog(ROOT, projects, DependencyGraph([p.slug for p in projects])),
        settings=settings or Settings(expected_git_email=EMAIL),
        files=files or MultiFiles(),  # type: ignore[arg-type]
        git=git or FakeGit(),  # type: ignore[arg-type]
        environments=environments or StubEnvironments(),  # type: ignore[arg-type]
        runs=None,
        environ={},
    )


def finding(fix: str | None, project: str | None = "alpha") -> Finding:
    return Finding("c", "code", Severity.ERROR, "m", project=project, fix=fix)  # type: ignore[arg-type]


async def test_the_ca_bundle_is_copied_from_a_sibling_that_has_it() -> None:
    files = MultiFiles({"beta": {".certs/cacert.pem": b"PEM DATA"}, "alpha": {}})
    ctx = context("alpha", "beta", files=files)
    service = FixService()

    preview = await service.preview(finding("copy-ca-bundle"), ctx)
    assert files.writes == []  # a preview never writes
    assert "beta" in preview.actions[0]
    message = await service.apply(finding("copy-ca-bundle"), ctx)

    assert files.files["alpha"][".certs/cacert.pem"] == b"PEM DATA"
    assert "Copied" in message


async def test_copying_the_ca_bundle_needs_a_donor() -> None:
    ctx = context("alpha", "beta", files=MultiFiles({"alpha": {}, "beta": {}}))

    with pytest.raises(FixError, match="No project in the workspace has a"):
        await FixService().preview(finding("copy-ca-bundle"), ctx)


async def test_the_environment_fix_previews_the_commands_and_runs_setup() -> None:
    environments = StubEnvironments()
    ctx = context("alpha", environments=environments)
    service = FixService()

    preview = await service.preview(finding("setup-environment"), ctx)
    assert environments.set_up == []
    assert any("-m venv" in action for action in preview.actions)
    assert any("pip install -r requirements.txt" in action for action in preview.actions)
    assert preview.summary.startswith("Create")

    await service.apply(finding("setup-environment"), ctx)
    assert environments.set_up == ["alpha"]


async def test_an_existing_venv_is_repaired_not_recreated(tmp_path: Path) -> None:
    project = make_project(tmp_path, "alpha")
    project.venv_dir.mkdir(parents=True)
    ctx = CheckContext(
        catalog=Catalog(tmp_path, (project,), DependencyGraph([project.slug])),
        settings=Settings(),
        files=MultiFiles(),  # type: ignore[arg-type]
        git=FakeGit(),  # type: ignore[arg-type]
        environments=StubEnvironments(),  # type: ignore[arg-type]
        runs=None,
        environ={},
    )

    preview = await FixService().preview(finding("setup-environment"), ctx)

    assert preview.summary.startswith("Repair")
    assert len(preview.actions) == 1


async def test_a_failed_environment_setup_is_reported_with_the_run_id() -> None:
    ctx = context("alpha", environments=StubEnvironments(RunStatus.FAILED))

    with pytest.raises(FixError, match="pip exploded") as raised:
        await FixService().apply(finding("setup-environment"), ctx)

    assert raised.value.hint is not None
    assert "qw logs run-1" in raised.value.hint


async def test_the_git_identity_is_set_locally_for_email_and_name() -> None:
    git = FakeGit()
    root = ROOT / "alpha"
    git.repos[root] = FakeRepo()
    settings = Settings(expected_git_email=EMAIL, expected_git_name="María")
    ctx = context("alpha", git=git, settings=settings)
    service = FixService()

    preview = await service.preview(finding("set-git-identity"), ctx)
    assert git.local_writes == []
    assert len(preview.actions) == 2
    assert all("--local" in action for action in preview.actions)

    await service.apply(finding("set-git-identity"), ctx)

    assert git.local_writes == [(root, "user.email", EMAIL), (root, "user.name", "María")]


async def test_the_git_identity_fix_without_a_configured_name_only_sets_the_email() -> None:
    git = FakeGit()
    git.repos[ROOT / "alpha"] = FakeRepo()
    ctx = context("alpha", git=git)

    await FixService().apply(finding("set-git-identity"), ctx)

    assert [key for _, key, _ in git.local_writes] == ["user.email"]


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        (None, "venv/\n"),
        ("", "venv/\n"),
        ("data/\n", "data/\nvenv/\n"),
        ("data/", "data/\nvenv/\n"),  # a file without a final newline gets one first
    ],
)
async def test_ignoring_the_venv_appends_one_line(existing: str | None, expected: str) -> None:
    files = MultiFiles({"alpha": {} if existing is None else {".gitignore": existing}})
    ctx = context("alpha", files=files)
    service = FixService()

    preview = await service.preview(finding("ignore-venv"), ctx)
    assert files.writes == []
    assert "+venv/" in preview.diff
    await service.apply(finding("ignore-venv"), ctx)

    assert files.files["alpha"][".gitignore"] == expected


def test_can_fix_only_knows_registered_fixes_tied_to_a_project() -> None:
    service = FixService()

    assert service.can_fix(finding("copy-ca-bundle"))
    assert not service.can_fix(finding("no-such-fix"))
    assert not service.can_fix(finding(None))
    assert not service.can_fix(finding("copy-ca-bundle", project=None))


async def test_resolving_a_finding_without_a_fix_is_an_error() -> None:
    ctx = context("alpha")

    with pytest.raises(FixError, match="no automatic fix"):
        await FixService().preview(finding(None), ctx)
    with pytest.raises(FixError, match="not tied to a project"):
        await FixService().preview(finding("copy-ca-bundle", project=None), ctx)
