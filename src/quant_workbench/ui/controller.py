"""The application controller: what the windows call, and what tells them something happened.

It is the GUI's counterpart of the CLI command functions: it owns no widgets, calls the same
use cases through the :class:`~quant_workbench.bootstrap.Container`, and reports back with Qt
signals. Widgets talk to the controller and never to the engine or to each other, which keeps
every widget testable with a fake controller and the controller testable without a window.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TypeVar

from PySide6.QtCore import QObject, Signal

from quant_workbench.application.catalog import Catalog
from quant_workbench.application.config import ApplyResult, ConfigPlan, ProjectConstant
from quant_workbench.application.diagnostics import DoctorOptions
from quant_workbench.application.fixes import FixPreview
from quant_workbench.application.runs import BatchResult
from quant_workbench.bootstrap import Container
from quant_workbench.domain.code_analysis import parse
from quant_workbench.domain.diagnostics import DoctorReport, Finding, Severity
from quant_workbench.domain.errors import WorkbenchError
from quant_workbench.domain.ids import RunId, Slug
from quant_workbench.domain.project import Project
from quant_workbench.domain.run_events import BatchProgress, RunFinished
from quant_workbench.domain.runs import LogLine, Run
from quant_workbench.ui.async_bridge import AsyncBridge, EventRelay

_log = logging.getLogger(__name__)

T = TypeVar("T")


class AppController(QObject):
    """Coordinates the engine, the catalog and the windows."""

    #: The catalog was (re)loaded; views must rebuild what they show.
    catalog_changed = Signal()
    #: A batch of domain events, delivered on the GUI thread (see :class:`EventRelay`).
    events = Signal(list)
    #: A doctor run finished.
    report_ready = Signal(object)
    #: Something the user should read: ``(level, text)`` with level ``info``/``error``.
    message = Signal(str, str)
    #: ``True`` while any job submitted through the controller is running.
    busy_changed = Signal(bool)
    #: A project's configuration files were rewritten (edit, undo or redo): its slug.
    config_changed = Signal(str)

    def __init__(self, container: Container, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.container = container
        self.catalog: Catalog | None = None
        self.report: DoctorReport | None = None
        self._pending = 0
        # Build the lazily-created services here, on the GUI thread, before the engine thread
        # can race to create them.
        for name in ("runs", "environments", "doctor", "fixes", "git", "config", "repository"):
            getattr(container, name)
        self._bridge = AsyncBridge(self)
        self._relay = EventRelay(container.events, parent=self)
        self._relay.batch.connect(self._on_events)

    # ------------------------------------------------------------------ plumbing
    @property
    def bridge(self) -> AsyncBridge:
        return self._bridge

    @property
    def relay(self) -> EventRelay:
        return self._relay

    def _on_events(self, batch: list[object]) -> None:
        self.events.emit(batch)

    def _track(self) -> None:
        self._pending += 1
        if self._pending == 1:
            self.busy_changed.emit(True)

    def _untrack(self) -> None:
        # a job's last events may still be waiting for the relay's next tick: deliver them
        # first, so the views never announce "done" while still showing "running"
        self._relay.drain()
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self.busy_changed.emit(False)

    @property
    def is_busy(self) -> bool:
        return self._pending > 0

    def _fail(self, error: BaseException) -> None:
        self._untrack()
        self.report_error(error)

    # ------------------------------------------------------------------- catalog
    def open_workspace(self, workspace: Path) -> bool:
        """Load the projects in ``workspace``. Returns whether it worked."""
        try:
            self.catalog = self.container.catalog.load(workspace)
        except WorkbenchError as error:
            self.message.emit("error", f"{error.message}\n{error.hint or ''}".strip())
            return False
        self.report = None
        self.catalog_changed.emit()
        return True

    def project(self, slug: str) -> Project | None:
        if self.catalog is None:
            return None
        return next((p for p in self.catalog.projects if p.slug == slug), None)

    def latest_run(self, slug: str) -> Run | None:
        return self.container.repository.latest(Slug(slug))

    # ---------------------------------------------------------------------- runs
    def run_projects(
        self,
        slugs: Sequence[str],
        *,
        include_dependencies: bool = False,
        skip_dependents: bool = False,
    ) -> None:
        """Run projects through the dependency graph (independent ones concurrently)."""
        if self.catalog is None or not slugs:
            return
        self._track()
        self._bridge.submit(
            self.container.runs.run_batch(
                self.catalog,
                [Slug(s) for s in slugs],
                include_dependencies=include_dependencies,
                skip_dependents_on_failure=skip_dependents,
            ),
            on_result=self._batch_done,
            on_error=self._fail,
        )

    def run_all(self, *, skip_dependents: bool = False) -> None:
        if self.catalog is not None:
            self.run_projects(self.catalog.slugs, skip_dependents=skip_dependents)

    def run_impacted(self, slug: str) -> None:
        """Re-run a project and everything that (transitively) depends on it."""
        if self.catalog is not None:
            self.run_projects(self.catalog.graph.impact_of([Slug(slug)]))

    def _batch_done(self, result: BatchResult) -> None:
        self._untrack()
        failed = len(result.failed)
        text = f"{len(result.runs)} run(s) finished" + (f", {failed} failed" if failed else "")
        self.message.emit("error" if failed else "info", text)

    def cancel_all(self) -> None:
        """Ask the engine to stop every running project (their process trees are killed)."""
        self._bridge.call(self.container.runs.cancel_all)

    def cancel(self, run_id: str) -> None:
        self._bridge.call(lambda: self.container.runs.cancel(RunId(run_id)))

    # -------------------------------------------------------------------- doctor
    def run_doctor(self, slugs: Sequence[str] | None = None) -> None:
        """Diagnose the given projects (default all); the report arrives via ``report_ready``."""
        if self.catalog is None:
            return
        catalog = self.catalog
        targets = [p for p in catalog.projects if slugs is None or p.slug in slugs]
        context = self.container.check_context(catalog)
        self._track()
        self._bridge.submit(
            self.container.doctor.run(context, targets, DoctorOptions()),
            on_result=self._doctor_done,
            on_error=self._fail,
        )

    def _doctor_done(self, report: DoctorReport) -> None:
        self._untrack()
        self.report = report
        self.report_ready.emit(report)
        counts = report.counts()
        self.message.emit(
            "error" if not report.ok else "info",
            f"Doctor: {counts[Severity.ERROR]} error(s), {counts[Severity.WARNING]} warning(s), "
            f"{counts[Severity.INFO]} note(s)",
        )

    def can_fix(self, finding: Finding) -> bool:
        return self.container.fixes.can_fix(finding)

    async def preview_fix(self, finding: Finding) -> FixPreview:
        if self.catalog is None:
            raise WorkbenchError("No workspace is open")
        return await self.container.fixes.preview(
            finding, self.container.check_context(self.catalog)
        )

    def apply_fix(self, finding: Finding) -> None:
        """Apply a finding's automatic fix, then diagnose the project again."""
        if self.catalog is None or finding.project is None:
            return
        context = self.container.check_context(self.catalog)
        slug = str(finding.project)

        async def apply() -> str:
            return await self.container.fixes.apply(finding, context)

        def done(text: str) -> None:
            self._untrack()
            self.message.emit("info", text)
            self.run_doctor([slug])

        self._track()
        self._bridge.submit(apply(), on_result=done, on_error=self._fail)

    # ------------------------------------------------------------- configuration
    def config_constants(self, slug: str) -> tuple[ProjectConstant, ...]:
        """The editable constants of a project (reads its configuration files)."""
        project = self.project(slug)
        return self.container.config.constants(project) if project else ()

    def plan_config(self, slug: str, changes: Mapping[str, object]) -> ConfigPlan:
        """The effect of setting constants; raises ``UnsafeEditError`` when it cannot be done."""
        project = self._require_project(slug)
        return self.container.config.plan(project, changes)

    def apply_config(self, slug: str, plan: ConfigPlan) -> ApplyResult:
        """Write a plan (or the inverse of one, for undo) and tell the views."""
        result = self.container.config.apply(self._require_project(slug), plan)
        self.config_changed.emit(slug)
        return result

    def _require_project(self, slug: str) -> Project:
        project = self.project(slug)
        if project is None:
            raise WorkbenchError(f"No project {slug!r} in the open workspace")
        return project

    # -------------------------------------------------------------------- sources
    def project_sources(self, slug: str) -> tuple[str, ...]:
        """Relative paths of the project's Python files, its configuration files and README."""
        project = self.project(slug)
        if project is None:
            return ()
        files = self.container.project_files
        extra = [t.file for t in project.spec.config_targets]
        readme = [n for n in ("README.md",) if files.exists(project, n)]
        return tuple(dict.fromkeys([*files.python_sources(project), *extra, *readme]))

    def read_source(self, slug: str, relative: str) -> str | None:
        project = self.project(slug)
        return self.container.project_files.read_text(project, relative) if project else None

    def save_source(self, slug: str, relative: str, text: str, *, expected: str) -> None:
        """Write a source file the user edited, keeping a backup.

        Refused if the file no longer holds ``expected`` (someone else changed it) or, for
        Python, if the new text does not parse: the viewer must not break a project.
        """
        project = self._require_project(slug)
        files = self.container.project_files
        if files.read_text(project, relative) != expected:
            raise WorkbenchError(
                f"{relative} changed on disk since it was opened",
                hint="Reload the file and apply your edit again.",
            )
        if relative.endswith(".py") and parse(text) is None:
            raise WorkbenchError(
                f"{relative} would not be valid Python",
                hint="Fix the syntax error first; nothing was saved.",
            )
        files.write_text(project, relative, text)
        self.config_changed.emit(slug)

    # ------------------------------------------------------------------- history
    def list_runs(self, slug: str, limit: int = 100) -> tuple[Run, ...]:
        return self.container.repository.list_runs(Slug(slug), limit=limit)

    def run_logs(self, run_id: str) -> tuple[LogLine, ...]:
        return self.container.repository.logs(RunId(run_id))

    # -------------------------------------------------------------- blocking work
    def run_blocking(
        self,
        function: Callable[[], T],
        on_result: Callable[[T], None],
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        """Run a blocking function (git, file scanning) off the GUI thread.

        ``on_result`` / ``on_error`` run on the GUI thread; without ``on_error`` the failure
        is shown as a message like any other engine error.
        """
        self._bridge.submit(
            asyncio.to_thread(function), on_result=on_result, on_error=on_error or self.report_error
        )

    def report_error(self, error: BaseException) -> None:
        """Show an exception as a message (expected errors without a traceback)."""
        if isinstance(error, WorkbenchError):
            hint = f"\n{error.hint}" if error.hint else ""
            self.message.emit("error", f"{error.message}{hint}")
        else:
            _log.error("unexpected failure", exc_info=error)
            self.message.emit("error", f"Unexpected error: {type(error).__name__}: {error}")

    # ------------------------------------------------------------------ shutdown
    def shutdown(self) -> None:
        """Stop the engine (killing running projects) and release the database."""
        self._relay.close()
        self._bridge.stop()
        self.container.close()


def finished_runs(events: Sequence[object]) -> list[Run]:
    """The runs that reached a terminal state in a batch of events (helper for views)."""
    return [e.run for e in events if isinstance(e, RunFinished)]


def latest_progress(events: Sequence[object]) -> BatchProgress | None:
    """The most recent batch-progress event in a batch of events, if any."""
    found = [e for e in events if isinstance(e, BatchProgress)]
    return found[-1] if found else None
