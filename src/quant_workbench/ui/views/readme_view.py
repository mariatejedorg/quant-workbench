"""README viewer: the project's README.md rendered from Markdown.

Shown in the same embedded browser as the dashboard, not Qt's rich text engine — that engine
draws every element (images especially) at its natural size regardless of the panel's own
width, and never fetches a remote image at all, so a README's badges and any oversized preview
screenshot always rendered wrong. A real browser engine reflows content to fit, and fetches
remote images itself, the same as any other page.
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from quant_workbench.ui.controller import AppController
from quant_workbench.ui.markdown import page_html
from quant_workbench.ui.theme import Tokens
from quant_workbench.ui.views.base import ProjectView

try:  # QtWebEngine ships in PySide6-Addons; the app must still start without it
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    HAS_WEB_ENGINE = True
except ImportError:  # pragma: no cover - depends on the installation
    HAS_WEB_ENGINE = False


if HAS_WEB_ENGINE:

    class _ReadmePage(QWebEnginePage):
        """Opens a clicked link in the system's own browser instead of navigating the README
        panel away from the README it exists to show."""

        def acceptNavigationRequest(
            self, url: QUrl | str, type: QWebEnginePage.NavigationType, isMainFrame: bool
        ) -> bool:
            if type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
                QDesktopServices.openUrl(QUrl(url))
                return False
            return super().acceptNavigationRequest(url, type, isMainFrame)


class ReadmeView(ProjectView):
    def __init__(
        self, controller: AppController, tokens: Tokens, parent: QWidget | None = None
    ) -> None:
        super().__init__(tokens, parent)
        self._controller = controller
        self.web: QWidget
        if HAS_WEB_ENGINE:
            self.web = QWebEngineView()
            self.web.setPage(_ReadmePage(self.web))
            # A page rendered from setHtml() is treated as local content, which by default
            # cannot load remote images at all — the README's own badges (shields.io) never
            # even attempt to fetch, unlike a real website (which is never "local").
            self.web.settings().setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
            )
        else:  # pragma: no cover - only without PySide6-Addons
            self.web = QLabel("The embedded browser (QtWebEngine) is not installed.")
        layout = QVBoxLayout(self)
        layout.addWidget(self.web)
        self.refresh()

    def refresh(self) -> None:
        if self._project is None:
            self._load("<p>Select a project to read its README.</p>")
            return
        text = self._controller.read_source(self._project.slug, "README.md")
        if text is None:
            self._load("<p>This project has no README.md.</p>")
            return
        # A trailing slash: without it, Qt treats the base URL as a *file*, and every
        # relative image path in the README (e.g. "outputs/preview.png") fails to resolve.
        base = QUrl.fromLocalFile(str(self._project.root) + "/")
        self._load(page_html(text, self._tokens), base)

    def _load(self, html: str, base: QUrl | None = None) -> None:
        if HAS_WEB_ENGINE:
            self.web.setHtml(html, base or QUrl())  # type: ignore[attr-defined]
