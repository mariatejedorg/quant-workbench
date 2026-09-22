"""File watching with ``watchdog`` (operating-system change notifications, no polling)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from watchdog.events import (
    EVENT_TYPE_CLOSED,
    EVENT_TYPE_CREATED,
    EVENT_TYPE_MODIFIED,
    EVENT_TYPE_MOVED,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

#: Editors save in bursts (write a temporary file, rename it, touch the timestamp...). Waiting
#: for this much silence turns such a burst into a single batch, hence a single run.
DEFAULT_QUIET_SECONDS = 0.4
_JOIN_TIMEOUT_SECONDS = 5

#: watchdog's Linux (inotify) backend also reports files being *opened* and closed after a
#: read-only open (``FileOpenedEvent``/``FileClosedNoWriteEvent``) — a project's own process
#: merely importing its source triggers these. Windows' backend has no such notion, so this
#: distinction is invisible there; only these four event types represent an actual write.
_RELEVANT_EVENT_TYPES = frozenset(
    {EVENT_TYPE_CREATED, EVENT_TYPE_MODIFIED, EVENT_TYPE_MOVED, EVENT_TYPE_CLOSED}
)


def _mtime(path: Path) -> int | None:
    """The file's modification time in nanoseconds, or ``None`` if it cannot be stat'd right now."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


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

        # macOS's FSEvents backend goes further than Linux: a metadata-only touch (which a read
        # can cause) is not distinguishable from a real write at the *event type* level, and it
        # can replay events for files that existed before watching even started (see below). So
        # a file's modification time is also compared against what it was when last seen; this
        # snapshot of what already exists is what makes that possible. Resolved consistently
        # with the event handler below: macOS routes /tmp and /var through a /private symlink,
        # and FSEvents reports the resolved form, so comparing an unresolved key against it
        # would silently never match.
        last_mtime: dict[Path, int] = {}
        for directory in directories:
            for candidate in directory.resolve().rglob("*"):
                if candidate.is_file() and accept(candidate):
                    mtime = _mtime(candidate)
                    if mtime is not None:
                        last_mtime[candidate] = mtime

        class Handler(FileSystemEventHandler):
            # watchdog calls this from its own thread, so the queue is only touched through
            # ``call_soon_threadsafe``: the event loop is the sole owner of the queue.
            def on_any_event(self, event: FileSystemEvent) -> None:
                if event.is_directory or event.event_type not in _RELEVANT_EVENT_TYPES:
                    return
                # A rename reports the new name in ``dest_path``: that is the file that now exists.
                for raw in (event.src_path, event.dest_path):
                    if raw:
                        path = Path(os.fsdecode(raw)).resolve()
                        if not accept(path):
                            continue
                        mtime = _mtime(path)
                        # FSEvents (macOS) can replay a "created" event for a file that existed
                        # before watching even started — its own documented history replay, up
                        # to ~30s back — so "created" needs the same modification-time check as
                        # "modified" (a genuinely new path has no prior entry, so it is never
                        # filtered by this; only a path already known unchanged is). "moved" is
                        # deliberately excluded: a rename can legitimately land with a
                        # modification time that coincides with what was last recorded for the
                        # destination path (observed as an intermittent failure on Windows,
                        # roughly 1 in 10 runs, once this check covered every event type).
                        if (
                            event.event_type != EVENT_TYPE_MOVED
                            and mtime is not None
                            and last_mtime.get(path) == mtime
                        ):
                            continue  # same modification time as last seen: not a real edit
                        if mtime is not None:
                            last_mtime[path] = mtime
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
