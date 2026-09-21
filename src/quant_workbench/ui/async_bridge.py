"""The only frontier between Qt and asyncio (see ADR 0002).

The engine is asyncio code with no knowledge of Qt. The desktop app runs its event loop in a
dedicated thread; this module submits coroutines to it and brings their results back to the
GUI thread, and forwards the engine's domain events to Qt in batches.

Two rules make the thread boundary boring:

* callbacks (``on_result`` / ``on_error``) **always run on the GUI thread**, delivered by a
  queued Qt signal, so widget code never has to think about threads;
* the engine's events are collected in a thread-safe buffer and released to the GUI at a fixed
  rate. A project printing thousands of lines a second therefore costs the UI one update per
  tick instead of one per line.
"""

from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import gc
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from quant_workbench.domain.events import DomainEvent
from quant_workbench.domain.ports import EventBus, Subscription

_log = logging.getLogger(__name__)

T = TypeVar("T")

_SHUTDOWN_SECONDS = 10.0
_TRANSPORT_GRACE_SECONDS = 0.1


class AsyncBridge(QObject):
    """Owns the engine thread and its event loop."""

    #: Carries a callable from the engine thread to the GUI thread (queued delivery).
    _to_gui = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._loop = asyncio.new_event_loop()
        started = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop, args=(started,), name="qw-engine", daemon=True
        )
        self._thread.start()
        started.wait()
        self._closed = False
        self._to_gui.connect(self._deliver)

    # ----------------------------------------------------------------- the thread
    def _run_loop(self, started: threading.Event) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(started.set)
        self._loop.run_forever()

    @property
    def engine_thread_id(self) -> int | None:
        return self._thread.ident

    @property
    def is_running(self) -> bool:
        return self._thread.is_alive() and not self._closed

    # --------------------------------------------------------------------- submit
    def submit(
        self,
        coroutine: Coroutine[Any, Any, T],
        *,
        on_result: Callable[[T], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> concurrent.futures.Future[T]:
        """Run ``coroutine`` on the engine loop.

        ``on_result`` / ``on_error`` are called on the GUI thread when it finishes. A coroutine
        that was cancelled calls neither. Without an ``on_error``, an exception is logged.
        """
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)

        def finished(done: concurrent.futures.Future[T]) -> None:
            # runs on the engine thread: hand over to the GUI thread, never touch widgets here
            try:
                self._to_gui.emit(lambda: self._complete(done, on_result, on_error))
            except RuntimeError:  # the bridge was deleted during shutdown
                _log.debug("bridge gone before a job finished")

        future.add_done_callback(finished)
        return future

    def call(self, function: Callable[[], object]) -> None:
        """Run a plain function on the engine thread (for things that touch engine state)."""
        self._loop.call_soon_threadsafe(function)

    @staticmethod
    def _complete(
        done: concurrent.futures.Future[T],
        on_result: Callable[[T], None] | None,
        on_error: Callable[[BaseException], None] | None,
    ) -> None:
        if done.cancelled():
            return
        error = done.exception()
        if error is not None:
            if on_error is not None:
                on_error(error)
            else:
                _log.error("engine job failed", exc_info=error)
        elif on_result is not None:
            on_result(done.result())

    @Slot(object)
    def _deliver(self, callback: Callable[[], None]) -> None:
        callback()

    # ------------------------------------------------------------------- shutdown
    def stop(self) -> None:
        """Cancel whatever is running, stop the loop and join the thread. Idempotent."""
        if self._closed:
            return
        self._closed = True

        async def cancel_everything() -> None:
            current = asyncio.current_task()
            tasks = [t for t in asyncio.all_tasks() if t is not current]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # let the subprocess pipe transports finish closing while the loop still exists;
            # closing the loop first leaves them to be garbage-collected against a dead loop
            gc.collect()  # transports of finished processes are finalised now, against a live loop
            await asyncio.sleep(_TRANSPORT_GRACE_SECONDS)
            await asyncio.get_running_loop().shutdown_asyncgens()

        if self._thread.is_alive():
            try:
                asyncio.run_coroutine_threadsafe(cancel_everything(), self._loop).result(
                    _SHUTDOWN_SECONDS
                )
            except (concurrent.futures.TimeoutError, RuntimeError):
                _log.warning("engine tasks did not stop in time")
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(_SHUTDOWN_SECONDS)
        if not self._thread.is_alive():
            self._loop.close()


class EventRelay(QObject):
    """Forwards every domain event to the GUI, batched.

    The engine publishes from its own thread through the (thread-safe) event bus; the relay
    only appends to a deque there. A timer that lives on the GUI thread empties the deque and
    emits :attr:`batch`, so listeners are always called on the GUI thread.
    """

    batch = Signal(list)

    def __init__(self, bus: EventBus, interval_ms: int = 40, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._buffer: collections.deque[DomainEvent] = collections.deque()
        self._subscription: Subscription | None = bus.subscribe(DomainEvent, self._buffer.append)
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.drain)
        self._timer.start()

    @Slot()
    def drain(self) -> None:
        """Emit everything received since the last call (also callable directly by tests)."""
        if not self._buffer:
            return
        events: list[DomainEvent] = []
        while self._buffer:
            events.append(self._buffer.popleft())
        self.batch.emit(events)

    def close(self) -> None:
        self._timer.stop()
        if self._subscription is not None:
            self._subscription()
            self._subscription = None
        self.drain()
