"""Composition root.

This is the *only* module that knows about every layer: it instantiates the adapters
and injects them into the use cases. Delivery layers (CLI, GUI) call
:func:`build_container` once at start-up and pass the resulting :class:`Container`
around; nothing else constructs infrastructure objects. Plain dependency injection, no
framework: the wiring is short enough to read in one sitting, which is the point.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from quant_workbench.application.catalog import ProjectCatalog
from quant_workbench.application.settings import Settings, load_settings
from quant_workbench.domain.paths import AppPaths
from quant_workbench.domain.ports import Clock, EventBus
from quant_workbench.infrastructure.clock import SystemClock
from quant_workbench.infrastructure.event_bus import InProcessEventBus
from quant_workbench.infrastructure.paths import default_app_paths
from quant_workbench.infrastructure.resources import data_path
from quant_workbench.infrastructure.workspace import FileSystemWorkspace


@dataclass(frozen=True, slots=True)
class Container:
    """Everything a delivery layer needs, wired together."""

    paths: AppPaths
    settings: Settings
    clock: Clock
    events: EventBus
    catalog: ProjectCatalog


def build_container(
    paths: AppPaths | None = None,
    settings: Settings | None = None,
    clock: Clock | None = None,
    registry_dir: Path | None = None,
) -> Container:
    """Assemble the object graph.

    Every argument is optional and exists so tests (and ``qw --home``) can substitute a
    hermetic directory layout, fixed settings, a fake clock or a custom registry.
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
    )
