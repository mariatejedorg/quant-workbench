"""README parsing, SM-2 scheduling, study service and the README-claims checker."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from quant_workbench.application.catalog import Catalog
from quant_workbench.application.checkers.readme_claims import ReadmeClaimsChecker
from quant_workbench.application.diagnostics import CheckContext
from quant_workbench.application.settings import Settings
from quant_workbench.application.study import StudyService
from quant_workbench.domain.graph import DependencyGraph
from quant_workbench.domain.readme import claimed_numbers, section, study_cards
from quant_workbench.domain.runs import JobKind, MetricValue, Run, RunStatus
from quant_workbench.domain.study import (
    DEFAULT_EASE,
    MIN_EASE,
    CardState,
    Grade,
    due_cards,
    review,
    stats,
)
from quant_workbench.infrastructure.sqlite_runs import create_sqlite_engine
from quant_workbench.infrastructure.sqlite_study import SqliteStudyRepository
from tests.conftest import FakeClock
from tests.fakes import FakeGit, MemoryRepository, MultiFiles, make_project

README = """# Project

## Results

_(live data)_

| | Value |
|---|---|
| Fitted alpha | 0.0664 |
| Persistence | 0.9431 |
| Historical volatility | 31.75% |
| Market cap | 54,330,990,963 |
| Tiny | 1.561e-05 |

## Key findings

- **High persistence (0.94)**: the shock decays slowly.
- Plain finding without a bold title.

## Concepts to be able to explain in an interview

- **Volatility clustering**: information arrives in bursts, so
  variance is persistent.
- **`alpha` and `beta`**: reaction and memory.
1. A numbered concept

## How to run it

