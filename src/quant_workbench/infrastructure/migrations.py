"""Versioned schema migrations for the workbench database.

The schema is changed only through this list: never edit an existing entry, append a new
one. Each migration is plain DDL so it can be reviewed, and each runs in a transaction
together with the bump of ``schema_version``, so a crash leaves the database at a
consistent version.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

_V1 = """
CREATE TABLE runs (
    id                TEXT PRIMARY KEY,
    project           TEXT NOT NULL,
    kind              TEXT NOT NULL,
    status            TEXT NOT NULL,
    queued_at         TEXT NOT NULL,
    attempt           INTEGER NOT NULL DEFAULT 1,
    retry_of          TEXT,
    started_at        TEXT,
    finished_at       TEXT,
    exit_code         INTEGER,
    command_json      TEXT NOT NULL DEFAULT '[]',
    timeout_seconds   INTEGER,
    peak_rss_bytes    INTEGER NOT NULL DEFAULT 0,
    avg_cpu_percent   REAL NOT NULL DEFAULT 0,
    failure           TEXT,
    transient         INTEGER NOT NULL DEFAULT 0,
    signatures_json   TEXT NOT NULL DEFAULT '[]',
    metrics_json      TEXT NOT NULL DEFAULT '[]',
    config_json       TEXT NOT NULL DEFAULT '[]',
    outputs_json      TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX ix_runs_project_queued ON runs (project, queued_at DESC);
CREATE INDEX ix_runs_status ON runs (status);

CREATE TABLE run_logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    seq     INTEGER NOT NULL,
    stream  TEXT NOT NULL,
    level   TEXT NOT NULL,
    text    TEXT NOT NULL,
    at      TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_run_logs_run_seq ON run_logs (run_id, seq);
"""

#: Ordered list of migrations; index + 1 is the schema version it produces.
MIGRATIONS: tuple[str, ...] = (_V1,)

SCHEMA_VERSION = len(MIGRATIONS)


def current_version(connection: Connection) -> int:
    exists = connection.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'")
    ).first()
    if exists is None:
        return 0
    row = connection.execute(text("SELECT version FROM schema_version")).first()
    return int(row[0]) if row else 0


def migrate(connection: Connection) -> int:
    """Apply every pending migration and return the resulting version."""
    version = current_version(connection)
    if version == 0:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        )
        connection.execute(text("INSERT INTO schema_version (version) VALUES (0)"))
    for target in range(version + 1, SCHEMA_VERSION + 1):
        for statement in _statements(MIGRATIONS[target - 1]):
            connection.execute(text(statement))
        connection.execute(text("UPDATE schema_version SET version = :v"), {"v": target})
    return SCHEMA_VERSION


def _statements(script: str) -> list[str]:
    return [part.strip() for part in script.split(";") if part.strip()]
