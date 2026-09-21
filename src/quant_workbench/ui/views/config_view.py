"""The configuration form: edit a project's constants, review the diff, save, undo."""

from __future__ import annotations

from collections.abc import Callable
from html import escape

from PySide6.QtGui import QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from quant_workbench.application.config import ConfigPlan
from quant_workbench.domain.errors import WorkbenchError
from quant_workbench.domain.literals import ValueKind
from quant_workbench.domain.project import Project
from quant_workbench.ui.config_form import ConfigFormState, Field, wants_multiline_editor
from quant_workbench.ui.controller import AppController
from quant_workbench.ui.dialogs import DiffDialog
from quant_workbench.ui.theme import Tokens
from quant_workbench.ui.views.base import ProjectView

_MULTILINE_HEIGHT = 110


class _EditCommand(QUndoCommand):
    """Applying a plan is ``redo``; applying its inverse is ``undo``.

    A file that changed on disk in the meantime makes the write refuse (see
    :meth:`ConfigService.apply`); the command then marks itself obsolete so the stack drops it
    and the view can show why.
    """

    def __init__(self, controller: AppController, slug: str, plan: ConfigPlan, text: str) -> None:
        super().__init__(text)
        self._controller = controller
        self._slug = slug
        self._plan = plan
        self.error: WorkbenchError | None = None

    def redo(self) -> None:
        self._write(self._plan)

    def undo(self) -> None:
        self._write(self._plan.inverse())

    def _write(self, plan: ConfigPlan) -> None:
        try:
            self._controller.apply_config(self._slug, plan)
        except WorkbenchError as error:
            self.error = error
            self.setObsolete(True)


