"""Fixtures for the desktop UI tests (Qt runs offscreen: no display needed)."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Must be set before Qt creates its platform plugin. On Windows the offscreen platform has no
# fonts of its own, so point it at the system's (otherwise every glyph is drawn as a box).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from quant_workbench.application.settings import Settings
from quant_workbench.bootstrap import Container, build_container
from quant_workbench.domain.paths import AppPaths
from quant_workbench.infrastructure.paths import ensure_app_dirs
from quant_workbench.ui.controller import AppController
from quant_workbench.ui.main_window import MainWindow, make_settings_store
from tests.support import manifest_toml, write_project

pytestmark = pytest.mark.gui

OK_MAIN = 'print("Sharpe: 1.75")\nprint("María OK")\n'
SLOW_MAIN = 'import time\nprint("started", flush=True)\ntime.sleep(120)\nprint("never printed")\n'
FAIL_MAIN = 'raise ValueError("boom")\n'


def make_venv(root: Path) -> None:
    """A real (pip-less, hence fast) virtual environment for a project."""
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(root / "venv")],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def workspace(accented_root: Path) -> Path:
    """``alpha`` (ok), ``beta`` (needs alpha), ``slow`` (sleeps) and ``broken`` (fails)."""
    layout = {
        "alpha": ((), OK_MAIN),
        "beta": (("alpha",), OK_MAIN),
        "slow": ((), SLOW_MAIN),
        "broken": ((), FAIL_MAIN),
    }
    for name, (deps, main) in layout.items():
        write_project(
            accented_root,
            name,
            refs=list(deps),
            manifest=manifest_toml(name, deps=deps),
            extra_files={"src/main.py": main},
        )
        make_venv(accented_root / name)
    return accented_root


@pytest.fixture
def container(tmp_path: Path) -> Iterator[Container]:
    """A container over a *file* database: the engine thread and the GUI thread both use it."""
    paths = AppPaths.under(tmp_path / "app-home")
    ensure_app_dirs(paths)
    settings = Settings(max_concurrency=3, run_timeout_seconds=120, max_retries=0)
    built = build_container(paths=paths, settings=settings, database=paths.database_file)
    yield built
    built.close()


@pytest.fixture
def controller(container: Container) -> Iterator[AppController]:
    made = AppController(container)
    yield made
    made.shutdown()


@pytest.fixture
def window(qtbot, controller: AppController, tmp_path: Path) -> Iterator[MainWindow]:  # type: ignore[no-untyped-def]
    shown = MainWindow(
        controller,
        controller.container.settings,
        settings_file=controller.container.paths.settings_file,
        store=make_settings_store(tmp_path / "layout.ini"),
    )
    qtbot.addWidget(shown)
    shown.show()
    yield shown
    shown.hide()  # closing would ask about running projects; the controller fixture stops them
