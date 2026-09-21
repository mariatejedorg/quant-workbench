"""Value objects describing an operating-system process to run and what came of it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    """What to execute. ``env`` is layered on top of a sanitised copy of the environment."""

    args: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResourceSample:
    """CPU and memory of a process *tree* at one instant."""

    at: datetime
    cpu_percent: float
    rss_bytes: int


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """How a process ended. ``exit_code`` is ``None`` when it was killed on timeout."""

    exit_code: int | None
    timed_out: bool = False
    peak_rss_bytes: int = 0
    avg_cpu_percent: float = 0.0
