"""The job executor: the life cycle of one attempt at running one or more processes.

Everything that turns "start these processes" into a fully recorded :class:`Run` happens
here, once: queueing, the concurrency limit, streaming and classifying output, timeouts
and cancellation, extracting metrics, recognising known failures, snapshotting the
configuration, persisting, and publishing events. ``RunService`` (retries, batches) and
``EnvironmentService`` (venv set-up) are thin callers of this class.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from quant_workbench.domain.errors import WorkbenchError
from quant_workbench.domain.events import DomainEvent
from quant_workbench.domain.failures import FailureSignature, match_signatures
from quant_workbench.domain.ids import RunId, new_run_id
from quant_workbench.domain.logs import LogClassifier, summarize_failure
from quant_workbench.domain.metrics import extract_metrics
from quant_workbench.domain.ports import (
    Clock,
    EventBus,
    ProcessRunner,
    ProjectFiles,
    RunRepository,
)
from quant_workbench.domain.process import ProcessOutcome, ProcessSpec, ResourceSample
from quant_workbench.domain.project import Project
from quant_workbench.domain.run_events import (
    RunFinished,
    RunOutput,
    RunQueued,
    RunResourcesSampled,
    RunStarted,
)
from quant_workbench.domain.runs import JobKind, LogLine, LogStream, Run, RunStatus

#: Policy applied to every child process, on top of the runner's own sanitising of the
#: environment. ``MPLBACKEND=Agg`` guarantees matplotlib never tries to open a window.
DEFAULT_JOB_ENV: Mapping[str, str] = {"MPLBACKEND": "Agg", "NO_COLOR": "1"}

_FLUSH_EVERY = 200


@dataclass(frozen=True, slots=True)
class JobSpec:
    """One job: a sequence of processes executed in order; the first failure stops it."""

    project: Project
    kind: JobKind
    steps: tuple[ProcessSpec, ...]
    timeout_seconds: int | None = None
    attempt: int = 1
    retry_of: RunId | None = None
    #: Failure signatures and metric extraction only make sense for a project's own run.
    extract_metrics: bool = False
    #: Configuration files (project-relative) whose text is snapshotted with the run.
    snapshot_files: tuple[str, ...] = ()
    #: Extra output shown to the user before the first process starts.
    banner: str | None = None
    #: If set, the job cannot start (e.g. the project has no virtual environment yet): it is
    #: recorded as a failed run with this message instead of launching any process.
    preflight_error: str | None = None


@dataclass(slots=True)
class _Output:
    """Collects one job's output: numbering, classification, batching, event publishing."""

    run: Run
    clock: Clock
    repository: RunRepository
    publish: Callable[[DomainEvent], None]
    classifier: LogClassifier = field(default_factory=LogClassifier)
    seq: int = 0
    texts: list[str] = field(default_factory=list)
    stderr_texts: list[str] = field(default_factory=list)
    pending: list[LogLine] = field(default_factory=list)

    def add(self, stream: LogStream, text: str) -> None:
        line = LogLine(
            seq=self.seq,
            stream=stream,
            level=self.classifier.classify(stream, text),
            text=text,
            at=self.clock.now(),
        )
        self.seq += 1
        if stream is not LogStream.SYSTEM:
            self.texts.append(text)
        if stream is LogStream.STDERR:
            self.stderr_texts.append(text)
        self.pending.append(line)
        self.publish(RunOutput(run_id=self.run.id, project=self.run.project, line=line))
        if len(self.pending) >= _FLUSH_EVERY:
            self.flush()

    def flush(self) -> None:
        if self.pending:
            self.repository.append_logs(self.run.id, self.pending)
            self.pending = []


