"""Watch mode: run a project again every time its code or configuration is saved."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from quant_workbench.application.runs import RunOptions, RunService
from quant_workbench.domain.ports import ChangeSource
from quant_workbench.domain.project import Project
from quant_workbench.domain.runs import Run

#: Folders a run writes to or fills with generated files. A change there is a *consequence* of
#: running, never a reason to run again (otherwise every run would trigger the next one).
IGNORED_FOLDERS = frozenset({"venv", ".venv", "outputs", "__pycache__", ".git", ".certs", "data"})
WATCHED_SUFFIXES = frozenset({".py"})


@dataclass(frozen=True, slots=True)
class WatchedRun:
    """One run started by watch mode, and what triggered it (empty for the first run)."""

    number: int
    run: Run
    changed: tuple[str, ...]


class WatchService:
    def __init__(self, *, runs: RunService, source: ChangeSource) -> None:
        self._runs = runs
        self._source = source

    @staticmethod
    def watched_directories(project: Project) -> tuple[Path, ...]:
        """``src/`` plus the folders of the declared config files, when they exist."""
        candidates = [project.root / "src"]
        candidates += [(project.root / t.file).parent for t in project.spec.config_targets]
        unique = dict.fromkeys(c for c in candidates if c.is_dir())
        return tuple(unique)

    @staticmethod
    def is_relevant(project: Project, path: Path) -> bool:
        """Whether saving ``path`` should re-run the project: Python code, not generated files."""
        try:
            relative = path.resolve().relative_to(project.root.resolve())
        except ValueError:
            return False
        if IGNORED_FOLDERS.intersection(relative.parts[:-1]):
            return False
        return path.suffix in WATCHED_SUFFIXES

    async def watch(
        self,
        project: Project,
        *,
        options: RunOptions | None = None,
        initial: bool = True,
        max_runs: int | None = None,
        on_run: Callable[[WatchedRun], None] = lambda _: None,
    ) -> int:
        """Run ``project`` (now, if ``initial``) and again after every relevant save.

        Runs never overlap: edits made while a run is in progress are kept and cause exactly
        one more run afterwards. Stops after ``max_runs`` runs (tests) or when the caller's
        task is cancelled (Ctrl+C in the CLI); returns how many runs were made.
        """
        directories = self.watched_directories(project)
        done = 0
        # Watching starts *before* the first run, so an edit made during it is not lost.
        async with self._source.watching(
            directories, lambda p: self.is_relevant(project, p)
        ) as stream:
            if initial:
                done += 1
                on_run(WatchedRun(done, await self._runs.run(project, options), ()))
            while max_runs is None or done < max_runs:
                batch = await stream.next_batch()
                done += 1
                changed = tuple(sorted(self._relative(project, p) for p in batch))
                on_run(WatchedRun(done, await self._runs.run(project, options), changed))
        return done

    @staticmethod
    def _relative(project: Project, path: Path) -> str:
        return path.resolve().relative_to(project.root.resolve()).as_posix()
