"""Read-only access to files inside a project, and the venv interpreter lookup."""

from __future__ import annotations

import hashlib
from pathlib import Path

from quant_workbench.domain.errors import EnvironmentSetupError
from quant_workbench.domain.project import Project
from quant_workbench.domain.runs import OutputFileState

_CHUNK = 1024 * 1024


class FileSystemProjectFiles:
    """Implements :class:`~quant_workbench.domain.ports.ProjectFiles`."""

    def read_text(self, project: Project, relative: str) -> str | None:
        """The file's text, or ``None`` if it does not exist or would escape the project.

        The escape check is defence in depth: manifest paths are validated to be relative
        without ``..``, but a symlink could still point outside the project folder.
        """
        path = self._inside(project, relative)
        if path is None or not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")

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