class ConfigView(ProjectView):
    """A typed form over the project's editable constants (see :class:`ConfigFormState`)."""

    def __init__(
        self, controller: AppController, tokens: Tokens, parent: QWidget | None = None
    ) -> None:
        super().__init__(tokens, parent)
        self._controller = controller
        self._state = ConfigFormState(())
        self._editors: dict[str, QWidget] = {}
        self._stacks: dict[str, QUndoStack] = {}
        #: Asks the user to confirm a plan; tests replace it to skip the modal dialog.
        self.confirm_diff: Callable[[ConfigPlan], bool] = self._show_diff_dialog

        self._form_host = QWidget()
        self._form_host.setObjectName("form-host")
        self._grid = QGridLayout(self._form_host)
        self._grid.setColumnStretch(1, 1)
        self._grid.setVerticalSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._form_host)
        self._placeholder = QLabel("Select a project to edit its configuration.")

        self.save_button = QPushButton("Review and save…")
        self.save_button.setDefault(True)
        self.discard_button = QPushButton("Discard edits")
        self.undo_button = QPushButton("Undo save")
        self.redo_button = QPushButton("Redo save")
        self.run_after = QCheckBox("Run the project after saving")
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.save_button.clicked.connect(self.save)
        self.discard_button.clicked.connect(self.discard)
        self.undo_button.clicked.connect(self._undo)
        self.redo_button.clicked.connect(self._redo)

        buttons = QHBoxLayout()
        for widget in (
            self.save_button,
            self.discard_button,
            self.undo_button,
            self.redo_button,
            self.run_after,
        ):
            buttons.addWidget(widget)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self._placeholder)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.notice)
        layout.addLayout(buttons)
        self._scroll = scroll
        controller.config_changed.connect(self._on_config_changed)
        self.refresh()

    # ------------------------------------------------------------------- building
    def refresh(self) -> None:
        """Rebuild the form from the files on disk (dropping unsaved edits)."""
        self._clear()
        entries = self._controller.config_constants(self._project.slug) if self._project else ()
        self._state = ConfigFormState(entries)
        for row, field in enumerate(self._state.fields):
            self._add_row(row, field)
        if self._project is None:
            self._placeholder.setText("Select a project to edit its configuration.")
        elif not entries:
            self._placeholder.setText("This project declares no editable configuration.")
        self._placeholder.setVisible(not entries)
        self._scroll.setVisible(bool(entries))
        self._update_buttons()

    def _clear(self) -> None:
        while (item := self._grid.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.deleteLater()
        self._editors.clear()

    def _add_row(self, row: int, field: Field) -> None:
        constant = field.constant
        muted = self._tokens.ink_muted
        secondary = self._tokens.ink_secondary
        description = (
            f"<br><span style='color:{secondary}'>{escape(constant.description)}</span>"
            if constant.description
            else ""
        )
        label = QLabel(
            f"<b>{escape(constant.name)}</b> "
            f"<span style='color:{muted}'>{constant.kind.value} - {escape(field.entry.file)}"
            f":{constant.line}</span>{description}"
        )
        label.setWordWrap(True)
        label.setMinimumWidth(320)
        editor = self._make_editor(field)
        self._grid.addWidget(label, row, 0)
        self._grid.addWidget(editor, row, 1)
        self._editors[field.key] = editor

    def _make_editor(self, field: Field) -> QWidget:
        key = field.key
        editor: QWidget
        if field.constant.kind is ValueKind.BOOL:
            box = QCheckBox()
            box.setChecked(field.text == "true")
            box.toggled.connect(lambda on, k=key: self._edited(k, "true" if on else "false"))
            editor = box
        elif wants_multiline_editor(field.constant):
            text = QPlainTextEdit(field.text)
            text.setFixedHeight(_MULTILINE_HEIGHT)
            text.textChanged.connect(lambda k=key, w=text: self._edited(k, w.toPlainText()))
            editor = text
        else:
            line = QLineEdit(field.text)
            line.textChanged.connect(lambda value, k=key: self._edited(k, value))
            editor = line
        editor.setObjectName(f"editor-{field.constant.name}")
        return editor

    # -------------------------------------------------------------------- editing
    def editor(self, name: str) -> QWidget:
        """The widget editing the constant called ``name`` (used by tests and shortcuts)."""
        return next(w for key, w in self._editors.items() if key.endswith(f":{name}"))

    def _edited(self, key: str, text: str) -> None:
        field = self._state.set_text(key, text)
        editor = self._editors[key]
        editor.setProperty("invalid", field.error is not None)
        editor.setToolTip(field.error or "")
        editor.style().unpolish(editor)
        editor.style().polish(editor)
        self._update_buttons()

    def _update_buttons(self) -> None:
        dirty = self._state.dirty_keys
        invalid = self._state.invalid_keys
        self.save_button.setEnabled(self._state.can_save)
        self.discard_button.setEnabled(bool(dirty or invalid))
        stack = self._stack()
        self.undo_button.setEnabled(stack is not None and stack.canUndo())
        self.redo_button.setEnabled(stack is not None and stack.canRedo())
        if invalid:
            first = self._state.field(invalid[0])
            self._show(f"{first.constant.name}: {first.error}", error=True)
        elif dirty:
            self._show(
                f"{len(dirty)} unsaved change(s): {', '.join(k.split(':')[-1] for k in dirty)}"
            )
        elif not self.notice.property("sticky"):
            self._show("")

    def _show(self, text: str, *, error: bool = False, sticky: bool = False) -> None:
        colour = self._tokens.error if error else self._tokens.ink_secondary
        self.notice.setStyleSheet(f"color: {colour}")
        self.notice.setText(text)
        self.notice.setProperty("sticky", sticky)

    def discard(self) -> None:
        """Throw away every unsaved edit."""
        self.refresh()
        self._show("")

    # --------------------------------------------------------------------- saving
    def save(self) -> None:
        """Compute the plan, let the user review its diff, then write it."""
        if self._project is None or not self._state.can_save:
            return
        try:
            plan = self._controller.plan_config(self._project.slug, self._state.changes())
        except WorkbenchError as error:
            hint = f" ({error.hint})" if error.hint else ""
            self._show(f"{error.message}{hint}", error=True, sticky=True)
            return
        if plan.is_empty:
            self._show("Nothing to change.", sticky=True)
        elif self.confirm_diff(plan):
            self._apply(self._project, plan)

    def _show_diff_dialog(self, plan: ConfigPlan) -> bool:
        return DiffDialog("Review changes", plan.diff, self._tokens, parent=self).exec() == 1

    def _apply(self, project: Project, plan: ConfigPlan) -> None:
        names = ", ".join(n for change in plan.changes for n in change.names)
        command = _EditCommand(self._controller, project.slug, plan, f"Edit {names}")
        stack = self._stack_for(project.slug)
        stack.push(command)
        if command.error is not None:
            self._show(command.error.message, error=True, sticky=True)
            return
        self.refresh()
        self._show(f"Saved {names}. You can undo it.", sticky=True)
        if self.run_after.isChecked():
            self._controller.run_projects([project.slug])

    # ----------------------------------------------------------------------- undo
    def _stack_for(self, slug: str) -> QUndoStack:
        stack = self._stacks.get(slug)
        if stack is None:
            stack = self._stacks[slug] = QUndoStack(self)
        return stack

    def _stack(self) -> QUndoStack | None:
        return self._stacks.get(self._project.slug) if self._project else None

    def _undo(self) -> None:
        self._step(lambda s: s.undo(), "Undone.")

    def _redo(self) -> None:
        self._step(lambda s: s.redo(), "Redone.")

    def _step(self, action: Callable[[QUndoStack], None], done: str) -> None:
        if (stack := self._stack()) is None:
            return
        before = stack.count()
        action(stack)
        if stack.count() < before:  # the command dropped itself: the file moved under us
            self._show(
                "The file changed on disk since; nothing was undone.", error=True, sticky=True
            )
        else:
            self._show(done, sticky=True)
        self.refresh()

    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens  # no refresh: switching theme must not discard unsaved edits

    def _on_config_changed(self, slug: str) -> None:
        if self._project is not None and slug == self._project.slug and not self._state.dirty_keys:
            self.refresh()
