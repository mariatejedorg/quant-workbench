"""Running operating-system processes with asyncio."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import psutil

from quant_workbench.domain.ports import Clock
from quant_workbench.domain.process import ProcessOutcome, ProcessSpec, ResourceSample
from quant_workbench.domain.runs import LogStream

_log = logging.getLogger(__name__)

#: Longest single output line accepted. Larger than any realistic console line, and large
#: enough that the stream reader's default 64 KiB limit never truncates data.
_STREAM_LIMIT = 16 * 1024 * 1024
_KILL_GRACE_SECONDS = 3.0
_DRAIN_SECONDS = 2.0

# ``subprocess.CREATE_NEW_PROCESS_GROUP``/``CREATE_NO_WINDOW`` exist only in typeshed's Windows
# stub, so mypy reports them as undefined when it checks this file for any other platform (as CI
# does for Linux). Looked up with getattr, so the module type-checks everywhere; the value is
# only ever used inside the ``os.name == "nt"`` branch below.
_CREATE_NEW_PROCESS_GROUP: int = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Variables that would leak the *workbench's* Python environment into a project's process.
_LEAKY_VARIABLES = frozenset(
    {
        "VIRTUAL_ENV",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "__PYVENV_LAUNCHER__",
        "PYTHONEXECUTABLE",
    }
)
# PYTHONIOENCODING makes stdout/stderr UTF-8, so accented text survives Windows pipes.
#
# PYTHONUTF8 is deliberately NOT forced, although it looks like the obvious switch. In
# UTF-8 mode Python hands file paths to C libraries encoded as UTF-8, but libcurl on
# Windows reads them in the ANSI code page. In a workspace called "Quant - María" the path
# of the project's CA bundle then no longer resolves, and every TLS request made through
# curl_cffi (yfinance) fails with "Cookie/crumb fetch failed (SSLError)". See ADR 0004.
#
# PYTHONDONTWRITEBYTECODE keeps a run from creating a fresh `__pycache__/` inside the
# project's own `src/`. Besides the clutter, creating that directory *for the first time*
# is itself a filesystem change under a folder `qw watch` recursively watches; on Linux this
# was observed to make the watcher (mis)report every sibling source file as changed on the
# very next batch, not just the one actually edited.
_FORCED_VARIABLES = {
    "PYTHONUNBUFFERED": "1",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONDONTWRITEBYTECODE": "1",
}


def build_environment(
    extra: Mapping[str, str], base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The environment a child process should see.

    Starts from the current environment but removes everything that points at the
    workbench's own virtual environment (a project must run purely in *its* environment),
    and makes the standard streams UTF-8 so accented text survives Windows pipes.
    """
    source = dict(os.environ if base is None else base)
    venv = source.get("VIRTUAL_ENV")
    env = {k: v for k, v in source.items() if k.upper() not in _LEAKY_VARIABLES}
    if venv:
        keep = [
            entry
            for entry in env.get("PATH", env.get("Path", "")).split(os.pathsep)
            if entry and not _is_inside(entry, venv)
        ]
        for key in [k for k in env if k.upper() == "PATH"]:
            env[key] = os.pathsep.join(keep)
    env.update(_FORCED_VARIABLES)
    env.update(extra)
    return env


def _is_inside(entry: str, directory: str) -> bool:
    return Path(entry).resolve().is_relative_to(Path(directory).resolve())


def kill_process_tree(pid: int, grace_seconds: float = _KILL_GRACE_SECONDS) -> None:
    """Terminate ``pid`` and every descendant; escalate to a hard kill after a grace period.

    Blocking (it waits for the processes to exit), so callers run it in a thread.
    """
    try:
        parent = psutil.Process(pid)
        processes = [*parent.children(recursive=True), parent]
    except psutil.NoSuchProcess:
        return
    for process in processes:
        with contextlib.suppress(psutil.NoSuchProcess):
            process.terminate()
    _, alive = psutil.wait_procs(processes, timeout=grace_seconds)
    for process in alive:
        with contextlib.suppress(psutil.NoSuchProcess):
            process.kill()
    psutil.wait_procs(alive, timeout=grace_seconds)


@dataclass(slots=True)
class _Stats:
    peak_rss: int = 0
    cpu_total: float = 0.0
    samples: int = 0

    def add(self, sample: ResourceSample) -> None:
        self.peak_rss = max(self.peak_rss, sample.rss_bytes)
        self.cpu_total += sample.cpu_percent
        self.samples += 1

    @property
    def avg_cpu(self) -> float:
        return self.cpu_total / self.samples if self.samples else 0.0


