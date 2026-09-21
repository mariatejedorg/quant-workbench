"""Owns the tabs of the central area and keeps each one in step with the selection.

Building every view for every selection would be wasteful (the Git tab shells out to git, the
dashboard tab loads a browser page), so a tab is brought up to date only when it is shown, or
when it is showing while its data changes.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QTabWidget

from quant_workbench.application.catalog import Catalog
from quant_workbench.domain.project import Project
from quant_workbench.domain.runs import RunStatus
from quant_workbench.ui.controller import AppController
from quant_workbench.ui.theme import Tokens
from quant_workbench.ui.views.base import ProjectView
from quant_workbench.ui.views.code_view import CodeView
from quant_workbench.ui.views.config_view import ConfigView
from quant_workbench.ui.views.dashboard_view import DashboardView
from quant_workbench.ui.views.git_view import GitView
from quant_workbench.ui.views.graph_view import GraphView
from quant_workbench.ui.views.history_view import HistoryView
from quant_workbench.ui.views.readme_view import ReadmeView

#: Tab names in display order (the Overview tab, owned by the window, comes first).
TAB_NAMES = ("config", "dashboard", "code", "readme", "history", "graph", "git")
_TITLES = {
    "config": "Config",
    "dashboard": "Dashboard",
    "code": "Code",
    "readme": "README",
    "history": "History",
    "graph": "Graph",
    "git": "Git",
}
#: Views whose content depends on how the project last ran.
AFTER_RUN = ("history", "dashboard", "git")


class ProjectViews(QObject):
    def __init__(self, controller: AppController, tokens: Tokens, tabs: QTabWidget) -> None:
        super().__init__(tabs)
        self._tabs = tabs
        self._project: Project | None = None
        self._stale: set[str] = set()
        self.config = ConfigView(controller, tokens)
        self.dashboard = DashboardView(controller, tokens)
        self.code = CodeView(controller, tokens)
        self.readme = ReadmeView(controller, tokens)
        self.history = HistoryView(controller, tokens)
        self.graph = GraphView(tokens)
        self.git = GitView(controller, tokens)
        self.by_name = {
            "config": self.config,
            "dashboard": self.dashboard,
            "code": self.code,
            "readme": self.readme,
            "history": self.history,
            "graph": self.graph,
            "git": self.git,
        }
        for name in TAB_NAMES:
            tabs.addTab(self.by_name[name], _TITLES[name])
        tabs.currentChanged.connect(lambda _: self._sync())

    # ---------------------------------------------------------------- selection
    def set_project(self, project: Project | None) -> None:
        self._project = project
        if project is not None:
            self.graph.select(project.slug)
        self._sync()

    def mark_stale(self, names: Iterable[str]) -> None:
        """Ask for these views to refresh the next time they are on screen (now if they are)."""
        self._stale.update(names)
        self._sync()

    def run_finished(self, slug: str) -> None:
        """A run of ``slug`` ended: what depends on how it ran needs a fresh look."""
        if self._project is not None and self._project.slug == slug:
            self.mark_stale(AFTER_RUN)

    def _sync(self) -> None:
        name = self.current_name()
        view = self.by_name.get(name) if name else None
        if not isinstance(view, ProjectView):
            return
        if view.project is not self._project:
            view.set_project(self._project)
            self._stale.discard(name or "")
        elif name in self._stale:
            view.refresh()
            self._stale.discard(name)

    def current_name(self) -> str | None:
        """Name of the visible tab, or ``None`` for the Overview."""
        index = self._tabs.currentIndex() - 1  # tab 0 is the Overview
        return TAB_NAMES[index] if 0 <= index < len(TAB_NAMES) else None

    def show(self, name: str) -> None:
        """Bring a tab to the front by name (``"overview"`` for the first tab)."""
        self._tabs.setCurrentIndex(0 if name == "overview" else TAB_NAMES.index(name) + 1)

    # ------------------------------------------------------------ workspace-level
    def set_catalog(self, catalog: Catalog | None, statuses: dict[str, RunStatus | None]) -> None:
        self.graph.set_catalog(catalog, statuses)
        self._project = None
        self._stale.clear()

    def set_status(self, slug: str, status: RunStatus | None) -> None:
        self.graph.set_status(slug, status)

    def open_code(self, relative: str, line: int | None) -> None:
        """Show ``relative`` (at ``line``) in the Code tab."""
        self.show("code")
        if self.code.project is not self._project:
            self.code.set_project(self._project)
        self.code.open_file(relative, line)

    def set_tokens(self, tokens: Tokens) -> None:
        for view in self.by_name.values():
            view.set_tokens(tokens)  # type: ignore[attr-defined]
