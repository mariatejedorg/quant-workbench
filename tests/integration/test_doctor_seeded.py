"""M4 quality gate: the doctor finds every defect seeded in a synthetic workspace.

The workspace lives in ``Quant - María``-like path (accent and space), each project is a real
git repository, and each defect is one the portfolio has actually suffered. Real adapters
throughout: files, git and the composition root; only the virtual environments are left out
(building one is what the CLI tests do).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from quant_workbench.application.diagnostics import DoctorOptions
from quant_workbench.application.settings import Settings
from quant_workbench.bootstrap import Container, build_container
from quant_workbench.cli.main import app
from quant_workbench.domain.diagnostics import DoctorReport, Severity
from quant_workbench.domain.ids import Slug
from quant_workbench.domain.paths import AppPaths
from quant_workbench.infrastructure.paths import ensure_app_dirs
from tests.support import manifest_toml, write_project

pytestmark = pytest.mark.integration

MARIA = "maria@example.org"
runner = CliRunner()

LOADER_WITHOUT_CLEANUP = dedent(
    """\
    import sys
    from pathlib import Path


    def load(name):
        \"\"\"Loads a sibling project's module (and leaks its `config` package).\"\"\"
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / name / "src"))
        import config

        return config
    """
)


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Throw-away git config, and the policy of the tests instead of the author's."""
    global_config = tmp_path / "global-gitconfig"
    global_config.write_text("[user]\n\temail = carlos@global.example\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("QW_EXPECTED_GIT_EMAIL", MARIA)
    monkeypatch.setenv("QW_MAX_TRACKED_FILE_MB", "1")
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONPATH", raising=False)


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=T", "-c", "user.email=t@example.org", *args],
        capture_output=True,
        check=True,
    )


def commit_all(root: Path, *, local_email: str | None) -> None:
    git(root, "init", "-q", "-b", "main")
    if local_email is not None:
        git(root, "config", "--local", "user.email", local_email)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")


def project(
    root: Path, folder: str, slug: str, *, files: dict[str, str], deps: tuple[str, ...] = ()
) -> Path:
    path = write_project(
        root,
        folder,
        manifest=manifest_toml(slug, folder=folder, deps=deps),
        extra_files=files,
    )
    return path


@pytest.fixture
def workspace(accented_root: Path) -> Path:
    """Three projects; ``alpha`` and ``beta`` each carry a different set of defects."""
    # alpha: uses yfinance without the CA bundle, has a venv folder that is not ignored, and
    # relies on the *global* git identity (which is not the portfolio's)
    alpha = project(
        accented_root,
        "proyecto-1-alpha",
        "alpha",
        files={"src/data.py": "import yfinance as yf\n\nDATA = yf\n"},
    )
    commit_all(alpha, local_email=None)
    (alpha / "venv").mkdir()  # created after the commit: present, untracked, not ignored
    (alpha / "venv" / "pyvenv.cfg").write_text("home=x\n", encoding="utf-8")

    # beta: sibling loader that leaks `config`, a reference to a sibling that is not there,
    # a file that does not parse, a large tracked file, a committed credential, and a
    # dashboard older than the code that produces it
    beta = project(
        accented_root,
        "proyecto-2-beta",
        "beta",
        files={
            "src/loader.py": LOADER_WITHOUT_CLEANUP,
            "src/refs.py": 'MISSING = "proyecto-9-fantasma"\n',
            "src/broken.py": "def oops(:\n",
            "config/keys.py": "AWS = 'AKIAABCDEFGHIJKLMNOP'\n",
            "data_dump.bin": "x" * (2 * 1024 * 1024),
            ".certs/cacert.pem": "-----BEGIN CERTIFICATE-----\n",
            "venv/pyvenv.cfg": "home=x\n",  # a virtual environment committed by mistake
        },
    )
    old = time.time() - 3600
    os.utime(beta / "outputs" / "dashboard.html", (old, old))
    commit_all(beta, local_email=MARIA)

    # gamma: the well-behaved one, and the source of the CA bundle
    gamma = project(
        accented_root,
        "proyecto-3-gamma",
        "gamma",
        files={
            "src/data.py": '"""Data."""\n\n\ndef load():\n    """Load it."""\n    return 1\n',
            ".certs/cacert.pem": "-----BEGIN CERTIFICATE-----\nPEM\n",
        },
    )
    commit_all(gamma, local_email=MARIA)
    return accented_root


