"""Layered layout of the dependency graph (pure geometry, no Qt)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from quant_workbench.domain.ids import Slug


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


def layered_layout(
    layers: Sequence[Sequence[Slug]],
    dependencies_of: Callable[[Slug], frozenset[Slug]],
    *,
    x_gap: float = 260.0,
    y_gap: float = 90.0,
) -> dict[Slug, Point]:
    """Place each execution layer in a column, dependencies on the left.

    Inside a column the nodes are ordered by the average height of their dependencies (the
    barycentre heuristic), which keeps most edges short and uncrossed; nodes without
    dependencies keep alphabetical order. Each column is centred on the same horizontal axis.
    """
    positions: dict[Slug, Point] = {}
    for column, layer in enumerate(layers):

        def barycentre(slug: Slug) -> float:
            known = [positions[d].y for d in dependencies_of(slug) if d in positions]
            return sum(known) / len(known) if known else float("inf")

        ordered = sorted(layer, key=lambda slug: (barycentre(slug), slug))
        top = -(len(ordered) - 1) * y_gap / 2
        for row, slug in enumerate(ordered):
            positions[slug] = Point(column * x_gap, top + row * y_gap)
    return positions
