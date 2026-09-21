"""`qw run`, `run-all`, `env`, `history` and `logs` end to end, on real venvs and processes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quant_workbench.cli.main import app
from tests.support import manifest_toml, write_project

pytestmark = pytest.mark.integration

runner = CliRunner()

OK_MAIN = 'print("Sharpe: 1.75")\nprint("María OK")\n'
FAIL_MAIN = "raise ValueError('boom')\n"


def make_venv(root: Path, *, with_pip: bool = False) -> None:
    args = [sys.executable, "-m", "venv", str(root / "venv")]
    if not with_pip:
        args.insert(3, "--without-pip")
    subprocess.run(args, check=True, capture_output=True)


@pytest.fixture
def workspace(accented_root: Path) -> Path:
    """`alpha` (fails or succeeds by fixture use) <- `beta`, each with a real venv."""
    for name, deps in (("alpha", ()), ("beta", ("alpha",))):
        write_project(
            accented_root,
            name,
            refs=list(deps),
            manifest=manifest_toml(name, deps=deps),
            extra_files={"src/main.py": OK_MAIN},
        )
        make_venv(accented_root / name)
    return accented_root


def qw(home: Path, *args: str) -> tuple[int, str]:
    result = runner.invoke(app, ["--home", str(home / "home"), *args])
    return result.exit_code, result.output


def test_running_projects_streams_output_and_summarises(workspace: Path) -> None:
    code, output = qw(workspace, "run", "alpha", "beta", "-w", str(workspace))

    assert code == 0, output
    assert "[alpha] Sharpe: 1.75" in output
    assert "[beta] María OK" in output
    assert output.count("SUCCEEDED") == 2
    assert "Summary" in output


def test_a_failing_project_sets_a_non_zero_exit_code_and_explains_why(workspace: Path) -> None:
    (workspace / "alpha" / "src" / "main.py").write_text(FAIL_MAIN, encoding="utf-8")

    code, output = qw(workspace, "run", "alpha", "-w", str(workspace), "--retries", "0")

    assert code == 1
    assert "FAILED" in output
    assert "ValueError: boom" in output


def test_run_all_can_skip_dependents_of_a_failure(workspace: Path) -> None:
    (workspace / "alpha" / "src" / "main.py").write_text(FAIL_MAIN, encoding="utf-8")

    code, output = qw(workspace, "run-all", "-w", str(workspace), "--skip-dependents", "-q")

    assert code == 1
    assert "skipped" in output.lower()
    assert "beta" in output


def test_history_and_logs_are_recorded_and_retrievable(workspace: Path) -> None:
    qw(workspace, "run", "alpha", "-w", str(workspace), "-q")

    code, history = qw(workspace, "history", "alpha", "-w", str(workspace))
    assert code == 0
    assert "succeeded" in history

    run_id = next(w for w in history.split() if "-" in w and len(w) >= 20 and w[:6].isalnum())
    code, logs = qw(workspace, "logs", run_id)
    assert code == 0
    assert "Sharpe: 1.75" in logs
    assert "María OK" in logs


def test_logs_of_an_unknown_run_is_an_expected_failure(workspace: Path) -> None:
    code, output = qw(workspace, "logs", "does-not-exist")

    assert code == 2
    assert "No run with id" in output


def test_a_project_without_an_environment_fails_with_a_hint(accented_root: Path) -> None:
    write_project(accented_root, "novenv", manifest=manifest_toml("novenv"))
    write_project(accented_root, "other", manifest=manifest_toml("other"))

    code, output = qw(accented_root, "run", "novenv", "-w", str(accented_root))

    assert code == 1
    assert "qw env setup novenv" in output


def test_env_status_flags_missing_packages(workspace: Path) -> None:
    code, output = qw(workspace, "env", "status", "alpha", "-w", str(workspace))

    assert code == 1  # requirements.txt lists numpy; a venv created without pip has nothing
    assert "numpy" in output


def test_unknown_project_names_are_expected_failures(workspace: Path) -> None:
    code, output = qw(workspace, "run", "alpa", "-w", str(workspace))

    assert code == 2
    assert "Did you mean: alpha" in output


@pytest.mark.slow
def test_env_setup_builds_a_working_environment_from_scratch(accented_root: Path) -> None:
    write_project(accented_root, "fresh", manifest=manifest_toml("fresh"))
    write_project(accented_root, "sibling", manifest=manifest_toml("sibling"))
    (accented_root / "fresh" / "requirements.txt").write_text(
        "# nothing to install\n", encoding="utf-8"
    )

    code, output = qw(accented_root, "env", "setup", "fresh", "-w", str(accented_root), "-q")
    assert code == 0, output
    assert (accented_root / "fresh" / "venv").is_dir()

    code, output = qw(accented_root, "env", "status", "fresh", "-w", str(accented_root))
    assert code == 0, output
    assert "3." in output  # the python version of the new environment

    (accented_root / "fresh" / "src" / "main.py").write_text(
        'print("ran in the new venv")\n', encoding="utf-8"
    )
    code, output = qw(accented_root, "run", "fresh", "-w", str(accented_root))
    assert code == 0, output
    assert "ran in the new venv" in output
