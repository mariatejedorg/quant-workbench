from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

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
from quant_workbench.infrastructure.migrations import SCHEMA_VERSION, current_version, migrate
from quant_workbench.infrastructure.sqlite_runs import SqliteRunRepository, create_sqlite_engine

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 21, 12, 0, 0, 123456, tzinfo=UTC)


def make_run(n: int, project: str = "alpha", **kwargs: object) -> Run:
    defaults: dict[str, object] = {
        "id": RunId(f"{n:013x}-aaaaaaaa"),
        "project": Slug(project),
        "kind": JobKind.RUN,
        "status": RunStatus.SUCCEEDED,
        "queued_at": T0 + timedelta(seconds=n),
    }
    defaults.update(kwargs)
    return Run(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def repo(accented_root: Path) -> Iterator[SqliteRunRepository]:
    repository = SqliteRunRepository(
        create_sqlite_engine(accented_root / "db" / "workbench.sqlite3")
    )
    yield repository
    repository.close()


def test_a_fully_populated_run_round_trips_exactly(repo: SqliteRunRepository) -> None:
    run = make_run(
        1,
        attempt=2,
        retry_of=RunId("0000000000000-bbbbbbbb"),
        started_at=T0 + timedelta(seconds=2),
        finished_at=T0 + timedelta(seconds=9, microseconds=500),
        exit_code=0,
        command=("python", "C:/Users/María/proyecto 7/src/main.py"),
        timeout_seconds=900,
        peak_rss_bytes=123_456_789,
        avg_cpu_percent=87.5,
        failure="ñ: unicode survives",
        transient=True,
        signatures=("network-timeout", "yfinance-rate-limit"),
        metrics=(
            MetricValue("sharpe", 2.45, ""),
            MetricValue("ticker", "AAPL"),
            MetricValue("n", 7),
        ),
        config_snapshot=(("config/x.py", "TICKER = 'ñ'\r\nN = 3\n"),),
        outputs=(OutputFileState("outputs/dashboard.html", 1234, "ab" * 32),),
    )

    repo.save(run)

    assert repo.get(run.id) == run


def test_saving_again_updates_the_same_row(repo: SqliteRunRepository) -> None:
    run = make_run(1, status=RunStatus.RUNNING)
    repo.save(run)
    finished = make_run(1, status=RunStatus.FAILED, exit_code=1)

    repo.save(finished)

    assert repo.get(run.id) == finished
    assert len(repo.list_runs()) == 1


def test_unknown_ids_are_none(repo: SqliteRunRepository) -> None:
    assert repo.get(RunId("nope")) is None
    assert repo.latest(Slug("nobody")) is None


def test_listing_is_newest_first_filterable_and_limited(repo: SqliteRunRepository) -> None:
    for n in range(1, 6):
        repo.save(make_run(n, "alpha" if n % 2 else "beta"))

    everything = repo.list_runs()
    assert [r.id for r in everything] == sorted((r.id for r in everything), reverse=True)
    assert {r.project for r in repo.list_runs(Slug("beta"))} == {"beta"}
    assert len(repo.list_runs(limit=2)) == 2
    latest_alpha = repo.latest(Slug("alpha"))
    assert latest_alpha is not None
    assert latest_alpha.id == make_run(5).id


def test_logs_are_ordered_paged_and_keep_their_classification(repo: SqliteRunRepository) -> None:
    run = make_run(1)
    repo.save(run)
    lines = [
        LogLine(
            i,
            LogStream.STDERR if i % 2 else LogStream.STDOUT,
            LogLevel.ERROR if i == 3 else LogLevel.INFO,
            f"línea {i}",
            T0 + timedelta(milliseconds=i),
        )
        for i in range(10)
    ]
    repo.append_logs(run.id, lines[:6])
    repo.append_logs(run.id, lines[6:])
    repo.append_logs(run.id, [])

    assert repo.logs(run.id) == tuple(lines)
    assert repo.logs(run.id, offset=4, limit=3) == tuple(lines[4:7])
    assert repo.logs(run.id)[3].level is LogLevel.ERROR
    assert repo.logs(RunId("unknown")) == ()


def test_duplicate_sequence_numbers_are_rejected(repo: SqliteRunRepository) -> None:
    run = make_run(1)
    repo.save(run)
    line = LogLine(0, LogStream.STDOUT, LogLevel.INFO, "x", T0)
    repo.append_logs(run.id, [line])

    with pytest.raises(Exception, match="UNIQUE"):
        repo.append_logs(run.id, [line])


def test_logs_require_an_existing_run(repo: SqliteRunRepository) -> None:
    orphan = LogLine(0, LogStream.STDOUT, LogLevel.INFO, "x", T0)

    with pytest.raises(Exception, match="FOREIGN KEY"):
        repo.append_logs(RunId("ghost"), [orphan])


def test_data_persists_across_connections(accented_root: Path) -> None:
    path = accented_root / "workbench.sqlite3"
    first = SqliteRunRepository(create_sqlite_engine(path))
    first.save(make_run(1))
    first.close()

    second = SqliteRunRepository(create_sqlite_engine(path))
    try:
        assert second.get(make_run(1).id) is not None
    finally:
        second.close()


@pytest.fixture
def memory_engine() -> Iterator[Engine]:
    engine = create_sqlite_engine(None)
    yield engine
    engine.dispose()


def test_file_databases_use_wal_and_enforce_foreign_keys(accented_root: Path) -> None:
    engine = create_sqlite_engine(accented_root / "w.sqlite3")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
    finally:
        engine.dispose()


def test_concurrent_writers_do_not_lose_data(repo: SqliteRunRepository) -> None:
    errors: list[BaseException] = []

    def worker(offset: int) -> None:
        try:
            for i in range(25):
                run = make_run(offset * 100 + i, f"p{offset}")
                repo.save(run)
                repo.append_logs(run.id, [LogLine(0, LogStream.STDOUT, LogLevel.INFO, "x", T0)])
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(1, 7)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(repo.list_runs(limit=1000)) == 150


# ------------------------------------------------------------------- migrations
def test_migrating_is_idempotent_and_records_the_version(memory_engine: Engine) -> None:
    with memory_engine.begin() as connection:
        assert current_version(connection) == SCHEMA_VERSION
        assert migrate(connection) == SCHEMA_VERSION
        rows = connection.execute(text("SELECT COUNT(*) FROM schema_version")).scalar()

    assert rows == 1


def test_migration_starts_from_an_empty_database(memory_engine: Engine) -> None:
    with memory_engine.connect() as connection:
        tables = {
            r[0]
            for r in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }

    assert {"runs", "run_logs", "schema_version"} <= tables
