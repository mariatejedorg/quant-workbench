from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quant_workbench.cli.main import app
from tests.support import manifest_toml, write_project

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def workspace(accented_root: Path) -> Path:
    write_project(accented_root, "engine", manifest=manifest_toml("engine", title="Engine"))
    write_project(
        accented_root,
        "consumer",
        refs=["engine"],
        manifest=manifest_toml("consumer", deps=["engine"], title="Consumer"),
    )
    return accented_root


def invoke(home: Path, *args: str) -> tuple[int, str]:
    result = runner.invoke(app, ["--home", str(home / "home"), *args])
    return result.exit_code, result.output


def test_list_as_json_is_machine_readable(workspace: Path) -> None:
    code, output = invoke(workspace, "list", "--workspace", str(workspace), "--json")

    assert code == 0
    projects = json.loads(output)
    assert [p["slug"] for p in projects] == ["consumer", "engine"]
    consumer = next(p for p in projects if p["slug"] == "consumer")
    assert consumer["depends_on"] == ["engine"]
    assert consumer["has_dashboard"] is True


def test_list_renders_a_table(workspace: Path) -> None:
    code, output = invoke(workspace, "list", "-w", str(workspace))

    assert code == 0
    assert "engine" in output
    assert "consumer" in output


def test_graph_text_shows_layers_and_edge_origin(workspace: Path) -> None:
    code, output = invoke(workspace, "graph", "-w", str(workspace))

    assert code == 0
    assert "1. engine" in output
    assert "2. consumer" in output
    assert "consumer -> engine" in output
    assert "declared + detected" in output


def test_graph_mermaid_and_json(workspace: Path) -> None:
    code, mermaid = invoke(workspace, "graph", "-w", str(workspace), "-f", "mermaid")
    assert code == 0
    assert mermaid.startswith("flowchart LR")
    assert "n_consumer --> n_engine" in mermaid

    code, raw = invoke(workspace, "graph", "-w", str(workspace), "-f", "json")
    payload = json.loads(raw)
    assert payload["layers"] == [["engine"], ["consumer"]]
    assert payload["edges"][0]["origins"] == ["declared", "detected"]


def test_impact_lists_dependents(workspace: Path) -> None:
    code, output = invoke(workspace, "graph", "-w", str(workspace), "--impact", "engine")

    assert code == 0
    assert "* engine" in output
    assert "- consumer" in output


def test_an_unknown_project_exits_with_code_2_and_a_hint(workspace: Path) -> None:
    code, output = invoke(workspace, "graph", "-w", str(workspace), "--impact", "engin")

    assert code == 2
    assert "error:" in output
    assert "Did you mean: engine" in output
    assert "Traceback" not in output


def test_a_bad_workspace_is_an_expected_failure(accented_root: Path) -> None:
    code, output = invoke(accented_root, "list", "-w", str(accented_root / "missing"))

    assert code == 2
    assert "not a directory" in output


def test_schema_prints_and_writes_valid_json(accented_root: Path) -> None:
    code, output = invoke(accented_root, "schema")
    assert code == 0
    assert json.loads(output)["title"] == "quant-project.toml"

    target = accented_root / "manifest.schema.json"
    code, _ = invoke(accented_root, "schema", "--write", str(target))
    assert code == 0
    assert json.loads(target.read_text(encoding="utf-8"))["type"] == "object"
