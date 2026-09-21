"""Running projects: retries for transient failures, batches ordered by the dependency graph."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field

from quant_workbench.application.catalog import Catalog
from quant_workbench.application.jobs import JobExecutor, JobSpec
from quant_workbench.domain.errors import EnvironmentSetupError
from quant_workbench.domain.ids import RunId, Slug, new_run_id
from quant_workbench.domain.ports import Clock, EventBus, InterpreterResolver, RunRepository
from quant_workbench.domain.process import ProcessSpec
from quant_workbench.domain.project import Project
from quant_workbench.domain.run_events import BatchProgress, RunFinished
from quant_workbench.domain.runs import JobKind, Run, RunStatus

Sleeper = Callable[[float], Awaitable[None]]

_MAX_BACKOFF_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class RunOptions:
    """Per-invocation overrides of the configured defaults."""

    timeout_seconds: int | None = None
    max_retries: int | None = None
    extra_env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BatchResult:
    """The outcome of running several projects."""

    runs: tuple[Run, ...]

    @property
    def succeeded(self) -> bool:
        return all(run.status is RunStatus.SUCCEEDED for run in self.runs)

    @property
    def failed(self) -> tuple[Run, ...]:
        return tuple(
            r for r in self.runs if r.status not in {RunStatus.SUCCEEDED, RunStatus.SKIPPED}
        )

    @property
    def skipped(self) -> tuple[Run, ...]:
        return tuple(r for r in self.runs if r.status is RunStatus.SKIPPED)


class RunService:
    """Use case: run projects, one at a time or as a dependency-ordered batch."""

    def __init__(
        self,
        *,
        executor: JobExecutor,
        interpreters: InterpreterResolver,
        repository: RunRepository,
        events: EventBus,
        clock: Clock,
        default_timeout_seconds: int,
        default_max_retries: int,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._executor = executor
        self._interpreters = interpreters
        self._repository = repository
        self._events = events
        self._clock = clock
        self._default_timeout = default_timeout_seconds
        self._default_retries = default_max_retries
        self._sleep = sleep

    # -------------------------------------------------------------- one project
    async def run(self, project: Project, options: RunOptions | None = None) -> Run:
        """Run one project, retrying automatically while the failure looks transient."""
        options = options or RunOptions()
        retries = self._default_retries if options.max_retries is None else options.max_retries
        previous: Run | None = None
        attempt = 1
        while True:
            run = await self._executor.execute(self._job_for(project, options, attempt, previous))
            if run.status is RunStatus.FAILED and run.transient and attempt <= retries:
                await self._sleep(min(_MAX_BACKOFF_SECONDS, 2.0**attempt))
                previous, attempt = run, attempt + 1
                continue
            return run

    def _job_for(
        self, project: Project, options: RunOptions, attempt: int, previous: Run | None
    ) -> JobSpec:
        preflight_error: str | None = None
        try:
            interpreter = self._interpreters.resolve(project)
        except EnvironmentSetupError as exc:
            # A missing environment is an ordinary, recorded failure (it shows up in the
            # history and a batch carries on), carrying the hint on how to fix it.
            interpreter = project.venv_python
            preflight_error = str(exc)
        step = ProcessSpec(
            args=(str(interpreter), str(project.entrypoint_path)),
            cwd=project.root,
            env=options.extra_env,
        )
        return JobSpec(
            project=project,
            kind=JobKind.RUN,
            steps=(step,),
            timeout_seconds=options.timeout_seconds or self._default_timeout,
            attempt=attempt,
            retry_of=previous.id if previous else None,
            extract_metrics=True,
            snapshot_files=tuple(dict.fromkeys(t.file for t in project.spec.config_targets)),
            banner=f"{project.title} (attempt {attempt})" if attempt > 1 else None,
            preflight_error=preflight_error,
        )

    # ------------------------------------------------------------------- batch
    async def run_batch(
        self,
        catalog: Catalog,
        targets: Sequence[Slug] | None = None,
        *,
        include_dependencies: bool = False,
        skip_dependents_on_failure: bool = False,
        options: RunOptions | None = None,
    ) -> BatchResult:
        """Run ``targets`` (default: everything) layer by layer through the dependency graph.

        Projects in the same layer are independent and run concurrently (bounded by the
        executor's concurrency limit); a layer starts only after the previous one ended.
        By default a failure does not stop the batch, because in this portfolio projects
        share *code*, not *results*. With ``skip_dependents_on_failure`` the projects that
        depend on a failed one are recorded as skipped instead of run.

        Cancelling individual runs (``cancel_all``) ends the batch early and returns the
        partial result, cancelled runs included; cancelling the task awaiting the batch
        propagates ``CancelledError`` as usual.
        """
        wanted = tuple(targets) if targets else catalog.slugs
        plan = catalog.graph.execution_plan(wanted, include_dependencies=include_dependencies)
        total = sum(len(layer) for layer in plan)
        finished: list[Run] = []
        failed: set[Slug] = set()

        def progress(layer_index: int) -> None:
            self._events.publish(
                BatchProgress(
                    done=len(finished), total=total, current_layer=layer_index + 1, layers=len(plan)
                )
            )

        cancelled = False

        async def one(slug: Slug, layer_index: int) -> None:
            nonlocal cancelled
            project = catalog.get(slug)
            blocked = catalog.graph.dependencies_of(slug, transitive=True) & failed
            if skip_dependents_on_failure and blocked:
                run = self._skipped(project, sorted(blocked)[0])
            else:
                try:
                    run = await self.run(project, options)
                except asyncio.CancelledError:
                    # A TaskGroup does not treat a cancelled child as an error and would go on
                    # to the next layer: remember the cancellation so the batch stops instead,
                    # and keep the (already recorded) cancelled run in the batch's result.
                    cancelled = True
                    stored = self._repository.latest(slug)
                    if stored is not None and stored.status is RunStatus.CANCELLED:
                        finished.append(stored)
                    raise
            finished.append(run)
            if run.status is not RunStatus.SUCCEEDED:
                failed.add(slug)
            progress(layer_index)

        progress(0)
        for layer_index, layer in enumerate(plan):
            if cancelled:
                break
            async with asyncio.TaskGroup() as group:
                for slug in layer:
                    group.create_task(one(slug, layer_index))
        return BatchResult(runs=tuple(finished))

    def _skipped(self, project: Project, blocker: Slug) -> Run:
        now = self._clock.now()
        run = Run(
            id=new_run_id(self._clock),
            project=project.slug,
            kind=JobKind.RUN,
            status=RunStatus.SKIPPED,
            queued_at=now,
            finished_at=now,
            failure=f"skipped: prerequisite '{blocker}' did not succeed",
        )
        self._repository.save(run)
        self._events.publish(RunFinished(run=run))
        return run

    # ------------------------------------------------------------ cancellation
    def cancel(self, run_id: RunId) -> bool:
        return self._executor.cancel(run_id)

    def cancel_all(self) -> int:
        return self._executor.cancel_all()
