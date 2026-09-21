"""Small modal dialogs shared by several views: a diff preview and a fix preview."""

from __future__ import annotations

from html import escape

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from quant_workbench.application.fixes import FixPreview
from quant_workbench.ui.diffhtml import diff_html
from quant_workbench.ui.theme import Tokens


class DiffDialog(QDialog):
    """Shows a unified diff and asks for confirmation (Save / Cancel)."""

    def __init__(
        self,
        title: str,
        diff: str,
        tokens: Tokens,
        *,
        accept_text: str = "Save",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 460)
        view = QTextBrowser()
        view.setHtml(diff_html(diff, tokens))
        self.view = view
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.accept_button = buttons.addButton(accept_text, QDialogButtonBox.ButtonRole.AcceptRole)
        self.accept_button.setDefault(True)
        self.accept_button.setEnabled(bool(diff.strip()))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(view)
        layout.addWidget(buttons)


class FixPreviewDialog(QDialog):
    """What a fix will do (commands, files, diff), with Apply / Cancel."""

    def __init__(self, preview: FixPreview, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Apply fix")
        self.resize(680, 380)
        actions = "".join(f"<li><code>{escape(a)}</code></li>" for a in preview.actions)
        body = f"<h3>{escape(preview.summary)}</h3><ul>{actions}</ul>"
        if preview.diff:
            body += diff_html(preview.diff, tokens)
        self.view = QTextBrowser()
        self.view.setHtml(body)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("This is exactly what will happen:"))
        layout.addWidget(self.view)
        layout.addWidget(buttons)
