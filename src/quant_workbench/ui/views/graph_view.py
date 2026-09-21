"""The dependency graph: projects as boxes in execution layers, with the impact of a change."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from quant_workbench.application.catalog import Catalog
from quant_workbench.domain.graph import EdgeOrigin
from quant_workbench.domain.ids import Slug
from quant_workbench.domain.layout import Point, layered_layout
from quant_workbench.domain.runs import RunStatus
from quant_workbench.ui.theme import Tokens, status_colour

NODE_WIDTH = 230.0
NODE_HEIGHT = 44.0
_ARROW = 9.0
_MARGIN = 40.0


class _Node(QGraphicsRectItem):
    """One project. Clicking it reports its slug."""

    def __init__(self, slug: str, title: str, on_click: Callable[[str], None]) -> None:
        super().__init__(QRectF(0, -NODE_HEIGHT / 2, NODE_WIDTH, NODE_HEIGHT))
        self.slug = slug
        self._on_click = on_click
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(slug)
        label = QGraphicsSimpleTextItem(title, self)
        font = QFont(label.font())
        font.setPointSize(10)
        label.setFont(font)
        label.setPos(26, -label.boundingRect().height() / 2)
        self.label = label
        self.badge = QGraphicsRectItem(QRectF(10, -5, 10, 10), self)
        self.badge.setPen(QPen(Qt.PenStyle.NoPen))

    def click(self) -> None:
        self._on_click(self.slug)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self.click()
        event.accept()


class GraphView(QWidget):
    """Layered drawing of the workspace graph, coloured by the latest run of each project."""

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._catalog: Catalog | None = None
        self._nodes: dict[str, _Node] = {}
        self._edges: list[tuple[str, str, QGraphicsPathItem, QGraphicsPolygonItem, bool]] = []
        self._status: dict[str, RunStatus | None] = {}
        self._selected: str | None = None
        #: Called with the slug when a node is clicked.
        self.on_select: Callable[[str], object] = lambda slug: None
        #: Called with the slug when "Re-run impacted" is pressed.
        self.on_run_impacted: Callable[[str], None] = lambda slug: None

        self.scene_ = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene_)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.summary = QLabel("Select a project to see what depends on it.")
        self.summary.setWordWrap(True)
        self.impact_button = QPushButton("Re-run impacted")
        self.impact_button.setEnabled(False)
        self.impact_button.clicked.connect(self._run_impacted)
        bar = QHBoxLayout()
        bar.addWidget(self.summary, 1)
        bar.addWidget(self.impact_button)
        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.view, 1)

    # ------------------------------------------------------------------ building
    def set_catalog(self, catalog: Catalog | None, status: dict[str, RunStatus | None]) -> None:
        """(Re)draw the whole graph for ``catalog``."""
        self._catalog = catalog
        self._status = dict(status)
        self._selected = None
        self._draw()

    def set_status(self, slug: str, status: RunStatus | None) -> None:
        self._status[slug] = status
        node = self._nodes.get(slug)
        if node is not None:
            self._paint_node(node)

    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
        self._draw()

    def _draw(self) -> None:
        self.scene_.clear()
        self._nodes.clear()
        self._edges.clear()
        catalog = self._catalog
        if catalog is None or not catalog.projects:
            return
        graph = catalog.graph
        positions = layered_layout(
            graph.generations(),
            graph.dependencies_of,
            x_gap=NODE_WIDTH + 70,
            y_gap=NODE_HEIGHT + 22,
        )
        titles = {p.slug: p.title for p in catalog.projects}
        for edge in graph.edges:
            self._add_edge(
                positions,
                edge.dependency,
                edge.dependent,
                declared=EdgeOrigin.DECLARED in edge.origins,
            )
        for slug, point in positions.items():
            node = _Node(slug, titles.get(slug, slug), self._clicked)
            node.setPos(point.x, point.y)
            self.scene_.addItem(node)
            self._nodes[slug] = node
            self._paint_node(node)
        self.scene_.setSceneRect(
            self.scene_.itemsBoundingRect().adjusted(-_MARGIN, -_MARGIN, _MARGIN, _MARGIN)
        )
        self._restyle()
        self.view.fitInView(self.scene_.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _add_edge(
        self, positions: dict[Slug, Point], source: Slug, target: Slug, *, declared: bool
    ) -> None:
        start = QPointF(positions[source].x + NODE_WIDTH, positions[source].y)
        end = QPointF(positions[target].x, positions[target].y)
        path = QPainterPath(start)
        pull = (end.x() - start.x()) / 2
        path.cubicTo(QPointF(start.x() + pull, start.y()), QPointF(end.x() - pull, end.y()), end)
        line = QGraphicsPathItem(path)
        line.setZValue(-2)
        self.scene_.addItem(line)
        head = QPolygonF(
            [
                end,
                QPointF(end.x() - _ARROW, end.y() - _ARROW / 2),
                QPointF(end.x() - _ARROW, end.y() + _ARROW / 2),
            ]
        )
        arrow = QGraphicsPolygonItem(head)
        arrow.setZValue(-1)
        self.scene_.addItem(arrow)
        self._edges.append((source, target, line, arrow, declared))

    # ------------------------------------------------------------------ selection
    def _clicked(self, slug: str) -> None:
        self.select(slug)
        self.on_select(slug)

    def select(self, slug: str | None) -> None:
        """Highlight ``slug`` and everything that would need re-running after changing it."""
        self._selected = slug if slug in self._nodes else None
        self._restyle()
        self.impact_button.setEnabled(self._selected is not None)
        if self._selected is None or self._catalog is None:
            self.summary.setText("Select a project to see what depends on it.")
            return
        impacted = [s for s in self.impacted() if s != self._selected]
        self.summary.setText(
            f"Changing <b>{self._selected}</b> affects "
            + (", ".join(impacted) if impacted else "no other project")
            + "."
        )

    def impacted(self) -> list[str]:
        """The selected project and its transitive dependents, in a safe re-run order."""
        if self._catalog is None or self._selected is None:
            return []
        return list(self._catalog.graph.impact_of([Slug(self._selected)]))

    def _run_impacted(self) -> None:
        if self._selected is not None:
            self.on_run_impacted(self._selected)

    # ------------------------------------------------------------------- styling
    def _paint_node(self, node: _Node) -> None:
        t = self._tokens
        node.setBrush(QBrush(QColor(t.surface)))
        node.label.setBrush(QBrush(QColor(t.ink)))
        node.badge.setBrush(QBrush(QColor(status_colour(self._status.get(node.slug), t))))

    def _restyle(self) -> None:
        t = self._tokens
        impacted = set(self.impacted())
        for slug, node in self._nodes.items():
            self._paint_node(node)
            if slug == self._selected:
                node.setPen(QPen(QColor(t.accent), 3))
            elif slug in impacted:
                node.setPen(QPen(QColor(t.warning), 2.5))
            else:
                node.setPen(QPen(QColor(t.baseline), 1.2))
        for source, target, line, arrow, declared in self._edges:
            hot = self._selected is not None and source in impacted and target in impacted
            colour = QColor(t.warning if hot else t.ink_muted)
            pen = QPen(colour, 2.4 if hot else 1.3)
            if not declared:
                pen.setStyle(Qt.PenStyle.DashLine)  # found in the code but not in the manifest
            line.setPen(pen)
            arrow.setBrush(QBrush(colour))
            arrow.setPen(QPen(colour))
        self.view.setBackgroundBrush(QBrush(QColor(t.window)))

    def node_slugs(self) -> list[str]:
        return list(self._nodes)

    def edge_pairs(self) -> list[tuple[str, str]]:
        """``(dependency, dependent)`` for every drawn edge (used by tests)."""
        return [(source, target) for source, target, *_ in self._edges]

    def click_node(self, slug: str) -> None:
        """Simulate a click on a node (used by tests)."""
        self._nodes[slug].click()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._nodes:
            self.view.fitInView(self.scene_.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
