"""Clock adapters."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """The real wall clock (implements :class:`~quant_workbench.domain.ports.Clock`)."""

    def now(self) -> datetime:
        return datetime.now(UTC)
