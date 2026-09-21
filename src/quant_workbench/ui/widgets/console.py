"""The console: live output of every running project, with colours and a per-project filter."""

from __future__ import annotations

import collections
import zlib
from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from quant_workbench.domain.runs import LogLevel, LogStream
from quant_workbench.ui.ansi import AnsiParser, AnsiStyle
from quant_workbench.ui.theme import Tokens

#: How many lines are kept (and can be re-rendered when the filter changes).
MAX_LINES = 20_000
#: Prefix colours, hue-spaced so neighbouring projects are easy to tell apart.
_PREFIX_HUES = (210, 150, 30, 280, 340, 180, 60, 250)


@dataclass(frozen=True, slots=True)
class _Entry:
    project: str
    text: str
    stream: LogStream
    level: LogLevel


def prefix_colour(project: str, tokens: Tokens) -> QColor:
    """A stable colour per project, readable on the console background of either theme."""
    hue = _PREFIX_HUES[zlib.crc32(project.encode()) % len(_PREFIX_HUES)]
    lightness = 0.68 if tokens.name == "dark" else 0.36
    return QColor.fromHslF(hue / 360, 0.55, lightness)


class ConsoleView(QPlainTextEdit):
    """Read-only, monospaced, follows the output unless the user scrolled up to read."""

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("console")
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setMaximumBlockCount(MAX_LINES)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.setFont(font)
        self._tokens = tokens
        self._entries: collections.deque[_Entry] = collections.deque(maxlen=MAX_LINES)
        self._parsers: dict[str, AnsiParser] = {}
        self._filter: str | None = None

    # ------------------------------------------------------------------ content
    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
        self._rerender()

    def add_line(self, project: str, text: str, stream: LogStream, level: LogLevel) -> None:
        """Append one line of a project's output."""
        entry = _Entry(project, text, stream, level)
        self._entries.append(entry)
        if self._filter in (None, project):
            self._write(entry)

    def add_system(self, text: str) -> None:
        """A message from the workbench itself (not from a project)."""
        self.add_line("workbench", text, LogStream.SYSTEM, LogLevel.INFO)

    def set_filter(self, project: str | None) -> None:
        """Show only one project's lines (``None`` shows everything)."""
        if project == self._filter:
            return
        self._filter = project
        self._rerender()

    @property
    def active_filter(self) -> str | None:
        return self._filter

    def clear_console(self) -> None:
        self._entries.clear()
        self._parsers.clear()
        self.clear()

    def line_count(self) -> int:
        return len(self._entries)

    # ---------------------------------------------------------------- rendering
    def _rerender(self) -> None:
        self.clear()
        self._parsers.clear()
        for entry in self._entries:
            if self._filter in (None, entry.project):
                self._write(entry)

    def _write(self, entry: _Entry) -> None:
        scrollbar = self.verticalScrollBar()
        follow = scrollbar.value() >= scrollbar.maximum() - 2
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.beginEditBlock()
        if not self.document().isEmpty():
            cursor.insertBlock()
        prefix = QTextCharFormat()
        prefix.setForeground(prefix_colour(entry.project, self._tokens))
        cursor.insertText(f"[{entry.project}] ", prefix)
        parser = self._parsers.setdefault(entry.project, AnsiParser())
        for text, style in parser.feed(entry.text):
            cursor.insertText(text, self._format(entry, style))
        cursor.endEditBlock()
        if follow:
            scrollbar.setValue(scrollbar.maximum())

    def _format(self, entry: _Entry, style: AnsiStyle) -> QTextCharFormat:
        fmt = QTextCharFormat()
        colour = self._tokens.console_fg
        if entry.stream is LogStream.SYSTEM:
            colour = self._tokens.ink_muted
            fmt.setFontItalic(True)
        elif entry.level is LogLevel.ERROR:
            colour = self._tokens.error
        elif entry.level is LogLevel.WARNING:
            colour = self._tokens.warning
        fmt.setForeground(QColor(style.fg or colour))
        if style.bg:
            fmt.setBackground(QColor(style.bg))
        if style.bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        if style.dim:
            fmt.setForeground(QColor(self._tokens.ink_muted))
        fmt.setFontUnderline(style.underline)
        return fmt

    def plain_text(self) -> str:
        return self.toPlainText()

    def scroll_to_end(self) -> None:
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