class JobExecutor:
    """Executes :class:`JobSpec` instances under a shared concurrency limit."""

    def __init__(
        self,
        *,
        runner: ProcessRunner,
        repository: RunRepository,
        files: ProjectFiles,
        events: EventBus,
        clock: Clock,
        max_concurrency: int,
        signatures: Sequence[FailureSignature] = (),
    ) -> None:
        self._runner = runner
        self._repository = repository
        self._files = files
        self._events = events
        self._clock = clock
        self._max_concurrency = max_concurrency
        self._signatures = tuple(signatures)
        self._slots: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None
        self._active: dict[RunId, asyncio.Task[Any]] = {}

    # ------------------------------------------------------------ concurrency
    @contextlib.asynccontextmanager
    async def _slot(self) -> AsyncIterator[None]:
        """Acquire one of ``max_concurrency`` slots.

        The semaphore is bound to the running event loop, so it is (re)created if the
        loop changes (each ``asyncio.run`` in the CLI, the worker loop in the GUI).
        """
        loop = asyncio.get_running_loop()
        if self._slots is None or self._slots[0] is not loop:
            self._slots = (loop, asyncio.Semaphore(self._max_concurrency))
        async with self._slots[1]:
            yield

    def cancel(self, run_id: RunId) -> bool:
        """Cancel the task executing ``run_id``. Returns whether such a run was active."""
        task = self._active.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def cancel_all(self) -> int:
        """Cancel every active run; returns how many were signalled."""
        return sum(self.cancel(run_id) for run_id in list(self._active))

    @property
    def active_runs(self) -> tuple[RunId, ...]:
        return tuple(self._active)

    # -------------------------------------------------------------- execution
    async def execute(self, job: JobSpec) -> Run:
        """Run ``job`` to a terminal state and return its record. Never raises for a
        failing process; only ``CancelledError`` propagates (after being recorded)."""
        now = self._clock.now()
        run = Run(
            id=new_run_id(self._clock),
            project=job.project.slug,
            kind=job.kind,
            status=RunStatus.QUEUED,
            queued_at=now,
            attempt=job.attempt,
            retry_of=job.retry_of,
            timeout_seconds=job.timeout_seconds,
            command=job.steps[0].args if job.steps else (),
        )
        self._repository.save(run)
        self._events.publish(RunQueued(run=run))

        task = asyncio.current_task()
        if task is not None:
            self._active[run.id] = task
        try:
            async with self._slot():
                return await self._run_in_slot(run, job)
        except asyncio.CancelledError:
            # Cancelled while waiting for a slot: nothing ran, but the record must still
            # end in a terminal state. (If it had started, _run_in_slot already did this.)
            stored = self._repository.get(run.id)
            if stored is None or not stored.status.is_terminal:
                empty = _Output(run, self._clock, self._repository, self._events.publish)
                self._finish(
                    run,
                    empty,
                    None,
                    job=job,
                    status=RunStatus.CANCELLED,
                    reason="cancelled before it started",
                )
            raise
        finally:
            self._active.pop(run.id, None)

    async def _run_in_slot(self, queued: Run, job: JobSpec) -> Run:
        started = self._clock.now()
        snapshot = tuple(
            (relative, text)
            for relative in job.snapshot_files
            if (text := self._files.read_text(job.project, relative)) is not None
        )
        run = replace(
            queued, status=RunStatus.RUNNING, started_at=started, config_snapshot=snapshot
        )
        self._repository.save(run)
        self._events.publish(RunStarted(run=run))

        output = _Output(run, self._clock, self._repository, self._events.publish)
        if job.banner:
            output.add(LogStream.SYSTEM, job.banner)
        if job.preflight_error:
            output.add(LogStream.SYSTEM, job.preflight_error)
            return self._finish(
                run, output, None, job=job, status=RunStatus.FAILED, reason=job.preflight_error
            )

        outcome: ProcessOutcome | None = None
        peak = 0
        cpu_samples: list[float] = []

        def on_sample(sample: ResourceSample) -> None:
            nonlocal peak
            peak = max(peak, sample.rss_bytes)
            cpu_samples.append(sample.cpu_percent)
            self._events.publish(
                RunResourcesSampled(run_id=run.id, project=run.project, sample=sample)
            )

        try:
            for step in job.steps:
                output.add(LogStream.SYSTEM, "$ " + " ".join(_quote(a) for a in step.args))
                outcome = await self._runner.run(
                    ProcessSpec(step.args, step.cwd, {**DEFAULT_JOB_ENV, **step.env}),
                    on_line=output.add,
                    on_sample=on_sample,
                    timeout_seconds=job.timeout_seconds,
                )
                if outcome.timed_out or outcome.exit_code != 0:
                    break
        except asyncio.CancelledError:
            # Cancellation is not a failure: record it, then let it propagate so the
            # caller's own cancellation semantics still hold.
            self._finish(
                replace(run, peak_rss_bytes=peak),
                output,
                outcome,
                job=job,
                status=RunStatus.CANCELLED,
                reason="cancelled by the user",
            )
            raise
        except (OSError, WorkbenchError) as exc:
            output.add(LogStream.SYSTEM, f"could not start the process: {exc}")
            return self._finish(
                run, output, None, status=RunStatus.FAILED, reason=str(exc), job=job
            )

        avg_cpu = sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0.0
        run = replace(run, peak_rss_bytes=peak, avg_cpu_percent=avg_cpu)
        return self._finish(run, output, outcome, job=job)

    # -------------------------------------------------------------- finishing
    def _finish(
        self,
        run: Run,
        output: _Output,
        outcome: ProcessOutcome | None,
        *,
        job: JobSpec,
        status: RunStatus | None = None,
        reason: str | None = None,
    ) -> Run:
        """Derive the final state, persist it and publish it."""
        if status is None:
            status = self._status_of(outcome)
        text = "\n".join(output.texts)
        matched = match_signatures(text, self._signatures)

        failure = reason
        if failure is None and status is not RunStatus.SUCCEEDED:
            failure = self._explain(status, outcome, output, matched)

        if status is not RunStatus.SUCCEEDED:
            output.add(LogStream.SYSTEM, f"{status.value}: {failure}" if failure else status.value)
        output.flush()

        finished = replace(
            run,
            status=status,
            finished_at=self._clock.now(),
            exit_code=outcome.exit_code if outcome else None,
            failure=failure,
            transient=status is RunStatus.FAILED and any(s.transient for s in matched),
            signatures=tuple(s.id for s in matched),
            metrics=extract_metrics(output.texts, job.project.spec.extractors)
            if job.extract_metrics and status is RunStatus.SUCCEEDED
            else (),
            outputs=self._files.snapshot_outputs(job.project) if job.kind is JobKind.RUN else (),
        )
        self._repository.save(finished)
        self._events.publish(RunFinished(run=finished))
        return finished

    @staticmethod
    def _status_of(outcome: ProcessOutcome | None) -> RunStatus:
        if outcome is None:
            return RunStatus.FAILED
        if outcome.timed_out:
            return RunStatus.TIMED_OUT
        return RunStatus.SUCCEEDED if outcome.exit_code == 0 else RunStatus.FAILED

    @staticmethod
    def _explain(
        status: RunStatus,
        outcome: ProcessOutcome | None,
        output: _Output,
        matched: Sequence[FailureSignature],
    ) -> str:
        if status is RunStatus.TIMED_OUT:
            return "exceeded the time limit and was killed"
        summary = summarize_failure(output.stderr_texts) or summarize_failure(output.texts)
        if matched:
            return matched[0].title + (f" ({summary})" if summary else "")
        if summary:
            return summary
        return f"exited with code {outcome.exit_code}" if outcome else "did not start"


def _quote(argument: str) -> str:
    return f'"{argument}"' if " " in argument else argument
