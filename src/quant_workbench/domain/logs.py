"""Turning raw process output into classified log lines."""

from __future__ import annotations

import re
from collections.abc import Iterable

from quant_workbench.domain.runs import LogLevel, LogStream

_TRACEBACK_START = "Traceback (most recent call last):"
_ERROR_LINE = re.compile(r"^\s*(?:[\w.]+\.)?\w*(?:Error|Exception|Exit|Interrupt)\b")
_WARNING = re.compile(r"\b(?:warning|deprecat\w*)\b", re.IGNORECASE)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (colours, cursor movement) from ``text``."""
    return _ANSI.sub("", text)


class LogClassifier:
    """Assigns a :class:`LogLevel` to each line of one process's output.

    It is stateful because a Python traceback spans many lines: from the
    ``Traceback (most recent call last):`` header, every indented line and the final
    ``SomeError: message`` line belong to the same error and must all be marked
    ``ERROR``, not just the last one. One instance per run.
    """

    def __init__(self) -> None:
        self._in_traceback = False

    def classify(self, stream: LogStream, text: str) -> LogLevel:
        stripped = strip_ansi(text)
        if stream is LogStream.SYSTEM:
            return LogLevel.INFO
        if _TRACEBACK_START in stripped:
            self._in_traceback = True
            return LogLevel.ERROR
        if self._in_traceback:
            # Indented lines are the stack frames; the first non-indented line is the
            # exception message and closes the block. Both belong to the error.
            if not stripped.startswith((" ", "	")):
                self._in_traceback = False
            return LogLevel.ERROR
        if stream is LogStream.STDERR and _ERROR_LINE.match(stripped):
            return LogLevel.ERROR
        return LogLevel.WARNING if _WARNING.search(stripped) else LogLevel.INFO


_EXCEPTION_SUMMARY = re.compile(
    r"^(?P<name>(?:[\w.]+\.)?\w+(?:Error|Exception)): ?(?P<message>.*)$"
)


def summarize_failure(lines: Iterable[str]) -> str | None:
    """The last ``SomeError: message`` line of the output, or ``None`` if there is none.

    This is what the UI shows as the one-line reason a run failed.
    """
    summary: str | None = None
    for raw in lines:
        match = _EXCEPTION_SUMMARY.match(strip_ansi(raw).strip())
        if match:
            summary = f"{match['name']}: {match['message']}".rstrip(": ")
    return summary
