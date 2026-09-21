"""Acceptance test against the author's real workspace (skipped where it does not exist).

This is the M1 quality gate: static analysis of the ten real projects must rediscover
exactly the dependency edges their manifests declare, in both directions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quant_workbench.application.catalog import ProjectCatalog
from quant_workbench.domain.graph import EdgeOrigin
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
