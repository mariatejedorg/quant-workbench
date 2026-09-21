"""Access to files inside a project, and the venv interpreter lookup."""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

from quant_workbench.domain.errors import EnvironmentSetupError, UnsafeEditError
from quant_workbench.domain.ports import Clock
from quant_workbench.domain.project import Project
from quant_workbench.domain.runs import OutputFileState

_CHUNK = 1024 * 1024

#: Folders that never hold the project's own source: environments, caches, tool output.
_NOT_SOURCE = frozenset(
    {"venv", ".venv", "env", "__pycache__", "node_modules", "build", "dist", "site-packages"}
)


class FileSystemProjectFiles:
    """Implements :class:`~quant_workbench.domain.ports.ProjectFiles`.

    ``backups`` is the folder where :meth:`write_text` keeps the previous version of every
    file it replaces (``None`` disables backups); ``clock`` names each backup.
    """

    def __init__(self, backups: Path | None = None, clock: Clock | None = None) -> None:
        self._backups = backups
        self._clock = clock

    def read_text(self, project: Project, relative: str) -> str | None:
        """The file's text, or ``None`` if it does not exist or would escape the project.

        The escape check is defence in depth: manifest paths are validated to be relative
        without ``..``, but a symlink could still point outside the project folder. The
        bytes are decoded as they are, so Windows line endings (CRLF) survive: reading
        with ``Path.read_text`` would silently turn them into a bare LF.
        """
        path = self._inside(project, relative)
        if path is None or not path.is_file():
            return None
        return path.read_bytes().decode("utf-8", errors="replace")

    def write_text(self, project: Project, relative: str, text: str) -> Path | None:
        path = self._inside(project, relative)
        if path is None:
            raise UnsafeEditError(f"{relative} is outside {project.title}")
        backup = self._backup(project, path, relative)
        # Write next to the target and swap: a crash mid-write can never leave the project
        # with a half-written config file. ``Path.replace`` is atomic on the same volume.
        temporary = path.with_name(f".{path.name}.qw-tmp")
        try:
            temporary.write_bytes(text.encode("utf-8"))
            if path.exists():
                shutil.copymode(path, temporary)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return backup

    def python_sources(self, project: Project) -> tuple[str, ...]:
        root = project.root
        found: list[str] = []
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                entries = list(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir():
                    hidden = entry.name.startswith(".")
                    if not hidden and entry.name not in _NOT_SOURCE and entry != project.venv_dir:
                        stack.append(entry)
                elif entry.suffix == ".py":
                    found.append(entry.relative_to(root).as_posix())
        return tuple(sorted(found))

    def exists(self, project: Project, relative: str) -> bool:
        path = self._inside(project, relative)
        return path is not None and path.is_file()

    def modified_at(self, project: Project, relative: str) -> datetime | None:
        path = self._inside(project, relative)
        if path is None or not path.is_file():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)

    def size(self, project: Project, relative: str) -> int | None:
        path = self._inside(project, relative)
        return path.stat().st_size if path is not None and path.is_file() else None

    def read_bytes(self, project: Project, relative: str) -> bytes | None:
        path = self._inside(project, relative)
        if path is None or not path.is_file():
            return None
        return path.read_bytes()

    def write_bytes(self, project: Project, relative: str, data: bytes) -> None:
        path = self._inside(project, relative)
        if path is None:
            raise UnsafeEditError(f"{relative} is outside {project.title}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _backup(self, project: Project, path: Path, relative: str) -> Path | None:
        if self._backups is None or self._clock is None or not path.is_file():
            return None
        stamp = self._clock.now().strftime("%Y%m%dT%H%M%S%f")
        target = self._backups / project.slug / stamp / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return target

    def snapshot_outputs(self, project: Project) -> tuple[OutputFileState, ...]:
        outputs = project.spec.outputs
        declared = [
            *([outputs.dashboard] if outputs.dashboard else []),
            *outputs.images,
            *outputs.artifacts,
        ]
        states: list[OutputFileState] = []
        for relative in dict.fromkeys(declared):
            path = self._inside(project, relative)
            if path is not None and path.is_file():
                states.append(OutputFileState(relative, path.stat().st_size, _sha256(path)))
        return tuple(states)

    @staticmethod
    def _inside(project: Project, relative: str) -> Path | None:
        root = project.root.resolve()
        candidate = (root / relative).resolve()
        return candidate if candidate.is_relative_to(root) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class VenvInterpreterResolver:
    """Implements :class:`~quant_workbench.domain.ports.InterpreterResolver` per project venv."""

    def find(self, project: Project) -> Path | None:
        python = project.venv_python
        return python if python.is_file() else None

    def resolve(self, project: Project) -> Path:
        python = self.find(project)
        if python is None:
            raise EnvironmentSetupError(
                f"{project.title} has no virtual environment at {project.venv_dir}",
                hint=f"Create it with `qw env setup {project.slug}`.",
            )
        return python
