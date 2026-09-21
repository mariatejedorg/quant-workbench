"""Composition root.

This is the *only* module that knows about every layer: it instantiates the adapters
and injects them into the use cases. Delivery layers (CLI, GUI) call
:func:`build_container` once at start-up and pass the resulting :class:`Container`
around; nothing else constructs infrastructure objects. Plain dependency injection, no
framework: the wiring is short enough to read in one sitting, which is the point.

The engine services (database, runner, signatures) are built lazily, on first use, so
commands that never run anything (``qw list``, ``qw graph``) do not open the database.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from quant_workbench.application.catalog import ProjectCatalog
from quant_workbench.application.config import ConfigService
from quant_workbench.application.environments import EnvironmentService
from quant_workbench.application.jobs import JobExecutor
from quant_workbench.application.runs import RunService
from quant_workbench.application.settings import Settings, load_settings
from quant_workbench.domain.failures import FailureSignature
from quant_workbench.domain.paths import AppPaths
from quant_workbench.domain.ports import Clock, EventBus, ProjectFiles, RunRepository
from quant_workbench.infrastructure.clock import SystemClock
from quant_workbench.infrastructure.config_editor import LibCstConfigEditor
from quant_workbench.infrastructure.event_bus import InProcessEventBus
from quant_workbench.infrastructure.paths import default_app_paths
from quant_workbench.infrastructure.process import AsyncSubprocessRunner
from quant_workbench.infrastructure.project_files import (
    FileSystemProjectFiles,
    VenvInterpreterResolver,
)
from quant_workbench.infrastructure.resources import data_path
from quant_workbench.infrastructure.signatures import load_signatures
from quant_workbench.infrastructure.sqlite_runs import SqliteRunRepository, create_sqlite_engine
from quant_workbench.infrastructure.workspace import FileSystemWorkspace


@dataclass(frozen=True)
class Container:
    """Everything a delivery layer needs, wired together."""

    paths: AppPaths
    settings: Settings
    clock: Clock
    events: EventBus
    catalog: ProjectCatalog
    #: ``None`` keeps run history in memory (used by tests).
    database: Path | None = None
    signatures_dir: Path | None = None

    @cached_property
    def signatures(self) -> tuple[FailureSignature, ...]:
        return load_signatures(self.signatures_dir or data_path("failure_signatures"))

    @cached_property
    def project_files(self) -> ProjectFiles:
        return FileSystemProjectFiles(backups=self.paths.backups_dir, clock=self.clock)

    @cached_property
    def config(self) -> ConfigService:
        return ConfigService(files=self.project_files, editor=LibCstConfigEditor())

    @cached_property
    def repository(self) -> RunRepository:
        return SqliteRunRepository(create_sqlite_engine(self.database))

    @cached_property
    def executor(self) -> JobExecutor:
        return JobExecutor(
            runner=AsyncSubprocessRunner(self.clock),
            repository=self.repository,
            files=self.project_files,
            events=self.events,
            clock=self.clock,
            max_concurrency=self.settings.max_concurrency,
            signatures=self.signatures,
        )

    @cached_property
    def runs(self) -> RunService:
        return RunService(
            executor=self.executor,
            interpreters=VenvInterpreterResolver(),
            repository=self.repository,
            events=self.events,
            clock=self.clock,
            default_timeout_seconds=self.settings.run_timeout_seconds,
            default_max_retries=self.settings.max_retries,
        )

    def close(self) -> None:
        """Release what the lazily-created services hold (the database connection pool).

        Safe to call more than once, and when no service was ever created.
        """
        repository = self.__dict__.pop("repository", None)
        if repository is not None:
            repository.close()

    @cached_property
    def environments(self) -> EnvironmentService:
        return EnvironmentService(
            executor=self.executor,
            runner=AsyncSubprocessRunner(self.clock),
            interpreters=VenvInterpreterResolver(),
            files=self.project_files,
            base_python=Path(sys.executable),
            default_timeout_seconds=self.settings.run_timeout_seconds,
        )


def build_container(
    *,
    paths: AppPaths | None = None,
    settings: Settings | None = None,
    clock: Clock | None = None,
    registry_dir: Path | None = None,
    database: Path | None = None,
    signatures_dir: Path | None = None,
) -> Container:
    """Assemble the object graph.

    Every argument is optional and exists so tests (and ``qw --home``) can substitute a
    hermetic directory layout, fixed settings, a fake clock, a custom registry or a
    different database. ``database`` defaults to the per-user database file.
    """
    resolved_paths = paths or default_app_paths()
    resolved_settings = settings or load_settings(resolved_paths.settings_file)
    return Container(
        paths=resolved_paths,
        settings=resolved_settings,
        clock=clock or SystemClock(),
        events=InProcessEventBus(),
        catalog=ProjectCatalog(
            FileSystemWorkspace(registry_dir=registry_dir or data_path("registry"))
        ),
        database=database if database is not None else resolved_paths.database_file,
        signatures_dir=signatures_dir,
    )
