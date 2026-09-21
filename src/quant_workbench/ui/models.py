"""Qt item models: what the list and tables show, and how they follow the engine's events."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap

from quant_workbench.domain.diagnostics import DoctorReport, Finding
from quant_workbench.domain.project import Project
from quant_workbench.domain.run_events import RunFinished, RunQueued, RunStarted
from quant_workbench.domain.runs import Run, RunStatus
from quant_workbench.ui.summary import format_duration
from quant_workbench.ui.theme import Tokens, severity_colour, status_colour

#: Data roles shared by the models.
SlugRole = Qt.ItemDataRole.UserRole + 1
StatusRole = Qt.ItemDataRole.UserRole + 2
SearchRole = Qt.ItemDataRole.UserRole + 3
RunIdRole = Qt.ItemDataRole.UserRole + 4
ObjectRole = Qt.ItemDataRole.UserRole + 5

_BADGE_SIZE = 12
_ModelIndex = QModelIndex | QPersistentModelIndex


def badge_icon(colour: str) -> QIcon:
    """A small filled circle: the status badge next to a project's name."""
    pixmap = QPixmap(_BADGE_SIZE, _BADGE_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(colour))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, _BADGE_SIZE - 2, _BADGE_SIZE - 2)
    painter.end()
    return QIcon(pixmap)


class ProjectListModel(QAbstractListModel):
    """The project explorer: one row per project, with a badge for its latest run."""

    def __init__(self, tokens: Tokens, parent: Any = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._projects: list[Project] = []
        self._status: dict[str, RunStatus | None] = {}

    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
        if self._projects:
            self.dataChanged.emit(self.index(0), self.index(len(self._projects) - 1))

    def set_projects(
        self, projects: Sequence[Project], latest: Callable[[str], Run | None] = lambda _: None
    ) -> None:
        self.beginResetModel()
        self._projects = list(projects)
        self._status = {}
        for project in self._projects:
            run = latest(project.slug)
            self._status[project.slug] = run.status if run else None
        self.endResetModel()

    def set_status(self, slug: str, status: RunStatus | None) -> None:
        if slug not in self._status or self._status[slug] == status:
            return
        self._status[slug] = status
        row = next(i for i, p in enumerate(self._projects) if p.slug == slug)
        self.dataChanged.emit(self.index(row), self.index(row))

    def status(self, slug: str) -> RunStatus | None:
        return self._status.get(slug)

    def project_at(self, row: int) -> Project:
        return self._projects[row]

    def rowCount(self, parent: _ModelIndex = QModelIndex()) -> int:  # noqa: B008 - Qt signature
        return 0 if parent.isValid() else len(self._projects)

    def data(self, index: _ModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        project = self._projects[index.row()]
        status = self._status.get(project.slug)
        if role == Qt.ItemDataRole.DisplayRole:
            return project.title
        if role == Qt.ItemDataRole.DecorationRole:
            return badge_icon(status_colour(status, self._tokens))
        if role == Qt.ItemDataRole.ToolTipRole:
            shown = status.value if status else "never run"
            return f"{project.slug}\n{project.spec.category}\nlast run: {shown}"
        if role == SlugRole:
            return project.slug
        if role == StatusRole:
            return status
        if role == SearchRole:
            return f"{project.title} {project.slug} {project.spec.category}"
        return None


_RUN_COLUMNS = ("Project", "Status", "Job", "Started", "Duration", "Try")
_MAX_RUN_ROWS = 500


class RunsModel(QAbstractTableModel):
    """The job queue: every run seen since the app started, newest first."""

    def __init__(self, tokens: Tokens, parent: Any = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._runs: list[Run] = []

    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
        self.layoutChanged.emit()

    def apply(self, events: Sequence[object]) -> None:
        """Fold a batch of engine events into the table."""
        for event in events:
            if isinstance(event, RunQueued | RunStarted | RunFinished):
                self._upsert(event.run)

    def _upsert(self, run: Run) -> None:
        for row, existing in enumerate(self._runs):
            if existing.id == run.id:
                self._runs[row] = run
                self.dataChanged.emit(self.index(row, 0), self.index(row, len(_RUN_COLUMNS) - 1))
                return
        self.beginInsertRows(QModelIndex(), 0, 0)
        self._runs.insert(0, run)
        self.endInsertRows()
        if len(self._runs) > _MAX_RUN_ROWS:
            self.beginRemoveRows(QModelIndex(), _MAX_RUN_ROWS, len(self._runs) - 1)
            del self._runs[_MAX_RUN_ROWS:]
            self.endRemoveRows()

    def run_at(self, row: int) -> Run:
        return self._runs[row]

    def active_run_ids(self) -> list[str]:
        return [r.id for r in self._runs if not r.status.is_terminal]

    def rowCount(self, parent: _ModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._runs)

    def columnCount(self, parent: _ModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(_RUN_COLUMNS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _RUN_COLUMNS[section]
        return None

    def data(self, index: _ModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        run = self._runs[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                run.project,
                run.status.value,
                run.kind.value,
                _clock(run.started_at or run.queued_at),
                format_duration(run.duration),
                str(run.attempt),
            )[column]
        if role == Qt.ItemDataRole.ForegroundRole and column == 1:
            return QBrush(QColor(status_colour(run.status, self._tokens)))
        if role == Qt.ItemDataRole.ToolTipRole and run.failure:
            return run.failure
        if role == RunIdRole:
            return run.id
        if role == SlugRole:
            return run.project
        return None


def _clock(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%H:%M:%S") if moment else "-"


_FINDING_COLUMNS = ("Severity", "Project", "Problem", "Where", "Fix")


class FindingsModel(QAbstractTableModel):
    """The problems panel: the doctor's findings, most severe first."""

    def __init__(self, tokens: Tokens, parent: Any = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._findings: tuple[Finding, ...] = ()

    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
        self.layoutChanged.emit()

    def set_report(self, report: DoctorReport | None) -> None:
        self.beginResetModel()
        self._findings = report.findings if report else ()
        self.endResetModel()

    def finding_at(self, row: int) -> Finding:
        return self._findings[row]

    def rowCount(self, parent: _ModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._findings)

    def columnCount(self, parent: _ModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(_FINDING_COLUMNS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _FINDING_COLUMNS[section]
        return None

    def data(self, index: _ModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        finding = self._findings[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                finding.severity.value,
                finding.project or "workspace",
                finding.message,
                str(finding.location) if finding.location else "",
                "yes" if finding.fix else "",
            )[column]
        if role == Qt.ItemDataRole.ForegroundRole and column == 0:
            return QBrush(QColor(severity_colour(finding.severity, self._tokens)))
        if role == Qt.ItemDataRole.ToolTipRole:
            return finding.detail or finding.message
        if role == SlugRole:
            return finding.project
        if role == ObjectRole:
            return finding
        return None
