"""Watch mode logic with a scripted change source (no file system, no processes)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from quant_workbench.application.watch import WatchedRun, WatchService
from quant_workbench.domain.ids import Slug
from quant_workbench.domain.project import ConfigTarget, ManifestSource, Project, ProjectSpec


@pytest.fixture
def project(tmp_path: Path) -> Project:
    root = tmp_path / "Quant - María" / "proyecto"
    (root / "src").mkdir(parents=True)
    (root / "config").mkdir()
    spec = ProjectSpec(
        slug=Slug("alpha"),
        title="Alpha",
        folder="proyecto",
        category="Test",
        order=10,
        config_targets=(ConfigTarget("config/settings.py"),),
    )
    return Project(spec, root, ManifestSource.IN_REPO)


class ScriptedStream:
    def __init__(self, batches: list[frozenset[Path]], log: list[str]) -> None:
        self._batches = batches
        self._log = log

    async def next_batch(self) -> frozenset[Path]:
        self._log.append("wait")
        return self._batches.pop(0)


class ScriptedSource:
    def __init__(self, batches: list[frozenset[Path]]) -> None:
        self.batches = batches
        self.log: list[str] = []
        self.directories: Sequence[Path] = ()

    @asynccontextmanager
    async def watching(
        self, directories: Sequence[Path], accept: Any
    ) -> AsyncIterator[ScriptedStream]:
        self.directories = directories
        self.accept = accept
        self.log.append("start watching")
        try:
            yield ScriptedStream(self.batches, self.log)
        finally:
            self.log.append("stopped watching")


class FakeRuns:
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.count = 0

    async def run(self, project: Project, options: object = None) -> str:
        self.count += 1
        self._log.append(f"run {self.count}")
        return f"run-{self.count}"


def test_only_python_code_outside_generated_folders_is_relevant(project: Project) -> None:
    root = project.root
    relevant = WatchService.is_relevant

    assert relevant(project, root / "src" / "main.py")
    assert relevant(project, root / "config" / "settings.py")
    assert not relevant(project, root / "README.md")
    assert not relevant(project, root / "src" / "notes.txt")
    assert not relevant(project, root / "outputs" / "report.py")
    assert not relevant(project, root / "src" / "__pycache__" / "main.py")
    assert not relevant(project, root / "venv" / "Lib" / "site.py")
    assert not relevant(project, root.parent / "otro" / "src" / "main.py")  # not this project


def test_only_folders_that_exist_are_watched(project: Project, tmp_path: Path) -> None:
    (project.root / "config").rmdir()

    assert WatchService.watched_directories(project) == (project.root / "src",)


async def test_the_first_run_happens_after_watching_started_and_each_batch_causes_one_run(
    project: Project,
) -> None:
    source = ScriptedSource(
        [
            frozenset({project.root / "src" / "b.py", project.root / "src" / "a.py"}),
            frozenset({project.root / "config" / "settings.py"}),
        ]
    )
    service = WatchService(runs=FakeRuns(source.log), source=source)  # type: ignore[arg-type]
    seen: list[WatchedRun] = []

    done = await service.watch(project, max_runs=3, on_run=seen.append)

    assert done == 3
    assert [(w.number, w.run, w.changed) for w in seen] == [
        (1, "run-1", ()),
        (2, "run-2", ("src/a.py", "src/b.py")),  # sorted, relative, forward slashes
        (3, "run-3", ("config/settings.py",)),
    ]
    # watching is on before the first run (an edit during it is kept) and off at the end
    assert source.log[:2] == ["start watching", "run 1"]
    assert source.log[-1] == "stopped watching"


async def test_without_the_initial_run_it_waits_for_the_first_save(project: Project) -> None:
    source = ScriptedSource([frozenset({project.root / "src" / "main.py"})])
    service = WatchService(runs=FakeRuns(source.log), source=source)  # type: ignore[arg-type]

    done = await service.watch(project, initial=False, max_runs=1)

    assert done == 1
    assert source.log == ["start watching", "wait", "run 1", "stopped watching"]


async def test_it_asks_the_source_to_ignore_what_is_not_relevant(project: Project) -> None:
    source = ScriptedSource([])
    service = WatchService(runs=FakeRuns(source.log), source=source)  # type: ignore[arg-type]

    await service.watch(project, max_runs=1)

    assert source.directories == (project.root / "src", project.root / "config")
    assert source.accept(project.root / "src" / "main.py") is True
    assert source.accept(project.root / "outputs" / "dashboard.html") is False
