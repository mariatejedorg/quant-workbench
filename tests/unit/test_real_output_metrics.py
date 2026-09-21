"""Every shipped extractor must keep working on real console output.

`tests/fixtures/real_outputs/` holds the verbatim stdout of each portfolio project, captured
from a real run. If a project changes what it prints, or an extractor's regex rots, this
fails here instead of silently producing an empty metrics column in the run history.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quant_workbench.domain.metrics import extract_metrics
from quant_workbench.domain.project import ProjectSpec
from quant_workbench.infrastructure.manifest import read_manifest
from quant_workbench.infrastructure.resources import data_path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "real_outputs"
SPECS = {
    spec.slug: spec
    for spec in (read_manifest(p) for p in sorted(data_path("registry").glob("*.toml")))
}


@pytest.mark.parametrize("slug", sorted(SPECS))
def test_every_extractor_matches_the_real_output(slug: str) -> None:
    spec: ProjectSpec = SPECS[slug]
    lines = (FIXTURES / f"{slug}.txt").read_text(encoding="utf-8").splitlines()

    found = {m.name for m in extract_metrics(lines, spec.extractors)}

    assert found == {e.name for e in spec.extractors}, "extractors that no longer match the output"


def value(slug: str, name: str) -> float | int | str:
    lines = (FIXTURES / f"{slug}.txt").read_text(encoding="utf-8").splitlines()
    metrics = {m.name: m.value for m in extract_metrics(lines, SPECS[slug].extractors)}
    return metrics[name]


def test_the_sql_project_reports_the_first_of_several_matching_rows() -> None:
    """Regression: 'last match wins' picked a return (0.064) from a later query, not the P/E."""
    assert value("sql-fundamentals-database", "repsol_per") == pytest.approx(8.7, abs=0.5)


def test_percentages_thousands_and_negatives_are_parsed_from_real_lines() -> None:
    assert value("momentum-backtest", "strategy_max_drawdown") < 0
    assert value("monte-carlo-price-simulator", "current_price") > 1000  # "20,131.40" -> 20131.4
    assert 0 < value("garch-volatility-forecasting", "persistence") < 1
    assert value("capstone-multi-strategy-portfolio", "strategy_max_drawdown") < 0


def test_every_project_declares_at_least_three_metrics_except_the_sql_one() -> None:
    for slug, spec in SPECS.items():
        assert len(spec.extractors) >= (1 if slug == "sql-fundamentals-database" else 3), slug
