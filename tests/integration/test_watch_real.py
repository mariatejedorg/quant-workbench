"""Watch mode against the real file system (operating-system events) and real processes."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from quant_workbench.application.settings import Settings
from quant_workbench.application.watch import WatchedRun, WatchService
from quant_workbench.bootstrap import Container, build_container
from quant_workbench.domain.paths import AppPaths
from quant_workbench.infrastructure.paths import ensure_app_dirs
from quant_workbench.infrastructure.watcher import WatchdogChangeSource

pytestmark = pytest.mark.integration

#: Generous: an OS notification normally arrives in milliseconds, but CI machines stall.
PATIENCE_SECONDS = 20
QUIET = 0.2


def only_python(path: Path) -> bool:
    return path.suffix == ".py"


async def test_a_save_is_reported_as_a_batch(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "análisis.py"

    async with WatchdogChangeSource(QUIET).watching([tmp_path / "src"], only_python) as stream:
        target.write_text("x = 1\n", encoding="utf-8")
        batch = await asyncio.wait_for(stream.next_batch(), PATIENCE_SECONDS)

    assert batch == {target}


async def test_a_burst_of_writes_is_one_batch(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    files = [tmp_path / "src" / f"m{i}.py" for i in range(4)]

    async with WatchdogChangeSource(QUIET).watching([tmp_path / "src"], only_python) as stream:
        for path in files:
            path.write_text("x = 1\n", encoding="utf-8")
        first = await asyncio.wait_for(stream.next_batch(), PATIENCE_SECONDS)
        second = asyncio.create_task(stream.next_batch())
        await asyncio.sleep(QUIET * 4)
        assert not second.done()  # nothing else happened, so nothing more is reported
        second.cancel()

    assert first == set(files)


async def test_a_rename_reports_the_new_name(tmp_path: Path) -> None:
    """Editors save by writing a temporary file and renaming it over the real one."""
    (tmp_path / "src").mkdir()
    real = tmp_path / "src" / "settings.py"
    real.write_text("x = 1\n", encoding="utf-8")

    async with WatchdogChangeSource(QUIET).watching([tmp_path / "src"], only_python) as stream:
        temporary = tmp_path / "src" / ".settings.py.tmp"
        temporary.write_text("x = 2\n", encoding="utf-8")
        temporary.replace(real)
        batch = await asyncio.wait_for(stream.next_batch(), PATIENCE_SECONDS)

    assert real in batch  # the temporary file is filtered out by suffix, the real one is kept


async def test_rejected_paths_are_never_reported(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()

    async with WatchdogChangeSource(QUIET).watching([tmp_path / "src"], only_python) as stream:
        (tmp_path / "src" / "notes.txt").write_text("ignored\n", encoding="utf-8")
        (tmp_path / "src" / "code.py").write_text("x = 1\n", encoding="utf-8")
        batch = await asyncio.wait_for(stream.next_batch(), PATIENCE_SECONDS)

    assert batch == {tmp_path / "src" / "code.py"}


async def test_merely_reading_a_file_is_not_a_change(tmp_path: Path) -> None:
    """A project's own process importing its source must never look like an edit.

    On Linux, watchdog's inotify backend also reports a file being opened and closed for a
    read-only open. On macOS, FSEvents goes further: a metadata-only touch (which a read can
    cause) is not even distinguishable from a real write at the event-type level, both surfacing
    as "modified" — so the read is filtered out by comparing modification times, not event
    types, there. Windows' backend has no notion of either.

    FSEvents' own batching latency is also coarser than this module's default debounce, so the
    real edit's event can land in a *second* batch rather than the same one as the (correctly
    dropped) read; every batch received within the patience window is checked, not just the
    first, to make this assertion regardless of exactly how the events are split up.
    """
    (tmp_path / "src").mkdir()
    read_only = tmp_path / "src" / "read_only.py"
    read_only.write_text("x = 1\n", encoding="utf-8")

    async with WatchdogChangeSource(QUIET).watching([tmp_path / "src"], only_python) as stream:
        read_only.read_text(encoding="utf-8")  # a plain read: must never surface as a change
        real_edit = tmp_path / "src" / "edited.py"
        real_edit.write_text("y = 2\n", encoding="utf-8")

        seen: set[Path] = set()
        deadline = asyncio.get_running_loop().time() + PATIENCE_SECONDS
        while real_edit not in seen:
            remaining = deadline - asyncio.get_running_loop().time()
            assert remaining > 0, f"never saw {real_edit} — batches so far: {seen}"
            seen |= await asyncio.wait_for(stream.next_batch(), remaining)

    assert seen == {real_edit}


@pytest.fixture
def container(tmp_path: Path) -> Iterator[Container]:
    paths = AppPaths.under(tmp_path / "app-home")
    ensure_app_dirs(paths)
    built = build_container(paths=paths, settings=Settings(), database=None)
    yield built
    built.close()


@pytest.mark.slow
async def test_saving_the_config_of_a_generated_project_runs_it_again(
    accented_root: Path, container: Container
) -> None:
    catalog = container.catalog.load(accented_root)
    container.scaffold.create(catalog, "pairs-trading")
    root = accented_root / "proyecto-1-pairs-trading"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(root / "venv")],
        check=True,
        capture_output=True,
    )
    project = container.catalog.load(accented_root).get("pairs-trading")
    config = root / "config" / "pairs_trading.py"
    runs: list[WatchedRun] = []
    first_done = asyncio.Event()

    def on_run(item: WatchedRun) -> None:
        runs.append(item)
        first_done.set()

    service = WatchService(runs=container.runs, source=WatchdogChangeSource(QUIET))
    task = asyncio.create_task(service.watch(project, max_runs=2, on_run=on_run))
    await asyncio.wait_for(first_done.wait(), PATIENCE_SECONDS * 2)
    text = config.read_text(encoding="utf-8")
    config.write_text(text.replace("N_PATHS = 500", "N_PATHS = 50"), encoding="utf-8")
    done = await asyncio.wait_for(task, PATIENCE_SECONDS * 2)

    assert done == 2
    first, second = runs
    assert first.changed == ()
    # the run itself wrote outputs/ and __pycache__/: neither may count as a change
    assert second.changed == ("config/pairs_trading.py",)
    assert first.run.status.value == second.run.status.value == "succeeded"
    assert first.run.metrics != second.run.metrics  # fewer paths, different estimate
