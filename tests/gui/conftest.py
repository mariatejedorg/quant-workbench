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
# Chromium (QtWebEngine) inside a headless test process: no sandbox, no GPU, and no reliance on
# /dev/shm (CI containers cap it well below what Chromium wants, a common cause of crashes there).
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu --disable-dev-shm-usage"
)
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest

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


CONFIG_TEXT = (
    "# Rolling window, in sessions.\r\n"
    "WINDOW = 252  # one trading year\r\n"
    'TICKER = "AAPL"\r\n'
    "RANGE = (0.7, 1.3)\r\n"
    "ENABLED = True\r\n"
    "HOLDINGS = {\r\n"
    '    "A": 0.5,\r\n'
    '    "B": 0.5,\r\n'
    "}\r\n"
)
MANIFEST_EXTRAS = (
    '\n[[config_targets]]\nfile = "config/settings.py"\nsymbols = []\n'
    '\n[[metrics]]\nname = "sharpe"\npattern = \'Sharpe:\\s*([\\d.]+)\'\nkind = "float"\n'
)
#: A bare ``except`` on a known line, so the doctor produces a finding with a location.
UTIL_TEXT = "def risky():\n    try:\n        return 1\n    except:\n        return 0\n"


@pytest.fixture
def workspace(accented_root: Path) -> Path:
    """``alpha`` (ok), ``beta`` (needs alpha), ``slow`` (sleeps) and ``broken`` (fails).

    Each has a configuration file (CRLF, like the real ones), a README, a metric extractor and
    a real virtual environment. ``alpha`` also has seeded defects for the doctor to find: it
    uses yfinance without a CA bundle (``beta`` has one to copy) and a bare ``except``.
    """
    layout = {
        "alpha": ((), OK_MAIN),
        "beta": (("alpha",), OK_MAIN),
        "slow": ((), SLOW_MAIN),
        "broken": ((), FAIL_MAIN),
    }
    for name, (deps, main) in layout.items():
        extra = {"src/main.py": main, "README.md": f"# {name.title()}\n\nSome **notes**.\n"}
        if name == "alpha":
            extra["src/data.py"] = "import yfinance\n\nDATA = yfinance\n"
            extra["src/util.py"] = UTIL_TEXT
        if name == "beta":
            extra[".certs/cacert.pem"] = "-----BEGIN CERTIFICATE-----\nPEM\n"
        write_project(
            accented_root,
            name,
            refs=list(deps),
            manifest=manifest_toml(name, deps=deps) + MANIFEST_EXTRAS,
            extra_files=extra,
        )
        config = accented_root / name / "config" / "settings.py"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_bytes(CONFIG_TEXT.encode("utf-8"))  # exact bytes: CRLF must survive edits
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
    # Every window builds a DashboardView, hence a QWebEngineView, whether or not a test ever
    # shows it. Left to Python's GC and Qt's default parent-child deletion, ~100 of these across
    # the suite are torn down in a chaotic order at interpreter exit, which crashes on Linux
    # ("Release of profile requested but WebEnginePage still not deleted"). Deleting each window
    # deterministically, with its own short pump of the event loop for the deferred deletion (and
    # WebEngine's own asynchronous teardown) to actually run, avoids that pile-up. On its own this
    # reduced the crash's rate on Linux CI but did not eliminate it; see
    # ``_drain_qt_engine_after_session`` below for the rest of the fix.
    shown.deleteLater()
    qtbot.wait(150)


@pytest.fixture(scope="session", autouse=True)
def _shut_qt_engine_down_properly(qapp) -> Iterator[None]:  # type: ignore[no-untyped-def]
    """Give Qt (and WebEngine) a real quit sequence, instead of an abrupt interpreter exit.

    pytest-qt's session-wide ``QApplication`` never runs ``exec()``, so Qt's ``aboutToQuit``
    signal — the hook QtWebEngine's global context uses to shut Chromium's browser process down
    in an orderly way, pages before their profile — never fires. Without it, that teardown instead
    happens ad hoc, at whatever point Python's own finalization gets around to deleting the last
    reference, which is exactly when Linux CI segfaults, right after every test has already passed
    ("Release of profile requested but WebEnginePage still not deleted"). A zero-delay ``exec()``
    triggers a real quit sequence — ``aboutToQuit`` and all — without actually blocking; the
    ``qWait`` afterwards then gives whatever that sequence kicked off (WebEngine's page/profile
    teardown is itself asynchronous IPC to a renderer process) a little more time to finish.
    """
    yield
    QTimer.singleShot(0, qapp.quit)
    qapp.exec()
    QTest.qWait(500)
