"""Dashboard viewer: the project's ``outputs/dashboard.html`` in an embedded browser."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QTimer, QUrl
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from quant_workbench.ui.controller import AppController
from quant_workbench.ui.plotly_cache import Fetcher, PlotlyCache, download
from quant_workbench.ui.theme import Tokens
from quant_workbench.ui.views.base import ProjectView

try:  # QtWebEngine ships in PySide6-Addons; the app must still start without it
    from PySide6.QtWebEngineWidgets import QWebEngineView

    HAS_WEB_ENGINE = True
except ImportError:  # pragma: no cover - depends on the installation
    HAS_WEB_ENGINE = False

_RELOAD_DELAY_MS = 500


class DashboardView(ProjectView):
    """Shows the dashboard and reloads it by itself when a run rewrites it."""

    def __init__(
        self,
        controller: AppController,
        tokens: Tokens,
        parent: QWidget | None = None,
        *,
        fetch: Fetcher = download,
    ) -> None:
        super().__init__(tokens, parent)
        self._controller = controller
        self._cache = PlotlyCache(controller.container.paths.cache_dir / "plotly", fetch)
        self._shown: Path | None = None
        self._downloading = False
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.reload_button = QPushButton("Reload")
        self.reload_button.clicked.connect(self.reload)
        self.pdf_button = QPushButton("Export PDF…")
        self.pdf_button.clicked.connect(self._ask_pdf)
        self.png_button = QPushButton("Save image…")
        self.png_button.clicked.connect(self._ask_png)
        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        for button in (self.reload_button, self.pdf_button, self.png_button):
            bar.addWidget(button)

        self.web: QWidget
        if HAS_WEB_ENGINE:
            self.web = QWebEngineView()
        else:  # pragma: no cover - only without PySide6-Addons
            self.web = QLabel("The embedded browser (QtWebEngine) is not installed.")
        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.web, 1)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._file_changed)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_RELOAD_DELAY_MS)
        self._debounce.timeout.connect(self.reload)
        self.refresh()

    # ------------------------------------------------------------------- loading
    def refresh(self) -> None:
        for watched in self._watcher.files():
            self._watcher.removePath(watched)
        self._shown = None
        path = self._project.dashboard_path if self._project else None
        if path is None or not path.is_file():
            self.status.setText(
                "Select a project."
                if self._project is None
                else "No dashboard yet: run the project."
            )
            self._set_enabled(False)
            self._blank()
            return
        self._watcher.addPath(str(path))
        self.reload()

    def reload(self) -> None:
        """Load (or reload) the dashboard from disk."""
        path = self._project.dashboard_path if self._project else None
        if path is None or not path.is_file():
            return
        html = path.read_text(encoding="utf-8", errors="replace")
        target, offline = path, False
        if "cdn.plot.ly" in html:
            localised, changed = self._cache.localise(html, download=False)
            if changed:
                copy = self._local_copy_path()
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_text(localised, encoding="utf-8")
                target = copy
            offline = bool(self._cache.missing(html))
            if offline:
                self._download_in_background(html)
        self._shown = target
        if HAS_WEB_ENGINE:
            self.web.load(QUrl.fromLocalFile(str(target)))  # type: ignore[attr-defined]
        note = (
            ""
            if not offline
            else " (using the online copy of Plotly; it will be cached when online)"
        )
        self.status.setText(f"{path.name}{note}")
        self._set_enabled(True)
        if str(path) not in self._watcher.files():  # some platforms drop a replaced file
            self._watcher.addPath(str(path))

    def _local_copy_path(self) -> Path:
        slug = self._project.slug if self._project else "dashboard"
        return self._controller.container.paths.cache_dir / "dashboards" / f"{slug}.html"

    def _download_in_background(self, html: str) -> None:
        """Fetch the missing scripts off the GUI thread, then reload to use the local copies."""
        if self._downloading:
            return
        self._downloading = True
        cache, slug = self._cache, self._project.slug if self._project else None

        def work() -> bool:
            return bool(cache.localise(html, download=True)[1])

        def done(changed: bool) -> None:
            self._downloading = False
            if changed and self._project is not None and self._project.slug == slug:
                self.reload()

        def failed(_error: BaseException) -> None:
            self._downloading = False

        self._controller.run_blocking(work, done, failed)

    def _blank(self) -> None:
        if HAS_WEB_ENGINE:
            self.web.setHtml("")  # type: ignore[attr-defined]

    def _set_enabled(self, on: bool) -> None:
        for button in (self.reload_button, self.pdf_button, self.png_button):
            button.setEnabled(on)

    def _file_changed(self, _path: str) -> None:
        self._debounce.start()  # several writes in a row become one reload

    def shown_file(self) -> Path | None:
        """The file the browser was told to load (the original, or the localised copy)."""
        return self._shown

    # ------------------------------------------------------------------- export
    def export_pdf(self, target: Path) -> None:
        if HAS_WEB_ENGINE:
            self.web.page().printToPdf(str(target))  # type: ignore[attr-defined]

    def export_png(self, target: Path) -> bool:
        return bool(self.web.grab().save(str(target)))

    def _ask_pdf(self) -> None:
        from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415 - only when the user asks

        name, _ = QFileDialog.getSaveFileName(self, "Export PDF", "dashboard.pdf", "PDF (*.pdf)")
        if name:
            self.export_pdf(Path(name))

    def _ask_png(self) -> None:
        from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

        name, _ = QFileDialog.getSaveFileName(self, "Save image", "dashboard.png", "PNG (*.png)")
        if name:
            self.export_png(Path(name))

    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
