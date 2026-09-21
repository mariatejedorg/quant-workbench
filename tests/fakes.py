"""Test doubles for the engine's ports (structural typing: no inheritance needed)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from quant_workbench.domain.errors import EnvironmentSetupError
from quant_workbench.domain.git import GitState
from quant_workbench.domain.ids import RunId, Slug, parse_slug
from quant_workbench.domain.process import ProcessOutcome, ProcessSpec, ResourceSample
from quant_workbench.domain.project import (
    ConfigTarget,
    ManifestSource,
    MetricExtractorSpec,
    OutputSpec,
    Project,
    ProjectSpec,
)
from quant_workbench.domain.runs import LogLine, LogStream, OutputFileState, Run


def make_project(
    root: Path,
    slug: str,
    *,
    depends_on: Sequence[str] = (),
    extractors: Sequence[MetricExtractorSpec] = (),
    config_files: Sequence[str] = (),
    config_targets: Sequence[ConfigTarget] = (),
) -> Project:
    """A Project whose spec is built in memory (no files needed unless a test wants them)."""
    spec = ProjectSpec(
        slug=parse_slug(slug),
        title=slug.replace("-", " ").title(),
        folder=slug,
        category="Test",
        order=1,
        depends_on=tuple(parse_slug(d) for d in depends_on),
        extractors=tuple(extractors),
        config_targets=(*(ConfigTarget(file=f) for f in config_files), *config_targets),
        outputs=OutputSpec(dashboard="outputs/dashboard.html"),
    )
    return Project(spec=spec, root=root / slug, source=ManifestSource.REGISTRY)


@dataclass
class Script:
    """What a fake process does when it is 'run'."""

    lines: list[tuple[LogStream, str]] = field(default_factory=list)
    exit_code: int = 0
    timed_out: bool = False
    #: Block until this event is set (lets a test hold a run open).
    hold: asyncio.Event | None = None
    delay: float = 0.0
    samples: list[ResourceSample] = field(default_factory=list)


class ScriptedRunner:
    """A ProcessRunner that replays scripts instead of starting processes."""

    def __init__(self, script_for: Callable[[ProcessSpec], Script] | Script | None = None) -> None:
        if script_for is None:
            script_for = Script()
        self._script_for = (
            (lambda _spec: script_for) if isinstance(script_for, Script) else script_for
        )
        self.calls: list[ProcessSpec] = []
        self.started: list[str] = []
        self.running = 0
        self.max_running = 0

    async def run(
        self,
        spec: ProcessSpec,
        *,
        on_line: Callable[[LogStream, str], None],
        on_sample: Callable[[ResourceSample], None] | None = None,
        timeout_seconds: float | None = None,
    ) -> ProcessOutcome:
        script = self._script_for(spec)
        self.calls.append(spec)
        self.started.append(spec.cwd.name)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            for stream, text in script.lines:
                on_line(stream, text)
            if on_sample is not None:
                for sample in script.samples:
                    on_sample(sample)
            if script.hold is not None:
                await script.hold.wait()
            if script.delay:
                await asyncio.sleep(script.delay)
        finally:
            self.running -= 1
        return ProcessOutcome(
            exit_code=None if script.timed_out else script.exit_code, timed_out=script.timed_out
        )


class MemoryRepository:
    """RunRepository kept in dictionaries."""

    def __init__(self) -> None:
        self.runs: dict[RunId, Run] = {}
        self.log_lines: dict[RunId, list[LogLine]] = {}
        self.saves: list[Run] = []

    def save(self, run: Run) -> None:
        self.runs[run.id] = run
        self.saves.append(run)

    def get(self, run_id: RunId) -> Run | None:
        return self.runs.get(run_id)

    def list_runs(self, project: Slug | None = None, *, limit: int = 50) -> tuple[Run, ...]:
        chosen = [r for r in self.runs.values() if project is None or r.project == project]
        return tuple(sorted(chosen, key=lambda r: r.id, reverse=True)[:limit])

    def latest(self, project: Slug) -> Run | None:
        runs = self.list_runs(project, limit=1)
        return runs[0] if runs else None

    def append_logs(self, run_id: RunId, lines: Sequence[LogLine]) -> None:
        self.log_lines.setdefault(run_id, []).extend(lines)

    def logs(
        self, run_id: RunId, *, offset: int = 0, limit: int | None = None
    ) -> tuple[LogLine, ...]:
        lines = self.log_lines.get(run_id, [])[offset:]
        return tuple(lines if limit is None else lines[:limit])

    def close(self) -> None:
        """Nothing to release: kept so the fake satisfies the RunRepository port."""


class MemoryFiles:
    """ProjectFiles backed by a dict of ``relative path -> text``."""

    def __init__(
        self, files: dict[str, str] | None = None, outputs: tuple[OutputFileState, ...] = ()
    ) -> None:
        self.files = files or {}
        self.outputs = outputs
        self.writes: list[tuple[str, str]] = []

    def read_text(self, project: Project, relative: str) -> str | None:
        return self.files.get(relative)

    def snapshot_outputs(self, project: Project) -> tuple[OutputFileState, ...]:
        return self.outputs

    def write_text(self, project: Project, relative: str, text: str) -> Path | None:
        self.writes.append((relative, text))
        existed = relative in self.files
        self.files[relative] = text
        return Path(f"backups/{relative}") if existed else None


class FixedInterpreter:
    """InterpreterResolver that always answers with one interpreter (or none)."""

    def __init__(self, python: Path | None) -> None:
        self._python = python

    def find(self, project: Project) -> Path | None:
        return self._python

    def resolve(self, project: Project) -> Path:
        if self._python is None:
            raise EnvironmentSetupError(
                f"{project.title} has no virtual environment",
                hint=f"Create it with `qw env setup {project.slug}`.",
            )
        return self._python


class MultiFiles:
    """ProjectFiles for several projects at once: ``{slug: {relative path: text or bytes}}``."""

    def __init__(self, files: dict[str, dict[str, str | bytes]] | None = None) -> None:
        self.files: dict[str, dict[str, str | bytes]] = files or {}
        self.mtimes: dict[tuple[str, str], datetime] = {}
        self.writes: list[tuple[str, str, str | bytes]] = []

    def _of(self, project: Project) -> dict[str, str | bytes]:
        return self.files.setdefault(project.slug, {})

    def read_text(self, project: Project, relative: str) -> str | None:
        value = self._of(project).get(relative)
        return value.decode("utf-8") if isinstance(value, bytes) else value

    def read_bytes(self, project: Project, relative: str) -> bytes | None:
        value = self._of(project).get(relative)
        return value.encode("utf-8") if isinstance(value, str) else value

    def write_text(self, project: Project, relative: str, text: str) -> Path | None:
        self.writes.append((project.slug, relative, text))
        self._of(project)[relative] = text
        return None

    def write_bytes(self, project: Project, relative: str, data: bytes) -> None:
        self.writes.append((project.slug, relative, data))
        self._of(project)[relative] = data

    def exists(self, project: Project, relative: str) -> bool:
        return relative in self._of(project)

    def size(self, project: Project, relative: str) -> int | None:
        data = self.read_bytes(project, relative)
        return None if data is None else len(data)

    def python_sources(self, project: Project) -> tuple[str, ...]:
        return tuple(sorted(name for name in self._of(project) if name.endswith(".py")))

    def modified_at(self, project: Project, relative: str) -> datetime | None:
        if relative not in self._of(project):
            return None
        return self.mtimes.get((project.slug, relative), datetime(2026, 1, 1, tzinfo=UTC))

    def snapshot_outputs(self, project: Project) -> tuple[OutputFileState, ...]:
        return ()


@dataclass
class FakeRepo:
    """What a fake git repository answers."""

    config: dict[str, str] = field(default_factory=dict)
    remote: str | None = None
    state: GitState = field(default_factory=lambda: GitState("main", ()))
    tracked: tuple[str, ...] = ()
    ignored: frozenset[str] = frozenset()


class FakeGit:
    """GitGateway over in-memory repositories keyed by root folder."""

    def __init__(self) -> None:
        self.repos: dict[Path, FakeRepo] = {}
        self.local_writes: list[tuple[Path, str, str]] = []

    def is_repository(self, root: Path) -> bool:
        return root in self.repos

    def state(self, root: Path) -> GitState:
        return self.repos[root].state

    def config_value(self, root: Path, key: str) -> str | None:
        return self.repos[root].config.get(key)

    def remote_url(self, root: Path, name: str = "origin") -> str | None:
        return self.repos[root].remote

    def tracked_files(self, root: Path) -> tuple[str, ...]:
        return self.repos[root].tracked

    def is_ignored(self, root: Path, relative: str) -> bool:
        return relative in self.repos[root].ignored

    def clone(self, url: str, destination: Path) -> None:
        raise NotImplementedError("the fake never clones")

    def set_local_config(self, root: Path, key: str, value: str) -> None:
        self.local_writes.append((root, key, value))
        self.repos[root].config[key] = value
