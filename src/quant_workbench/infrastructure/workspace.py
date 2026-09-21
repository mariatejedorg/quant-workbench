"""File-system adapter for the workspace: finds projects and reads what describes them."""

from __future__ import annotations

import ast
import logging
import re
from collections.abc import Iterator
from pathlib import Path

from quant_workbench.domain.errors import DiscoveryError, ManifestError
from quant_workbench.domain.ids import parse_slug
from quant_workbench.domain.project import OutputSpec, ProjectSpec
from quant_workbench.infrastructure.manifest import MANIFEST_FILENAME, read_manifest

_log = logging.getLogger(__name__)

#: Directories never treated as projects, and never scanned for source code.
_IGNORED_DIRS = frozenset(
    {
        "venv",
        ".venv",
        "env",
        "node_modules",
        "__pycache__",
        ".git",
        "build",
        "dist",
        ".idea",
        ".vscode",
    }
)
_UNTRACKED_HEADING = re.compile(r"^#+\s*(?P<title>.+?)\s*$")
_LEADING_NON_WORD = re.compile(r"^[^\w(]+", re.UNICODE)


class FileSystemWorkspace:
    """Implements :class:`~quant_workbench.domain.ports.WorkspaceGateway` on disk."""

    def __init__(self, registry_dir: Path | None = None) -> None:
        self._registry_dir = registry_dir

    # ------------------------------------------------------------- discovery
    def candidate_directories(self, workspace: Path) -> tuple[Path, ...]:
        if not workspace.is_dir():
            raise DiscoveryError(
                f"Workspace {workspace} is not a directory",
                hint="Pass --workspace, or set 'workspace_root' in settings.toml.",
            )
        return tuple(
            sorted(
                child
                for child in workspace.iterdir()
                if child.is_dir()
                and child.name not in _IGNORED_DIRS
                and not child.name.startswith(".")
            )
        )

    def looks_like_workspace(self, path: Path) -> bool:
        """A workspace is a folder holding at least two recognisable projects."""
        if not path.is_dir():
            return False
        try:
            found = sum(
                1 for directory in self.candidate_directories(path) if self._is_project(directory)
            )
        except OSError:
            return False
        return found >= 2  # noqa: PLR2004

    @staticmethod
    def _is_project(directory: Path) -> bool:
        return (directory / MANIFEST_FILENAME).is_file() or (
            directory / "src" / "main.py"
        ).is_file()

    # -------------------------------------------------------------- manifests
    def read_manifest(self, directory: Path) -> ProjectSpec | None:
        manifest = directory / MANIFEST_FILENAME
        if not manifest.is_file():
            return None
        return read_manifest(manifest, folder=directory.name)

    def read_registry(self) -> tuple[ProjectSpec, ...]:
        if self._registry_dir is None or not self._registry_dir.is_dir():
            return ()
        specs: list[ProjectSpec] = []
        for path in sorted(self._registry_dir.glob("*.toml")):
            spec = read_manifest(path)
            specs.append(spec)
        return tuple(specs)

    def infer_spec(self, directory: Path) -> ProjectSpec | None:
        """Guess a manifest from the folder layout (convention over configuration).

        A directory qualifies when it has ``src/main.py`` plus either a
        ``requirements.txt`` or an ``outputs/`` folder, which is the shape every project
        in the portfolio follows.
        """
        if not (directory / "src" / "main.py").is_file():
            return None
        if not ((directory / "requirements.txt").is_file() or (directory / "outputs").is_dir()):
            return None
        slug_text = re.sub(r"[^a-z0-9]+", "-", directory.name.lower()).strip("-")
        try:
            slug = parse_slug(slug_text)
        except Exception as exc:
            raise ManifestError(
                f"Cannot derive a project slug from folder {directory.name!r}",
                hint=f"Add a {MANIFEST_FILENAME} to that folder.",
            ) from exc
        has_dashboard = (directory / "outputs" / "dashboard.html").is_file()
        return ProjectSpec(
            slug=slug,
            title=self._title_from_readme(directory) or directory.name,
            folder=directory.name,
            category="Uncategorised",
            order=1000,
            outputs=OutputSpec(dashboard="outputs/dashboard.html" if has_dashboard else None),
        )

    @staticmethod
    def _title_from_readme(directory: Path) -> str | None:
        readme = directory / "README.md"
        if not readme.is_file():
            return None
        try:
            with readme.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    match = _UNTRACKED_HEADING.match(line)
                    if match:
                        return _LEADING_NON_WORD.sub("", match.group("title")).strip() or None
        except OSError:
            return None
        return None

    # ------------------------------------------------- static dependency scan
    def sibling_references(self, directory: Path, known_folders: frozenset[str]) -> frozenset[str]:
        """Names of sibling projects whose folder appears as a string literal in the code.

        Projects in this portfolio load each other's *source* by path, e.g.
        ``Path(__file__).parents[2] / "proyecto-6-opciones-volatilidad-implicita"``. An
        exact string-literal match against the known folder names finds those references
        without false positives from prose (docstrings and comments merely *mentioning* a
        folder are longer strings, so they never match exactly).
        """
        wanted = known_folders - {directory.name}
        found: set[str] = set()
        for path in self._python_files(directory):
            tree = self._parse(path)
            if tree is None:
                continue
            found.update(
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in wanted
            )
        return frozenset(found)

    def _python_files(self, directory: Path) -> Iterator[Path]:
        stack: list[Path] = [directory]
        while stack:
            current = stack.pop()
            try:
                entries = sorted(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir():
                    if entry.name not in _IGNORED_DIRS and not entry.name.startswith("."):
                        stack.append(entry)
                elif entry.suffix == ".py":
                    yield entry

    @staticmethod
    def _parse(path: Path) -> ast.Module | None:
        try:
            return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except (SyntaxError, ValueError, OSError) as exc:
            _log.debug("Skipping %s during dependency scan: %s", path, exc)
            return None
