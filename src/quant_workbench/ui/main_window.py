"""The main window: project explorer, overview, console, job queue and problems.

Everything the window can do is a :class:`~quant_workbench.ui.commands.Command` in one
registry; the menus, the toolbar and the command palette are all generated from it. Widgets
never call the engine: they ask the :class:`~quant_workbench.ui.controller.AppController`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QByteArray, QModelIndex, QSettings, QSortFilterProxyModel, Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableView,
    QTabWidget,
    QTextBrowser,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from quant_workbench import __version__
from quant_workbench.application.fixes import FixPreview
from quant_workbench.application.settings import Settings, save_settings
from quant_workbench.domain.diagnostics import DoctorReport, Finding
from quant_workbench.domain.run_events import (
    BatchProgress,
    RunFinished,
    RunOutput,
    RunQueued,
    RunStarted,
)
from quant_workbench.domain.runs import RunStatus
from quant_workbench.ui.commands import Command, CommandRegistry
from quant_workbench.ui.controller import AppController
from quant_workbench.ui.dialogs import FixPreviewDialog
from quant_workbench.ui.models import (
    FindingsModel,
    ProjectListModel,
    RunIdRole,
    RunsModel,
    SearchRole,
    SlugRole,
)
from quant_workbench.ui.settings_dialog import SettingsDialog
from quant_workbench.ui.summary import project_summary_html
from quant_workbench.ui.theme import Tokens, build_stylesheet, tokens_for
from quant_workbench.ui.views.hub import TAB_NAMES, ProjectViews
from quant_workbench.ui.widgets.console import ConsoleView
from quant_workbench.ui.widgets.palette import CommandPalette

_BOTTOM_DOCK_HEIGHT = 210
_ORGANISATION = "QuantWorkbench"
_APPLICATION = "Quant Workbench"


def make_settings_store(path: Path | None = None) -> QSettings:
    """The window's persistent layout store (a private INI file when ``path`` is given)."""
    if path is not None:
        return QSettings(str(path), QSettings.Format.IniFormat)
    return QSettings(_ORGANISATION, _APPLICATION)


