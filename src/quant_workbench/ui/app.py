"""Start-up of the desktop application (``quant-workbench`` and ``qw gui``)."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from quant_workbench import __version__
from quant_workbench.bootstrap import Container, build_container
from quant_workbench.ui.controller import AppController
from quant_workbench.ui.main_window import MainWindow, app_icon, make_settings_store


def create_app(argv: Sequence[str] = ()) -> QApplication:
    """The process's ``QApplication`` (the existing one when tests already made it)."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    app = QApplication(list(argv) or ["quant-workbench"])
    app.setApplicationName("Quant Workbench")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("QuantWorkbench")
    app.setWindowIcon(app_icon())
    return app


def create_window(
    container: Container,
    *,
    workspace: Path | None = None,
    store: QSettings | None = None,
) -> MainWindow:
    """Wire a controller and a main window to ``container`` and open the workspace, if known."""
    controller = AppController(container)
    window = MainWindow(
        controller,
        container.settings,
        settings_file=container.paths.settings_file,
        store=store or make_settings_store(),
    )
    target = workspace or container.settings.workspace_root
    if target is None:
        target = container.catalog.detect_workspace(Path.cwd())
    if target is not None:
        window.open_workspace(target)
    return window


def run_gui(container: Container, *, workspace: Path | None = None) -> int:
    """Show the main window and run Qt's event loop until it is closed."""
    app = create_app(sys.argv)
    window = create_window(container, workspace=workspace)
    window.show()
    return app.exec()


def main() -> int:
    """Entry point of the ``quant-workbench`` GUI script."""
    return run_gui(build_container())