OPEN_CONTAINERS: list[Container] = []


@pytest.fixture(autouse=True)
def close_containers() -> Iterator[None]:
    """Release the (in-memory) run database of every container a test built."""
    yield
    while OPEN_CONTAINERS:
        OPEN_CONTAINERS.pop().close()


def container_for(workspace: Path) -> Container:
    paths = AppPaths.under(workspace / "app-home")
    ensure_app_dirs(paths)
    container = build_container(paths=paths, settings=Settings(), database=None)
    OPEN_CONTAINERS.append(container)
    return container


async def diagnose(workspace: Path, **options: frozenset[str]) -> tuple[DoctorReport, Container]:
    container = container_for(workspace)
    catalog = container.catalog.load(workspace)
    context = container.check_context(catalog)
    report = await container.doctor.run(context, options=DoctorOptions(**options))
    return report, container


def pairs(report: DoctorReport) -> set[tuple[str, str]]:
    return {(f.project or "-", f.code) for f in report.findings}


async def test_every_seeded_defect_is_found(workspace: Path) -> None:
    report, _ = await diagnose(workspace, skip=frozenset({"environment"}))

    found = pairs(report)
    assert {
        ("alpha", "ssl-cert-missing"),
        ("alpha", "git-email-mismatch"),
        ("alpha", "venv-not-ignored"),
        ("beta", "import-collision-risk"),
        ("beta", "sibling-missing"),
        ("beta", "syntax-error"),
        ("beta", "large-file"),
        ("beta", "secret-in-repository"),
        ("beta", "outputs-stale"),
        ("beta", "venv-tracked"),
    } <= found


async def test_the_well_behaved_project_has_no_errors_or_warnings(workspace: Path) -> None:
    report, _ = await diagnose(workspace, skip=frozenset({"environment"}))

    gamma = report.for_project(Slug("gamma"))
    assert [f for f in gamma if f.severity is not Severity.INFO] == []
    assert not report.ok  # ...but the workspace as a whole has errors


async def test_findings_carry_locations_and_severities(workspace: Path) -> None:
    report, _ = await diagnose(workspace, skip=frozenset({"environment"}))

    by_code = {f.code: f for f in report.findings if f.project == "beta"}
    assert str(by_code["import-collision-risk"].location) == "src/loader.py:7"
    assert str(by_code["sibling-missing"].location) == "src/refs.py:1"
    assert str(by_code["secret-in-repository"].location) == "config/keys.py:1"
    assert by_code["syntax-error"].severity is Severity.ERROR
    assert by_code["import-collision-risk"].severity is Severity.WARNING
    assert "AKIAABCDEFGHIJKLMNOP" not in json.dumps([f.message + f.detail for f in report.findings])