class MainWindow(QMainWindow):
    """Top-level window of the desktop application."""

    def __init__(
        self,
        controller: AppController,
        settings: Settings,
        *,
        settings_file: Path | None = None,
        store: QSettings | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Quant Workbench")
        self.resize(1360, 860)
        self._controller = controller
        self._settings = settings
        self._settings_file = settings_file
        self._store = store or make_settings_store()
        self._tokens = self._resolve_tokens(settings.theme)
        #: Asks the user to confirm a fix; tests replace it to skip the modal dialog.
        self.confirm_fix: Callable[[FixPreview], bool] = self._show_fix_dialog
        self.commands = CommandRegistry()

        self._build_models()
        self._build_central()
        self._build_docks()
        self._register_commands()
        self._build_menus_and_toolbar()
        self._build_status_bar()
        self._connect_controller()
        self.apply_theme(settings.theme)
        self._restore_layout()
        self._update_actions()

    # --------------------------------------------------------------------- setup
    @staticmethod
    def _resolve_tokens(theme: str) -> Tokens:
        app = QApplication.instance()
        dark = False
        if isinstance(app, QApplication):
            dark = app.styleHints().colorScheme() == Qt.ColorScheme.Dark
        return tokens_for(theme, system_is_dark=dark)

    def _build_models(self) -> None:
        self.project_model = ProjectListModel(self._tokens, self)
        self.project_filter = QSortFilterProxyModel(self)
        self.project_filter.setSourceModel(self.project_model)
        self.project_filter.setFilterRole(SearchRole)
        self.project_filter.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.runs_model = RunsModel(self._tokens, self)
        self.findings_model = FindingsModel(self._tokens, self)

    def _build_central(self) -> None:
        self.overview = QTextBrowser()
        self.overview.setOpenExternalLinks(False)
        self.run_button = QPushButton("Run")
        self.run_button.setDefault(True)
        self.impacted_button = QPushButton("Run + dependents")
        self.doctor_button = QPushButton("Diagnose")
        self.run_button.clicked.connect(lambda: self.commands.execute("run.selected"))
        self.impacted_button.clicked.connect(lambda: self.commands.execute("run.impacted"))
        self.doctor_button.clicked.connect(lambda: self.commands.execute("doctor.selected"))
        buttons = QHBoxLayout()
        for button in (self.run_button, self.impacted_button, self.doctor_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addLayout(buttons)
        layout.addWidget(self.overview)
        self.tabs = QTabWidget()
        self.tabs.addTab(page, "Overview")
        self.views = ProjectViews(self._controller, self._tokens, self.tabs)
        self.views.graph.on_select = self.select_project
        self.views.graph.on_run_impacted = self._controller.run_impacted
        self.setCentralWidget(self.tabs)

    def add_view(self, title: str, widget: QWidget) -> int:
        """Add a tab to the central area (later milestones plug their views in here)."""
        return self.tabs.addTab(widget, title)

    def _build_docks(self) -> None:
        self._build_projects_dock()
        self._build_console_dock()
        self._build_jobs_dock()
        self._build_problems_dock()
        self.tabifyDockWidget(self.console_dock, self.jobs_dock)
        self.tabifyDockWidget(self.jobs_dock, self.problems_dock)
        self.console_dock.raise_()
        self.resizeDocks([self.console_dock], [_BOTTOM_DOCK_HEIGHT], Qt.Orientation.Vertical)

    def _build_projects_dock(self) -> None:
        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText("Filter projects…")
        self.filter_box.setClearButtonEnabled(True)
        self.filter_box.textChanged.connect(self.project_filter.setFilterFixedString)
        self.project_view = QListView()
        self.project_view.setModel(self.project_filter)
        self.project_view.setUniformItemSizes(True)
        self.project_view.selectionModel().currentChanged.connect(
            lambda *_: self._selection_changed()
        )
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(6, 6, 6, 6)
        column.addWidget(self.filter_box)
        column.addWidget(self.project_view)
        self.projects_dock = self._dock("Projects", holder, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.projects_dock.setMinimumWidth(250)

    def _build_console_dock(self) -> None:
        self.console = ConsoleView(self._tokens)
        self.only_selected = QCheckBox("Only the selected project")
        self.only_selected.toggled.connect(lambda _: self._apply_console_filter())
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.commands.execute("console.clear"))
        bar = QHBoxLayout()
        bar.addWidget(self.only_selected)
        bar.addStretch(1)
        bar.addWidget(clear)
        console_holder = QWidget()
        console_layout = QVBoxLayout(console_holder)
        console_layout.setContentsMargins(6, 6, 6, 6)
        console_layout.addLayout(bar)
        console_layout.addWidget(self.console)
        self.console_dock = self._dock(
            "Console", console_holder, Qt.DockWidgetArea.BottomDockWidgetArea
        )

    def _build_jobs_dock(self) -> None:
        self.jobs_view = self._table(
            self.runs_model, {0: 230, 1: 90, 2: 100, 3: 90, 4: 90, 5: 50}, stretch=0
        )
        cancel = QPushButton("Stop all runs")
        cancel.clicked.connect(lambda: self.commands.execute("run.cancel"))
        jobs_holder = QWidget()
        jobs_layout = QVBoxLayout(jobs_holder)
        jobs_layout.setContentsMargins(6, 6, 6, 6)
        jobs_layout.addWidget(self.jobs_view)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        jobs_layout.addLayout(row)
        self.jobs_dock = self._dock("Jobs", jobs_holder, Qt.DockWidgetArea.BottomDockWidgetArea)

    def _build_problems_dock(self) -> None:
        self.problems_view = self._table(
            self.findings_model, {0: 80, 1: 220, 3: 190, 4: 50}, stretch=2
        )
        self.problems_view.doubleClicked.connect(self._problem_activated)
        self.fix_button = QPushButton("Fix…")
        self.fix_button.setToolTip("Preview and apply the automatic fix of the selected problem")
        self.fix_button.clicked.connect(lambda: self.commands.execute("problems.fix"))
        self.problems_view.selectionModel().selectionChanged.connect(
            lambda *_: self._update_actions()
        )
        problems_holder = QWidget()
        problems_layout = QVBoxLayout(problems_holder)
        problems_layout.setContentsMargins(6, 6, 6, 6)
        problems_layout.addWidget(self.problems_view)
        problems_row = QHBoxLayout()
        problems_row.addStretch(1)
        problems_row.addWidget(self.fix_button)
        problems_layout.addLayout(problems_row)
        self.problems_dock = self._dock(
            "Problems", problems_holder, Qt.DockWidgetArea.BottomDockWidgetArea
        )

    def _dock(self, title: str, widget: QWidget, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(f"dock-{title.lower()}")  # QMainWindow.saveState needs unique names
        dock.setWidget(widget)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.addDockWidget(area, dock)
        return dock

    @staticmethod
    def _table(model: object, widths: dict[int, int], stretch: int) -> QTableView:
        """A read-only table; ``widths`` fixes some columns, column ``stretch`` takes the rest."""
        view = QTableView()
        view.setModel(model)  # type: ignore[arg-type]
        view.setAlternatingRowColors(True)
        view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        view.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        view.verticalHeader().setVisible(False)
        view.setWordWrap(False)
        header = view.horizontalHeader()
        header.setStretchLastSection(False)
        for column, width in widths.items():
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            view.setColumnWidth(column, width)
        header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
        return view

    def _build_status_bar(self) -> None:
        self.workspace_label = QLabel("No workspace")
        self.busy_label = QLabel("")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(180)
        self.progress.setVisible(False)
        bar = self.statusBar()
        bar.addWidget(self.workspace_label, 1)
        bar.addPermanentWidget(self.busy_label)
        bar.addPermanentWidget(self.progress)

    # ------------------------------------------------------------------ commands
    def _register_commands(self) -> None:
        c = self._controller

        def add(
            command_id: str,
            title: str,
            callback: Callable[[], object],
            *,
            category: str,
            shortcut: str = "",
            enabled: Callable[[], bool] = lambda: True,
        ) -> None:
            self.commands.register(
                Command(command_id, title, callback, shortcut, category, enabled)
            )

        has_catalog = lambda: c.catalog is not None  # noqa: E731 - three tiny predicates
        has_selection = lambda: c.catalog is not None and self.selected_slug() is not None  # noqa: E731
        is_busy = lambda: c.is_busy  # noqa: E731

        add(
            "workspace.open",
            "Open workspace…",
            self.choose_workspace,
            category="File",
            shortcut="Ctrl+O",
        )
        add(
            "workspace.reload",
            "Reload projects",
            self.reload_workspace,
            category="File",
            shortcut="F5",
            enabled=has_catalog,
        )
        add("settings.open", "Settings…", self.open_settings, category="File", shortcut="Ctrl+,")
        add("app.quit", "Quit", self.close, category="File", shortcut="Ctrl+Q")

        add(
            "run.selected",
            "Run selected project",
            self._run_selected,
            category="Run",
            shortcut="Ctrl+R",
            enabled=has_selection,
        )
        add(
            "run.impacted",
            "Run selected project and its dependents",
            self._run_impacted,
            category="Run",
            shortcut="Ctrl+Shift+R",
            enabled=has_selection,
        )
        add(
            "run.all",
            "Run all projects",
            c.run_all,
            category="Run",
            shortcut="Ctrl+Alt+R",
            enabled=has_catalog,
        )
        add(
            "run.cancel",
            "Stop all runs",
            c.cancel_all,
            category="Run",
            shortcut="Ctrl+.",
            enabled=is_busy,
        )

        add(
            "doctor.all",
            "Diagnose all projects",
            lambda: c.run_doctor(None),
            category="Doctor",
            shortcut="Ctrl+D",
            enabled=has_catalog,
        )
        add(
            "doctor.selected",
            "Diagnose selected project",
            self._doctor_selected,
            category="Doctor",
            enabled=has_selection,
        )

        add(
            "problems.fix",
            "Apply the fix of the selected problem…",
            self.fix_selected_problem,
            category="Doctor",
            enabled=lambda: self._selected_fixable() is not None,
        )
        add("console.clear", "Clear the console", self.console.clear_console, category="View")
        for tab in ("overview", *TAB_NAMES):
            add(
                f"view.tab.{tab}",
                f"Show {tab}",
                lambda name=tab: self.views.show(name),  # type: ignore[misc]
                category="View",
            )
        add(
            "view.palette",
            "Command palette…",
            self.show_palette,
            category="View",
            shortcut="Ctrl+K",
        )
        for name in ("system", "light", "dark"):
            add(
                f"view.theme.{name}",
                f"Theme: {name}",
                lambda n=name: self.apply_theme(n),  # type: ignore[misc]
                category="View",
            )
        add("help.about", "About Quant Workbench", self.show_about, category="Help")

    def _build_menus_and_toolbar(self) -> None:
        self._actions: dict[str, QAction] = {}
        bar = self.menuBar()
        menus: dict[str, QMenu] = {}
        for command in self.commands.all():
            category = command.category or "Other"
            menu = menus.get(category) or menus.setdefault(category, bar.addMenu(f"&{category}"))
            action = QAction(command.title, self)
            if command.shortcut:
                action.setShortcut(QKeySequence(command.shortcut))
            action.triggered.connect(lambda _=False, i=command.id: self.commands.execute(i))
            menu.addAction(action)
            self._actions[command.id] = action
        view_menu = menus["View"]
        view_menu.addSeparator()
        for dock in (self.projects_dock, self.console_dock, self.jobs_dock, self.problems_dock):
            view_menu.addAction(dock.toggleViewAction())

        toolbar = QToolBar("Main")
        toolbar.setObjectName("toolbar-main")
        toolbar.setMovable(False)
        for command_id in (
            "run.selected",
            "run.all",
            "run.cancel",
            "doctor.all",
            "workspace.reload",
        ):
            toolbar.addAction(self._actions[command_id])
        self.addToolBar(toolbar)

    def action(self, command_id: str) -> QAction:
        return self._actions[command_id]

    # -------------------------------------------------------------- controller
    def _connect_controller(self) -> None:
        c = self._controller
        c.catalog_changed.connect(self._catalog_changed)
        c.events.connect(self._on_events)
        c.report_ready.connect(self._report_ready)
        c.message.connect(self._show_message)
        c.busy_changed.connect(self._busy_changed)

    def _catalog_changed(self) -> None:
        catalog = self._controller.catalog
        if catalog is None:
            return
        self.project_model.set_projects(catalog.projects, self._controller.latest_run)
        self.views.set_catalog(
            catalog, {p.slug: self.project_model.status(p.slug) for p in catalog.projects}
        )
        self.findings_model.set_report(None)
        self.workspace_label.setText(f"{catalog.workspace}  -  {len(catalog.projects)} projects")
        if catalog.projects:
            self.project_view.setCurrentIndex(self.project_filter.index(0, 0))
        self._selection_changed()
        self._update_actions()

    def _on_events(self, batch: list[object]) -> None:
        self.runs_model.apply(batch)
        for event in batch:
            if isinstance(event, RunQueued | RunStarted | RunFinished):
                self.project_model.set_status(event.run.project, event.run.status)
                self.views.set_status(event.run.project, event.run.status)
            elif isinstance(event, RunOutput):
                self.console.add_line(
                    event.project, event.line.text, event.line.stream, event.line.level
                )
            elif isinstance(event, BatchProgress):
                self._batch_progress(event)
        finished = [e.run.project for e in batch if isinstance(e, RunFinished)]
        for slug in dict.fromkeys(finished):
            self.views.run_finished(slug)
        if finished:
            self._refresh_overview()

    def _batch_progress(self, event: BatchProgress) -> None:
        finished = event.done >= event.total
        self.progress.setVisible(not finished)
        self.progress.setMaximum(max(1, event.total))
        self.progress.setValue(event.done)
        self.progress.setFormat(f"layer {event.current_layer}/{event.layers} - %v/%m")

    def _report_ready(self, report: DoctorReport) -> None:
        self.findings_model.set_report(report)
        self.problems_dock.setWindowTitle(f"Problems ({len(report.findings)})")
        self.problems_dock.raise_()
        self._refresh_overview()

    def _show_message(self, level: str, text: str) -> None:
        self.statusBar().showMessage(text.splitlines()[0], 8000)
        self.console.add_system(text)
        if level == "error" and self._controller.catalog is None:
            QMessageBox.warning(self, "Quant Workbench", text)

    def _busy_changed(self, busy: bool) -> None:
        self.busy_label.setText("working…" if busy else "")
        self._update_actions()

    # --------------------------------------------------------------- selection
    def selected_slug(self) -> str | None:
        index = self.project_view.currentIndex()
        return index.data(SlugRole) if index.isValid() else None

    def select_project(self, slug: str) -> bool:
        """Make ``slug`` the current project (also used when a problem is double-clicked)."""
        for row in range(self.project_filter.rowCount()):
            index = self.project_filter.index(row, 0)
            if index.data(SlugRole) == slug:
                self.project_view.setCurrentIndex(index)
                return True
        return False

    def _selection_changed(self) -> None:
        slug = self.selected_slug()
        self.views.set_project(self._controller.project(slug) if slug else None)
        self._refresh_overview()
        self._apply_console_filter()
        self._update_actions()

    def _refresh_overview(self) -> None:
        slug = self.selected_slug()
        project = self._controller.project(slug) if slug else None
        catalog = self._controller.catalog
        if project is None or catalog is None:
            self.overview.setHtml("<p>Open a workspace to see its projects.</p>")
            return
        report = self._controller.report
        self.overview.setHtml(
            project_summary_html(
                project,
                catalog,
                last_run=self._controller.latest_run(project.slug),
                score=report.scores.get(project.slug) if report else None,
                findings=report.for_project(project.slug) if report else (),
                tokens=self._tokens,
            )
        )

    def _apply_console_filter(self) -> None:
        self.console.set_filter(self.selected_slug() if self.only_selected.isChecked() else None)

    def _selected_finding(self) -> Finding | None:
        index = self.problems_view.currentIndex()
        return self.findings_model.finding_at(index.row()) if index.isValid() else None

    def _selected_fixable(self) -> Finding | None:
        finding = self._selected_finding()
        return finding if finding and self._controller.can_fix(finding) else None

    def _problem_activated(self, index: QModelIndex) -> None:
        """Double-click: select the project and open the code where the problem is."""
        finding = self.findings_model.finding_at(index.row())
        if finding.project:
            self.select_project(finding.project)
        if finding.location is not None and finding.project:
            self.views.open_code(finding.location.file, finding.location.line)

    def fix_selected_problem(self) -> None:
        """Preview the selected problem's fix and, if the user agrees, apply it."""
        finding = self._selected_fixable()
        if finding is None:
            return
        self._controller.bridge.submit(
            self._controller.preview_fix(finding),
            on_result=lambda preview: self._confirm_and_apply(finding, preview),
            on_error=self._controller.report_error,
        )

    def _confirm_and_apply(self, finding: Finding, preview: FixPreview) -> None:
        if self.confirm_fix(preview):
            self._controller.apply_fix(finding)

    def _show_fix_dialog(self, preview: FixPreview) -> bool:
        return FixPreviewDialog(preview, self._tokens, self).exec() == 1

    def _update_actions(self) -> None:
        for command in self.commands.all():
            self._actions[command.id].setEnabled(command.enabled())
        selected = self.selected_slug() is not None and self._controller.catalog is not None
        self.run_button.setEnabled(selected)
        self.impacted_button.setEnabled(selected)
        self.doctor_button.setEnabled(selected)

    # ---------------------------------------------------------------- commands
    def _run_selected(self) -> None:
        if (slug := self.selected_slug()) is not None:
            self._controller.run_projects([slug])

    def _run_impacted(self) -> None:
        if (slug := self.selected_slug()) is not None:
            self._controller.run_impacted(slug)

    def _doctor_selected(self) -> None:
        if (slug := self.selected_slug()) is not None:
            self._controller.run_doctor([slug])

    def choose_workspace(self) -> None:
        start = str(self._settings.workspace_root or Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Open workspace", start)
        if chosen:
            self.open_workspace(Path(chosen))

    def open_workspace(self, path: Path) -> bool:
        return self._controller.open_workspace(path)

    def reload_workspace(self) -> None:
        catalog = self._controller.catalog
        if catalog is not None:
            self._controller.open_workspace(catalog.workspace)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self)
        if dialog.exec() and (new := dialog.result_settings()) is not None:
            self.apply_settings(new)

    def apply_settings(self, new: Settings) -> None:
        """Adopt new settings: save them, restyle, and tell the user what needs a restart."""
        needs_restart = new.max_concurrency != self._settings.max_concurrency
        self._settings = new
        if self._settings_file is not None:
            save_settings(new, self._settings_file)
        self.apply_theme(new.theme)
        note = " Restart to apply the new number of parallel runs." if needs_restart else ""
        self.statusBar().showMessage(f"Settings saved.{note}", 6000)

    def show_palette(self) -> None:
        CommandPalette(self.commands, self).show()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Quant Workbench",
            f"<b>Quant Workbench {__version__}</b><br>Run, inspect, debug and extend a "
            "portfolio of quantitative finance projects.",
        )

    # -------------------------------------------------------------------- theme
    def apply_theme(self, name: str) -> None:
        self._tokens = self._resolve_tokens(name)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(build_stylesheet(self._tokens))
        for model in (self.project_model, self.runs_model, self.findings_model):
            model.set_tokens(self._tokens)
        self.console.set_tokens(self._tokens)
        self.views.set_tokens(self._tokens)
        self._refresh_overview()

    @property
    def tokens(self) -> Tokens:
        return self._tokens

    # ------------------------------------------------------------------- layout
    def _restore_layout(self) -> None:
        geometry = self._store.value("window/geometry")
        state = self._store.value("window/state")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        if isinstance(state, QByteArray):
            self.restoreState(state)

    def save_layout(self) -> None:
        self._store.setValue("window/geometry", self.saveGeometry())
        self._store.setValue("window/state", self.saveState())
        self._store.sync()

    def _confirm_quit(self) -> bool:
        answer = QMessageBox.question(
            self,
            "Quit Quant Workbench",
            "Projects are still running. Stop them and quit?",
        )
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._controller.is_busy and not self._confirm_quit():
            event.ignore()
            return
        self.save_layout()
        self._controller.shutdown()
        event.accept()

    # ------------------------------------------------------------------ testing
    def run_status(self, slug: str) -> RunStatus | None:
        """The badge status the explorer currently shows for ``slug``."""
        return self.project_model.status(slug)

    def active_run_ids(self) -> list[str]:
        return self.runs_model.active_run_ids()

    def run_id_at(self, row: int) -> str:
        return self.runs_model.index(row, 0).data(RunIdRole)
