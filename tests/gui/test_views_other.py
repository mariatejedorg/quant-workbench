"""Code viewer, README, dependency graph, Git panel, dashboard viewer and the problems' fixes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from quant_workbench.domain.runs import RunStatus
from quant_workbench.ui.controller import AppController
from quant_workbench.ui.main_window import MainWindow
from tests.gui.conftest import CONFIG_TEXT, UTIL_TEXT

pytestmark = pytest.mark.gui

WAIT_MS = 90_000


@pytest.fixture
def opened(qtbot, window: MainWindow, workspace: Path) -> MainWindow:  # type: ignore[no-untyped-def]
    assert window.open_workspace(workspace)
    window.select_project("alpha")
    return window


def wait_idle(qtbot, controller: AppController) -> None:  # type: ignore[no-untyped-def]
    qtbot.waitUntil(lambda: not controller.is_busy, timeout=WAIT_MS)


def web_text(qtbot, web) -> str:  # type: ignore[no-untyped-def]
    """The embedded browser's rendered text (``toPlainText`` is asynchronous in QtWebEngine)."""
    result: list[str] = []
    web.page().toPlainText(result.append)
    qtbot.waitUntil(lambda: bool(result), timeout=30_000)
    return result[0]


# --------------------------------------------------------------------- code view
def test_the_code_view_lists_sources_config_and_readme(opened: MainWindow) -> None:
    opened.views.show("code")
    code = opened.views.code

    files = [code.files.item(i).text() for i in range(code.files.count())]

    assert {"src/main.py", "src/util.py", "config/settings.py", "README.md"} <= set(files)
    assert code.current_file() is not None
    assert code.editor.isReadOnly()  # opening code must never be a way to break it


def test_opening_a_file_shows_it_with_an_outline_and_jumps_to_a_line(opened: MainWindow) -> None:
    code = opened.views.code
    opened.views.open_code("src/util.py", 4)

    assert opened.tabs.currentWidget() is code
    assert code.current_file() == "src/util.py"
    assert "def risky" in code.editor.toPlainText()
    assert code.editor.current_line() == 4
    assert code.symbols.count() == 1
    assert "risky  (function)" in code.symbols.item(0).text()
    code.symbols.itemClicked.emit(code.symbols.item(0))
    assert code.editor.current_line() == 1


def test_a_missing_file_is_reported(opened: MainWindow) -> None:
    code = opened.views.code
    opened.views.show("code")

    assert not code.open_file("src/nope.py")
    assert "does not exist" in code.status.text()


def test_search_finds_next_and_previous_and_wraps_around(opened: MainWindow) -> None:
    code = opened.views.code
    opened.views.open_code("config/settings.py", None)

    code.search.setText("ENABLED")
    assert code.find_next()
    assert code.editor.current_line() == 5
    assert code.find_next()  # only one match: wraps to the same place
    assert code.editor.current_line() == 5
    assert code.find_previous()
    code.search.setText("no such text")
    assert not code.find_next()
    code.search.setText("")
    assert not code.find_next()


def test_editing_saves_with_a_backup_and_keeps_windows_line_endings(
    opened: MainWindow, workspace: Path
) -> None:
    code = opened.views.code
    opened.views.open_code("config/settings.py", None)
    assert not code.save_button.isEnabled()

    code.edit_box.setChecked(True)
    code.editor.setPlainText(code.editor.toPlainText().replace("252", "300"))
    assert code.save_button.isEnabled()
    assert code.save()

    on_disk = (workspace / "alpha" / "config" / "settings.py").read_bytes()
    assert on_disk == CONFIG_TEXT.replace("252", "300").encode("utf-8")
    backups = list(
        (opened._controller.container.paths.data_dir / "backups" / "alpha").rglob("settings.py")
    )
    assert backups
    assert not code.save_button.isEnabled()


