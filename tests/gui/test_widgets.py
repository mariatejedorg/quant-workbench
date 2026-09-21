"""Models, console, palette and settings dialog, each on its own (no engine involved)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from quant_workbench.application.settings import Settings
from quant_workbench.domain.diagnostics import DoctorReport, Finding, Location, Severity
from quant_workbench.domain.run_events import RunFinished, RunOutput, RunQueued, RunStarted
from quant_workbench.domain.runs import JobKind, LogLevel, LogLine, LogStream, Run, RunStatus
from quant_workbench.ui.commands import Command, CommandRegistry
from quant_workbench.ui.models import (
    FindingsModel,
    ObjectRole,
    ProjectListModel,
    RunIdRole,
    RunsModel,
    SearchRole,
    SlugRole,
    StatusRole,
    badge_icon,
)
from quant_workbench.ui.settings_dialog import SettingsDialog
from quant_workbench.ui.theme import DARK, LIGHT
from quant_workbench.ui.widgets.console import MAX_LINES, ConsoleView, prefix_colour
from quant_workbench.ui.widgets.palette import CommandPalette
from tests.fakes import make_project

pytestmark = pytest.mark.gui

T0 = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def run(run_id: str, project: str, status: RunStatus, **kwargs: object) -> Run:
    return Run(
        id=run_id,  # type: ignore[arg-type]
        project=project,  # type: ignore[arg-type]
        kind=JobKind.RUN,
        status=status,
        queued_at=T0,
        **kwargs,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ project list
def test_the_project_list_shows_titles_badges_and_statuses(qtbot) -> None:  # type: ignore[no-untyped-def]
    model = ProjectListModel(LIGHT)
    projects = [make_project(Path("ws"), "alpha"), make_project(Path("ws"), "beta")]
    latest = {"alpha": run("r1", "alpha", RunStatus.SUCCEEDED)}

    model.set_projects(projects, latest.get)  # type: ignore[arg-type]

    assert model.rowCount() == 2
    first = model.index(0)
    assert first.data(Qt.ItemDataRole.DisplayRole) == "Alpha"
    assert first.data(SlugRole) == "alpha"
    assert first.data(StatusRole) == RunStatus.SUCCEEDED
    assert model.index(1).data(StatusRole) is None
    assert "alpha" in first.data(SearchRole)
    assert "last run: succeeded" in first.data(Qt.ItemDataRole.ToolTipRole)
    assert "never run" in model.index(1).data(Qt.ItemDataRole.ToolTipRole)
    assert not first.data(Qt.ItemDataRole.DecorationRole).isNull()
    assert model.project_at(1).slug == "beta"


def test_changing_a_status_notifies_only_when_it_changes(qtbot) -> None:  # type: ignore[no-untyped-def]
    model = ProjectListModel(LIGHT)
    model.set_projects([make_project(Path("ws"), "alpha")])
    changes: list[int] = []
    model.dataChanged.connect(lambda top, bottom: changes.append(top.row()))

    model.set_status("alpha", RunStatus.RUNNING)
    model.set_status("alpha", RunStatus.RUNNING)  # same value: nothing to tell
    model.set_status("nobody", RunStatus.FAILED)  # unknown project: ignored

    assert changes == [0]
    assert model.status("alpha") is RunStatus.RUNNING


def test_switching_theme_repaints_the_project_list(qtbot) -> None:  # type: ignore[no-untyped-def]
    model = ProjectListModel(LIGHT)
    model.set_projects([make_project(Path("ws"), "alpha")])
    changes: list[int] = []
    model.dataChanged.connect(lambda *_: changes.append(1))

    model.set_tokens(DARK)

    assert changes == [1]


def test_badges_are_round_and_coloured(qtbot) -> None:  # type: ignore[no-untyped-def]
    image = badge_icon("#ff0000").pixmap(12, 12).toImage()

    assert QColor(image.pixel(6, 6)).name() == "#ff0000"  # the middle is filled
    assert image.pixelColor(0, 0).alpha() == 0  # the corner is transparent


# ---------------------------------------------------------------------- runs table
def test_the_job_queue_follows_the_lifecycle_of_a_run(qtbot) -> None:  # type: ignore[no-untyped-def]
    model = RunsModel(LIGHT)
    queued = run("r1", "alpha", RunStatus.QUEUED)
    started = run("r1", "alpha", RunStatus.RUNNING, started_at=T0)
    finished = run(
        "r1",
        "alpha",
        RunStatus.SUCCEEDED,
        started_at=T0,
        finished_at=T0 + timedelta(seconds=3),
    )

    model.apply([RunQueued(run=queued)])
    assert model.rowCount() == 1
    assert model.active_run_ids() == ["r1"]
    model.apply([RunStarted(run=started)])
    model.apply([RunFinished(run=finished)])

    assert model.rowCount() == 1  # the same run updates in place
    assert model.index(0, 1).data() == "succeeded"
    assert model.index(0, 4).data() == "3.0 s"
    assert model.index(0, 0).data(RunIdRole) == "r1"
    assert model.index(0, 0).data(SlugRole) == "alpha"
    assert model.active_run_ids() == []
    assert model.headerData(1, Qt.Orientation.Horizontal) == "Status"
    assert model.headerData(1, Qt.Orientation.Vertical) is None
    assert model.run_at(0).status is RunStatus.SUCCEEDED


def test_new_runs_go_on_top_and_failures_are_explained_in_a_tooltip(qtbot) -> None:  # type: ignore[no-untyped-def]
    model = RunsModel(LIGHT)

    model.apply([RunQueued(run=run("r1", "alpha", RunStatus.QUEUED))])
    model.apply([RunFinished(run=run("r2", "beta", RunStatus.FAILED, failure="ValueError: boom"))])

    assert model.index(0, 0).data() == "beta"
    assert model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole) == "ValueError: boom"
    assert model.index(1, 0).data(Qt.ItemDataRole.ToolTipRole) is None
    assert model.index(0, 1).data(Qt.ItemDataRole.ForegroundRole).color().name() == LIGHT.error


def test_the_job_queue_is_capped(qtbot) -> None:  # type: ignore[no-untyped-def]
    model = RunsModel(LIGHT)

    model.apply([RunQueued(run=run(f"r{i}", "alpha", RunStatus.QUEUED)) for i in range(520)])

    assert model.rowCount() == 500
    assert model.index(0, 0).data(RunIdRole) == "r519"  # the newest survive


def test_other_events_are_ignored_by_the_job_queue(qtbot) -> None:  # type: ignore[no-untyped-def]
    model = RunsModel(LIGHT)
    line = LogLine(0, LogStream.STDOUT, LogLevel.INFO, "x", T0)

    model.apply([RunOutput(run_id="r1", project="alpha", line=line)])  # type: ignore[arg-type]

    assert model.rowCount() == 0


# ------------------------------------------------------------------------ findings
def test_the_problems_table_lists_the_reports_findings(qtbot) -> None:  # type: ignore[no-untyped-def]
    model = FindingsModel(LIGHT)
    findings = (
        Finding(
            "ssl-cert",
            "ssl-cert-missing",
            Severity.ERROR,
            "no CA bundle",
            project="alpha",  # type: ignore[arg-type]
            location=Location("src/data.py", 3),
            detail="TLS will fail",
            fix="copy-ca-bundle",
        ),
        Finding("environment-hazards", "pythonpath-set", Severity.INFO, "PYTHONPATH set"),
    )

    model.set_report(DoctorReport(findings))

    assert model.rowCount() == 2
    assert [model.index(0, c).data() for c in range(5)] == [
        "error",
        "alpha",
        "no CA bundle",
        "src/data.py:3",
        "yes",
    ]
    assert model.index(1, 1).data() == "workspace"
    assert model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole) == "TLS will fail"
    assert model.index(1, 0).data(Qt.ItemDataRole.ToolTipRole) == "PYTHONPATH set"
    assert model.index(0, 0).data(ObjectRole) is findings[0]
    assert model.index(0, 0).data(SlugRole) == "alpha"
    assert model.index(0, 0).data(Qt.ItemDataRole.ForegroundRole).color().name() == LIGHT.error
    assert model.finding_at(1) is findings[1]
    model.set_report(None)
    assert model.rowCount() == 0


# --------------------------------------------------------------------------- console
def test_the_console_prefixes_lines_with_their_project(qtbot) -> None:  # type: ignore[no-untyped-def]
    console = ConsoleView(LIGHT)
    qtbot.addWidget(console)

    console.add_line("alpha", "Sharpe: 1.7", LogStream.STDOUT, LogLevel.INFO)
    console.add_line("beta", "María OK", LogStream.STDOUT, LogLevel.INFO)

    assert console.plain_text() == "[alpha] Sharpe: 1.7\n[beta] María OK"
    assert console.line_count() == 2


def test_ansi_colours_become_text_formats_and_codes_are_not_shown(qtbot) -> None:  # type: ignore[no-untyped-def]
    console = ConsoleView(LIGHT)
    qtbot.addWidget(console)

    console.add_line("alpha", "\x1b[31mred\x1b[0m plain", LogStream.STDOUT, LogLevel.INFO)

    assert "\x1b" not in console.plain_text()
    assert console.plain_text() == "[alpha] red plain"
    fragments = {
        fragment.text(): fragment.charFormat().foreground().color().name()
        for block in [console.document().firstBlock()]
        for fragment in _fragments(block)
    }
    assert fragments["red"] == "#cd3131"
    assert fragments[" plain"] == LIGHT.console_fg


def _fragments(block):  # type: ignore[no-untyped-def]
    iterator = block.begin()
    while not iterator.atEnd():
        yield iterator.fragment()
        iterator += 1


def test_errors_and_system_messages_use_their_own_colours(qtbot) -> None:  # type: ignore[no-untyped-def]
    console = ConsoleView(LIGHT)
    qtbot.addWidget(console)

    console.add_line("alpha", "ValueError: boom", LogStream.STDERR, LogLevel.ERROR)
    console.add_line("alpha", "DeprecationWarning", LogStream.STDOUT, LogLevel.WARNING)
    console.add_system("2 run(s) finished")

    blocks = []
    block = console.document().firstBlock()
    while block.isValid():
        blocks.append(list(_fragments(block))[-1].charFormat())
        block = block.next()
    assert blocks[0].foreground().color().name() == LIGHT.error
    assert blocks[1].foreground().color().name() == LIGHT.warning
    assert blocks[2].foreground().color().name() == LIGHT.ink_muted
    assert blocks[2].fontItalic()


def test_the_filter_shows_one_project_and_restores_everything(qtbot) -> None:  # type: ignore[no-untyped-def]
    console = ConsoleView(LIGHT)
    qtbot.addWidget(console)
    for project in ("alpha", "beta", "alpha"):
        console.add_line(project, f"line of {project}", LogStream.STDOUT, LogLevel.INFO)

    console.set_filter("alpha")
    assert console.plain_text().count("line of") == 2
    assert "beta" not in console.plain_text()
    console.add_line("beta", "hidden while filtered", LogStream.STDOUT, LogLevel.INFO)
    assert "hidden" not in console.plain_text()

    console.set_filter(None)
    assert console.plain_text().count("line of") == 3
    assert "hidden while filtered" in console.plain_text()  # it was kept, only not shown
    assert console.active_filter is None
    console.set_filter(None)  # no-op


def test_the_console_keeps_a_bounded_history_and_can_be_cleared(qtbot) -> None:  # type: ignore[no-untyped-def]
    console = ConsoleView(LIGHT)
    qtbot.addWidget(console)

    # both the widget and the replay buffer are bounded, so a chatty project cannot eat memory
    assert console.maximumBlockCount() == MAX_LINES
    assert console._entries.maxlen == MAX_LINES
    for number in range(600):
        console.add_line("a", str(number), LogStream.STDOUT, LogLevel.INFO)
    assert console.line_count() == 600

    console.clear_console()

    assert console.line_count() == 0
    assert console.plain_text() == ""


def test_the_console_follows_new_output_unless_the_user_scrolled_up(qtbot) -> None:  # type: ignore[no-untyped-def]
    console = ConsoleView(LIGHT)
    qtbot.addWidget(console)
    console.resize(500, 120)
    console.show()
    for number in range(200):
        console.add_line("a", f"line {number}", LogStream.STDOUT, LogLevel.INFO)
    bar = console.verticalScrollBar()
    assert bar.value() == bar.maximum()  # following

    bar.setValue(0)  # the user scrolls up to read
    console.add_line("a", "new line", LogStream.STDOUT, LogLevel.INFO)
    assert bar.value() == 0  # left alone

    console.scroll_to_end()
    assert bar.value() == bar.maximum()


def test_prefix_colours_are_stable_and_readable(qtbot) -> None:  # type: ignore[no-untyped-def]
    assert prefix_colour("alpha", LIGHT) == prefix_colour("alpha", LIGHT)
    assert prefix_colour("alpha", LIGHT).lightnessF() < prefix_colour("alpha", DARK).lightnessF()
    assert len({prefix_colour(f"p{i}", LIGHT).name() for i in range(30)}) > 1


# --------------------------------------------------------------------------- palette
def registry_with(calls: list[str]) -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(
        Command("run.all", "Run all projects", lambda: calls.append("all"), "Ctrl+R", "Run")
    )
    registry.register(
        Command("run.sel", "Run selected", lambda: calls.append("sel"), category="Run")
    )
    registry.register(Command("doctor", "Diagnose", lambda: calls.append("doc"), category="Doctor"))
    registry.register(
        Command("off", "Disabled thing", lambda: calls.append("off"), enabled=lambda: False)
    )
    return registry


def test_the_palette_filters_ranks_and_hides_disabled_commands(qtbot) -> None:  # type: ignore[no-untyped-def]
    palette = CommandPalette(registry_with([]))
    qtbot.addWidget(palette)

    assert palette.visible_commands() == ["doctor", "run.all", "run.sel"]
    palette.type_text("run")
    assert palette.visible_commands() == ["run.all", "run.sel"]
    palette.type_text("dgn")
    assert palette.visible_commands() == ["doctor"]
    palette.type_text("zzz")
    assert palette.visible_commands() == []
    assert not palette.run_current()


def test_enter_runs_the_highlighted_command_and_closes(qtbot) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []
    palette = CommandPalette(registry_with(calls))
    qtbot.addWidget(palette)
    palette.show()

    palette.type_text("run")
    qtbot.keyClick(palette._input, Qt.Key.Key_Down)  # second result
    qtbot.keyClick(palette._input, Qt.Key.Key_Return)

    assert calls == ["sel"]
    assert not palette.isVisible()


def test_arrows_stay_inside_the_list(qtbot) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []
    palette = CommandPalette(registry_with(calls))
    qtbot.addWidget(palette)
    palette.show()
    palette.type_text("run")

    for _ in range(5):
        qtbot.keyClick(palette._input, Qt.Key.Key_Down)
    for _ in range(9):
        qtbot.keyClick(palette._input, Qt.Key.Key_Up)
    qtbot.keyClick(palette._input, Qt.Key.Key_Enter)

    assert calls == ["all"]  # clamped at the top


# ----------------------------------------------------------------------- settings
def test_the_settings_dialog_returns_the_edited_values(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = SettingsDialog(Settings(expected_git_email="a@b.org", theme="light"))
    qtbot.addWidget(dialog)

    dialog.concurrency.setValue(7)
    dialog.timeout.setValue(300)
    dialog.retries.setValue(2)
    dialog.theme.setCurrentText("dark")
    dialog.git_email.setText("maria@example.org")
    dialog.git_name.setText("María")
    dialog.git_host.setText("")
    dialog.workspace.setText("C:/somewhere")
    dialog.disabled.setText("determinism, outputs ,")
    dialog._accept_if_valid()

    new = dialog.result_settings()
    assert new is not None
    assert (new.max_concurrency, new.run_timeout_seconds, new.max_retries) == (7, 300, 2)
    assert new.theme == "dark"
    assert new.expected_git_email == "maria@example.org"
    assert new.expected_git_name == "María"
    assert new.expected_git_remote_host is None
    assert new.workspace_root == Path("C:/somewhere")
    assert new.disabled_checkers == ("determinism", "outputs")
    assert dialog.result() == 1  # accepted


def test_an_invalid_value_keeps_the_dialog_open_and_explains(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = SettingsDialog(Settings())
    qtbot.addWidget(dialog)
    dialog.show()

    dialog.theme.addItem("neon")
    dialog.theme.setCurrentText("neon")
    dialog._accept_if_valid()

    assert dialog.result_settings() is None
    assert dialog.isVisible()
    assert "theme" in dialog._error.text()


def test_the_dialog_starts_from_the_current_settings(qtbot) -> None:  # type: ignore[no-untyped-def]
    current = Settings(max_concurrency=4, expected_git_name="Ana", disabled_checkers=("outputs",))

    dialog = SettingsDialog(current)
    qtbot.addWidget(dialog)

    assert dialog.concurrency.value() == 4
    assert dialog.git_name.text() == "Ana"
    assert dialog.disabled.text() == "outputs"
    assert dialog.workspace.text() == ""
