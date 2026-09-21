"""Pure logic added for the functional GUI: run comparison, metric series, graph layout, git service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quant_workbench.application.config import ConfigPlan, ConfigService, FileChange
from quant_workbench.application.git_service import GitService
from quant_workbench.application.settings import Settings
from quant_workbench.domain.errors import GitError, PolicyViolationError, UnsafeEditError
from quant_workbench.domain.git import CommitInfo, GitState
from quant_workbench.domain.graph import DependencyGraph
from quant_workbench.domain.history import (
    MetricChange,
    compare_runs,
    metric_names,
    metric_series,
)
from quant_workbench.domain.layout import layered_layout
from quant_workbench.domain.runs import JobKind, MetricValue, OutputFileState, Run, RunStatus
from quant_workbench.infrastructure.config_editor import LibCstConfigEditor
from tests.fakes import FakeGit, FakeRepo, MemoryFiles, make_project

T0 = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def run(n: int, status: RunStatus = RunStatus.SUCCEEDED, **kwargs: object) -> Run:
    return Run(
        id=f"r{n}",  # type: ignore[arg-type]
        project="alpha",  # type: ignore[arg-type]
        kind=JobKind.RUN,
        status=status,
        queued_at=T0 + timedelta(minutes=n),
        started_at=T0 + timedelta(minutes=n),
        finished_at=T0 + timedelta(minutes=n, seconds=10),
        **kwargs,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------- comparison
def test_comparing_two_runs_finds_config_metric_and_output_changes() -> None:
    before = run(
        1,
        config_snapshot=(("config/a.py", "N = 5\nX = 1\n"), ("config/b.py", "same\n")),
        metrics=(MetricValue("sharpe", 1.0, ""), MetricValue("ticker", "AAPL")),
        outputs=(
            OutputFileState("outputs/a.png", 10, "aa"),
            OutputFileState("outputs/gone.png", 10, "gg"),
            OutputFileState("outputs/same.png", 10, "ss"),
        ),
    )
    after = run(
        2,
        config_snapshot=(("config/a.py", "N = 7\nX = 1\n"), ("config/b.py", "same\n")),
        metrics=(MetricValue("sharpe", 1.5), MetricValue("ticker", "MSFT"), MetricValue("n", 3)),
        outputs=(
            OutputFileState("outputs/a.png", 10, "AA"),
            OutputFileState("outputs/new.png", 10, "nn"),
            OutputFileState("outputs/same.png", 10, "ss"),
        ),
    )

    comparison = compare_runs(before, after)

    ((file, diff),) = comparison.config_diffs
    assert file == "config/a.py"
    assert "-N = 5" in diff
    assert "+N = 7" in diff
    assert comparison.config_changed
    by_name = {m.name: m for m in comparison.metrics}
    assert by_name["sharpe"].delta == pytest.approx(0.5)
    assert by_name["ticker"].delta is None  # text has no delta
    assert by_name["ticker"].changed
    assert by_name["n"].before is None
    assert {o.path: o.kind for o in comparison.outputs} == {
        "outputs/a.png": "changed",
        "outputs/gone.png": "removed",
        "outputs/new.png": "added",
        "outputs/same.png": "same",
    }
    assert {o.path for o in comparison.changed_outputs} == {
        "outputs/a.png",
        "outputs/gone.png",
        "outputs/new.png",
    }
    assert len(comparison.changed_metrics) == 3
    assert not comparison.status_changed
    assert comparison.duration_delta == timedelta(0)


def test_identical_runs_have_nothing_to_report() -> None:
    same = run(1, config_snapshot=(("a.py", "X = 1\n"),), metrics=(MetricValue("m", 1.0),))

    comparison = compare_runs(
        same, run(2, config_snapshot=(("a.py", "X = 1\n"),), metrics=(MetricValue("m", 1.0),))
    )

    assert not comparison.config_changed
    assert comparison.changed_metrics == ()
    assert comparison.changed_outputs == ()


def test_a_new_or_removed_config_file_is_diffed_against_nothing() -> None:
    comparison = compare_runs(
        run(1, config_snapshot=(("old.py", "A = 1\n"),)),
        run(2, config_snapshot=(("new.py", "B = 2\n"),)),
    )

    diffs = dict(comparison.config_diffs)
    assert "--- a/old.py" in diffs["old.py"]
    assert "+++ /dev/null" in diffs["old.py"]
    assert "--- /dev/null" in diffs["new.py"]


def test_status_and_duration_changes() -> None:
    fast = run(1)
    slow = Run(
        id="r9",  # type: ignore[arg-type]
        project="alpha",  # type: ignore[arg-type]
        kind=JobKind.RUN,
        status=RunStatus.FAILED,
        queued_at=T0,
        started_at=T0,
        finished_at=T0 + timedelta(seconds=40),
    )

    comparison = compare_runs(fast, slow)

    assert comparison.status_changed
    assert comparison.duration_delta == timedelta(seconds=30)
    unfinished = Run(
        id="x", project="alpha", kind=JobKind.RUN, status=RunStatus.RUNNING, queued_at=T0
    )  # type: ignore[arg-type]
    assert compare_runs(fast, unfinished).duration_delta is None


def test_metric_change_properties() -> None:
    assert MetricChange("m", 1, 3).delta == 2.0
    assert MetricChange("m", None, 3).delta is None
    assert not MetricChange("m", 2, 2).changed


# --------------------------------------------------------------------- series
def test_a_metric_series_uses_only_successful_runs_oldest_first() -> None:
    runs = [
        run(3, metrics=(MetricValue("sharpe", 1.3),)),
        run(1, metrics=(MetricValue("sharpe", 1.1),)),
        run(2, RunStatus.FAILED, metrics=(MetricValue("sharpe", 99.0),)),
        run(4, metrics=(MetricValue("sharpe", "n/a"),)),  # not numeric
        run(5, metrics=(MetricValue("other", 5),)),
    ]

    series = metric_series(runs, "sharpe")

    assert [value for _, value in series] == [1.1, 1.3]
    assert series[0][0] < series[1][0]
    assert metric_series(runs, "missing") == []


def test_metric_names_lists_numeric_metrics_once() -> None:
    runs = [
        run(1, metrics=(MetricValue("sharpe", 1.0), MetricValue("ticker", "A"))),
        run(2, metrics=(MetricValue("sharpe", 1.2), MetricValue("n", 3))),
    ]

    assert metric_names(runs) == ["sharpe", "n"]


# ---------------------------------------------------------------------- layout
def test_layers_become_columns_dependencies_on_the_left() -> None:
    graph = DependencyGraph(["a", "b", "c"], [])  # type: ignore[list-item]
    layers = [("a", "b"), ("c",)]

    positions = layered_layout(layers, graph.dependencies_of)  # type: ignore[arg-type]

    assert positions["a"].x == positions["b"].x == 0
    assert positions["c"].x > positions["a"].x
    assert positions["a"].y != positions["b"].y
    assert (positions["a"].y + positions["b"].y) == pytest.approx(0)  # centred on the axis
    assert positions["c"].y == pytest.approx(0)


def test_nodes_are_ordered_by_the_height_of_their_dependencies() -> None:
    dependencies = {"x": frozenset({"lower"}), "y": frozenset({"upper"})}

    positions = layered_layout(
        [("upper", "lower"), ("y", "x")],  # type: ignore[list-item]
        lambda slug: dependencies.get(slug, frozenset()),  # type: ignore[arg-type,return-value]
    )

    # alphabetical order puts "lower" above "upper"; the dependants follow their dependencies
    assert positions["lower"].y < positions["upper"].y
    assert (positions["x"].y < positions["y"].y) == (positions["lower"].y < positions["upper"].y)


def test_an_empty_graph_has_an_empty_layout() -> None:
    assert layered_layout([], lambda slug: frozenset()) == {}


# -------------------------------------------------------------- config undo plan
def test_a_plan_can_be_undone_by_applying_its_inverse(tmp_path: Path) -> None:
    files = MemoryFiles({"config/a.py": "N = 5\r\n"})
    service = ConfigService(files=files, editor=LibCstConfigEditor())
    project = make_project(tmp_path, "alpha", config_files=["config/a.py"])

    plan = service.plan(project, {"N": 9})
    service.apply(project, plan)
    assert files.files["config/a.py"] == "N = 9\r\n"

    service.apply(project, plan.inverse())

    assert files.files["config/a.py"] == "N = 5\r\n"
    assert plan.inverse().inverse() == plan


def test_the_inverse_is_refused_if_the_file_changed_since(tmp_path: Path) -> None:
    files = MemoryFiles({"config/a.py": "N = 5\n"})
    service = ConfigService(files=files, editor=LibCstConfigEditor())
    project = make_project(tmp_path, "alpha", config_files=["config/a.py"])
    plan = service.plan(project, {"N": 9})
    service.apply(project, plan)
    files.files["config/a.py"] = "N = 9\n# edited by hand\n"

    with pytest.raises(UnsafeEditError, match="changed on disk"):
        service.apply(project, plan.inverse())


def test_an_empty_plan_has_an_empty_inverse() -> None:
    assert ConfigPlan("alpha", ()).inverse().is_empty  # type: ignore[arg-type]
    assert FileChange("f", "a", "b", ("N",)).inverse() == FileChange("f", "b", "a", ("N",))


# ------------------------------------------------------------------ git service
EMAIL = "maria@example.org"


class LoggingGit(FakeGit):
    """FakeGit that also records commits and answers the extended port."""

    def __init__(self) -> None:
        super().__init__()
        self.commits: list[tuple[Path, str, tuple[str, ...]]] = []

    def log(self, root: Path, limit: int = 20) -> tuple[CommitInfo, ...]:
        return (CommitInfo("a" * 40, "María", EMAIL, T0, "first"),)

    def diff(self, root: Path, relative: str | None = None) -> str:
        return f"diff of {relative}"

    def commit(self, root: Path, message: str, paths: tuple[str, ...]) -> str:  # type: ignore[override]
        self.commits.append((root, message, tuple(paths)))
        return "abc1234"


def service_for(tmp_path: Path, **settings: object) -> tuple[GitService, LoggingGit, object]:
    git = LoggingGit()
    project = make_project(tmp_path, "alpha")
    git.repos[project.root] = FakeRepo(
        config={"user.email": EMAIL}, remote="git@github.com-maria:o/r.git"
    )
    service = GitService(git, Settings(expected_git_email=EMAIL, **settings))  # type: ignore[arg-type]
    return service, git, project


def test_the_overview_gathers_state_history_and_the_push_command(tmp_path: Path) -> None:
    service, git, project = service_for(tmp_path)
    git.repos[project.root].state = GitState("main", ("a.py",), ahead=2, has_upstream=True)  # type: ignore[attr-defined]

    overview = service.overview(project)  # type: ignore[arg-type]

    assert overview is not None
    assert overview.state.ahead == 2
    assert overview.commits[0].short == "aaaaaaa"
    assert overview.identity_ok
    assert overview.email == EMAIL
    assert overview.remote == "git@github.com-maria:o/r.git"
    assert overview.push_command.startswith("git -C ")
    assert overview.push_command.endswith(" push")


def test_a_folder_that_is_not_a_repository_has_no_overview(tmp_path: Path) -> None:
    service = GitService(LoggingGit(), Settings())

    assert service.overview(make_project(tmp_path, "alpha")) is None


def test_the_overview_flags_a_wrong_identity(tmp_path: Path) -> None:
    service, git, project = service_for(tmp_path)
    git.repos[project.root].config["user.email"] = "carlos@elsewhere.com"  # type: ignore[attr-defined]

    overview = service.overview(project)  # type: ignore[arg-type]

    assert overview is not None
    assert not overview.identity_ok


def test_commit_goes_through_with_the_right_identity_and_appends_the_trailer(
    tmp_path: Path,
) -> None:
    service, git, project = service_for(tmp_path, commit_trailer="Co-Authored-By: X <x@y.z>")

    short = service.commit(project, "  fix things  ", ["a.py"])  # type: ignore[arg-type]

    assert short == "abc1234"
    assert git.commits == [(project.root, "fix things\n\nCo-Authored-By: X <x@y.z>", ("a.py",))]  # type: ignore[attr-defined]


def test_the_trailer_is_not_duplicated(tmp_path: Path) -> None:
    service, git, project = service_for(tmp_path, commit_trailer="Signed-off-by: A")

    service.commit(project, "msg\n\nSigned-off-by: A")  # type: ignore[arg-type]

    assert git.commits[0][1].count("Signed-off-by") == 1


def test_a_commit_with_the_wrong_identity_is_blocked(tmp_path: Path) -> None:
    service, git, project = service_for(tmp_path)
    git.repos[project.root].config["user.email"] = "carlos@elsewhere.com"  # type: ignore[attr-defined]

    with pytest.raises(PolicyViolationError, match=r"carlos@elsewhere\.com") as raised:
        service.commit(project, "msg")  # type: ignore[arg-type]

    assert git.commits == []
    assert raised.value.hint is not None
    assert "doctor" in raised.value.hint


def test_a_missing_identity_is_blocked_too(tmp_path: Path) -> None:
    service, git, project = service_for(tmp_path)
    git.repos[project.root].config.clear()  # type: ignore[attr-defined]

    with pytest.raises(PolicyViolationError, match="no e-mail configured"):
        service.commit(project, "msg")  # type: ignore[arg-type]


def test_the_name_policy_is_enforced_when_configured(tmp_path: Path) -> None:
    service, git, project = service_for(tmp_path, expected_git_name="María")
    git.repos[project.root].config["user.name"] = "Someone else"  # type: ignore[attr-defined]

    with pytest.raises(PolicyViolationError, match=r"user\.name"):
        service.commit(project, "msg")  # type: ignore[arg-type]


def test_an_empty_message_is_refused_and_the_diff_is_forwarded(tmp_path: Path) -> None:
    service, _, project = service_for(tmp_path)

    with pytest.raises(GitError, match="needs a message"):
        service.commit(project, "   ")  # type: ignore[arg-type]
    assert service.diff(project, "a.py") == "diff of a.py"  # type: ignore[arg-type]