@dataclass(slots=True)
class AsyncSubprocessRunner:
    """Implements :class:`~quant_workbench.domain.ports.ProcessRunner`."""

    clock: Clock
    sample_interval: float = 0.5

    async def run(
        self,
        spec: ProcessSpec,
        *,
        on_line: Callable[[LogStream, str], None],
        on_sample: Callable[[ResourceSample], None] | None = None,
        timeout_seconds: float | None = None,
    ) -> ProcessOutcome:
        creation: dict[str, object] = (
            {"creationflags": _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        process = await asyncio.create_subprocess_exec(
            *spec.args,
            cwd=spec.cwd,
            env=build_environment(spec.env),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT,
            **creation,  # type: ignore[arg-type]
        )
        if (
            process.stdout is None or process.stderr is None
        ):  # pragma: no cover - PIPE was requested
            raise RuntimeError("subprocess pipes were not created")

        stats = _Stats()
        readers = [
            asyncio.create_task(self._pump(process.stdout, LogStream.STDOUT, on_line)),
            asyncio.create_task(self._pump(process.stderr, LogStream.STDERR, on_line)),
        ]
        sampler = asyncio.create_task(self._sample(process.pid, stats, on_sample))
        timed_out = False
        try:
            try:
                async with asyncio.timeout(timeout_seconds):
                    await process.wait()
                    await asyncio.gather(*readers)
            except TimeoutError:
                timed_out = True
                await self._terminate(process)
            except asyncio.CancelledError:
                await self._terminate(process)
                raise
        finally:
            sampler.cancel()
            await self._finish_readers(readers)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sampler
            # asyncio only closes a subprocess transport when every pipe has seen EOF; after a
            # kill or a timeout that may never happen, and the transport would then be
            # finalised against a closed event loop ("unclosed transport" warnings).
            transport = getattr(process, "_transport", None)
            if transport is not None:
                transport.close()

        return ProcessOutcome(
            exit_code=None if timed_out else process.returncode,
            timed_out=timed_out,
            peak_rss_bytes=stats.peak_rss,
            avg_cpu_percent=stats.avg_cpu,
        )

    # ---------------------------------------------------------------- helpers
    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        """Kill the whole tree without blocking the event loop, then reap the process."""
        await asyncio.to_thread(kill_process_tree, process.pid)
        with contextlib.suppress(ProcessLookupError):
            await asyncio.wait_for(process.wait(), timeout=_KILL_GRACE_SECONDS)

    @staticmethod
    async def _finish_readers(readers: list[asyncio.Task[None]]) -> None:
        """Let the readers drain what is left in the pipes, but never wait for long."""
        _, pending = await asyncio.wait(readers, timeout=_DRAIN_SECONDS)
        for task in pending:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)

    @staticmethod
    async def _pump(
        stream: asyncio.StreamReader,
        kind: LogStream,
        on_line: Callable[[LogStream, str], None],
    ) -> None:
        while True:
            try:
                raw = await stream.readline()
            except ValueError:
                on_line(
                    LogStream.SYSTEM,
                    f"[{kind.value}: a line exceeded the size limit and was dropped]",
                )
                continue
            if not raw:
                return
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            # Progress bars redraw one line with carriage returns; only the last state matters.
            text = text.rsplit("\r", 1)[-1]
            on_line(kind, text)

    async def _sample(
        self, pid: int, stats: _Stats, on_sample: Callable[[ResourceSample], None] | None
    ) -> None:
        try:
            root = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return
        known: dict[int, psutil.Process] = {}
        while True:
            sample = await asyncio.to_thread(self._measure, root, known)
            if sample is not None:
                stats.add(sample)
                if on_sample is not None:
                    on_sample(sample)
            await asyncio.sleep(self.sample_interval)

    def _measure(
        self, root: psutil.Process, known: dict[int, psutil.Process]
    ) -> ResourceSample | None:
        try:
            tree = [root, *root.children(recursive=True)]
        except psutil.NoSuchProcess:
            return None
        rss = 0
        cpu = 0.0
        for candidate in tree:
            # Reuse the Process object: psutil computes cpu_percent between successive calls.
            process = known.setdefault(candidate.pid, candidate)
            try:
                rss += process.memory_info().rss
                cpu += process.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return ResourceSample(at=self.clock.now(), cpu_percent=cpu, rss_bytes=rss)
