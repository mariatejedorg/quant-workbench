"""Acceptance test against the author's real workspace (skipped where it does not exist).

This is the M1 quality gate: static analysis of the ten real projects must rediscover
exactly the dependency edges their manifests declare, in both directions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quant_workbench.application.catalog import ProjectCatalog
from quant_workbench.application.config import ConfigService
from quant_workbench.application.settings import Settings
from quant_workbench.bootstrap import build_container
from quant_workbench.domain.diagnostics import Severity
from quant_workbench.domain.graph import EdgeOrigin
from quant_workbench.domain.paths import AppPaths
from quant_workbench.infrastructure.config_editor import LibCstConfigEditor
from quant_workbench.infrastructure.project_files import FileSystemProjectFiles
from quant_workbench.infrastructure.resources import data_path
from quant_workbench.infrastructure.workspace import FileSystemWorkspace

pytestmark = pytest.mark.integration

REAL_WORKSPACE = Path(__file__).resolve().parents[3]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (REAL_WORKSPACE / "proyecto-6-opciones-volatilidad-implicita").is_dir(),
        reason="the real portfolio workspace is not available here",
    ),
]

EXPECTED_EDGES = {
    ("capstone-multi-strategy-portfolio", "momentum-backtest"),
    ("capstone-multi-strategy-portfolio", "markowitz-efficient-frontier"),
    ("credit-risk-merton-model", "options-pricing-implied-volatility-surface"),
    ("credit-risk-merton-model", "monte-carlo-price-simulator"),
    ("options-pricing-implied-volatility-surface", "monte-carlo-price-simulator"),
    ("garch-volatility-forecasting", "options-pricing-implied-volatility-surface"),
}


def load() -> object:
    catalog = ProjectCatalog(FileSystemWorkspace(registry_dir=data_path("registry")))
    return catalog.load(REAL_WORKSPACE)


def test_all_ten_projects_are_discovered_from_the_registry() -> None:
    catalog = load()

    assert len(catalog.projects) == 10  # type: ignore[attr-defined]
    assert catalog.missing == ()  # type: ignore[attr-defined]


def test_static_analysis_finds_exactly_the_declared_dependencies() -> None:
    graph = load().graph  # type: ignore[attr-defined]

    edges = {(e.dependent, e.dependency): e.origins for e in graph.edges}

    assert set(edges) == EXPECTED_EDGES
    assert all(o == {EdgeOrigin.DECLARED, EdgeOrigin.DETECTED} for o in edges.values())
    assert graph.find_cycle() is None
    assert len(graph.generations()) == 3


# ------------------------------------------------------------ M3: the config editor
def test_every_project_exposes_its_declared_config_symbols_and_they_round_trip() -> None:
    """Reads the real files (never writes): editing each constant to its own value is a no-op."""
    catalog = load()
    service = ConfigService(files=FileSystemProjectFiles(), editor=LibCstConfigEditor())

    for project in catalog.projects:  # type: ignore[attr-defined]
        constants = service.constants(project)
        declared = {s for target in project.spec.config_targets for s in target.symbols}
        found = {c.constant.name for c in constants}

        assert constants, f"{project.slug} has no editable constants"
        assert declared <= found, f"{project.slug}: declared symbols not found: {declared - found}"
        same_values = {c.qualified_name: c.constant.value for c in constants}
        assert service.plan(project, same_values).is_empty, project.slug


def test_a_real_edit_is_previewed_as_a_minimal_diff_without_touching_the_disk() -> None:
    catalog = load()
    project = catalog.get("garch-volatility-forecasting")  # type: ignore[attr-defined]
    service = ConfigService(files=FileSystemProjectFiles(), editor=LibCstConfigEditor())
    path = project.root / "config" / "garch.py"
    before = path.read_bytes()

    plan = service.plan(project, {"FORECAST_HORIZON_DAYS": 45, "TICKER": "MSFT"})

    changes = [
        line.rstrip("\r")
        for line in plan.diff.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    assert changes == [
        '-TICKER = "AAPL"',
        '+TICKER = "MSFT"',
        "-FORECAST_HORIZON_DAYS = 30",
        "+FORECAST_HORIZON_DAYS = 45",
    ]
    assert path.read_bytes() == before


# ------------------------------------------------------------- M4: the doctor's gate
async def test_the_doctor_finds_no_errors_in_the_real_portfolio(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Zero false positives at ERROR level; warnings limited to the documented ones.

    The only warning tolerated is ``explainability-low``: it is a real observation (some
    projects have public functions without a docstring), not a defect in the doctor.
    """
    container = build_container(
        paths=AppPaths.under(tmp_path_factory.mktemp("app-home")),
        settings=Settings(),
        database=None,
    )
    catalog = container.catalog.load(REAL_WORKSPACE)

    try:
        report = await container.doctor.run(container.check_context(catalog))
    finally:
        container.close()

    serious = [f for f in report.findings if f.severity is not Severity.INFO]
    assert [f for f in serious if f.severity is Severity.ERROR] == []
    assert {f.code for f in serious} <= {"explainability-low"}, serious
    assert len(report.scores) == 10
    assert all(0 <= score.score <= 100 for score in report.scores.values())
