"""Runs: one execution of a job (usually a project's entry point) and everything it produced."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from quant_workbench.domain.ids import RunId, Slug


class JobKind(StrEnum):
    RUN = "run"  # execute a project's entry point
    ENV_SETUP = "env-setup"  # create / repair its virtual environment
    SYNC = "sync"  # clone a missing project from the registry
    LINT = "lint"  # static analysis


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed-out"
    SKIPPED = "skipped"  # not executed because a prerequisite failed

    @property
    def is_terminal(self) -> bool:
        return self not in {RunStatus.QUEUED, RunStatus.RUNNING}

    @property
    def is_success(self) -> bool:
        return self is RunStatus.SUCCEEDED


class LogStream(StrEnum):
    STDOUT = "stdout"
    STDERR = "stderr"
    SYSTEM = "system"  # messages written by the workbench itself, not by the process


class LogLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class LogLine:
    """One line of a run's output, classified and time-stamped."""

    seq: int
    stream: LogStream
    level: LogLevel
    text: str
    at: datetime


@dataclass(frozen=True, slots=True)
class MetricValue:
    """A number (or string) extracted from a run's output by a metric extractor."""

    name: str
    value: float | int | str
    unit: str = ""


@dataclass(frozen=True, slots=True)
class OutputFileState:
    """Size and content hash of one output file right after a run, for later diffing."""

    path: str  # relative to the project root, forward slashes
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Run:
    """The full record of one attempt. Immutable: state changes produce a new instance."""

    id: RunId
    project: Slug
    kind: JobKind
    status: RunStatus
    queued_at: datetime
    attempt: int = 1
    retry_of: RunId | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    command: tuple[str, ...] = ()
    timeout_seconds: int | None = None
    peak_rss_bytes: int = 0
    avg_cpu_percent: float = 0.0
    failure: str | None = None  # one human-readable line explaining a non-success
    transient: bool = False  # the failure looks temporary, so a retry may succeed
    metrics: tuple[MetricValue, ...] = ()
    #: ``(relative path, file text)`` of every configuration file as it was for this run.
    config_snapshot: tuple[tuple[str, str], ...] = ()
    outputs: tuple[OutputFileState, ...] = ()
    signatures: tuple[str, ...] = field(default=())  # ids of matched failure signatures

    @property
    def duration(self) -> timedelta | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return self.finished_at - self.started_at

    def metric(self, name: str) -> MetricValue | None:
        return next((m for m in self.metrics if m.name == name), None)
