"""The configuration form and the run history, end to end (scenario 2 of the plan).

Change a parameter in the form, review the diff, save, run, and see in the history exactly
what changed between the two runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QCheckBox, QLineEdit, QPlainTextEdit

from quant_workbench.application.config import ConfigPlan
from quant_workbench.domain.runs import RunStatus
from quant_workbench.ui.controller import AppController
from quant_workbench.ui.main_window import MainWindow
from tests.gui.conftest import CONFIG_TEXT

pytestmark = pytest.mark.gui

WAIT_MS = 90_000


@pytest.fixture
def opened(qtbot, window: MainWindow, workspace: Path) -> MainWindow:  # type: ignore[no-untyped-def]
    assert window.open_workspace(workspace)
    window.select_project("alpha")
    window.views.show("config")
    return window


def config_file(workspace: Path) -> Path:
    return workspace / "alpha" / "config" / "settings.py"


def wait_idle(qtbot, controller: AppController) -> None:  # type: ignore[no-untyped-def]
    qtbot.waitUntil(lambda: not controller.is_busy, timeout=WAIT_MS)


def accept_diffs(view, seen: list[str]) -> None:  # type: ignore[no-untyped-def]
    def confirm(plan: ConfigPlan) -> bool:
        seen.append(plan.diff)
        return True

    view.confirm_diff = confirm


# ----------------------------------------------------------------------- the form
def test_the_form_lists_every_constant_with_its_comment_as_help(opened: MainWindow) -> None:
    form = opened.views.config

    names = [f.constant.name for f in form._state.fields]

    assert names == ["WINDOW", "TICKER", "RANGE", "ENABLED", "HOLDINGS"]
    assert isinstance(form.editor("WINDOW"), QLineEdit)
    assert form.editor("WINDOW").text() == "252"  # type: ignore[attr-defined]
    assert isinstance(form.editor("ENABLED"), QCheckBox)
    assert form.editor("ENABLED").isChecked()  # type: ignore[attr-defined]
    assert isinstance(form.editor("HOLDINGS"), QPlainTextEdit)  # multi-line dict
    labels = "".join(w.text() for w in form.findChildren(type(form.notice)) if w.text())
    assert "Rolling window, in sessions." in labels
    assert "one trading year" in labels
    assert not form.save_button.isEnabled()


def test_a_project_without_configuration_says_so(opened: MainWindow, workspace: Path) -> None:
    # `slow` is a normal project too; remove its target to see the empty state
    (workspace / "slow" / "quant-project.toml").write_text(
        (workspace / "slow" / "quant-project.toml")
        .read_text(encoding="utf-8")
        .split("[[config_targets]]")[0],
        encoding="utf-8",
    )
    opened.reload_workspace()
    opened.select_project("slow")

    assert not opened.views.config._scroll.isVisibleTo(opened.views.config)
    assert "no editable configuration" in opened.views.config._placeholder.text()


def test_invalid_text_marks_the_editor_and_blocks_saving(opened: MainWindow) -> None:
    form = opened.views.config

    form.editor("WINDOW").setText("many")  # type: ignore[attr-defined]

    assert form.editor("WINDOW").property("invalid") is True
    assert "not a valid int" in form.editor("WINDOW").toolTip()
    assert not form.save_button.isEnabled()
    assert "WINDOW" in form.notice.text()
    form.editor("WINDOW").setText("100")  # type: ignore[attr-defined]
    assert form.editor("WINDOW").property("invalid") is False
    assert form.save_button.isEnabled()


def test_saving_shows_the_diff_writes_only_that_line_and_keeps_windows_line_endings(
    qtbot, opened: MainWindow, workspace: Path
) -> None:
    form, seen = opened.views.config, []
    accept_diffs(form, seen)

    form.editor("WINDOW").setText("126")  # type: ignore[attr-defined]
    form.editor("TICKER").setText("MSFT")  # type: ignore[attr-defined]
    form.save_button.click()

    expected = CONFIG_TEXT.replace("WINDOW = 252", "WINDOW = 126").replace('"AAPL"', '"MSFT"')
    assert config_file(workspace).read_bytes() == expected.encode("utf-8")
    assert "-WINDOW = 252" in seen[0]
    assert "+WINDOW = 126" in seen[0]
    assert '+TICKER = "MSFT"' in seen[0]
    assert "Saved WINDOW, TICKER" in form.notice.text()
    assert form.undo_button.isEnabled()
    assert not form.save_button.isEnabled()  # nothing left to save
    backups = list(
        (opened._controller.container.paths.data_dir / "backups" / "alpha").rglob("settings.py")
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == CONFIG_TEXT.encode("utf-8")


def test_a_cancelled_review_writes_nothing(opened: MainWindow, workspace: Path) -> None:
    form = opened.views.config
    form.confirm_diff = lambda plan: False

    form.editor("WINDOW").setText("1")  # type: ignore[attr-defined]
    form.save_button.click()

    assert config_file(workspace).read_bytes() == CONFIG_TEXT.encode("utf-8")
    assert form.save_button.isEnabled()  # the edit is still pending


def test_undo_and_redo_restore_and_reapply_the_file_exactly(
    opened: MainWindow, workspace: Path
) -> None:
    form = opened.views.config
    accept_diffs(form, [])
    form.editor("HOLDINGS").setPlainText('{\n    "A": 0.5,\n    "B": 0.4,\n    "C": 0.1,\n}')  # type: ignore[attr-defined]
    form.save_button.click()
    saved = config_file(workspace).read_bytes()
    assert b'"C": 0.1' in saved

    form.undo_button.click()
    assert config_file(workspace).read_bytes() == CONFIG_TEXT.encode("utf-8")
    assert form.editor("HOLDINGS").toPlainText().count("\n") == 3  # type: ignore[attr-defined]  # back to two entries

    form.redo_button.click()
    assert config_file(workspace).read_bytes() == saved


def test_a_file_edited_while_the_diff_was_open_is_not_overwritten(
    opened: MainWindow, workspace: Path
) -> None:
    form = opened.views.config

    def confirm_after_outside_edit(plan: ConfigPlan) -> bool:
        with config_file(workspace).open("ab") as handle:
            handle.write(b"# edited elsewhere\r\n")
        return True

    form.confirm_diff = confirm_after_outside_edit
    form.editor("WINDOW").setText("1")  # type: ignore[attr-defined]
    form.save_button.click()

    assert config_file(workspace).read_bytes().endswith(b"# edited elsewhere\r\n")
    assert b"WINDOW = 252" in config_file(workspace).read_bytes()  # our edit was not applied
    assert "changed on disk" in form.notice.text()


def test_undo_refuses_when_the_file_changed_afterwards(opened: MainWindow, workspace: Path) -> None:
    form = opened.views.config
    accept_diffs(form, [])
    form.editor("WINDOW").setText("5")  # type: ignore[attr-defined]
    form.save_button.click()
    with config_file(workspace).open("ab") as handle:
        handle.write(b"# later edit\r\n")

    form.undo_button.click()

    assert b"WINDOW = 5" in config_file(workspace).read_bytes()  # not rolled back over the edit
    assert "nothing was undone" in form.notice.text()


def test_discard_throws_away_unsaved_edits(opened: MainWindow) -> None:
    form = opened.views.config
    form.editor("TICKER").setText("XXX")  # type: ignore[attr-defined]

    form.discard_button.click()

    assert form.editor("TICKER").text() == "AAPL"  # type: ignore[attr-defined]
    assert not form.save_button.isEnabled()


def test_switching_theme_does_not_discard_unsaved_edits(opened: MainWindow) -> None:
    form = opened.views.config
    form.editor("TICKER").setText("KEEP")  # type: ignore[attr-defined]

    opened.apply_theme("dark")

    assert form.editor("TICKER").text() == "KEEP"  # type: ignore[attr-defined]


def test_the_form_follows_changes_made_outside(opened: MainWindow, workspace: Path) -> None:
    form = opened.views.config
    config_file(workspace).write_bytes(CONFIG_TEXT.replace("252", "300").encode("utf-8"))

    opened._controller.config_changed.emit("alpha")

    assert form.editor("WINDOW").text() == "300"  # type: ignore[attr-defined]


def test_a_boolean_is_edited_with_a_checkbox(opened: MainWindow, workspace: Path) -> None:
    form = opened.views.config
    accept_diffs(form, [])

    form.editor("ENABLED").setChecked(False)  # type: ignore[attr-defined]
    form.save_button.click()

    assert b"ENABLED = False" in config_file(workspace).read_bytes()


# ------------------------------------------------- scenario 2: edit, run, compare
def test_edit_save_run_and_see_the_configuration_diff_in_the_history(
    qtbot, opened: MainWindow, controller: AppController
) -> None:
    opened.commands.execute("run.selected")
    qtbot.waitUntil(lambda: opened.run_status("alpha") is RunStatus.SUCCEEDED, timeout=WAIT_MS)
    wait_idle(qtbot, controller)

    form = opened.views.config
    accept_diffs(form, [])
    form.run_after.setChecked(True)
    form.editor("WINDOW").setText("126")  # type: ignore[attr-defined]
    form.save_button.click()
    qtbot.waitUntil(
        lambda: not controller.is_busy and len(controller.list_runs("alpha")) == 2, timeout=WAIT_MS
    )
    wait_idle(qtbot, controller)

    opened.views.show("history")
    history = opened.views.history
    assert history.model.rowCount() == 2
    assert history.model.index(0, 1).data() == "succeeded"
    history.select_rows([0])  # the newest run, compared with the previous one
    html = history.comparison.toPlainText()
    assert "-WINDOW = 252" in html
    assert "+WINDOW = 126" in html
    assert "No metric changed" in html
    assert "Sharpe: 1.75" in history.log.toPlainText()


def test_the_history_shows_logs_metrics_and_a_two_run_comparison(
    qtbot, opened: MainWindow, controller: AppController
) -> None:
    controller.run_projects(["alpha"])
    wait_idle(qtbot, controller)
    controller.run_projects(["alpha"])
    wait_idle(qtbot, controller)
    opened.views.show("history")
    history = opened.views.history

    history.select_rows([0])
    assert "Sharpe: 1.75" in history.log.toPlainText()
    assert "sharpe=1.75" in history.model.index(0, 4).data()
    history.select_rows([0, 1])
    assert "The configuration is identical" in history.comparison.toPlainText()
    history.select_rows([0, 1])
    assert history.comparison_hint.isHidden()
    history.select_rows([])
    assert not history.comparison_hint.isHidden()


def test_the_trend_chart_plots_a_metric_over_successful_runs(
    qtbot, opened: MainWindow, controller: AppController
) -> None:
    for _ in range(3):
        controller.run_projects(["alpha"])
        wait_idle(qtbot, controller)
    opened.views.show("history")
    history = opened.views.history

    assert history.metric_box.currentText() == "sharpe"
    assert [value for _, value in history.trend_points()] == [1.75, 1.75, 1.75]
    assert len(history.chart.series()) == 2  # the line and its markers
    assert "3 successful run(s)" in history.trend_note.text()


def test_a_history_without_runs_has_an_explanatory_note(opened: MainWindow) -> None:
    opened.views.show("history")

    assert opened.views.history.model.rowCount() == 0
    assert "No successful run" in opened.views.history.trend_note.text()


def test_a_finished_run_refreshes_the_history_that_is_on_screen(
    qtbot, opened: MainWindow, controller: AppController
) -> None:
    opened.views.show("history")
    assert opened.views.history.model.rowCount() == 0

    opened.commands.execute("run.selected")
    qtbot.waitUntil(lambda: opened.views.history.model.rowCount() == 1, timeout=WAIT_MS)
    wait_idle(qtbot, controller)

    assert opened.views.history.model.index(0, 1).data() == "succeeded"
