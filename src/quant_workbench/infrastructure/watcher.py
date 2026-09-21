"""File watching with ``watchdog`` (operating-system change notifications, no polling)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

#: Editors save in bursts (write a temporary file, rename it, touch the timestamp...). Waiting
#: for this much silence turns such a burst into a single batch, hence a single run.
DEFAULT_QUIET_SECONDS = 0.4
_JOIN_TIMEOUT_SECONDS = 5


class _Stream:
    """Implements :class:`~quant_workbench.domain.ports.ChangeStream` over an asyncio queue."""

    def __init__(self, queue: asyncio.Queue[Path], quiet_seconds: float) -> None:
        self._queue = queue
        self._quiet = quiet_seconds

    async def next_batch(self) -> frozenset[Path]:
        batch = {await self._queue.get()}
        while True:
            try:
                batch.add(await asyncio.wait_for(self._queue.get(), self._quiet))
            except TimeoutError:
                return frozenset(batch)


class WatchdogChangeSource:
    """Implements :class:`~quant_workbench.domain.ports.ChangeSource`."""

    def __init__(self, quiet_seconds: float = DEFAULT_QUIET_SECONDS) -> None:
        self._quiet = quiet_seconds

    @asynccontextmanager
    async def watching(
        self, directories: Sequence[Path], accept: Callable[[Path], bool]
    ) -> AsyncIterator[_Stream]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Path] = asyncio.Queue()

        class Handler(FileSystemEventHandler):
            # watchdog calls this from its own thread, so the queue is only touched through
            # ``call_soon_threadsafe``: the event loop is the sole owner of the queue.
            def on_any_event(self, event: FileSystemEvent) -> None:
                if event.is_directory:
                    return
                # A rename reports the new name in ``dest_path``: that is the file that now exists.
                for raw in (event.src_path, event.dest_path):
                    if raw:
                        path = Path(os.fsdecode(raw))
                        if accept(path):
                            loop.call_soon_threadsafe(queue.put_nowait, path)

        observer = Observer()
        for directory in directories:
            observer.schedule(Handler(), str(directory), recursive=True)
        observer.start()
        try:
            yield _Stream(queue, self._quiet)
        finally:
            observer.stop()
            # ``join`` blocks, so it must not run on the event loop's thread.
            await asyncio.to_thread(observer.join, _JOIN_TIMEOUT_SECONDS)
