"""Ports: the interfaces the application layer depends on.

Adapters in :mod:`quant_workbench.infrastructure` implement them. They are
:class:`typing.Protocol` classes (structural typing), so adapters do not need to inherit
from anything and test doubles are trivial to write.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

from quant_workbench.domain.config import Constant
from quant_workbench.domain.events import DomainEvent
from quant_workbench.domain.git import CommitInfo, GitState
from quant_workbench.domain.ids import RunId, Slug
from quant_workbench.domain.process import ProcessOutcome, ProcessSpec, ResourceSample
from quant_workbench.domain.project import Project, ProjectSpec
from quant_workbench.domain.runs import LogLine, LogStream, OutputFileState, Run
from quant_workbench.domain.study import CardState

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


class ProcessRunner(Protocol):
    """Runs one OS process to completion, streaming its output."""

    async def run(
        self,
        spec: ProcessSpec,
        *,
        on_line: Callable[[LogStream, str], None],
        on_sample: Callable[[ResourceSample], None] | None = None,
        timeout_seconds: float | None = None,
    ) -> ProcessOutcome:
        """Execute ``spec`` and return how it ended.

        Every output line is delivered to ``on_line`` as soon as it is produced. If the
        awaiting task is cancelled, or ``timeout_seconds`` elapses, the **whole process
        tree** is terminated before returning (or re-raising ``CancelledError``): no
        orphaned child processes may survive.
        """
        ...


class InterpreterResolver(Protocol):
    """Finds the Python interpreter a project must run with."""

    def find(self, project: Project) -> Path | None:
        """The interpreter of the project's own environment, or ``None`` if it has none."""
        ...

    def resolve(self, project: Project) -> Path:
        """The interpreter of the project's own environment.

        Raises :class:`~quant_workbench.domain.errors.EnvironmentSetupError` when the
        environment does not exist, with a hint on how to create it.
        """
        ...


class ProjectFiles(Protocol):
    """Read access to files inside a project."""

    def read_text(self, project: Project, relative: str) -> str | None:
        """The text of ``relative`` inside ``project``, or ``None`` if it does not exist."""
        ...

    def snapshot_outputs(self, project: Project) -> tuple[OutputFileState, ...]:
        """Size and SHA-256 of every declared output file that currently exists."""
        ...

    def python_sources(self, project: Project) -> tuple[str, ...]:
        """Relative paths (forward slashes) of the project's own ``.py`` files, sorted.

        Virtual environments, caches and other vendored folders are excluded.
        """
        ...

    def exists(self, project: Project, relative: str) -> bool:
        """Whether ``relative`` is an existing file inside the project."""
        ...

    def modified_at(self, project: Project, relative: str) -> datetime | None:
        """When ``relative`` was last modified (UTC), or ``None`` if it does not exist."""
        ...

    def size(self, project: Project, relative: str) -> int | None:
        """The size in bytes of ``relative``, or ``None`` if it does not exist."""
        ...

    def read_bytes(self, project: Project, relative: str) -> bytes | None:
        """The raw content of ``relative``, or ``None`` if it does not exist."""
        ...

    def write_bytes(self, project: Project, relative: str, data: bytes) -> None:
        """Create or replace ``relative`` (parent folders included) with ``data``."""
        ...

    def write_text(self, project: Project, relative: str, text: str) -> Path | None:
        """Replace ``relative`` with ``text`` atomically, byte for byte (no newline changes).

        The previous content is copied to the workbench's backup folder first; the backup's
        location is returned (``None`` if the file did not exist, so there was nothing to keep).
        """
        ...


class ConfigEditor(Protocol):
    """Reads and rewrites the literal constants of a Python source file."""

    def constants(self, source: str, *, symbols: Collection[str] = ()) -> tuple[Constant, ...]:
        """The editable constants in ``source`` (only ``symbols`` when given), in file order."""
        ...

    def edit(self, source: str, changes: Mapping[str, object]) -> str:
        """``source`` with the constants in ``changes`` set to their new values.

        Everything else in the file (comments, spacing, line endings, other constants) is
        returned untouched. Raises :class:`~quant_workbench.domain.errors.UnsafeEditError`
        rather than produce a file it cannot vouch for.
        """
        ...


class RunRepository(Protocol):
    """Persistence of runs and their logs."""

    def save(self, run: Run) -> None:
        """Insert ``run`` or replace the stored one with the same id."""
        ...

    def get(self, run_id: RunId) -> Run | None: ...

    def list_runs(self, project: Slug | None = None, *, limit: int = 50) -> tuple[Run, ...]:
        """Most recent first."""
        ...

    def latest(self, project: Slug) -> Run | None: ...

    def append_logs(self, run_id: RunId, lines: Sequence[LogLine]) -> None: ...

    def logs(
        self, run_id: RunId, *, offset: int = 0, limit: int | None = None
    ) -> tuple[LogLine, ...]: ...

    def close(self) -> None:
        """Release the underlying connections. The repository is unusable afterwards."""
        ...


class GitGateway(Protocol):
    """Read access to a repository, plus the few writes the doctor's fixes need."""

    def is_repository(self, root: Path) -> bool:
        """Whether ``root`` is the top level of a git working tree."""
        ...

    def state(self, root: Path) -> GitState:
        """Branch, uncommitted paths and ahead/behind counts."""
        ...

    def config_value(self, root: Path, key: str) -> str | None:
        """The *effective* value of a config key in this repository (local, then global)."""
        ...

    def remote_url(self, root: Path, name: str = "origin") -> str | None:
        """The URL of remote ``name``, or ``None`` if there is no such remote."""
        ...

    def tracked_files(self, root: Path) -> tuple[str, ...]:
        """Every path tracked by the repository, relative to ``root``."""
        ...

    def is_ignored(self, root: Path, relative: str) -> bool:
        """Whether ``relative`` is matched by the repository's ignore rules."""
        ...

    def set_local_config(self, root: Path, key: str, value: str) -> None:
        """Set ``key`` in this repository's own config. Never touches the global config."""
        ...

    def log(self, root: Path, limit: int = 20) -> tuple[CommitInfo, ...]:
        """The most recent commits of the current branch, newest first."""
        ...

    def diff(self, root: Path, relative: str | None = None) -> str:
        """Uncommitted changes against ``HEAD`` (of one path, or of everything tracked)."""
        ...

    def commit(self, root: Path, message: str, paths: Sequence[str]) -> str:
        """Stage ``paths`` (everything when empty) and commit; returns the new short hash.

        Hooks are never skipped. There is deliberately no ``push`` in this port.
        """
        ...


class StudyRepository(Protocol):
    """Persistence of the spaced-repetition state of the study cards."""

    def states(self, project: Slug | None = None) -> dict[str, CardState]:
        """The state of every card that has been reviewed (all projects, or one)."""
        ...

    def save(self, card_id: str, project: Slug, state: CardState, reviewed_at: datetime) -> None:
        """Insert or replace the state of one card."""
        ...
