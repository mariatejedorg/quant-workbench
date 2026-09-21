"""`qw new`: the generated project must be valid, healthy and actually run."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jinja2 import UndefinedError
from typer.testing import CliRunner

from quant_workbench.bootstrap import data_path
from quant_workbench.cli.main import app
from quant_workbench.infrastructure.templates import JinjaProjectTemplates
from tests.support import manifest_toml, write_project

pytestmark = pytest.mark.integration

runner = CliRunner()

CONTEXT: dict[str, str | int] = {
    "slug": "pairs-trading",
    "title": "Pairs Trading",
    "module": "pairs_trading",
    "folder": "proyecto-1-pairs-trading",
    "category": "New projects",
    "description": "Cointegration between two assets.",
    "order": 10,
}


def qw(workspace: Path, *args: str) -> tuple[int, str]:
    result = runner.invoke(app, ["--home", str(workspace / "home"), *args, "-w", str(workspace)])
    return result.exit_code, result.output


def test_the_template_renders_every_file_of_the_project() -> None:
    templates = JinjaProjectTemplates(data_path("templates") / "project")

    files = templates.render(CONTEXT)

    assert set(files) == {
        "quant-project.toml",
        "README.md",
        "requirements.txt",
        ".gitignore",
        "config/pairs_trading.py",
        "src/main.py",
        "src/simulate.py",
        "src/dashboard.py",
        "outputs/.gitkeep",
    }
    assert "Pairs Trading" in files["README.md"]
    assert "from config.pairs_trading import" in files["src/main.py"]
    assert "{{" not in files["src/main.py"]  # nothing left unrendered
    assert "{{" in files["src/dashboard.py"]  # verbatim: its braces belong to the CSS


def test_a_missing_variable_is_an_error_not_an_empty_string() -> None:
    templates = JinjaProjectTemplates(data_path("templates") / "project")
    incomplete = {k: v for k, v in CONTEXT.items() if k != "title"}

    with pytest.raises(UndefinedError):
        templates.render(incomplete)


def test_new_creates_a_project_that_the_catalog_and_the_doctor_accept(accented_root: Path) -> None:
    code, output = qw(accented_root, "new", "pairs-trading", "--title", "Pairs Trading")

    assert code == 0, output
    root = accented_root / "proyecto-1-pairs-trading"
    assert (root / "src" / "main.py").is_file()
    assert (root / "config" / "pairs_trading.py").is_file()
    # A brand-new project has no environment and has not run yet; nothing else may be wrong.
    assert "venv-missing" in output
    assert "output-missing" in output
    assert "1 error(s), 1 warning(s)" in output  # exactly those two, no more
    code, listing = qw(accented_root, "list", "--json")
    assert code == 0
    (project,) = json.loads(listing)
    assert project["slug"] == "pairs-trading"
    assert project["folder"] == "proyecto-1-pairs-trading"
    assert project["source"] == "in-repo"  # discovered from the manifest we generated


def test_the_config_form_can_read_the_generated_parameters(accented_root: Path) -> None:
    qw(accented_root, "new", "pairs-trading")

    code, output = qw(accented_root, "config", "get", "pairs-trading", "--json")

    assert code == 0, output
    names = {c["name"] for c in json.loads(output)}
    assert {"SEED", "N_PATHS", "VOLATILITY", "DRIFT"} <= names
    assert all(c["description"] for c in json.loads(output))  # every constant is explained


def test_folders_continue_the_numbering_of_the_workspace(accented_root: Path) -> None:
    for name in ("proyecto-3-alpha", "proyecto-7-beta"):
        write_project(accented_root, name, manifest=manifest_toml(name.split("-", 2)[2]))

    code, _ = qw(accented_root, "new", "gamma")

    assert code == 0
    assert (accented_root / "proyecto-8-gamma" / "quant-project.toml").is_file()


def test_the_ca_bundle_is_inherited_from_a_sibling(accented_root: Path) -> None:
    write_project(
        accented_root,
        "alpha",
        manifest=manifest_toml("alpha"),
        extra_files={".certs/cacert.pem": "-----BEGIN CERTIFICATE-----\n"},
    )

    code, output = qw(accented_root, "new", "gamma", "--folder", "gamma")

    assert code == 0, output
    assert (
        (accented_root / "gamma" / ".certs" / "cacert.pem")
        .read_text(encoding="utf-8")
        .startswith("-----BEGIN")
    )
    assert "copied from alpha" in output


def test_a_taken_slug_or_folder_is_refused_without_touching_anything(accented_root: Path) -> None:
    write_project(accented_root, "alpha", manifest=manifest_toml("alpha"))

    code, output = qw(accented_root, "new", "alpha")
    assert code == 2
    assert "already exists" in output

    (accented_root / "occupied").mkdir()
    code, output = qw(accented_root, "new", "delta", "--folder", "occupied")
    assert code == 2
    assert "already exists" in output
    assert list((accented_root / "occupied").iterdir()) == []


def test_an_invalid_slug_is_an_expected_failure(accented_root: Path) -> None:
    code, output = qw(accented_root, "new", "Bad Name")

    assert code == 2
    assert "slug" in output.lower()


@pytest.mark.slow
def test_the_generated_project_runs_extracts_metrics_and_is_deterministic(
    accented_root: Path,
) -> None:
    qw(accented_root, "new", "pairs-trading")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "venv",
            "--without-pip",
            str(accented_root / "proyecto-1-pairs-trading" / "venv"),
        ],
        check=True,
        capture_output=True,
    )

    code, output = qw(accented_root, "run", "pairs-trading", "-q")
    assert code == 0, output

    outputs = accented_root / "proyecto-1-pairs-trading" / "outputs"
    assert "<html" in (outputs / "dashboard.html").read_text(encoding="utf-8")
    metrics = json.loads((outputs / "metrics.json").read_text(encoding="utf-8"))
    assert set(metrics) == {"expected_price", "sharpe", "max_drawdown_pct"}

    code, history = qw(accented_root, "history", "pairs-trading")
    assert code == 0
    assert "succeeded" in history

    # Same seed, same numbers: the doctor's determinism check runs the project twice.
    code, report = qw(
        accented_root, "doctor", "pairs-trading", "--deterministic", "--min-severity", "warning"
    )
    assert code == 0, report
    assert "0 error(s), 0 warning(s)" in report
