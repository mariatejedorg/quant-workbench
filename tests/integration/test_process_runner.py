"""The real subprocess runner: streaming, encoding, limits, and killing whole process trees."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import psutil
import pytest

from quant_workbench.domain.process import ProcessOutcome, ProcessSpec, ResourceSample
from quant_workbench.domain.runs import LogStream
from quant_workbench.infrastructure.process import (
    AsyncSubprocessRunner,
    build_environment,
    kill_process_tree,
)
from tests.conftest import FakeClock

pytestmark = pytest.mark.integration

PY = sys.executable

#: Starts a long-lived grandchild, announces its pid, then idles: the classic orphan trap.
SPAWN_CHILD = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
print(child.pid, flush=True)
time.sleep(120)
"""


class Collector:
    def __init__(self) -> None:
        self.lines: list[tuple[LogStream, str]] = []
        self.samples: list[ResourceSample] = []

    def on_line(self, stream: LogStream, text: str) -> None:
        self.lines.append((stream, text))

    def on_sample(self, sample: ResourceSample) -> None:
        self.samples.append(sample)

    def texts(self, stream: LogStream) -> list[str]:
        return [t for s, t in self.lines if s is stream]


def spec(code: str, cwd: Path, **env: str) -> ProcessSpec:
    return ProcessSpec((PY, "-c", code), cwd, env)


def runner() -> AsyncSubprocessRunner:
    return AsyncSubprocessRunner(FakeClock(), sample_interval=0.1)


async def wait_until_dead(pids: list[int], seconds: float = 5.0) -> list[int]:
    """Poll until every pid is gone; return the ones still alive after ``seconds``."""
    deadline = time.monotonic() + seconds
    alive = list(pids)
    while alive and time.monotonic() < deadline:
        alive = [
            p for p in alive if psutil.pid_exists(p) and psutil.Process(p).status() != "zombie"
        ]
        if alive:
            await asyncio.sleep(0.1)
    return alive


# ------------------------------------------------------------------ basic runs
async def test_streams_are_delivered_line_by_line_with_their_origin(accented_root: Path) -> None:
    collector = Collector()
    code = "import sys; print('out1'); print('err1', file=sys.stderr); print('out2'); sys.exit(3)"

    outcome = await runner().run(spec(code, accented_root), on_line=collector.on_line)

    assert outcome.exit_code == 3
    assert not outcome.timed_out
    assert collector.texts(LogStream.STDOUT) == ["out1", "out2"]
    assert collector.texts(LogStream.STDERR) == ["err1"]


async def test_accented_output_survives_the_windows_code_page(accented_root: Path) -> None:
    collector = Collector()

    await runner().run(
        spec("print('María — volatilidad → ñandú')", accented_root), on_line=collector.on_line
    )

    assert collector.texts(LogStream.STDOUT) == ["María — volatilidad → ñandú"]


async def test_the_working_directory_may_contain_spaces_and_accents(accented_root: Path) -> None:
    collector = Collector()

    await runner().run(
        spec("import os; print(os.getcwd())", accented_root), on_line=collector.on_line
    )

    assert collector.texts(LogStream.STDOUT) == [str(accented_root)]
    assert "María" in collector.texts(LogStream.STDOUT)[0]


async def test_very_long_lines_are_not_truncated_or_lost(accented_root: Path) -> None:
    collector = Collector()
    code = "print('x' * 300_000); print('after')"

    await runner().run(spec(code, accented_root), on_line=collector.on_line)

    lines = collector.texts(LogStream.STDOUT)
    assert len(lines[0]) == 300_000
    assert lines[1] == "after"


async def test_progress_bars_collapse_to_their_final_state(accented_root: Path) -> None:
    collector = Collector()
    code = r"import sys; sys.stdout.write('10%\r50%\r100%\n')"

    await runner().run(spec(code, accented_root), on_line=collector.on_line)

    assert collector.texts(LogStream.STDOUT) == ["100%"]