def test_a_python_edit_that_does_not_parse_is_refused(opened: MainWindow, workspace: Path) -> None:
    code = opened.views.code
    opened.views.open_code("src/util.py", None)
    code.edit_box.setChecked(True)

    code.editor.setPlainText("def broken(:\n")

    assert not code.save()
    assert (workspace / "alpha" / "src" / "util.py").read_text(encoding="utf-8") == UTIL_TEXT
    assert "would not be valid Python" in code.status.text()


def test_saving_over_a_file_that_changed_meanwhile_is_refused(
    opened: MainWindow, workspace: Path
) -> None:
    code = opened.views.code
    opened.views.open_code("src/util.py", None)
    code.edit_box.setChecked(True)
    code.editor.setPlainText(UTIL_TEXT + "# mine\n")
    (workspace / "alpha" / "src" / "util.py").write_text(UTIL_TEXT + "# theirs\n", encoding="utf-8")

    assert not code.save()

    assert "changed on disk" in code.status.text()
    assert "# theirs" in (workspace / "alpha" / "src" / "util.py").read_text(encoding="utf-8")


def test_the_highlighter_colours_the_visible_text(opened: MainWindow) -> None:
    code = opened.views.code
    opened.views.open_code("src/util.py", None)
    block = code.editor.document().findBlockByNumber(0)  # "def risky():"

    formats = block.layout().formats()

    assert formats, "the `def` keyword and the function name are coloured"


def test_the_gutter_grows_with_the_number_of_lines(opened: MainWindow) -> None:
    editor = opened.views.code.editor

    editor.load("x\n" * 5, "a.txt")
    narrow = editor.gutter_width()
    editor.load("x\n" * 5000, "a.txt")

    assert editor.gutter_width() > narrow


# ------------------------------------------------------------------------ README
def test_the_readme_is_rendered_from_markdown(qtbot, opened: MainWindow) -> None:  # type: ignore[no-untyped-def]
    readme = opened.views.readme
    with qtbot.waitSignal(readme.web.loadFinished, timeout=30_000):  # type: ignore[attr-defined]
        opened.views.show("readme")

    text = web_text(qtbot, readme.web)
    assert "Alpha" in text
    assert "notes" in text
    assert "**" not in text  # the markup was interpreted


def test_a_project_without_a_readme_says_so(qtbot, opened: MainWindow, workspace: Path) -> None:  # type: ignore[no-untyped-def]
    (workspace / "beta" / "README.md").unlink()
    readme = opened.views.readme
    with qtbot.waitSignal(readme.web.loadFinished, timeout=30_000):  # type: ignore[attr-defined]
        opened.select_project("beta")
        opened.views.show("readme")

    assert "no README.md" in web_text(qtbot, readme.web)


def test_a_remote_readme_badge_actually_loads(
    qtbot,  # type: ignore[no-untyped-def]
    opened: MainWindow,
    workspace: Path,
) -> None:
    """Regression, in two parts. First, a custom QTextBrowser subclass used to fetch README
    badges (shields.io images) itself, and a bug in that code could freeze or crash the app the
    instant a README opened (every project's opens with four) — fixed by switching to this real
    embedded browser, which fetches images the same way it fetches everything else on any page.
    Second, that browser treats a page built from ``setHtml()`` as *local* content, and by
    default local content cannot load remote images at all (or anything else remote) — the
    badges silently never even attempted to load, "complete" but 0x0. ``ReadmeView`` now opts
    into ``LocalContentCanAccessRemoteUrls`` explicitly; this checks the image's real rendered
    size, not just that the page loaded without crashing.
    """
    (workspace / "alpha" / "README.md").write_text(
        "# Alpha\n\n![badge](https://img.shields.io/badge/x-y-blue)\n", encoding="utf-8"
    )
    readme = opened.views.readme
    with qtbot.waitSignal(readme.web.loadFinished, timeout=30_000):  # type: ignore[attr-defined]
        opened.select_project("alpha")
        opened.views.show("readme")

    assert "Alpha" in web_text(qtbot, readme.web)

    sizes: list[str] = []
    readme.web.page().runJavaScript(  # type: ignore[attr-defined]
        "JSON.stringify(Array.from(document.querySelectorAll('img'))"
        ".map(img => [img.naturalWidth, img.naturalHeight]))",
        sizes.append,
    )
    qtbot.waitUntil(lambda: bool(sizes), timeout=30_000)
    [[width, height]] = json.loads(sizes[0])
    assert width > 0 and height > 0  # not the silently-failed "complete but 0x0" state


