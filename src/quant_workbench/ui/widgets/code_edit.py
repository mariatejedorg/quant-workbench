"""A code editor widget: line-number gutter, current-line highlight and Pygments colouring."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from quant_workbench.ui.highlight import Span, SyntaxColours, spans_by_line, syntax_colours
from quant_workbench.ui.theme import Tokens

_GUTTER_PADDING = 12


class SpanHighlighter(QSyntaxHighlighter):
    """Applies precomputed per-line spans (see :func:`spans_by_line`) to a document."""

    def __init__(self, document: QTextDocument, colours: SyntaxColours) -> None:
        super().__init__(document)
        self._colours = colours
        self._spans: list[list[Span]] = []

    def set_spans(self, spans: list[list[Span]]) -> None:
        self._spans = spans
        self.rehighlight()

    def set_colours(self, colours: SyntaxColours) -> None:
        self._colours = colours
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        number = self.currentBlock().blockNumber()
        if number >= len(self._spans):
            return
        for start, length, category in self._spans[number]:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(self._colours.of(category)))
            if category == "keyword":
                fmt.setFontWeight(QFont.Weight.DemiBold)
            if category == "comment":
                fmt.setFontItalic(True)
            self.setFormat(start, length, fmt)


class _Gutter(QWidget):
    def __init__(self, editor: CodeEdit) -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, event: QPaintEvent) -> None:
        self._editor.paint_gutter(event)


class CodeEdit(QPlainTextEdit):
    """Monospaced editor with line numbers; read-only until the view enables editing."""

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("console")  # same monospace styling as the console
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.setFont(font)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self._tokens = tokens
        self._gutter = _Gutter(self)
        self._highlighter = SpanHighlighter(self.document(), syntax_colours(tokens))
        self._filename = "file.txt"
        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._scroll_gutter)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_margin()
        self._highlight_current_line()

    # ------------------------------------------------------------------- content
    def load(self, text: str, filename: str) -> None:
        """Show ``text`` (highlighted according to ``filename``)."""
        self._filename = filename
        self.setPlainText(text)
        self.rehighlight()

    def rehighlight(self) -> None:
        """Recompute the colours from the current text (call after edits settle)."""
        self._highlighter.set_spans(spans_by_line(self.toPlainText(), self._filename))

    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
        self._highlighter.set_colours(syntax_colours(tokens))
        self._gutter.update()
        self._highlight_current_line()

    def goto_line(self, line: int) -> None:
        """Put the cursor at the start of 1-based ``line`` and scroll it into view."""
        block = self.document().findBlockByNumber(max(0, line - 1))
        if block.isValid():
            cursor = self.textCursor()
            cursor.setPosition(block.position())
            self.setTextCursor(cursor)
            self.centerCursor()

    def current_line(self) -> int:
        return self.textCursor().blockNumber() + 1

    # -------------------------------------------------------------------- gutter
    def gutter_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return _GUTTER_PADDING + self.fontMetrics().horizontalAdvance("9") * max(3, digits)

    def _update_margin(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _scroll_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margin()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        area = self.contentsRect()
        self._gutter.setGeometry(QRect(area.left(), area.top(), self.gutter_width(), area.height()))

    def paint_gutter(self, event: QPaintEvent) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor(self._tokens.window))
        painter.setPen(QColor(self._tokens.ink_muted))
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        current = self.textCursor().blockNumber()
        while block.isValid() and top <= event.rect().bottom():
            height = round(self.blockBoundingRect(block).height())
            if block.isVisible() and top + height >= event.rect().top():
                painter.setFont(self.font())
                painter.setPen(
                    QColor(
                        self._tokens.ink
                        if block.blockNumber() == current
                        else self._tokens.ink_muted
                    )
                )
                painter.drawText(
                    0,
                    top,
                    self._gutter.width() - 6,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    str(block.blockNumber() + 1),
                )
            block = block.next()
            top += height

    def _highlight_current_line(self) -> None:
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor(self._tokens.selection))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])
