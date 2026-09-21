"""The settings dialog: workspace, concurrency, git policy, theme."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from quant_workbench.application.settings import Settings


class SettingsDialog(QDialog):
    """Edits a :class:`Settings`; :meth:`result_settings` is the validated new value."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        self._original = settings

        self.workspace = QLineEdit(str(settings.workspace_root or ""))
        self.workspace.setPlaceholderText("(auto-detect from the folder the app starts in)")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        workspace_row = QHBoxLayout()
        workspace_row.addWidget(self.workspace)
        workspace_row.addWidget(browse)

        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 16)
        self.concurrency.setValue(settings.max_concurrency)
        self.timeout = QSpinBox()
        self.timeout.setRange(10, 86_400)
        self.timeout.setSuffix(" s")
        self.timeout.setValue(settings.run_timeout_seconds)
        self.retries = QSpinBox()
        self.retries.setRange(0, 5)
        self.retries.setValue(settings.max_retries)
        self.theme = QComboBox()
        self.theme.addItems(["system", "light", "dark"])
        self.theme.setCurrentText(settings.theme)
        self.git_email = QLineEdit(settings.expected_git_email)
        self.git_name = QLineEdit(settings.expected_git_name or "")
        self.git_host = QLineEdit(settings.expected_git_remote_host or "")
        self.disabled = QLineEdit(", ".join(settings.disabled_checkers))
        self.disabled.setPlaceholderText("checker ids, comma separated (see `qw doctor --list`)")

        form = QFormLayout()
        form.addRow("Workspace folder", workspace_row)
        form.addRow("Runs in parallel", self.concurrency)
        form.addRow("Run time limit", self.timeout)
        form.addRow("Retries (transient errors)", self.retries)
        form.addRow("Theme", self.theme)
        form.addRow("Expected git e-mail", self.git_email)
        form.addRow("Expected git name", self.git_name)
        form.addRow("Expected remote host", self.git_host)
        form.addRow("Disabled doctor checks", self.disabled)

        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setStyleSheet("color: #d13b3a")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._error)
        layout.addWidget(buttons)
        self._result: Settings | None = None

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Workspace folder", self.workspace.text())
        if chosen:
            self.workspace.setText(chosen)

    def _build(self) -> Settings:
        """The settings the form describes (raises ``ValidationError`` if they are invalid)."""
        values = self._original.model_dump()
        values.update(
            workspace_root=Path(self.workspace.text()) if self.workspace.text().strip() else None,
            max_concurrency=self.concurrency.value(),
            run_timeout_seconds=self.timeout.value(),
            max_retries=self.retries.value(),
            theme=self.theme.currentText(),
            expected_git_email=self.git_email.text().strip(),
            expected_git_name=self.git_name.text().strip() or None,
            expected_git_remote_host=self.git_host.text().strip() or None,
            disabled_checkers=tuple(
                part.strip() for part in self.disabled.text().split(",") if part.strip()
            ),
        )
        return Settings(**values)

    def _accept_if_valid(self) -> None:
        try:
            self._result = self._build()
        except ValidationError as error:
            first = error.errors()[0]
            self._error.setText(f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}")
            return
        self.accept()

    def result_settings(self) -> Settings | None:
        """The validated settings after the dialog was accepted, else ``None``."""
        return self._result
