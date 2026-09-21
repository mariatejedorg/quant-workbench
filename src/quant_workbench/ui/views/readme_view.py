"""README viewer: the project's README.md rendered from Markdown."""

from __future__ import annotations

from PySide6.QtWidgets import QTextBrowser, QVBoxLayout, QWidget

from quant_workbench.ui.controller import AppController
from quant_workbench.ui.markdown import render_markdown, stylesheet
from quant_workbench.ui.theme import Tokens
from quant_workbench.ui.views.base import ProjectView


class ReadmeView(ProjectView):
    def __init__(
        self, controller: AppController, tokens: Tokens, parent: QWidget | None = None
    ) -> None:
        super().__init__(tokens, parent)
        self._controller = controller
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.browser)
        self.refresh()

    def refresh(self) -> None:
        self.browser.document().setDefaultStyleSheet(stylesheet(self._tokens))
        if self._project is None:
            self.browser.setHtml("<p>Select a project to read its README.</p>")
            return
        text = self._controller.read_source(self._project.slug, "README.md")
        if text is None:
            self.browser.setHtml("<p>This project has no README.md.</p>")
            return
        self.browser.setSearchPaths([str(self._project.root)])
        self.browser.setHtml(render_markdown(text))
