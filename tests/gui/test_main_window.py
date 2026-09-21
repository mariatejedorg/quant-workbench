"""The main window with the real engine behind it: real projects, real processes, real threads."""

from __future__ import annotations

import time
from pathlib import Path

import psutil
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from quant_workbench.application.settings import Settings, load_settings
from quant_workbench.domain.runs import RunStatus
from quant_workbench.ui.controller import AppController
from quant_workbench.ui.main_window import MainWindow, make_settings_store
from quant_workbench.ui.widgets.palette import CommandPalette

pytestmark = pytest.mark.gui

WAIT_MS = 90_000


def status_of(window: MainWindow, slug: str) -> RunStatus | None:
    return window.run_status(slug)


def wait_idle(qtbot, controller: AppController) -> None:  # type: ignore[no-untyped-def]
    qtbot.waitUntil(lambda: not controller.is_busy, timeout=WAIT_MS)


@pytest.fixture
def opened(qtbot, window: MainWindow, workspace: Path) -> MainWindow:  # type: ignore[no-untyped-def]
    assert window.open_workspace(workspace)
    return window


def row_of(window: MainWindow, slug: str) -> int:
    for row in range(window.project_filter.rowCount()):
        if window.project_filter.index(row, 0).data(Qt.ItemDataRole.UserRole + 1) == slug:
            return row
    raise AssertionError(slug)


# ------------------------------------------------------------------------ opening
def test_opening_a_workspace_fills_the_explorer_and_the_overview(opened: MainWindow) -> None:
    titles = [
        opened.project_filter.index(r, 0).data() for r in range(opened.project_filter.rowCount())
    ]

    assert sorted(titles) == ["Alpha", "Beta", "Broken", "Slow"]
    assert opened.selected_slug() is not None
    assert "has not been run yet" in opened.overview.toPlainText()
    assert "4 projects" in opened.workspace_label.text()
    assert all(opened.run_status(s) is None for s in ("alpha", "beta", "slow", "broken"))


