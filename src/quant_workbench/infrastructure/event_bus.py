"""In-process, thread-safe event bus."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from quant_workbench.domain.events import DomainEvent
from quant_workbench.domain.ports import Subscription

E = TypeVar("E", bound=DomainEvent)

_log = logging.getLogger(__name__)


class InProcessEventBus:
    """Synchronous publish/subscribe keyed by event type.

    Design notes
    ------------
    * A handler subscribed to a base class also receives its subclasses, so a UI panel
      can listen to ``DomainEvent`` to observe everything.
    * Handlers run **in the publisher's thread**. The engine publishes from its worker
      thread; the desktop UI adapts by re-emitting each event as a Qt signal, which Qt
      queues onto the GUI thread (see ``ui/async_bridge.py``). Keeping the bus itself
      thread-agnostic is what allows the CLI to reuse it unchanged.
    * A failing handler never prevents the others from running and never propagates
      into the publisher: an observer must not be able to break the engine.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handlers: dict[type[DomainEvent], list[Callable[[Any], None]]] = {}

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> Subscription:
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers.get(event_type)
                if handlers and handler in handlers:
                    handlers.remove(handler)

        return unsubscribe

    def publish(self, event: DomainEvent) -> None:
        # Snapshot under the lock, invoke outside it: handlers may (un)subscribe or
        # publish further events without deadlocking.
        with self._lock:
            targets = [
                handler
                for registered_type, handlers in self._handlers.items()
                if isinstance(event, registered_type)
                for handler in handlers
            ]
        for handler in targets:
            try:
                handler(event)
            except Exception:
                _log.exception("Event handler %r failed for %s", handler, type(event).__name__)
