"""Ports: the interfaces the application layer depends on.

Adapters in :mod:`quant_workbench.infrastructure` implement them. They are
:class:`typing.Protocol` classes (structural typing), so adapters do not need to inherit
from anything and test doubles are trivial to write.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, TypeVar, runtime_checkable

from quant_workbench.domain.events import DomainEvent

E = TypeVar("E", bound=DomainEvent)

#: Calling a ``Subscription`` cancels it. Idempotent.
Subscription = Callable[[], None]


@runtime_checkable
class Clock(Protocol):
    """Source of the current time; injected so time-dependent logic is testable."""

    def now(self) -> datetime:
        """Return the current, timezone-aware, UTC time."""
        ...


class EventBus(Protocol):
    """Publish/subscribe channel for :class:`DomainEvent` instances."""

    def publish(self, event: DomainEvent) -> None:
        """Deliver ``event`` to every handler subscribed to its type or a base type."""
        ...

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> Subscription:
        """Register ``handler`` for ``event_type`` (and its subclasses)."""
        ...
