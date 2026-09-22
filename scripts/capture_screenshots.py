"""Regenerate the screenshots in ``docs/images`` from the real workspace.

Run it from the repository root, with the GUI extras installed::

    python scripts/capture_screenshots.py [WORKSPACE]

It opens the main window *offscreen* (no display needed) on a throw-away application home, so
it never touches the user's settings, history or database, and it only reads the projects.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Must be set before Qt creates its platform plugin (same reasons as tests/gui/conftest.py).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu --disable-dev-shm-usage"
)
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import QEventLoop, QTimer

from quant_workbench.application.settings import Settings
from quant_workbench.bootstrap import build_container
from quant_workbench.domain.paths import AppPaths
from quant_workbench.infrastructure.paths import ensure_app_dirs
from quant_workbench.ui.app import create_app, create_window
from quant_workbench.ui.main_window import MainWindow, make_settings_store

IMAGES = Path(__file__).resolve().parent.parent / "docs" / "images"
WINDOW_SIZE = (1440, 1000)
#: (file name, tab, project slug or None for the first one, seconds to let it load)
SHOTS = (
    ("overview", "overview", None, 1),
    ("config", "config", "options-pricing-implied-volatility-surface", 2),
    ("dashboard", "dashboard", "capstone-multi-strategy-portfolio", 12),
    ("graph", "graph", "capstone-multi-strategy-portfolio", 2),
    ("study", "study", "markowitz-efficient-frontier", 2),
)


def pump(milliseconds: int) -> None:
    """Let Qt process events (layout, painting, web page loading) for a while."""
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def capture(window: MainWindow, name: str) -> None:
    path = IMAGES / f"{name}.png"
    image = window.grab().toImage()
    # The status bar shows the workspace's absolute path (a user name and folders): leave it out.
    image.copy(0, 0, image.width(), image.height() - window.statusBar().height()).save(str(path))
    print(f"wrote {path}")


def main(workspace: Path) -> int:
    IMAGES.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as home:
        paths = AppPaths.under(Path(home))
        ensure_app_dirs(paths)
        container = build_container(paths=paths, settings=Settings(), database=paths.database_file)
        app = create_app()
        window = create_window(
            container, workspace=workspace, store=make_settings_store(Path(home) / "layout.ini")
        )
        window.resize(*WINDOW_SIZE)
        window.show()
        pump(1500)
        window.commands.execute("doctor.all")  # fills the Problems dock
        pump(6000)
        known = (
            {p.slug for p in window.controller.catalog.projects}
            if window.controller.catalog
            else set()
        )
        for name, tab, slug, seconds in SHOTS:
            if slug in known:
                window.select_project(slug)
            window.views.show(tab)
            pump(seconds * 1000)
            capture(window, name)
        window.hide()
        window.controller.shutdown()
        container.close()
        app.quit()
    return 0


if __name__ == "__main__":
    default = Path(__file__).resolve().parent.parent.parent
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else default))
