"""Structured logging: readable on the console, JSON lines on disk."""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

_project: contextvars.ContextVar[str | None] = contextvars.ContextVar("qw_project", default=None)
_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("qw_run_id", default=None)

_ROOT_NAME = "quant_workbench"
_HANDLER_TAG = "_qw_handler"
# Attributes that are not user-supplied ``extra`` fields. ``project`` / ``run_id`` are
# injected by _ContextFilter and serialised explicitly (and only when set).
_STANDARD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
    "project",
    "run_id",
}


@contextlib.contextmanager
def log_context(*, project: str | None = None, run_id: str | None = None) -> Iterator[None]:
    """Attach ``project`` / ``run_id`` to every record emitted inside the ``with`` block.

    Backed by :mod:`contextvars`, so it is correct across ``asyncio`` tasks: each task
    sees the values of the context it was created in, with no cross-talk between
    concurrent runs.
    """
    tokens = (_project.set(project), _run_id.set(run_id))
    try:
        yield
    finally:
        _project.reset(tokens[0])
        _run_id.reset(tokens[1])


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.project = _project.get()
        record.run_id = _run_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line; extra ``extra={...}`` fields are preserved."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("project", "run_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and key not in payload and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    log_dir: Path | None, *, level: int = logging.INFO, console: bool = True
) -> None:
    """(Re)configure the ``quant_workbench`` logger. Safe to call repeatedly."""
    logger = logging.getLogger(_ROOT_NAME)
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            logger.removeHandler(handler)
            handler.close()

    logger.setLevel(level)
    logger.propagate = False
    context_filter = _ContextFilter()

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
        _attach(logger, stream, context_filter)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "workbench.jsonl", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(JsonFormatter())
        _attach(logger, file_handler, context_filter)


def _attach(logger: logging.Logger, handler: logging.Handler, flt: logging.Filter) -> None:
    handler.addFilter(flt)
    setattr(handler, _HANDLER_TAG, True)
    logger.addHandler(handler)
