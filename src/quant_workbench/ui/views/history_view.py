"""Run history of the selected project: logs, metrics, comparison of two runs, metric trend."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCharts import (
    QChart,
    QChartView,
    QDateTimeAxis,
    QLineSeries,
    QScatterSeries,
    QValueAxis,
)
from PySide6.QtCore import QAbstractTableModel, QDateTime, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QTableView,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from quant_workbench.domain.history import compare_runs, metric_names, metric_series
from quant_workbench.domain.runs import Run
from quant_workbench.ui.controller import AppController
from quant_workbench.ui.history_html import comparison_html
from quant_workbench.ui.models import RunIdRole
from quant_workbench.ui.summary import format_duration, format_metric
from quant_workbench.ui.theme import Tokens, status_colour
from quant_workbench.ui.views.base import ProjectView

_ModelIndex = QModelIndex | QPersistentModelIndex
_COLUMNS = ("Started", "Status", "Duration", "Try", "Metrics")
_MAX_RUNS = 200
_PAIR = 2  # comparing two selected runs


class HistoryModel(QAbstractTableModel):
    """All stored runs of one project, newest first."""

    def __init__(self, tokens: Tokens, parent: Any = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._runs: list[Run] = []

    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
        self.layoutChanged.emit()

    def set_runs(self, runs: tuple[Run, ...]) -> None:
        self.beginResetModel()
        self._runs = list(runs)
        self.endResetModel()

    def run_at(self, row: int) -> Run:
        return self._runs[row]

    @property
    def runs(self) -> list[Run]:
        return list(self._runs)

    def rowCount(self, parent: _ModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._runs)

    def columnCount(self, parent: _ModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(_COLUMNS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _COLUMNS[section]
        return None

    def data(self, index: _ModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        run = self._runs[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            started = (run.started_at or run.queued_at).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            metrics = ", ".join(f"{m.name}={format_metric(m)}" for m in run.metrics[:3])
            return (
                started,
                run.status.value,
                format_duration(run.duration),
                str(run.attempt),
                metrics,
            )[column]
        if role == Qt.ItemDataRole.ForegroundRole and column == 1:
            return QBrush(QColor(status_colour(run.status, self._tokens)))
        if role == RunIdRole:
            return run.id
        return None


class HistoryView(ProjectView):
    """Table of runs on the left; logs, metrics, comparison and trend on the right."""

    def __init__(
        self, controller: AppController, tokens: Tokens, parent: QWidget | None = None
    ) -> None:
        super().__init__(tokens, parent)
        self._controller = controller
        self.model = HistoryModel(tokens, self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((150, 90, 90, 40)):
            self.table.setColumnWidth(column, width)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._selection_changed())

        self._build_pages()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.table)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)
        self.refresh()

    def _build_pages(self) -> None:
        self.log = QPlainTextEdit()
        self.log.setObjectName("console")
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.comparison = QTextBrowser()
        self.comparison_hint = QLabel(
            "Select one run to compare it with the previous one, or two runs to compare them."
        )
        compare_page = QWidget()
        compare_layout = QVBoxLayout(compare_page)
        compare_layout.addWidget(self.comparison_hint)
        compare_layout.addWidget(self.comparison)

        self.metric_box = QComboBox()
        self.metric_box.currentTextChanged.connect(self._draw_trend)
        self.chart = QChart()
        self.chart.legend().hide()
        self.chart_view = QChartView(self.chart)
        self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        trend_page = QWidget()
        trend_layout = QVBoxLayout(trend_page)
        top = QHBoxLayout()
        top.addWidget(QLabel("Metric"))
        top.addWidget(self.metric_box)
        top.addStretch(1)
        trend_layout.addLayout(top)
        trend_layout.addWidget(self.chart_view)
        self.trend_note = QLabel()
        trend_layout.addWidget(self.trend_note)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.log, "Log")
        self.tabs.addTab(compare_page, "Compare")
        self.tabs.addTab(trend_page, "Trend")

    # -------------------------------------------------------------------- content
    def refresh(self) -> None:
        selected = self.selected_run_ids()
        runs = self._controller.list_runs(self._project.slug, _MAX_RUNS) if self._project else ()
        self.model.set_runs(runs)
        names = metric_names(list(runs))
        self.metric_box.blockSignals(True)
        current = self.metric_box.currentText()
        self.metric_box.clear()
        self.metric_box.addItems(names)
        if current in names:
            self.metric_box.setCurrentText(current)
        self.metric_box.blockSignals(False)
        self._draw_trend()
        if selected:
            self._reselect(selected)
        else:
            self._selection_changed()

    def _reselect(self, run_ids: list[str]) -> None:
        selection = self.table.selectionModel()
        for row in range(self.model.rowCount()):
            if self.model.run_at(row).id in run_ids:
                selection.select(
                    self.model.index(row, 0),
                    selection.SelectionFlag.Select | selection.SelectionFlag.Rows,
                )

    def selected_run_ids(self) -> list[str]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.model.run_at(r).id for r in rows]

    def select_rows(self, rows: list[int]) -> None:
        """Select these table rows (0 is the newest run); used by tests and shortcuts."""
        selection = self.table.selectionModel()
        selection.clearSelection()
        for row in rows:
            selection.select(
                self.model.index(row, 0),
                selection.SelectionFlag.Select | selection.SelectionFlag.Rows,
            )

    def _selection_changed(self) -> None:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        self._show_log(rows)
        self._show_comparison(rows)

    def _show_log(self, rows: list[int]) -> None:
        if len(rows) != 1:
            self.log.setPlainText("" if not rows else "Select a single run to read its log.")
            return
        lines = self._controller.run_logs(self.model.run_at(rows[0]).id)
        self.log.setPlainText("\n".join(line.text for line in lines))

    def _show_comparison(self, rows: list[int]) -> None:
        runs = self.model.runs
        pair: tuple[Run, Run] | None = None
        if len(rows) == _PAIR:
            newer, older = sorted(rows)
            pair = (runs[older], runs[newer])
        elif len(rows) == 1 and rows[0] + 1 < len(runs):
            pair = (runs[rows[0] + 1], runs[rows[0]])
        if pair is None:
            self.comparison.setHtml("")
            self.comparison_hint.setVisible(True)
            return
        self.comparison_hint.setVisible(False)
        self.comparison.setHtml(comparison_html(compare_runs(*pair), self._tokens))

    # ---------------------------------------------------------------------- trend
    def _draw_trend(self) -> None:
        self.chart.removeAllSeries()
        for axis in self.chart.axes():
            self.chart.removeAxis(axis)
        name = self.metric_box.currentText()
        series_data = metric_series(self.model.runs, name) if name else []
        if len(series_data) < 1:
            self.trend_note.setText(
                "No successful run with a numeric metric yet." if not name else "No data."
            )
            return
        self.trend_note.setText(f"{len(series_data)} successful run(s) with '{name}'.")
        line = QLineSeries()
        dots = QScatterSeries()
        dots.setMarkerSize(9.0)
        for moment, value in series_data:
            stamp = _msecs(moment)
            line.append(stamp, value)
            dots.append(stamp, value)
        accent = QColor(self._tokens.accent)
        line.setColor(accent)
        dots.setColor(accent)
        dots.setBorderColor(accent)
        self.chart.addSeries(line)
        self.chart.addSeries(dots)
        x = QDateTimeAxis()
        x.setFormat("dd MMM HH:mm")
        x.setTickCount(min(6, max(2, len(series_data))))
        y = QValueAxis()
        values = [v for _, v in series_data]
        low, high = min(values), max(values)
        pad = (high - low) * 0.1 or abs(high) * 0.1 or 1.0
        y.setRange(low - pad, high + pad)
        for axis, alignment in ((x, Qt.AlignmentFlag.AlignBottom), (y, Qt.AlignmentFlag.AlignLeft)):
            self.chart.addAxis(axis, alignment)
            line.attachAxis(axis)
            dots.attachAxis(axis)
        if len(series_data) == 1:
            first = _msecs(series_data[0][0])
            x.setRange(
                QDateTime.fromMSecsSinceEpoch(first - 3_600_000),
                QDateTime.fromMSecsSinceEpoch(first + 3_600_000),
            )
        self.chart.setBackgroundBrush(QBrush(QColor(self._tokens.surface)))
        self.chart.setPlotAreaBackgroundBrush(QBrush(QColor(self._tokens.surface)))
        for axis in (x, y):
            axis.setLabelsColor(QColor(self._tokens.ink_secondary))
            axis.setGridLineColor(QColor(self._tokens.grid))

    def trend_points(self) -> list[tuple[datetime, float]]:
        """The points currently drawn (used by tests)."""
        name = self.metric_box.currentText()
        return metric_series(self.model.runs, name) if name else []

    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
        self.model.set_tokens(tokens)
        self._draw_trend()
        self._selection_changed()


def _msecs(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)