def test_a_table_of_oversized_preview_images_still_fits_the_panel(
    qtbot,  # type: ignore[no-untyped-def]
    opened: MainWindow,
    workspace: Path,
) -> None:
    """The common README pattern across the portfolio (a multi-column table, each cell a
    dashboard-preview screenshot) used to blow the panel out completely: Qt's rich text engine
    draws every ``<img>`` at its native pixel size, several images wide. A real browser's own
    table and image layout shrinks each cell's image to fit instead, the same as any web page.
    """
    for name in ("x.png", "y.png", "z.png"):
        image = QImage(1600, 900, QImage.Format.Format_RGB32)
        image.fill(0xFFAA00)
        image.save(str(workspace / "alpha" / name))
    (workspace / "alpha" / "README.md").write_text(
        "# Alpha\n\n| a | b | c |\n|---|---|---|\n| ![x](x.png) | ![y](y.png) | ![z](z.png) |\n",
        encoding="utf-8",
    )
    readme = opened.views.readme
    with qtbot.waitSignal(readme.web.loadFinished, timeout=30_000):  # type: ignore[attr-defined]
        opened.select_project("alpha")
        opened.views.show("readme")

    # QtWebEngine's result callback loses a plain JS array in the QVariant conversion (it comes
    # back as an empty string); JSON round-tripping it through a string sidesteps that entirely.
    raw: list[str] = []
    readme.web.page().runJavaScript(  # type: ignore[attr-defined]
        "JSON.stringify(Array.from(document.querySelectorAll('img'))"
        ".map(img => img.getBoundingClientRect().width))",
        raw.append,
    )
    qtbot.waitUntil(lambda: bool(raw), timeout=30_000)
    widths = json.loads(raw[0])
    assert len(widths) == 3
    assert all(0 < w < 1600 for w in widths)  # shrunk to fit their column, not their native size


# ------------------------------------------------------------------------- graph
def test_the_graph_draws_projects_and_their_dependencies(opened: MainWindow) -> None:
    graph = opened.views.graph

    assert sorted(graph.node_slugs()) == ["alpha", "beta", "broken", "slow"]
    assert graph.edge_pairs() == [("alpha", "beta")]  # beta needs alpha


def test_clicking_a_node_selects_the_project_and_shows_its_impact(opened: MainWindow) -> None:
    graph = opened.views.graph

    graph.click_node("alpha")

    assert opened.selected_slug() == "alpha"
    assert graph.impacted() == ["alpha", "beta"]
    assert "beta" in graph.summary.text()
    assert graph.impact_button.isEnabled()
    graph.click_node("broken")
    assert graph.impacted() == ["broken"]
    assert "no other project" in graph.summary.text()


def test_rerun_impacted_from_the_graph_runs_the_dependents_too(
    qtbot, opened: MainWindow, controller: AppController
) -> None:
    opened.views.graph.click_node("alpha")

    opened.views.graph.impact_button.click()
    qtbot.waitUntil(lambda: opened.run_status("beta") is RunStatus.SUCCEEDED, timeout=WAIT_MS)
    wait_idle(qtbot, controller)

    assert opened.run_status("alpha") is RunStatus.SUCCEEDED


def test_nodes_follow_the_status_of_their_last_run(opened: MainWindow) -> None:
    graph = opened.views.graph

    graph.set_status("alpha", RunStatus.FAILED)

    assert graph._nodes["alpha"].badge.brush().color().name() == opened.tokens.error


def test_selecting_in_the_explorer_highlights_the_node(opened: MainWindow) -> None:
    opened.select_project("beta")

    assert opened.views.graph._selected == "beta"


