"""Code viewer/editor: the project's files, an outline, search, and (opt-in) editing with backup."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from quant_workbench.domain.code_analysis import outline, parse
from quant_workbench.domain.errors import WorkbenchError
from quant_workbench.ui.controller import AppController
from quant_workbench.ui.theme import Tokens
from quant_workbench.ui.views.base import ProjectView
from quant_workbench.ui.widgets.code_edit import CodeEdit

_LINE_ROLE = Qt.ItemDataRole.UserRole + 1
_RELOAD_DELAY_MS = 300
_INDENT = "    "


class CodeView(ProjectView):
    """Read-only by default: opening a project's code must never be a way to break it."""

    def __init__(
        self, controller: AppController, tokens: Tokens, parent: QWidget | None = None
    ) -> None:
        super().__init__(tokens, parent)
        self._controller = controller
        self._relative: str | None = None
        self._loaded_text = ""

        self.files = QListWidget()
        self.files.currentItemChanged.connect(self._file_chosen)
        self.symbols = QListWidget()
        self.symbols.itemActivated.connect(self._symbol_chosen)
        self.symbols.itemClicked.connect(self._symbol_chosen)
        side = QSplitter(Qt.Orientation.Vertical)
        side.addWidget(self.files)
        side.addWidget(self.symbols)

        bar = self._build_toolbar(tokens)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addLayout(bar)
        right_layout.addWidget(self.editor, 1)
        right_layout.addWidget(self.status)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(side)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 4)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        self._retint = QTimer(self)
        self._retint.setSingleShot(True)
        self._retint.setInterval(_RELOAD_DELAY_MS)
        self._retint.timeout.connect(self.editor.rehighlight)
        self.editor.document().contentsChanged.connect(self._text_changed)
        controller.config_changed.connect(self._on_files_changed)
        self.refresh()

    def _build_toolbar(self, tokens: Tokens) -> QHBoxLayout:
        """The editor and the row of controls above it (search, edit toggle, save, reload)."""
        self.editor = CodeEdit(tokens)
        self.editor.setReadOnly(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find in file…")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self.find_next)
        find_next = QPushButton("Next")
        find_next.clicked.connect(self.find_next)
        find_previous = QPushButton("Previous")
        find_previous.clicked.connect(self.find_previous)
        self.edit_box = QCheckBox("Allow editing")
        self.edit_box.toggled.connect(self._toggle_editing)
        self.save_button = QPushButton("Save")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)
        self.reload_button = QPushButton("Reload")
        self.reload_button.clicked.connect(self.reload)
        self.status = QLabel()

        bar = QHBoxLayout()
        for widget in (
            self.search,
            find_next,
            find_previous,
            self.edit_box,
            self.save_button,
            self.reload_button,
        ):
            bar.addWidget(widget)
        return bar

    # ------------------------------------------------------------------- files
    def refresh(self) -> None:
        """Re-list the project's files and reopen the one that was open, if it still exists."""
        remembered = self._relative
        self.files.blockSignals(True)
        self.files.clear()
        names = self._controller.project_sources(self._project.slug) if self._project else ()
        for name in names:
            self.files.addItem(name)
        self.files.blockSignals(False)
        if remembered in names:
            self.open_file(remembered)
        elif names:
            self.open_file(names[0])
        else:
            self._show_nothing()

    def _show_nothing(self) -> None:
        self._relative = None
        self._loaded_text = ""
        self.editor.load("", "file.txt")
        self.symbols.clear()
        self.status.setText("")

    def _file_chosen(self, current: QListWidgetItem | None) -> None:
        if current is not None and current.text() != self._relative:
            self.open_file(current.text())

    def open_file(self, relative: str, line: int | None = None) -> bool:
        """Show ``relative`` (optionally at a line). Returns whether the file could be read."""
        if self._project is None:
            return False
        text = self._controller.read_source(self._project.slug, relative)
        if text is None:
            self.status.setText(f"{relative} does not exist")
            return False
        self._relative = relative
        self._loaded_text = text
        for row in range(self.files.count()):
            if self.files.item(row).text() == relative:
                self.files.blockSignals(True)
                self.files.setCurrentRow(row)
                self.files.blockSignals(False)
        self.editor.load(text.replace("\r\n", "\n"), relative)
        self.save_button.setEnabled(False)
        self._fill_outline(text, relative)
        self.status.setText(f"{relative} - {text.count(chr(10)) + 1} lines")
        if line is not None:
            self.editor.goto_line(line)
        return True

    def reload(self) -> None:
        if self._relative is not None:
            self.open_file(self._relative)

    def _fill_outline(self, text: str, relative: str) -> None:
        self.symbols.clear()
        tree = parse(text) if relative.endswith(".py") else None
        for symbol in outline(tree) if tree else ():
            item = QListWidgetItem(f"{_INDENT * symbol.depth}{symbol.name}  ({symbol.kind})")
            item.setData(_LINE_ROLE, symbol.line)
            self.symbols.addItem(item)

    def _symbol_chosen(self, item: QListWidgetItem) -> None:
        self.editor.goto_line(int(item.data(_LINE_ROLE)))

    def current_file(self) -> str | None:
        return self._relative

    # ------------------------------------------------------------------- search
    def find_next(self) -> bool:
        return self._find(QTextDocument.FindFlag(0))

    def find_previous(self) -> bool:
        return self._find(QTextDocument.FindFlag.FindBackward)

    def _find(self, flags: QTextDocument.FindFlag) -> bool:
        text = self.search.text()
        if not text:
            return False
        if self.editor.find(text, flags):
            return True
        # wrap around: start again from the other end of the file
        cursor = self.editor.textCursor()
        cursor.movePosition(
            cursor.MoveOperation.End
            if flags & QTextDocument.FindFlag.FindBackward
            else cursor.MoveOperation.Start
        )
        self.editor.setTextCursor(cursor)
        return self.editor.find(text, flags)

    # ------------------------------------------------------------------ editing
    def _toggle_editing(self, on: bool) -> None:
        self.editor.setReadOnly(not on)
        self._update_save_button()
        self.status.setText("Editing: saving keeps a backup of the previous version." if on else "")

    def _text_changed(self) -> None:
        self._retint.start()
        self._update_save_button()

    def _update_save_button(self) -> None:
        changed = self._relative is not None and self._text() != self._loaded_text.replace(
            "\r\n", "\n"
        )
        self.save_button.setEnabled(self.edit_box.isChecked() and changed)

    def _text(self) -> str:
        return self.editor.toPlainText()

    def save(self) -> bool:
        """Write the edited text (keeping the file's line endings). Returns whether it worked."""
        if self._project is None or self._relative is None:
            return False
        text = self._text()
        if "\r\n" in self._loaded_text:
            text = text.replace("\n", "\r\n")
        try:
            self._controller.save_source(
                self._project.slug, self._relative, text, expected=self._loaded_text
            )
        except WorkbenchError as error:
            self.status.setText(error.message + (f" - {error.hint}" if error.hint else ""))
            return False
        self._loaded_text = text
        self.save_button.setEnabled(False)
        self.status.setText(f"Saved {self._relative}")
        return True

    def _on_files_changed(self, slug: str) -> None:
        if (
            self._project is not None
            and slug == self._project.slug
            and not self.save_button.isEnabled()
        ):
            self.reload()

    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
        self.editor.set_tokens(tokens)