```
## not a heading
```
"""


# ----------------------------------------------------------------------- sections
def test_a_section_runs_to_the_next_heading_of_the_same_level() -> None:
    line, body = section(README, "Key findings")  # type: ignore[misc]

    assert line == 15
    assert "High persistence" in body
    assert "Volatility clustering" not in body


def test_sections_are_found_case_insensitively_and_missing_ones_are_none() -> None:
    assert section(README, "results") is not None
    assert section(README, "Nope") is None


def test_headings_inside_code_fences_do_not_end_a_section() -> None:
    text = "## A\n\n```\n## inside\n```\n\nstill A\n\n## B\n"

    _, body = section(text, "A")  # type: ignore[misc]

    assert "still A" in body


def test_subsections_belong_to_their_parent() -> None:
    _, body = section("## A\n\n### sub\ntext\n\n## B\n", "A")  # type: ignore[misc]

    assert "text" in body


# ----------------------------------------------------------------------- cards
def test_concepts_and_findings_become_cards() -> None:
    cards = study_cards("proj", README)

    concepts = [c for c in cards if c.kind == "concept"]
    findings = [c for c in cards if c.kind == "finding"]
    assert [c.prompt for c in concepts] == [
        "Explain: Volatility clustering",
        "Explain: `alpha` and `beta`",
        "Explain: A numbered concept",
    ]
    assert "variance is persistent" in concepts[0].answer  # the continuation line is joined
    assert concepts[2].answer == "A numbered concept"  # no bold title: the bullet is the answer
    assert len(findings) == 2
    assert findings[0].answer.startswith("**High persistence (0.94)**")
    assert all(c.project == "proj" for c in cards)


def test_card_ids_are_stable_and_distinct() -> None:
    first, second = study_cards("p", README)[:2]

    assert first.id == study_cards("p", README)[0].id
    assert first.id != second.id
    assert first.id.startswith("p:")
    assert study_cards("q", README)[0].id != first.id


def test_a_readme_without_the_sections_has_no_cards() -> None:
    assert study_cards("p", "# Title\n\nJust text.\n") == ()


# ---------------------------------------------------------------------- numbers
def test_numbers_of_the_results_section_are_read_with_their_precision() -> None:
    numbers = {n.text: n for n in claimed_numbers(README)}

    assert numbers["0.0664"].decimals == 4
    assert numbers["31.75"].decimals == 2
    assert numbers["54,330,990,963"].value == 54_330_990_963
    assert numbers["1.561e-05"].value == pytest.approx(1.561e-05)
    assert numbers["0.9431"].line == 10
    assert "0.94" not in numbers  # that one is in Key findings, not Results


def test_a_number_matches_at_the_precision_the_readme_uses() -> None:
    number = claimed_numbers("## Results\n\n| a | 26.97% |\n")[0]

    assert number.matches(26.9712)
    assert number.matches(0.269712, percent=True)  # as a fraction
    assert not number.matches(26.98)
    assert not number.matches(0.2698, percent=True)


def test_no_results_section_means_no_numbers() -> None:
    assert claimed_numbers("# T\n\n## Other\n\n1 2 3\n") == ()


# ------------------------------------------------------------------------ SM-2
TODAY = date(2026, 9, 21)


def test_the_first_reviews_follow_one_day_then_six() -> None:
    first = review(CardState(), Grade.GOOD, TODAY)
    second = review(first, Grade.GOOD, TODAY + timedelta(days=1))
    third = review(second, Grade.GOOD, TODAY + timedelta(days=7))

    assert (first.interval_days, first.due) == (1, TODAY + timedelta(days=1))
    assert (second.interval_days, second.repetitions) == (6, 2)
    assert third.interval_days == round(6 * second.ease)
    assert third.repetitions == 3


def test_forgetting_resets_the_card_and_counts_a_lapse() -> None:
    learned = CardState(ease=2.2, interval_days=30, repetitions=5, due=TODAY)

    after = review(learned, Grade.WRONG, TODAY)

    assert (after.repetitions, after.interval_days, after.lapses) == (0, 1, 1)
    assert after.ease == 2.2  # SM-2 keeps the ease on a failure
    assert after.due == TODAY + timedelta(days=1)


def test_easy_answers_raise_the_ease_and_hard_ones_lower_it() -> None:
    base = CardState(ease=DEFAULT_EASE, interval_days=6, repetitions=2)

    easy = review(base, Grade.EASY, TODAY)
    good = review(base, Grade.GOOD, TODAY)
    hard = review(base, Grade.HARD, TODAY)

    assert easy.ease > good.ease > hard.ease
    assert good.ease == pytest.approx(DEFAULT_EASE)  # SM-2: quality 4 leaves it unchanged


@given(st.lists(st.sampled_from(list(Grade)), min_size=1, max_size=40))
def test_the_ease_never_falls_below_the_floor_and_intervals_stay_positive(
    grades: list[Grade],
) -> None:
    state, today = CardState(), TODAY
    for grade in grades:
        state = review(state, grade, today)
        today = state.due or today
        assert state.ease >= MIN_EASE
        assert state.interval_days >= 1
        assert state.due is not None
        assert state.due > today - timedelta(days=state.interval_days + 1)


@given(st.integers(min_value=2, max_value=15))
def test_a_run_of_good_answers_spaces_reviews_ever_further_apart(n: int) -> None:
    state, gaps = CardState(), []
    for _ in range(n):
        state = review(state, Grade.GOOD, TODAY)
        gaps.append(state.interval_days)

    assert gaps == sorted(gaps)
    assert gaps[-1] > gaps[0]


def test_grades_that_pass() -> None:
    assert not Grade.WRONG_BUT_FAMILIAR.passed
    assert Grade.HARD.passed


def test_the_queue_puts_new_cards_first_then_the_most_overdue() -> None:
    states = {
        "old": CardState(due=TODAY - timedelta(days=10), repetitions=1),
        "recent": CardState(due=TODAY - timedelta(days=1), repetitions=1),
        "later": CardState(due=TODAY + timedelta(days=3), repetitions=1),
    }

    queue = due_cards(states, ["later", "recent", "new", "old"], TODAY)

    assert queue == ["new", "old", "recent"]  # "later" is not due yet


def test_progress_statistics() -> None:
    states = {
        "a": CardState(due=TODAY + timedelta(days=5), repetitions=2),
        "b": CardState(due=TODAY - timedelta(days=1), repetitions=1),
    }

    summary = stats(states, ["a", "b", "c", "d"], TODAY)

    assert (summary.total, summary.new, summary.due, summary.learned) == (4, 2, 3, 1)
    assert summary.mastered_share == pytest.approx(0.25)
    assert stats({}, [], TODAY).mastered_share == 0.0


# ----------------------------------------------------------------- persistence
def test_study_state_round_trips_and_counts_reviews() -> None:
    engine = create_sqlite_engine(None)
    repo = SqliteStudyRepository(engine)
    when = datetime(2026, 9, 21, 12, tzinfo=UTC)
    state = CardState(
        ease=2.36, interval_days=6, repetitions=2, due=TODAY + timedelta(days=6), lapses=1
    )

    repo.save("alpha:abc", "alpha", state, when)  # type: ignore[arg-type]
    repo.save("alpha:abc", "alpha", state, when)  # type: ignore[arg-type]  # a second review: replaced
    repo.save("beta:def", "beta", CardState(due=TODAY), when)  # type: ignore[arg-type]

    assert repo.states("alpha") == {"alpha:abc": state}  # type: ignore[arg-type]
    assert set(repo.states()) == {"alpha:abc", "beta:def"}
    with engine.connect() as connection:
        reviews = connection.exec_driver_sql(
            "SELECT reviews FROM study_cards WHERE id='alpha:abc'"
        ).scalar()
    assert reviews == 2
    engine.dispose()


def test_a_card_without_a_due_date_cannot_be_saved() -> None:
    engine = create_sqlite_engine(None)
    with pytest.raises(ValueError, match="due date"):
        SqliteStudyRepository(engine).save("x", "alpha", CardState(), datetime.now(UTC))  # type: ignore[arg-type]
    engine.dispose()


class MemoryStudy:
    def __init__(self) -> None:
        self.data: dict[str, CardState] = {}
        self.projects: dict[str, str] = {}

    def states(self, project: str | None = None) -> dict[str, CardState]:
        return {
            k: v for k, v in self.data.items() if project is None or self.projects[k] == project
        }

    def save(self, card_id: str, project: str, state: CardState, reviewed_at: datetime) -> None:
        self.data[card_id] = state
        self.projects[card_id] = project


def study_service(files: MultiFiles) -> tuple[StudyService, MemoryStudy, FakeClock]:
    clock = FakeClock(datetime(2026, 9, 21, 9, tzinfo=UTC))
    repo = MemoryStudy()
    return StudyService(files=files, repository=repo, clock=clock), repo, clock  # type: ignore[arg-type]


def test_the_service_builds_the_queue_and_records_answers(tmp_path: Path) -> None:
    alpha = make_project(tmp_path, "alpha")
    files = MultiFiles({"alpha": {"README.md": README}})
    service, _repo, clock = study_service(files)

    queue = service.queue([alpha])
    assert len(queue) == 5
    assert all(item.is_new for item in queue)
    state = service.answer(queue[0], Grade.GOOD)
    assert state.due == date(2026, 9, 22)

    assert len(service.queue([alpha])) == 4  # the answered card is scheduled for tomorrow
    assert len(service.queue([alpha], limit=2)) == 2
    clock.advance(days=1)
    assert len(service.queue([alpha])) == 5  # ...and comes back when due
    summary = service.summary([alpha])["alpha"]
    assert (summary.total, summary.new) == (5, 4)


def test_a_project_without_a_readme_has_no_cards(tmp_path: Path) -> None:
    service, _, _ = study_service(MultiFiles({"alpha": {}}))

    assert service.cards(make_project(tmp_path, "alpha")) == ()
    assert service.queue([make_project(tmp_path, "alpha")]) == []


# --------------------------------------------------------------------- claims
T0 = datetime(2026, 9, 21, 12, tzinfo=UTC)


def run_with(status: RunStatus, *metrics: MetricValue, n: int = 1) -> Run:
    return Run(
        id=f"r{n}",  # type: ignore[arg-type]
        project="alpha",  # type: ignore[arg-type]
        kind=JobKind.RUN,
        status=status,
        queued_at=T0 + timedelta(minutes=n),
        metrics=metrics,
    )


def claims_context(tmp_path: Path, readme: str | None, *runs: Run) -> tuple[CheckContext, object]:
    project = make_project(tmp_path, "alpha")
    repo = MemoryRepository()
    for run in runs:
        repo.save(run)
    files = MultiFiles({"alpha": {} if readme is None else {"README.md": readme}})
    context = CheckContext(
        catalog=Catalog(tmp_path, (project,), DependencyGraph([project.slug])),
        settings=Settings(),
        files=files,  # type: ignore[arg-type]
        git=FakeGit(),  # type: ignore[arg-type]
        environments=None,  # type: ignore[arg-type]
        runs=None,
        environ={},
        repository=repo,
    )
    return context, project


async def test_metrics_that_the_readme_shows_produce_no_finding(tmp_path: Path) -> None:
    run = run_with(
        RunStatus.SUCCEEDED,
        MetricValue("alpha", 0.06641),
        MetricValue("hist_vol", 31.7512),
        MetricValue("persistence", 0.94309),
    )
    context, project = claims_context(tmp_path, README, run)

    assert await ReadmeClaimsChecker().check(project, context) == []  # type: ignore[arg-type]


async def test_a_metric_missing_from_the_readme_is_reported_with_a_location(tmp_path: Path) -> None:
    run = run_with(
        RunStatus.SUCCEEDED, MetricValue("persistence", 0.9431), MetricValue("sharpe", 2.45)
    )
    context, project = claims_context(tmp_path, README, run)

    (finding,) = await ReadmeClaimsChecker().check(project, context)  # type: ignore[arg-type]

    assert finding.code == "readme-metric-not-found"
    assert finding.severity.value == "info"
    assert "sharpe = 2.45" in finding.message
    assert "persistence" not in finding.message
    assert str(finding.location) == "README.md:9"


async def test_only_the_last_successful_run_counts(tmp_path: Path) -> None:
    good = run_with(RunStatus.SUCCEEDED, MetricValue("hist_vol", 31.75), n=1)
    failed = run_with(RunStatus.FAILED, MetricValue("hist_vol", 99.0), n=2)
    context, project = claims_context(tmp_path, README, good, failed)

    assert await ReadmeClaimsChecker().check(project, context) == []  # type: ignore[arg-type]


async def test_nothing_to_compare_means_no_finding(tmp_path: Path) -> None:
    ok = run_with(RunStatus.SUCCEEDED, MetricValue("sharpe", 2.45))
    no_runs, p1 = claims_context(tmp_path, README)
    no_readme, p2 = claims_context(tmp_path, None, ok)
    no_results, p3 = claims_context(tmp_path, "# T\n\n## Other\n\ntext\n", ok)
    text_metric, p4 = claims_context(
        tmp_path, README, run_with(RunStatus.SUCCEEDED, MetricValue("t", "AAPL"))
    )

    for context, project in ((no_runs, p1), (no_readme, p2), (no_results, p3), (text_metric, p4)):
        assert await ReadmeClaimsChecker().check(project, context) == []  # type: ignore[arg-type]


async def test_a_long_list_of_missing_metrics_is_summarised(tmp_path: Path) -> None:
    run = run_with(RunStatus.SUCCEEDED, *(MetricValue(f"m{i}", 1000.5 + i) for i in range(8)))
    context, project = claims_context(tmp_path, README, run)

    (finding,) = await ReadmeClaimsChecker().check(project, context)  # type: ignore[arg-type]

    assert "and 3 more" in finding.message
