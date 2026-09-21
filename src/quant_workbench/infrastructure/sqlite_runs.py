"""SQLite persistence of runs and their logs (SQLAlchemy 2.0, typed)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Engine,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from quant_workbench.domain.ids import RunId, Slug
from quant_workbench.domain.runs import (
    JobKind,
    LogLevel,
    LogLine,
    LogStream,
    MetricValue,
    OutputFileState,
    Run,
    RunStatus,
)
from quant_workbench.infrastructure.migrations import migrate


class _Base(DeclarativeBase):
    """Mapping only: the DDL lives in ``migrations.py`` and is the source of truth."""


class _RunRow(_Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    queued_at: Mapped[str] = mapped_column(String)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    retry_of: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    command_json: Mapped[str] = mapped_column(Text, default="[]")
    timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peak_rss_bytes: Mapped[int] = mapped_column(Integer, default=0)
    avg_cpu_percent: Mapped[float] = mapped_column(Float, default=0.0)
    failure: Mapped[str | None] = mapped_column(Text, nullable=True)
    transient: Mapped[int] = mapped_column(Integer, default=0)
    signatures_json: Mapped[str] = mapped_column(Text, default="[]")
    metrics_json: Mapped[str] = mapped_column(Text, default="[]")
    config_json: Mapped[str] = mapped_column(Text, default="[]")
    outputs_json: Mapped[str] = mapped_column(Text, default="[]")


class _LogRow(_Base):
    __tablename__ = "run_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    stream: Mapped[str] = mapped_column(String)
    level: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    at: Mapped[str] = mapped_column(String)


def _iso(moment: datetime | None) -> str | None:
    """UTC ISO-8601 with microseconds: fixed width, so string order is time order."""
    return None if moment is None else moment.astimezone(UTC).isoformat(timespec="microseconds")


def _parse(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)


def _require(text: str) -> datetime:
    return datetime.fromisoformat(text)


def create_sqlite_engine(database: Path | None) -> Engine:
    """Create the engine (``None`` gives an in-memory database, for tests) and migrate it."""
    url = URL.create("sqlite", database=str(database) if database else ":memory:")
    if database is not None:
        database.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 5000")
        if database is not None:
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.close()

    with engine.begin() as connection:
        migrate(connection)
    return engine


class SqliteRunRepository:
    """Implements :class:`~quant_workbench.domain.ports.RunRepository`."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = sessionmaker(engine, expire_on_commit=False)

    # ------------------------------------------------------------------ runs
    def save(self, run: Run) -> None:
        with self._session.begin() as session:
            session.merge(_to_row(run))

    def get(self, run_id: RunId) -> Run | None:
        with self._session() as session:
            row = session.get(_RunRow, run_id)
            return None if row is None else _to_run(row)

    def list_runs(self, project: Slug | None = None, *, limit: int = 50) -> tuple[Run, ...]:
        statement = (
            select(_RunRow).order_by(_RunRow.queued_at.desc(), _RunRow.id.desc()).limit(limit)
        )
        if project is not None:
            statement = statement.where(_RunRow.project == project)
        with self._session() as session:
            return tuple(_to_run(row) for row in session.scalars(statement))

    def latest(self, project: Slug) -> Run | None:
        runs = self.list_runs(project, limit=1)
        return runs[0] if runs else None

    # ------------------------------------------------------------------ logs
    def append_logs(self, run_id: RunId, lines: Sequence[LogLine]) -> None:
        if not lines:
            return
        with self._session.begin() as session:
            session.add_all(
                _LogRow(
                    run_id=run_id,
                    seq=line.seq,
                    stream=line.stream.value,
                    level=line.level.value,
                    text=line.text,
                    at=_iso(line.at) or "",
                )
                for line in lines
            )

    def logs(
        self, run_id: RunId, *, offset: int = 0, limit: int | None = None
    ) -> tuple[LogLine, ...]:
        statement = (
            select(_LogRow).where(_LogRow.run_id == run_id).order_by(_LogRow.seq).offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self._session() as session:
            return tuple(
                LogLine(
                    seq=row.seq,
                    stream=LogStream(row.stream),
                    level=LogLevel(row.level),
                    text=row.text,
                    at=_require(row.at),
                )
                for row in session.scalars(statement)
            )

    def close(self) -> None:
        self._engine.dispose()


# ------------------------------------------------------------------ conversion
def _to_row(run: Run) -> _RunRow:
    return _RunRow(
        id=run.id,
        project=run.project,
        kind=run.kind.value,
        status=run.status.value,
        queued_at=_iso(run.queued_at) or "",
        attempt=run.attempt,
        retry_of=run.retry_of,
        started_at=_iso(run.started_at),
        finished_at=_iso(run.finished_at),
        exit_code=run.exit_code,
        command_json=json.dumps(list(run.command)),
        timeout_seconds=run.timeout_seconds,
        peak_rss_bytes=run.peak_rss_bytes,
        avg_cpu_percent=run.avg_cpu_percent,
        failure=run.failure,
        transient=int(run.transient),
        signatures_json=json.dumps(list(run.signatures)),
        metrics_json=json.dumps(
            [{"name": m.name, "value": m.value, "unit": m.unit} for m in run.metrics]
        ),
        config_json=json.dumps([[path, text] for path, text in run.config_snapshot]),
        outputs_json=json.dumps(
            [{"path": o.path, "size": o.size, "sha256": o.sha256} for o in run.outputs]
        ),
    )


def _to_run(row: _RunRow) -> Run:
    return Run(
        id=RunId(row.id),
        project=Slug(row.project),
        kind=JobKind(row.kind),
        status=RunStatus(row.status),
        queued_at=_require(row.queued_at),
        attempt=row.attempt,
        retry_of=RunId(row.retry_of) if row.retry_of else None,
        started_at=_parse(row.started_at),
        finished_at=_parse(row.finished_at),
        exit_code=row.exit_code,
        command=tuple(json.loads(row.command_json)),
        timeout_seconds=row.timeout_seconds,
        peak_rss_bytes=row.peak_rss_bytes,
        avg_cpu_percent=row.avg_cpu_percent,
        failure=row.failure,
        transient=bool(row.transient),
        signatures=tuple(json.loads(row.signatures_json)),
        metrics=tuple(
            MetricValue(m["name"], m["value"], m["unit"]) for m in json.loads(row.metrics_json)
        ),
        config_snapshot=tuple((path, text) for path, text in json.loads(row.config_json)),
        outputs=tuple(
            OutputFileState(o["path"], o["size"], o["sha256"]) for o in json.loads(row.outputs_json)
        ),
    )
