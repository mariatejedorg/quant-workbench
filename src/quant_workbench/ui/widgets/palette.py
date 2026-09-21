"""The command palette (Ctrl+K): type a few letters, press Enter."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from quant_workbench.ui.commands import Command, CommandRegistry

_COMMAND_ID_ROLE = Qt.ItemDataRole.UserRole + 1
_WIDTH = 520


class CommandPalette(QDialog):
    """A search box over the :class:`CommandRegistry`; Enter runs the highlighted command."""

    def __init__(self, registry: CommandRegistry, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("palette")
        self.setMinimumWidth(_WIDTH)
        self._registry = registry
        self._input = QLineEdit(self)
        self._input.setPlaceholderText("Type a command…")
        self._list = QListWidget(self)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._input)
        layout.addWidget(self._list)
        self._input.textChanged.connect(self.refresh)
        self._input.installEventFilter(self)
        self._list.itemActivated.connect(self._run_item)
        self.refresh()

    # ------------------------------------------------------------------- content
    def refresh(self) -> None:
        """Re-run the search for the text in the box."""
        self._list.clear()
        for command in self._registry.search(self._input.text()):
            self._list.addItem(self._item_for(command))
        if self._list.count():
            self._list.setCurrentRow(0)

    @staticmethod
    def _item_for(command: Command) -> QListWidgetItem:
        text = f"{command.label}\t{command.shortcut}" if command.shortcut else command.label
        item = QListWidgetItem(text)
        item.setData(_COMMAND_ID_ROLE, command.id)
        return item

    def visible_commands(self) -> list[str]:
        """Ids of the commands currently listed, best match first (used by tests)."""
        return [self._list.item(i).data(_COMMAND_ID_ROLE) for i in range(self._list.count())]

    def type_text(self, text: str) -> None:
        self._input.setText(text)

    # ----------------------------------------------------------------- behaviour
    def run_current(self) -> bool:
        """Run the highlighted command and close the palette. Returns whether one ran."""
        if self._list.count() == 0:
            return False
        self._run_item(self._list.currentItem())
        return True

    def _run_item(self, item: QListWidgetItem) -> None:
        command_id = item.data(_COMMAND_ID_ROLE)
        self.accept()
        self._registry.execute(command_id)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # QKeyEvent also covers ShortcutOverride and KeyRelease: react to the press only
        if (
            watched is self._input
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
        ):
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                step = 1 if key == Qt.Key.Key_Down else -1
                row = max(0, min(self._list.count() - 1, self._list.currentRow() + step))
                self._list.setCurrentRow(row)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.run_current()
                return True
        return super().eventFilter(watched, event)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._input.setFocus()
        parent = self.parentWidget()
        if parent is not None:  # centred near the top of the window, like an editor's palette
            geometry = parent.geometry()
            self.move(
                parent.mapToGlobal(parent.rect().topLeft()).x()
                + (geometry.width() - self.width()) // 2,
                parent.mapToGlobal(parent.rect().topLeft()).y() + geometry.height() // 6,
            )