# --------------------------------------------------------------------------- git
@pytest.fixture
def repo(opened: MainWindow, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``alpha`` as a real git repository with one commit and one uncommitted change."""
    global_config = workspace.parent / "global-gitconfig"
    global_config.write_text("[user]\n\temail = carlos@global.example\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = workspace / "alpha"

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.name=T", "-c", "user.email=t@example.org", *args],
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "main")
    (root / ".gitignore").write_text("venv/\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "first")
    (root / "README.md").write_text("# Alpha\n\nchanged\n", encoding="utf-8")
    return root


def wait_git(qtbot, opened: MainWindow) -> None:  # type: ignore[no-untyped-def]
    opened.views.show("git")
    qtbot.waitUntil(lambda: opened.views.git._overview is not None, timeout=30_000)


def test_the_git_panel_shows_branch_changes_and_commits(
    qtbot, opened: MainWindow, repo: Path
) -> None:
    wait_git(qtbot, opened)
    git = opened.views.git

    assert "main" in git.header.text()
    assert "1 changed" in git.header.text()
    assert git.changes.count() == 1
    assert git.changes.item(0).text() == "README.md"
    assert "first" in git.commits.item(0).text()
    assert "wrong identity" in git.header.text()  # the global e-mail is not the portfolio's
    assert not git.commit_button.isEnabled()
    assert "blocked" in git.notice.text()


def test_selecting_a_changed_file_shows_its_diff(qtbot, opened: MainWindow, repo: Path) -> None:
    wait_git(qtbot, opened)
    git = opened.views.git

    git.changes.setCurrentRow(0)
    qtbot.waitUntil(lambda: "changed" in git.diff.toPlainText(), timeout=30_000)

    assert "+changed" in git.diff.toPlainText()


def test_a_commit_is_authored_with_the_required_identity_and_never_pushed(
    qtbot, opened: MainWindow, repo: Path
) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "user.email", "mariatg.invers@gmail.com"],
        check=True,
    )
    # The identity policy only requires the e-mail (see Settings.expected_git_name), but an
    # actual `git commit` still needs *some* user.name; the fake global config in `repo` deliberately
    # has none, so without this, committing falls back to git's platform-dependent auto-detection.
    subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "user.name", "María Tejedor García"],
        check=True,
    )
    wait_git(qtbot, opened)
    git = opened.views.git
    assert "identity ok" in git.header.text()

    git.message.setPlainText("update the readme")
    assert git.commit_button.isEnabled()
    git.commit_button.click()
    qtbot.waitUntil(lambda: "Committed" in git.notice.text(), timeout=30_000)
    qtbot.waitUntil(lambda: git.changes.count() == 0, timeout=30_000)

    log = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%ae|%s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert log == "mariatg.invers@gmail.com|update the readme"
    assert "Nothing was pushed" in git.notice.text()
    assert git.message.toPlainText() == ""


def test_a_commit_needs_a_message(qtbot, opened: MainWindow, repo: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "user.email", "mariatg.invers@gmail.com"],
        check=True,
    )
    wait_git(qtbot, opened)

    assert not opened.views.git.commit_button.isEnabled()
    opened.views.git.message.setPlainText("  ")
    assert not opened.views.git.commit_button.isEnabled()


def test_there_is_no_push_button_only_the_command_to_copy(
    qtbot, opened: MainWindow, repo: Path
) -> None:
    wait_git(qtbot, opened)
    git = opened.views.git

    buttons = [b.text() for b in git.findChildren(type(git.commit_button))]
    assert not any("push" in text.lower() and "copy" not in text.lower() for text in buttons)
    git.push_button.click()

    clipboard = QApplication.clipboard().text()
    assert clipboard == f'git -C "{repo}" push'
    assert "Command copied" in git.notice.text()


def test_a_project_that_is_not_a_repository_says_so(qtbot, opened: MainWindow) -> None:
    opened.select_project("slow")
    opened.views.show("git")

    qtbot.waitUntil(
        lambda: "not a git repository" in opened.views.git.header.text(), timeout=30_000
    )
    assert not opened.views.git.push_button.isEnabled()


# --------------------------------------------------------------------- dashboard
def test_a_project_without_a_dashboard_says_so(opened: MainWindow, workspace: Path) -> None:
    (workspace / "alpha" / "outputs" / "dashboard.html").unlink()
    opened.select_project("beta")
    opened.select_project("alpha")
    opened.views.show("dashboard")

    assert "No dashboard yet" in opened.views.dashboard.status.text()
    assert not opened.views.dashboard.reload_button.isEnabled()
    assert opened.views.dashboard.shown_file() is None


def test_the_dashboard_is_loaded_in_the_embedded_browser(
    qtbot, opened: MainWindow, workspace: Path
) -> None:
    page = workspace / "alpha" / "outputs" / "dashboard.html"
    page.write_text("<html><body><h1 id='t'>Hello dashboard</h1></body></html>", encoding="utf-8")
    dashboard = opened.views.dashboard

    with qtbot.waitSignal(dashboard.web.loadFinished, timeout=60_000) as loaded:  # type: ignore[attr-defined]
        opened.views.show("dashboard")
        dashboard.set_project(opened._controller.project("alpha"))

    assert loaded.args == [True]
    assert dashboard.shown_file() == page
    assert Path(dashboard.web.url().toLocalFile()) == page  # type: ignore[attr-defined]
    assert dashboard.export_png(workspace / "shot.png")
    assert (workspace / "shot.png").stat().st_size > 1000


def test_the_plotly_script_is_cached_locally_and_the_original_is_never_modified(
    qtbot, opened: MainWindow, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = workspace / "alpha" / "outputs" / "dashboard.html"
    original = '<html><script src="https://cdn.plot.ly/plotly-9.9.9.min.js"></script></html>'
    page.write_text(original, encoding="utf-8")
    dashboard = opened.views.dashboard
    dashboard._cache._fetch = lambda url: b"/* plotly */"
    loaded: list[str] = []
    monkeypatch.setattr(dashboard.web, "load", lambda url: loaded.append(url.toLocalFile()))

    opened.views.show("dashboard")
    dashboard.set_project(opened._controller.project("alpha"))
    qtbot.waitUntil(
        lambda: dashboard.shown_file() != page and dashboard.shown_file() is not None,
        timeout=30_000,
    )

    copy = dashboard.shown_file()
    assert copy is not None
    assert "cdn.plot.ly" not in copy.read_text(encoding="utf-8")
    assert "file:///" in copy.read_text(encoding="utf-8")
    assert page.read_text(encoding="utf-8") == original  # the project's own file is untouched
    assert Path(loaded[-1]) == copy


def test_offline_the_dashboard_falls_back_to_the_online_script(
    qtbot, opened: MainWindow, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = workspace / "alpha" / "outputs" / "dashboard.html"
    page.write_text(
        '<html><script src="https://cdn.plot.ly/plotly-9.9.9.min.js"></script></html>',
        encoding="utf-8",
    )
    dashboard = opened.views.dashboard

    def offline(url: str) -> bytes:
        raise OSError("no network")

    dashboard._cache._fetch = offline
    monkeypatch.setattr(dashboard.web, "load", lambda url: None)
    opened.views.show("dashboard")
    dashboard.set_project(opened._controller.project("alpha"))

    assert dashboard.shown_file() == page
    assert "online copy of Plotly" in dashboard.status.text()


def test_the_dashboard_reloads_by_itself_when_a_run_rewrites_it(
    qtbot, opened: MainWindow, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dashboard = opened.views.dashboard
    loads: list[str] = []
    monkeypatch.setattr(dashboard.web, "load", lambda url: loads.append(url.toLocalFile()))
    opened.views.show("dashboard")
    dashboard.set_project(opened._controller.project("alpha"))
    first = len(loads)

    (workspace / "alpha" / "outputs" / "dashboard.html").write_text(
        "<html>new</html>", encoding="utf-8"
    )

    qtbot.waitUntil(lambda: len(loads) > first, timeout=10_000)


# --------------------------------------------------------- problems, fixes, go-to
def diagnose(qtbot, opened: MainWindow, controller: AppController) -> None:  # type: ignore[no-untyped-def]
    controller.run_doctor(["alpha"])
    qtbot.waitUntil(lambda: controller.report is not None, timeout=WAIT_MS)
    wait_idle(qtbot, controller)


def row_of(opened: MainWindow, code: str) -> int:
    model = opened.findings_model
    return next(r for r in range(model.rowCount()) if model.finding_at(r).code == code)


def test_double_clicking_a_problem_opens_the_code_at_its_line(
    qtbot, opened: MainWindow, controller: AppController
) -> None:
    diagnose(qtbot, opened, controller)
    row = row_of(opened, "bare-except")

    opened.problems_view.doubleClicked.emit(opened.findings_model.index(row, 0))

    assert opened.tabs.currentWidget() is opened.views.code
    assert opened.views.code.current_file() == "src/util.py"
    assert opened.views.code.editor.current_line() == 4


def test_the_fix_button_is_enabled_only_for_fixable_problems(
    qtbot, opened: MainWindow, controller: AppController
) -> None:
    diagnose(qtbot, opened, controller)

    opened.problems_view.setCurrentIndex(
        opened.findings_model.index(row_of(opened, "bare-except"), 0)
    )
    assert not opened.action("problems.fix").isEnabled()
    opened.problems_view.setCurrentIndex(
        opened.findings_model.index(row_of(opened, "ssl-cert-missing"), 0)
    )
    assert opened.action("problems.fix").isEnabled()


def test_a_fix_is_previewed_confirmed_applied_and_the_problem_disappears(
    qtbot, opened: MainWindow, controller: AppController, workspace: Path
) -> None:
    diagnose(qtbot, opened, controller)
    opened.problems_view.setCurrentIndex(
        opened.findings_model.index(row_of(opened, "ssl-cert-missing"), 0)
    )
    previews: list[str] = []

    def confirm(preview) -> bool:  # type: ignore[no-untyped-def]
        previews.append(preview.summary)
        assert not (
            workspace / "alpha" / ".certs" / "cacert.pem"
        ).exists()  # a preview writes nothing
        return True

    opened.confirm_fix = confirm
    opened.fix_selected_problem()
    bundle = workspace / "alpha" / ".certs" / "cacert.pem"
    qtbot.waitUntil(bundle.exists, timeout=WAIT_MS)
    qtbot.waitUntil(
        lambda: (
            controller.report is not None
            and not any(f.code == "ssl-cert-missing" for f in controller.report.findings)
        ),
        timeout=WAIT_MS,
    )
    wait_idle(qtbot, controller)

    assert previews == ["Add .certs/cacert.pem to Alpha"]
    assert "BEGIN CERTIFICATE" in (workspace / "alpha" / ".certs" / "cacert.pem").read_text(
        encoding="utf-8"
    )
    assert all(
        opened.findings_model.finding_at(r).code != "ssl-cert-missing"
        for r in range(opened.findings_model.rowCount())
    )


def test_declining_the_preview_changes_nothing(
    qtbot, opened: MainWindow, controller: AppController, workspace: Path
) -> None:
    diagnose(qtbot, opened, controller)
    opened.problems_view.setCurrentIndex(
        opened.findings_model.index(row_of(opened, "ssl-cert-missing"), 0)
    )
    asked: list[bool] = []
    opened.confirm_fix = lambda preview: asked.append(True) and False  # type: ignore[func-returns-value,return-value]

    opened.fix_selected_problem()
    qtbot.waitUntil(lambda: bool(asked), timeout=30_000)
    qtbot.wait(200)

    assert not (workspace / "alpha" / ".certs").exists()
