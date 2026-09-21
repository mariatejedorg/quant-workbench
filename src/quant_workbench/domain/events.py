"""Domain events.

Events are immutable facts about something that already happened. Use cases publish
them on the :class:`~quant_workbench.domain.ports.EventBus`; delivery layers subscribe
(the desktop UI turns them into Qt signals, the CLI turns them into console output).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Base class for all events. ``occurred_at`` is always timezone-aware UTC."""

    occurred_at: datetime = field(default_factory=_utcnow, kw_only=True)