def test_a_folder_that_is_not_a_workspace_is_reported_not_crashed(
    qtbot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    messages: list[tuple[str, str]] = []
    window._controller.message.connect(lambda level, text: messages.append((level, text)))

    ok = window.open_workspace(tmp_path / "does-not-exist")

    assert not ok
    assert window._controller.catalog is None
    assert messages[0][0] == "error"
    assert "is not a directory" in messages[0][1]
    assert warnings  # with nothing open, the user is told in a dialog, not only in the console


def test_filtering_the_project_list(opened: MainWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    qtbot.keyClicks(opened.filter_box, "bro")

    assert opened.project_filter.rowCount() == 1
    assert opened.project_filter.index(0, 0).data() == "Broken"
    opened.filter_box.clear()
    assert opened.project_filter.rowCount() == 4


def test_selecting_a_project_updates_the_overview_and_the_buttons(opened: MainWindow) -> None:
    assert opened.select_project("beta")

    text = opened.overview.toPlainText()
    assert "Beta" in text
    assert "alpha" in text  # its dependency
    assert opened.run_button.isEnabled()
    assert not opened.select_project("nobody")


# --------------------------------------------------------------------------- runs
def test_running_a_project_streams_its_output_and_marks_it_succeeded(
    qtbot, opened: MainWindow, controller: AppController
) -> None:
    opened.select_project("alpha")

    opened.commands.execute("run.selected")
    assert opened.action("run.cancel").isEnabled()  # busy while it runs
    qtbot.waitUntil(lambda: status_of(opened, "alpha") is RunStatus.SUCCEEDED, timeout=WAIT_MS)
    wait_idle(qtbot, controller)

    console = opened.console.plain_text()
    assert "[alpha] Sharpe: 1.75" in console
    assert "[alpha] María OK" in console
    assert opened.runs_model.rowCount() == 1
    assert opened.runs_model.index(0, 1).data() == "succeeded"
    assert "SUCCEEDED" in opened.overview.toPlainText()
    assert not opened.action("run.cancel").isEnabled()
    assert opened.progress.isHidden()
    assert status_of(opened, "beta") is None  # only the selected one ran


def test_running_a_project_and_its_dependents_follows_the_graph(
    qtbot, opened: MainWindow, controller: AppController
) -> None:
    opened.select_project("alpha")

    opened.commands.execute("run.impacted")
    qtbot.waitUntil(lambda: status_of(opened, "beta") is RunStatus.SUCCEEDED, timeout=WAIT_MS)
    wait_idle(qtbot, controller)

    assert status_of(opened, "alpha") is RunStatus.SUCCEEDED
    started = {
        opened.runs_model.run_at(r).project: opened.runs_model.run_at(r).started_at
        for r in range(opened.runs_model.rowCount())
    }
    assert started["alpha"] <= started["beta"]  # the dependency went first


def test_a_failing_project_is_marked_and_its_error_is_visible(
    qtbot, opened: MainWindow, controller: AppController
) -> None:
    opened.select_project("broken")

    opened.commands.execute("run.selected")
    qtbot.waitUntil(lambda: status_of(opened, "broken") is RunStatus.FAILED, timeout=WAIT_MS)
    wait_idle(qtbot, controller)

    assert "ValueError: boom" in opened.console.plain_text()
    assert "ValueError: boom" in opened.overview.toPlainText()
    assert opened.runs_model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole)


def test_run_all_respects_the_graph_and_reports_progress(
    qtbot, opened: MainWindow, controller: AppController
) -> None:
    progress: list[int] = []
    opened.progress.valueChanged.connect(progress.append)
    opened.select_project("alpha")
    messages: list[tuple[str, str]] = []
    controller.message.connect(lambda level, text: messages.append((level, text)))

    controller.cancel_all()  # nothing running: harmless
    # `slow` would run for two minutes, so run everything except it through the controller
    controller.run_projects(["alpha", "beta", "broken"])
    wait_idle(qtbot, controller)

    assert opened.run_status("beta") is RunStatus.SUCCEEDED
    assert opened.run_status("broken") is RunStatus.FAILED
    assert any("3 run(s) finished, 1 failed" in text for _, text in messages)
    assert progress  # the bar moved
    assert opened.progress.isHidden()  # ...and disappeared when the batch ended


def test_stopping_kills_the_whole_process_tree_and_leaves_no_orphans(
    qtbot, opened: MainWindow, controller: AppController, workspace: Path
) -> None:
    opened.select_project("slow")
    opened.commands.execute("run.selected")
    qtbot.waitUntil(lambda: "started" in opened.console.plain_text(), timeout=WAIT_MS)
    slow_root = str(workspace / "slow")
    children = [p for p in psutil.process_iter(["cmdline"]) if _runs_project(p, slow_root)]
    assert children, "the project's process should be running"

    opened.commands.execute("run.cancel")
    qtbot.waitUntil(lambda: status_of(opened, "slow") is RunStatus.CANCELLED, timeout=WAIT_MS)
    wait_idle(qtbot, controller)

    time.sleep(0.5)
    assert not [p for p in psutil.process_iter(["cmdline"]) if _runs_project(p, slow_root)]


def _runs_project(process: psutil.Process, root: str) -> bool:
    try:
        return any(root in part for part in (process.info["cmdline"] or []))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def test_the_console_can_follow_only_the_selected_project(
    qtbot, opened: MainWindow, controller: AppController
) -> None:
    controller.run_projects(["alpha", "broken"])
    wait_idle(qtbot, controller)
    opened.select_project("alpha")

    opened.only_selected.setChecked(True)

    text = opened.console.plain_text()
    assert "[alpha]" in text
    assert "[broken]" not in text
    opened.only_selected.setChecked(False)
    assert "[broken]" in opened.console.plain_text()


# ------------------------------------------------------------------------- doctor
def test_the_doctor_fills_the_problems_panel_and_the_overview(
    qtbot, opened: MainWindow, controller: AppController
) -> None:
    opened.select_project("alpha")

    opened.commands.execute("doctor.all")
    qtbot.waitUntil(lambda: controller.report is not None, timeout=WAIT_MS)
    wait_idle(qtbot, controller)

    assert opened.findings_model.rowCount() > 0
    assert opened.problems_dock.windowTitle().startswith("Problems (")
    assert "Explainability" in opened.overview.toPlainText()
    codes = {
        opened.findings_model.finding_at(r).code for r in range(opened.findings_model.rowCount())
    }
    assert "venv-missing" not in codes  # every synthetic project has its venv


def test_double_clicking_a_problem_selects_its_project(
    qtbot, opened: MainWindow, controller: AppController
) -> None:
    controller.run_doctor()
    qtbot.waitUntil(lambda: controller.report is not None, timeout=WAIT_MS)
    row = next(
        r
        for r in range(opened.findings_model.rowCount())
        if opened.findings_model.finding_at(r).project == "broken"
    )

    opened.problems_view.doubleClicked.emit(opened.findings_model.index(row, 0))

    assert opened.selected_slug() == "broken"


# --------------------------------------------------------------------- commands
def test_actions_are_enabled_only_when_they_make_sense(window: MainWindow) -> None:
    assert not window.action("run.all").isEnabled()  # no workspace yet
    assert not window.action("run.selected").isEnabled()
    assert not window.action("doctor.all").isEnabled()
    assert window.action("workspace.open").isEnabled()
    assert not window.run_button.isEnabled()


def test_every_command_has_a_menu_action_and_a_unique_shortcut(window: MainWindow) -> None:
    shortcuts = [c.shortcut for c in window.commands.all() if c.shortcut]

    assert len(shortcuts) == len(set(shortcuts))
    for command in window.commands.all():
        assert window.action(command.id).text() == command.title
    assert {"run.all", "run.selected", "doctor.all", "view.palette"} <= {
        c.id for c in window.commands.all()
    }


def test_the_palette_runs_commands(qtbot, opened: MainWindow, controller: AppController) -> None:
    opened.select_project("alpha")
    opened.show_palette()
    palette = opened.findChildren(CommandPalette)[-1]

    palette.type_text("selected proj")
    assert palette.visible_commands()[0] == "run.selected"
    palette.run_current()

    qtbot.waitUntil(lambda: status_of(opened, "alpha") is RunStatus.SUCCEEDED, timeout=WAIT_MS)
    wait_idle(qtbot, controller)


# ------------------------------------------------------------------ theme, settings
def test_switching_theme_restyles_the_whole_app(qtbot, opened: MainWindow) -> None:  # type: ignore[no-untyped-def]
    opened.commands.execute("view.theme.dark")

    assert opened.tokens.name == "dark"
    assert opened.tokens.window in QApplication.instance().styleSheet()  # type: ignore[union-attr]
    opened.commands.execute("view.theme.light")
    assert opened.tokens.name == "light"


def test_applying_settings_saves_them_and_restyles(
    qtbot, window: MainWindow, controller: AppController
) -> None:
    new = Settings(theme="dark", max_concurrency=5, expected_git_email="x@y.org")

    window.apply_settings(new)

    assert window.tokens.name == "dark"
    saved = load_settings(controller.container.paths.settings_file)
    assert saved.theme == "dark"
    assert saved.expected_git_email == "x@y.org"
    assert "Restart" in window.statusBar().currentMessage()


# ------------------------------------------------------------------------ layout
def test_the_layout_is_restored_by_the_next_window(
    qtbot, controller: AppController, tmp_path: Path
) -> None:
    store_path = tmp_path / "layout-a.ini"
    first = MainWindow(
        controller, controller.container.settings, store=make_settings_store(store_path)
    )
    qtbot.addWidget(first)
    first.show()
    first.resize(1000, 700)
    first.projects_dock.hide()
    first.save_layout()
    first.hide()

    second = MainWindow(
        controller, controller.container.settings, store=make_settings_store(store_path)
    )
    qtbot.addWidget(second)
    second.show()

    assert second.projects_dock.isHidden()
    # (the size itself is not asserted: the offscreen screen is smaller than the window)
    assert make_settings_store(store_path).value("window/geometry") is not None


# ---------------------------------------------------------------------------- close
def test_closing_while_projects_run_asks_first(
    qtbot,
    opened: MainWindow,
    controller: AppController,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened.select_project("slow")
    opened.commands.execute("run.selected")
    qtbot.waitUntil(lambda: "started" in opened.console.plain_text(), timeout=WAIT_MS)

    monkeypatch.setattr(opened, "_confirm_quit", lambda: False)
    assert not opened.close()  # refused: the user said no
    assert controller.is_busy
    assert opened.isVisible()

    monkeypatch.setattr(opened, "_confirm_quit", lambda: True)
    assert opened.close()  # stops the run (killing its process tree) and quits
    assert not controller.bridge.is_running


# ------------------------------------------------------------------ appearance
def test_the_window_renders_something_in_both_themes(
    qtbot, opened: MainWindow, tmp_path: Path
) -> None:
    for theme in ("light", "dark"):
        opened.apply_theme(theme)
        image = opened.grab().toImage()
        image.save(str(tmp_path / f"window-{theme}.png"))
        colours = {
            image.pixel(x, y)
            for x in range(0, image.width(), 37)
            for y in range(0, image.height(), 41)
        }

        assert image.width() > 800
        assert len(colours) > 5  # not a blank or single-colour window