async def test_a_missing_executable_raises_a_clear_os_error(accented_root: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await runner().run(
            ProcessSpec((str(accented_root / "no-such-python.exe"),), accented_root),
            on_line=Collector().on_line,
        )


# ------------------------------------------------------------------ environment
def test_the_workbenchs_own_venv_never_leaks_into_child_processes(tmp_path: Path) -> None:
    venv = tmp_path / "workbench-venv"
    scripts = venv / ("Scripts" if os.name == "nt" else "bin")
    other = tmp_path / "elsewhere"
    base = {
        "VIRTUAL_ENV": str(venv),
        "PYTHONPATH": "/leak",
        "PYTHONHOME": "/leak",
        "PATH": os.pathsep.join([str(scripts), str(other), str(venv / "sub")]),
        "KEEP": "me",
    }

    env = build_environment({"TICKER": "MSFT"}, base)

    assert "VIRTUAL_ENV" not in env
    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env
    assert env["PATH"] == str(other)  # only the entries outside the workbench venv survive
    assert env["KEEP"] == "me"
    assert env["TICKER"] == "MSFT"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert "PYTHONUTF8" not in env  # ADR 0004: forcing UTF-8 mode breaks libcurl paths on Windows


async def test_extra_environment_reaches_the_process(accented_root: Path) -> None:
    collector = Collector()
    code = "import os; print(os.environ['TICKER'], os.environ.get('VIRTUAL_ENV'))"

    await runner().run(spec(code, accented_root, TICKER="MSFT"), on_line=collector.on_line)

    assert collector.texts(LogStream.STDOUT) == ["MSFT None"]


async def test_children_do_not_run_in_utf8_mode_regression_for_accented_paths(
    accented_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: PYTHONUTF8=1 made libcurl unable to open a CA bundle under 'Quant - María'.

    The symptom was every yfinance request failing with 'Cookie/crumb fetch failed
    (SSLError)' inside the workbench while the same project worked from a terminal.
    """
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    collector = Collector()

    await runner().run(
        spec("import sys; print(sys.flags.utf8_mode)", accented_root), on_line=collector.on_line
    )

    assert collector.texts(LogStream.STDOUT) == ["0"]


# -------------------------------------------------------------- resource usage
async def test_resource_usage_is_sampled_while_the_process_runs(accented_root: Path) -> None:
    collector = Collector()
    code = "import time; blob = bytearray(60_000_000); time.sleep(1.5)"

    outcome = await runner().run(
        spec(code, accented_root), on_line=collector.on_line, on_sample=collector.on_sample
    )

    assert outcome.exit_code == 0
    assert len(collector.samples) >= 3
    assert outcome.peak_rss_bytes >= 50_000_000
    assert outcome.avg_cpu_percent >= 0.0


# ---------------------------------------------------------- timeouts and cancel
async def test_a_timeout_kills_the_process_and_its_children(accented_root: Path) -> None:
    collector = Collector()

    started = time.monotonic()
    outcome = await runner().run(
        spec(SPAWN_CHILD, accented_root), on_line=collector.on_line, timeout_seconds=1.5
    )
    elapsed = time.monotonic() - started

    assert outcome.timed_out
    assert outcome.exit_code is None
    assert elapsed < 10
    (child_pid,) = (int(t) for t in collector.texts(LogStream.STDOUT))
    assert await wait_until_dead([child_pid]) == [], "the grandchild process was orphaned"


async def test_cancelling_the_task_kills_the_whole_process_tree(accented_root: Path) -> None:
    collector = Collector()
    task = asyncio.create_task(
        runner().run(spec(SPAWN_CHILD, accented_root), on_line=collector.on_line)
    )

    for _ in range(100):  # wait until the child announced its pid
        if collector.texts(LogStream.STDOUT):
            break
        await asyncio.sleep(0.05)
    (child_pid,) = (int(t) for t in collector.texts(LogStream.STDOUT))
    assert psutil.pid_exists(child_pid)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await wait_until_dead([child_pid]) == [], "cancelling left an orphaned process behind"


def test_kill_process_tree_ignores_processes_that_are_already_gone() -> None:
    kill_process_tree(2**22 + 12345)  # no such pid: must not raise


async def test_the_runner_is_reusable_after_a_cancellation(accented_root: Path) -> None:
    r = runner()
    task = asyncio.create_task(
        r.run(spec("import time; time.sleep(60)", accented_root), on_line=Collector().on_line)
    )
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    outcome: ProcessOutcome = await r.run(
        spec("print('still works')", accented_root), on_line=Collector().on_line
    )

    assert outcome.exit_code == 0
