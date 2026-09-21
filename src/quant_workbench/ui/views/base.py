"""What every central-area tab has in common."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from quant_workbench.domain.project import Project
from quant_workbench.ui.theme import Tokens


class ProjectView(QWidget):
    """A tab that shows one aspect of the currently selected project.

    The main window calls :meth:`set_project` when the selection changes, :meth:`refresh`
    when something the view depends on changed on disk or in the engine, and
    :meth:`set_tokens` when the theme changes. Subclasses override what they need.
    """

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._project: Project | None = None

    @property
    def project(self) -> Project | None:
        return self._project

    def set_project(self, project: Project | None) -> None:
        self._project = project
        self.refresh()

    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
        self.refresh()

    def refresh(self) -> None:  # pragma: no cover - overridden by every concrete view
        """Reload what the view shows for the current project."""
