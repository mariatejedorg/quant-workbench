"""The engine's behaviour, driven through fakes so every scenario is deterministic."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from quant_workbench.application.catalog import Catalog
from quant_workbench.application.jobs import JobExecutor
from quant_workbench.application.runs import RunOptions, RunService
from quant_workbench.domain.events import DomainEvent
from quant_workbench.domain.failures import FailureSignature
from quant_workbench.domain.graph import Dependency, DependencyGraph, EdgeOrigin
from quant_workbench.domain.ids import Slug, parse_slug
from quant_workbench.domain.process import ProcessSpec, ResourceSample
from quant_workbench.domain.project import MetricExtractorSpec, MetricKind, Project
from quant_workbench.domain.run_events import (
    BatchProgress,
    RunFinished,
    RunOutput,
    RunQueued,
    RunResourcesSampled,
    RunStarted,
)
from quant_workbench.domain.runs import LogLevel, LogStream, OutputFileState, RunStatus
from quant_workbench.infrastructure.event_bus import InProcessEventBus
from tests.conftest import FakeClock
from tests.fakes import (
    FixedInterpreter,
    MemoryFiles,
    MemoryRepository,
    Script,
    ScriptedRunner,
    make_project,
)

OUT, ERR = LogStream.STDOUT, LogStream.STDERR
PYTHON = Path("python")


class Harness:
    """Wires a RunService with fakes and records every published event."""

    def __init__(
        self,
        tmp: Path,
        runner: ScriptedRunner,
        *,
        clock: FakeClock,
        concurrency: int = 3,
        retries: int = 1,
        files: MemoryFiles | None = None,
        interpreter: Path | None = PYTHON,
        signatures: tuple[FailureSignature, ...] = (),
    ) -> None:
        self.tmp = tmp
        self.runner = runner
        self.repo = MemoryRepository()
        self.events = InProcessEventBus()
        self.published: list[DomainEvent] = []
        self.events.subscribe(DomainEvent, self.published.append)
        self.sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.executor = JobExecutor(
            runner=runner,
            repository=self.repo,
            files=files or MemoryFiles(),
            events=self.events,
            clock=clock,
            max_concurrency=concurrency,
            signatures=signatures,
        )
        self.service = RunService(
            executor=self.executor,
            interpreters=FixedInterpreter(interpreter),
            repository=self.repo,
            events=self.events,
            clock=clock,
            default_timeout_seconds=60,
            default_max_retries=retries,
            sleep=fake_sleep,
        )

    def project(self, slug: str = "alpha", **kwargs: object) -> Project:
        return make_project(self.tmp, slug, **kwargs)  # type: ignore[arg-type]

    def catalog(self, projects: list[Project], edges: list[tuple[str, str]] = ()) -> Catalog:  # type: ignore[assignment]
        graph = DependencyGraph(
            [p.slug for p in projects],
            [
                Dependency(parse_slug(a), parse_slug(b), frozenset({EdgeOrigin.DECLARED}))
                for a, b in edges
            ],
        )
        return Catalog(workspace=self.tmp, projects=tuple(projects), graph=graph)

    def of_type(self, kind: type[DomainEvent]) -> list[DomainEvent]:
        return [e for e in self.published if isinstance(e, kind)]


@pytest.fixture
def harness_factory(tmp_path: Path, clock: FakeClock):  # type: ignore[no-untyped-def]
    def build(runner: ScriptedRunner | Script | None = None, **kwargs: object) -> Harness:
        scripted = runner if isinstance(runner, ScriptedRunner) else ScriptedRunner(runner)
        return Harness(tmp_path, scripted, clock=clock, **kwargs)  # type: ignore[arg-type]

    return build


# --------------------------------------------------------------------- lifecycle
async def test_a_successful_run_is_recorded_streamed_and_published(harness_factory) -> None:  # type: ignore[no-untyped-def]
    metric = MetricExtractorSpec("sharpe", r"Sharpe:\s*([\d.]+)", MetricKind.FLOAT)
    outputs = (OutputFileState("outputs/dashboard.html", 10, "abc"),)
    h = harness_factory(
        Script(
            lines=[(OUT, "hello"), (OUT, "Sharpe: 1.50"), (OUT, "Sharpe: 2.45")],
            samples=[ResourceSample(at=None, cpu_percent=50.0, rss_bytes=1_000)],  # type: ignore[arg-type]
        ),
        files=MemoryFiles({"config/x.py": "N = 1\n"}, outputs),
    )
    project = h.project(extractors=[metric], config_files=["config/x.py", "config/missing.py"])

    run = await h.service.run(project)

    assert run.status is RunStatus.SUCCEEDED
    assert run.exit_code == 0
    assert run.metric("sharpe") is not None
    assert run.metric("sharpe").value == 2.45  # type: ignore[union-attr]
    assert run.config_snapshot == (("config/x.py", "N = 1\n"),)
    assert run.outputs == outputs
    assert run.peak_rss_bytes == 1_000
    assert run.duration is not None
    assert h.repo.get(run.id) == run
    assert [line.text for line in h.repo.logs(run.id) if line.stream is not LogStream.SYSTEM] == [
        "hello",
        "Sharpe: 1.50",
        "Sharpe: 2.45",
    ]

    kinds = [type(e) for e in h.published]
    assert kinds[0] is RunQueued
    assert kinds[1] is RunStarted
    assert kinds[-1] is RunFinished
    assert RunOutput in kinds
    assert RunResourcesSampled in kinds


async def test_a_failing_run_explains_itself(harness_factory) -> None:  # type: ignore[no-untyped-def]
    h = harness_factory(
        Script(
            lines=[
                (ERR, "Traceback (most recent call last):"),
                (ERR, '  File "main.py", line 1'),
                (ERR, "ZeroDivisionError: division by zero"),
            ],
            exit_code=1,
        )
    )

    run = await h.service.run(h.project())

    assert run.status is RunStatus.FAILED
    assert run.exit_code == 1
    assert run.failure == "ZeroDivisionError: division by zero"
    assert run.metrics == ()  # metrics are only extracted from successful runs
    levels = [line.level for line in h.repo.logs(run.id)]
    assert LogLevel.ERROR in levels


async def test_known_failures_are_recognised_by_signature(harness_factory) -> None:  # type: ignore[no-untyped-def]
    ssl = FailureSignature(
        id="ssl",
        pattern="CERTIFICATE_VERIFY_FAILED",
        title="HTTPS certificate verification failed",
        advice="use .certs",
    )
    h = harness_factory(
        Script(lines=[(ERR, "curl: (60) CERTIFICATE_VERIFY_FAILED")], exit_code=1),
        signatures=(ssl,),
    )

    run = await h.service.run(h.project())

    assert run.signatures == ("ssl",)
    assert run.failure is not None
    assert run.failure.startswith("HTTPS certificate verification failed")
    assert not run.transient


async def test_a_missing_environment_is_a_recorded_failure_with_a_hint(harness_factory) -> None:  # type: ignore[no-untyped-def]
    h = harness_factory(Script(), interpreter=None)

    run = await h.service.run(h.project("beta"))

    assert run.status is RunStatus.FAILED
    assert "qw env setup beta" in (run.failure or "")
    assert h.runner.calls == []  # nothing was started


async def test_timeouts_are_reported_as_such(harness_factory) -> None:  # type: ignore[no-untyped-def]
    h = harness_factory(Script(timed_out=True))

    run = await h.service.run(h.project(), RunOptions(timeout_seconds=5))

    assert run.status is RunStatus.TIMED_OUT
    assert run.exit_code is None
    assert "time limit" in (run.failure or "")
    assert run.timeout_seconds == 5


async def test_the_command_and_environment_policy_reach_the_runner(harness_factory) -> None:  # type: ignore[no-untyped-def]
    h = harness_factory(Script())
    project = h.project()

    await h.service.run(project, RunOptions(extra_env={"TICKER": "MSFT"}))

    (spec,) = h.runner.calls
    assert spec.args == (str(PYTHON), str(project.entrypoint_path))
    assert spec.cwd == project.root
    assert spec.env["TICKER"] == "MSFT"
    assert spec.env["MPLBACKEND"] == "Agg"


# ----------------------------------------------------------------------- retries
async def test_transient_failures_are_retried_with_backoff(harness_factory) -> None:  # type: ignore[no-untyped-def]
    rate_limit = FailureSignature(
        id="rl", pattern="Too Many Requests", title="Rate limit", advice="wait", transient=True
    )
    attempts = iter(
        [
            Script(lines=[(ERR, "Too Many Requests")], exit_code=1),
            Script(lines=[(ERR, "Too Many Requests")], exit_code=1),
            Script(lines=[(OUT, "finally")], exit_code=0),
        ]
    )
    h = harness_factory(
        ScriptedRunner(lambda _spec: next(attempts)), retries=3, signatures=(rate_limit,)
    )

    run = await h.service.run(h.project())

    assert run.status is RunStatus.SUCCEEDED
    assert run.attempt == 3
    assert run.retry_of is not None
    assert h.sleeps == [2.0, 4.0]
    by_attempt = {r.attempt: r for r in h.repo.runs.values()}
    assert sorted(by_attempt) == [1, 2, 3]
    assert by_attempt[1].retry_of is None
    assert by_attempt[2].retry_of == by_attempt[1].id  # the chain of attempts is recorded
    assert by_attempt[3].retry_of == by_attempt[2].id


async def test_retries_stop_when_exhausted(harness_factory) -> None:  # type: ignore[no-untyped-def]
    rate_limit = FailureSignature(
        id="rl", pattern="Too Many Requests", title="Rate limit", advice="", transient=True
    )
    h = harness_factory(
        Script(lines=[(ERR, "Too Many Requests")], exit_code=1), retries=2, signatures=(rate_limit,)
    )

    run = await h.service.run(h.project())

    assert run.status is RunStatus.FAILED
    assert run.attempt == 3  # the original attempt plus two retries
    assert len(h.runner.calls) == 3


async def test_permanent_failures_are_not_retried(harness_factory) -> None:  # type: ignore[no-untyped-def]
    h = harness_factory(Script(lines=[(ERR, "SyntaxError: bad")], exit_code=1), retries=5)

    run = await h.service.run(h.project())

    assert run.attempt == 1
    assert len(h.runner.calls) == 1
    assert h.sleeps == []


async def test_retries_can_be_overridden_per_run(harness_factory) -> None:  # type: ignore[no-untyped-def]
    rate_limit = FailureSignature(id="rl", pattern="429", title="RL", advice="", transient=True)
    h = harness_factory(
        Script(lines=[(ERR, "429")], exit_code=1), retries=5, signatures=(rate_limit,)
    )

    run = await h.service.run(h.project(), RunOptions(max_retries=0))

    assert run.attempt == 1


# ------------------------------------------------------------------- concurrency
async def test_the_concurrency_limit_is_respected(harness_factory) -> None:  # type: ignore[no-untyped-def]
    gate = asyncio.Event()
    h = harness_factory(Script(hold=gate), concurrency=2)
    projects = [h.project(f"p{i}") for i in range(5)]

    tasks = [asyncio.create_task(h.service.run(p)) for p in projects]
    await asyncio.sleep(0.05)
    assert h.runner.running == 2  # the other three are queued behind the semaphore
    gate.set()
    runs = await asyncio.gather(*tasks)

    assert h.runner.max_running == 2
    assert all(r.status is RunStatus.SUCCEEDED for r in runs)


# ------------------------------------------------------------------ cancellation
async def test_cancelling_a_running_run_records_it_and_propagates(harness_factory) -> None:  # type: ignore[no-untyped-def]
    gate = asyncio.Event()
    h = harness_factory(Script(lines=[(OUT, "started")], hold=gate))
    task = asyncio.create_task(h.service.run(h.project()))
    await asyncio.sleep(0.05)
    (run_id,) = h.executor.active_runs

    assert h.service.cancel(run_id) is True
    with pytest.raises(asyncio.CancelledError):
        await task

    stored = h.repo.get(run_id)
    assert stored is not None
    assert stored.status is RunStatus.CANCELLED
    assert "cancelled" in (stored.failure or "")
    assert h.executor.active_runs == ()
    assert any(
        isinstance(e, RunFinished) and e.run.status is RunStatus.CANCELLED for e in h.published
    )
    assert [line.text for line in h.repo.logs(run_id) if line.stream is OUT] == ["started"]


async def test_cancelling_a_queued_run_marks_it_cancelled_without_starting_it(
    harness_factory,
) -> None:  # type: ignore[no-untyped-def]
    gate = asyncio.Event()
    h = harness_factory(Script(hold=gate), concurrency=1)
    first = asyncio.create_task(h.service.run(h.project("first")))
    second = asyncio.create_task(h.service.run(h.project("second")))
    await asyncio.sleep(0.05)
    assert len(h.executor.active_runs) == 2

    queued_id = next(
        r.id for r in h.repo.runs.values() if r.project == "second" and r.status is RunStatus.QUEUED
    )
    assert h.executor.cancel(queued_id)
    with pytest.raises(asyncio.CancelledError):
        await second

    assert h.repo.get(queued_id).status is RunStatus.CANCELLED  # type: ignore[union-attr]
    assert h.runner.started == ["first"]
    gate.set()
    await first


async def test_cancel_all_and_unknown_ids(harness_factory) -> None:  # type: ignore[no-untyped-def]
    gate = asyncio.Event()
    h = harness_factory(Script(hold=gate))
    tasks = [asyncio.create_task(h.service.run(h.project(f"p{i}"))) for i in range(3)]
    await asyncio.sleep(0.05)

    assert h.service.cancel_all() == 3
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert all(isinstance(r, asyncio.CancelledError) for r in results)
    assert h.service.cancel(parse_slug("ghost")) is False  # type: ignore[arg-type]


# ------------------------------------------------------------------------ batches
def layered(h: Harness) -> Catalog:
    """engine <- (lib, app) ; solo is independent. Layers: [engine, solo], [app, lib]."""
    projects = [h.project(name) for name in ("engine", "lib", "app", "solo")]
    return h.catalog(projects, [("lib", "engine"), ("app", "engine")])


async def test_a_batch_runs_layer_by_layer_and_reports_progress(harness_factory) -> None:  # type: ignore[no-untyped-def]
    h = harness_factory(Script())
    catalog = layered(h)

    result = await h.service.run_batch(catalog)

    assert result.succeeded
    assert set(h.runner.started[:2]) == {"engine", "solo"}
    assert set(h.runner.started[2:]) == {"app", "lib"}
    progress = [e for e in h.published if isinstance(e, BatchProgress)]
    assert progress[0].done == 0
    assert progress[-1].done == progress[-1].total == 4
    assert progress[-1].layers == 2


async def test_dependents_can_be_skipped_when_a_prerequisite_fails(harness_factory) -> None:  # type: ignore[no-untyped-def]
    def script_for(spec: ProcessSpec) -> Script:
        return (
            Script(lines=[(ERR, "ValueError: boom")], exit_code=1)
            if spec.cwd.name == "engine"
            else Script()
        )

    h = harness_factory(ScriptedRunner(script_for))
    catalog = layered(h)

    result = await h.service.run_batch(catalog, skip_dependents_on_failure=True)

    assert not result.succeeded
    assert {r.project for r in result.skipped} == {"app", "lib"}
    assert {r.project for r in result.failed} == {"engine"}
    assert "engine" in (result.skipped[0].failure or "")
    assert set(h.runner.started) == {"engine", "solo"}


async def test_by_default_a_failure_does_not_stop_dependents(harness_factory) -> None:  # type: ignore[no-untyped-def]
    def script_for(spec: ProcessSpec) -> Script:
        return Script(exit_code=1) if spec.cwd.name == "engine" else Script()

    h = harness_factory(ScriptedRunner(script_for))

    result = await h.service.run_batch(layered(h))

    assert set(h.runner.started) == {"engine", "solo", "app", "lib"}
    assert {r.project for r in result.failed} == {"engine"}


async def test_a_subset_can_pull_in_its_prerequisites(harness_factory) -> None:  # type: ignore[no-untyped-def]
    h = harness_factory(Script())
    catalog = layered(h)

    await h.service.run_batch(catalog, [Slug("app")], include_dependencies=True)

    assert h.runner.started == ["engine", "app"]


async def test_cancel_all_ends_a_batch_early_with_a_partial_result(harness_factory) -> None:  # type: ignore[no-untyped-def]
    gate = asyncio.Event()
    h = harness_factory(Script(hold=gate))
    batch = asyncio.create_task(h.service.run_batch(layered(h)))
    await asyncio.sleep(0.05)
    assert set(h.runner.started) == {"engine", "solo"}

    h.service.cancel_all()
    result = await batch  # a normal return: the runs were cancelled, not the batch task

    assert not result.succeeded
    assert {r.project: r.status for r in result.runs} == {
        "engine": RunStatus.CANCELLED,
        "solo": RunStatus.CANCELLED,
    }
    assert set(h.runner.started) == {"engine", "solo"}  # layer two never began


async def test_cancelling_the_batch_task_itself_propagates(harness_factory) -> None:  # type: ignore[no-untyped-def]
    gate = asyncio.Event()
    h = harness_factory(Script(hold=gate))
    batch = asyncio.create_task(h.service.run_batch(layered(h)))
    await asyncio.sleep(0.05)

    batch.cancel()
    with pytest.raises(asyncio.CancelledError):
        await batch

    assert h.executor.active_runs == ()
    assert {r.status for r in h.repo.runs.values()} == {RunStatus.CANCELLED}
    assert set(h.runner.started) == {"engine", "solo"}