async def test_the_utf8_hazard_is_found_only_when_the_variable_is_set(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quiet, _ = await diagnose(workspace, only=frozenset({"environment-hazards"}))
    monkeypatch.setenv("PYTHONUTF8", "1")
    loud, _ = await diagnose(workspace, only=frozenset({"environment-hazards"}))

    assert quiet.findings == ()
    assert [f.code for f in loud.findings] == ["utf8-mode-with-non-ascii-path"]


async def test_a_project_without_an_environment_is_an_error(workspace: Path) -> None:
    report, _ = await diagnose(workspace, only=frozenset({"environment"}))

    assert {(f.project, f.code) for f in report.findings} == {
        ("alpha", "venv-missing"),
        ("beta", "venv-missing"),
        ("gamma", "venv-missing"),
    }
    assert all(f.fix == "setup-environment" for f in report.findings)


async def test_the_fixes_repair_what_they_promise_and_the_recheck_agrees(workspace: Path) -> None:
    report, container = await diagnose(workspace, skip=frozenset({"environment"}))
    context = container.check_context(container.catalog.load(workspace))
    fixable = [f for f in report.findings if container.fixes.can_fix(f)]
    assert {f.fix for f in fixable} == {"copy-ca-bundle", "set-git-identity", "ignore-venv"}

    for finding in fixable:
        await container.fixes.preview(finding, context)  # previews must not raise or write
    alpha = workspace / "proyecto-1-alpha"
    assert not (alpha / ".certs" / "cacert.pem").exists()
    for finding in fixable:
        await container.fixes.apply(finding, context)

    assert "BEGIN CERTIFICATE" in (alpha / ".certs" / "cacert.pem").read_text(encoding="utf-8")
    assert "venv/" in (alpha / ".gitignore").read_text(encoding="utf-8")
    after, _ = await diagnose(workspace, skip=frozenset({"environment"}))
    still = pairs(after)
    assert ("alpha", "ssl-cert-missing") not in still
    assert ("alpha", "git-email-mismatch") not in still
    assert ("alpha", "venv-not-ignored") not in still
    assert ("beta", "syntax-error") in still  # no automatic fix for a real bug
    local = subprocess.run(
        ["git", "-C", str(alpha), "config", "--local", "user.email"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert local == MARIA


async def test_the_global_git_config_is_never_modified(workspace: Path, tmp_path: Path) -> None:
    global_config = tmp_path / "global-gitconfig"
    before = global_config.read_bytes()
    report, container = await diagnose(workspace, skip=frozenset({"environment"}))
    context = container.check_context(container.catalog.load(workspace))

    for finding in report.findings:
        if finding.fix == "set-git-identity":
            await container.fixes.apply(finding, context)

    assert global_config.read_bytes() == before


# ------------------------------------------------------------------------------- CLI
def qw(workspace: Path, *args: str) -> tuple[int, str]:
    result = runner.invoke(app, ["--home", str(workspace / "app-home"), *args])
    return result.exit_code, result.output


def test_the_cli_fails_the_run_when_errors_are_found_and_lists_them(workspace: Path) -> None:
    code, output = qw(
        workspace,
        "doctor",
        "-w",
        str(workspace),
        "--skip",
        "environment",
        "--min-severity",
        "error",
    )

    assert code == 1
    assert "ssl-cert-missing" in output
    assert "syntax-error" in output
    assert "fix available" in output
    assert "error(s)" in output


def test_the_cli_json_output_is_machine_readable(workspace: Path) -> None:
    code, output = qw(workspace, "doctor", "-w", str(workspace), "--skip", "environment", "--json")

    payload = json.loads(output)
    assert code == 1
    assert payload["ok"] is False
    assert payload["counts"]["error"] >= 5
    codes = {(f["project"], f["code"]) for f in payload["findings"]}
    assert ("alpha", "ssl-cert-missing") in codes
    assert set(payload["explainability"]) <= {"alpha", "beta", "gamma"}


def test_the_cli_can_diagnose_a_single_project(workspace: Path) -> None:
    code, output = qw(workspace, "doctor", "gamma", "-w", str(workspace), "--skip", "environment")

    assert code == 0, output
    assert "ssl-cert-missing" not in output
    assert "0 error(s)" in output


def test_the_cli_lists_the_checkers(workspace: Path) -> None:
    code, output = qw(workspace, "doctor", "--list")

    assert code == 0
    for checker_id in (
        "environment",
        "ssl-cert",
        "import-collision",
        "git-identity",
        "determinism",
    ):
        assert checker_id in output
    assert "no (opt-in)" in output


def test_unknown_checker_names_are_an_expected_failure(workspace: Path) -> None:
    code, output = qw(workspace, "doctor", "-w", str(workspace), "--only", "nope")

    assert code == 2
    assert "Unknown checker(s): nope" in output
    assert "ssl-cert" in output  # the hint lists the known ones


def test_fix_with_yes_applies_every_safe_fix_and_rechecks(workspace: Path) -> None:
    code, output = qw(
        workspace,
        "doctor",
        "alpha",
        "-w",
        str(workspace),
        "--skip",
        "environment",
        "--fix",
        "--yes",
    )

    assert code == 0, output  # every error of alpha had an automatic fix
    assert "Copied .certs/cacert.pem" in output
    assert "Re-checking after the fixes" in output
    assert (workspace / "proyecto-1-alpha" / ".certs" / "cacert.pem").is_file()


def test_fix_asks_before_applying_and_a_no_changes_nothing(workspace: Path) -> None:
    result = runner.invoke(
        app,
        [
            "--home",
            str(workspace / "app-home"),
            "doctor",
            "alpha",
            "-w",
            str(workspace),
            "--skip",
            "environment",
            "--fix",
        ],
        input="n\nn\nn\n",
    )

    assert "Apply this fix?" in result.output
    assert "skipped" in result.output
    assert not (workspace / "proyecto-1-alpha" / ".certs" / "cacert.pem").exists()
