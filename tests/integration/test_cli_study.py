"""`qw study` end to end: a session on a real database, then the schedule it left behind."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from quant_workbench.cli.main import app
from quant_workbench.infrastructure.migrations import SCHEMA_VERSION
from quant_workbench.infrastructure.sqlite_runs import create_sqlite_engine
from tests.support import manifest_toml, write_project

pytestmark = pytest.mark.integration

runner = CliRunner()

README = """# Alpha

## Key findings

- **Persistence is high**: 0.94.

## Concepts to be able to explain in an interview

- **Volatility clustering**: information arrives in bursts.
- **Alpha and beta**: reaction and memory.
"""


@pytest.fixture
def workspace(accented_root: Path) -> Path:
    for name in ("alpha", "beta"):
        write_project(
            accented_root,
            name,
            manifest=manifest_toml(name),
            readme=README if name == "alpha" else "# Beta\n",
        )
    return accented_root


def qw(workspace: Path, *args: str, answers: str = "") -> tuple[int, str]:
    result = runner.invoke(
        app,
        ["--home", str(workspace / "home"), "study", *args, "-w", str(workspace)],
        input=answers,
    )
    return result.exit_code, result.output


def test_a_session_asks_reveals_grades_and_schedules(workspace: Path) -> None:
    # for each of the 3 cards: Enter (I answered), then a grade
    code, output = qw(workspace, answers="\n4\n\n5\n\n1\n")

    assert code == 0, output
    assert "Explain: Volatility clustering" in output
    assert "information arrives in bursts" in output  # the answer is shown after Enter
    assert "next review in 1 day(s)" in output
    assert "3 card(s) reviewed" in output
    assert "Study progress" in output


def test_reviewed_cards_are_not_due_again_today(workspace: Path) -> None:
    qw(workspace, answers="\n4\n\n4\n\n4\n")

    code, output = qw(workspace)

    assert code == 0
    assert "Nothing due today" in output


def test_a_failed_card_is_scheduled_for_tomorrow_and_counted(workspace: Path) -> None:
    qw(workspace, "--limit", "1", answers="\n1\n")

    code, stats = qw(workspace, "--stats")

    assert code == 0
    assert "alpha" in stats
    row = next(line for line in stats.splitlines() if "alpha" in line)
    assert row.replace("│", " ").split()[1:] == ["3", "2", "2", "1"]  # cards, new, due, learned


def test_q_stops_the_session_without_recording_that_card(workspace: Path) -> None:
    code, output = qw(workspace, answers="\n4\n\nq\n")

    assert code == 0
    assert "1 card(s) reviewed" in output


def test_bad_grades_are_asked_again(workspace: Path) -> None:
    code, output = qw(workspace, "--limit", "1", answers="\nnine\n7\n3\n")

    assert code == 0
    assert output.count("Type a number from 0 to 5") == 2
    assert "1 card(s) reviewed" in output


def test_only_the_projects_asked_for_are_studied(workspace: Path) -> None:
    code, output = qw(workspace, "beta")

    assert code == 0
    assert "Nothing due today" in output  # beta has no cards


def test_the_study_table_is_part_of_the_schema(tmp_path: Path) -> None:
    engine = create_sqlite_engine(tmp_path / "db.sqlite3")
    with engine.connect() as connection:
        tables = {r[0] for r in connection.exec_driver_sql("SELECT name FROM sqlite_master")}
    engine.dispose()

    assert SCHEMA_VERSION >= 2
    assert "study_cards" in tables
