"""Ports: the interfaces the application layer depends on.

Adapters in :mod:`quant_workbench.infrastructure` implement them. They are
:class:`typing.Protocol` classes (structural typing), so adapters do not need to inherit
from anything and test doubles are trivial to write.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

from quant_workbench.domain.events import DomainEvent
from quant_workbench.domain.project import ProjectSpec

E = TypeVar("E", bound=DomainEvent)

#: Calling a ``Subscription`` cancels it. Idempotent.
Subscription = Callable[[], None]


@runtime_checkable
class Clock(Protocol):
    """Source of the current time; injected so time-dependent logic is testable."""

    def now(self) -> datetime:
        """Return the current, timezone-aware, UTC time."""
        ...


class EventBus(Protocol):
    """Publish/subscribe channel for :class:`DomainEvent` instances."""

    def publish(self, event: DomainEvent) -> None:
        """Deliver ``event`` to every handler subscribed to its type or a base type."""
        ...

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> Subscription:
        """Register ``handler`` for ``event_type`` (and its subclasses)."""
        ...


class WorkspaceGateway(Protocol):
    """Everything the catalog needs to know about the folder that holds the projects."""

    def candidate_directories(self, workspace: Path) -> tuple[Path, ...]:
        """Immediate sub-directories that could be projects, in a stable order."""
        ...

    def looks_like_workspace(self, path: Path) -> bool:
        """Whether ``path`` contains enough recognisable projects to be a workspace."""
        ...

    def read_manifest(self, directory: Path) -> ProjectSpec | None:
        """The manifest inside ``directory``, or ``None`` when it has none."""
        ...

    def read_registry(self) -> tuple[ProjectSpec, ...]:
        """Manifests shipped with the workbench (they carry an explicit ``folder``)."""
        ...

    def infer_spec(self, directory: Path) -> ProjectSpec | None:
        """A best-effort spec derived from the folder layout, or ``None`` if not a project."""
        ...

    def sibling_references(self, directory: Path, known_folders: frozenset[str]) -> frozenset[str]:
        """Folder names from ``known_folders`` that ``directory``'s source code refers to."""
        ...
