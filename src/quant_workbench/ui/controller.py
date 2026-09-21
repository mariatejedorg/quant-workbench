"""The application controller: what the windows call, and what tells them something happened.

It is the GUI's counterpart of the CLI command functions: it owns no widgets, calls the same
use cases through the :class:`~quant_workbench.bootstrap.Container`, and reports back with Qt
signals. Widgets talk to the controller and never to the engine or to each other, which keeps
every widget testable with a fake controller and the controller testable without a window.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from quant_workbench.application.catalog import Catalog
from quant_workbench.application.diagnostics import DoctorOptions
from quant_workbench.application.fixes import FixPreview
from quant_workbench.application.runs import BatchResult
from quant_workbench.bootstrap import Container
from quant_workbench.domain.diagnostics import DoctorReport, Finding, Severity
from quant_workbench.domain.errors import WorkbenchError
from quant_workbench.domain.ids import RunId, Slug
from quant_workbench.domain.project import Project
from quant_workbench.domain.run_events import BatchProgress, RunFinished
from quant_workbench.domain.runs import Run
from quant_workbench.ui.async_bridge import AsyncBridge, EventRelay

_log = logging.getLogger(__name__)


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
        if isinstance(error, WorkbenchError):
            hint = f"\n{error.hint}" if error.hint else ""
            self.message.emit("error", f"{error.message}{hint}")
        else:
            _log.error("unexpected failure in the engine", exc_info=error)
            self.message.emit("error", f"Unexpected error: {type(error).__name__}: {error}")

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
