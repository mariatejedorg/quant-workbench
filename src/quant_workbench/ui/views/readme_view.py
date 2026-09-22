"""README viewer: the project's README.md rendered from Markdown."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QTextBrowser, QVBoxLayout, QWidget

from quant_workbench.ui.controller import AppController
from quant_workbench.ui.markdown import constrain_local_image_widths, render_markdown, stylesheet
from quant_workbench.ui.theme import Tokens
from quant_workbench.ui.views.base import ProjectView


class _ReadmeBrowser(QTextBrowser):
    """A ``QTextBrowser`` that also fetches the README's remote images.

    ``QTextBrowser.loadResource`` only ever resolves *local* resources, via ``setSearchPaths`` —
    it never fetches ``http(s)://`` URLs, so the shields.io badges every project README opens
    with always rendered as broken-image boxes. This fetches them in the background and re-renders
    once they arrive; ``document().resource()`` is checked first so an already-fetched badge is
    reused instead of being downloaded again on every re-render.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._network = QNetworkAccessManager(self)
        self._pending: set[str] = set()
        self._html = ""

    def setHtml(self, html: str) -> None:
        self._html = html
        super().setHtml(html)

    def loadResource(self, resource_type: int, name: QUrl | str) -> object:
        if (
            resource_type == QTextDocument.ResourceType.ImageResource.value
            and isinstance(name, QUrl)
            and name.scheme() in ("http", "https")
        ):
            cached = self.document().resource(resource_type, name)
            if cached is not None:
                return cached
            url = name.toString()
            if url not in self._pending:
                self._pending.add(url)
                reply = self._network.get(QNetworkRequest(name))
                reply.finished.connect(lambda: self._on_image_fetched(name, reply))
            return None
        return super().loadResource(resource_type, name)

    def _on_image_fetched(self, url: QUrl, reply: QNetworkReply) -> None:
        self._pending.discard(url.toString())
        data = reply.readAll()
        reply.deleteLater()
        image = QImage()
        if image.loadFromData(data) and self._html:
            self.document().addResource(QTextDocument.ResourceType.ImageResource.value, url, image)
            super().setHtml(self._html)  # re-render now that the badge is cached


class ReadmeView(ProjectView):
    def __init__(
        self, controller: AppController, tokens: Tokens, parent: QWidget | None = None
    ) -> None:
        super().__init__(tokens, parent)
        self._controller = controller
        self.browser = _ReadmeBrowser()
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
        html = constrain_local_image_widths(render_markdown(text), self._project.root)
        self.browser.setHtml(html)
