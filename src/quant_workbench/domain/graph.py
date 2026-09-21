"""The project dependency graph and the algorithms that run on it.

Edges point from a *dependent* to the project it *depends on* (``10 -> 9`` reads "project
10 needs project 9"). Every algorithm here is deterministic (ties are broken by sorting
slugs) so plans, diagrams and tests are reproducible.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from quant_workbench.domain.errors import DependencyCycleError, ManifestError
from quant_workbench.domain.ids import Slug


class EdgeOrigin(StrEnum):
    """How an edge became known. An edge can have both origins."""

    DECLARED = "declared"  # written in a manifest's ``depends_on``
    DETECTED = "detected"  # found by static analysis of the project's source code


@dataclass(frozen=True, slots=True)
class Dependency:
    dependent: Slug
    dependency: Slug
    origins: frozenset[EdgeOrigin]


class DependencyGraph:
    """An immutable directed graph of projects; the constructor validates its edges."""

    def __init__(self, nodes: Iterable[Slug], edges: Iterable[Dependency] = ()) -> None:
        self._nodes: tuple[Slug, ...] = tuple(sorted(set(nodes)))
        known = set(self._nodes)
        merged: dict[tuple[Slug, Slug], set[EdgeOrigin]] = {}
        for edge in edges:
            for slug in (edge.dependent, edge.dependency):
                if slug not in known:
                    raise ManifestError(
                        f"Dependency edge mentions unknown project {slug!r}",
                        hint="Check the 'depends_on' entries of your manifests.",
                    )
            if edge.dependent == edge.dependency:
                raise DependencyCycleError((edge.dependent,))
            merged.setdefault((edge.dependent, edge.dependency), set()).update(edge.origins)

        self._edges: tuple[Dependency, ...] = tuple(
            Dependency(dependent, dependency, frozenset(origins))
            for (dependent, dependency), origins in sorted(merged.items())
        )
        self._deps: dict[Slug, frozenset[Slug]] = {n: frozenset() for n in self._nodes}
        self._dependents: dict[Slug, frozenset[Slug]] = {n: frozenset() for n in self._nodes}
        for edge in self._edges:
            self._deps[edge.dependent] |= {edge.dependency}
            self._dependents[edge.dependency] |= {edge.dependent}

    # ------------------------------------------------------------------ queries
    @property
    def nodes(self) -> tuple[Slug, ...]:
        return self._nodes

    @property
    def edges(self) -> tuple[Dependency, ...]:
        return self._edges

    def dependencies_of(self, slug: Slug, *, transitive: bool = False) -> frozenset[Slug]:
        """Projects ``slug`` needs (directly, or through any chain if ``transitive``)."""
        return self._reach(slug, self._deps, transitive)

    def dependents_of(self, slug: Slug, *, transitive: bool = False) -> frozenset[Slug]:
        """Projects that need ``slug``: what breaks or goes stale if ``slug`` changes."""
        return self._reach(slug, self._dependents, transitive)

    def _reach(
        self, slug: Slug, adjacency: dict[Slug, frozenset[Slug]], transitive: bool
    ) -> frozenset[Slug]:
        if slug not in adjacency:
            raise ManifestError(f"Unknown project {slug!r}")
        if not transitive:
            return adjacency[slug]
        seen: set[Slug] = set()
        stack = list(adjacency[slug])
        while stack:
            current = stack.pop()
            if current not in seen:
                seen.add(current)
                stack.extend(adjacency[current])
        return frozenset(seen)

    # --------------------------------------------------------------- algorithms
    def find_cycle(self) -> tuple[Slug, ...] | None:
        """Return one dependency cycle as an ordered tuple of slugs, or ``None``.

        Iterative three-colour DFS (no recursion limit to trip over). The returned tuple
        is a real cycle: each element depends on the next, and the last on the first.
        """
        white, grey, black = 0, 1, 2
        colour = dict.fromkeys(self._nodes, white)
        for root in self._nodes:
            if colour[root] != white:
                continue
            path: list[Slug] = []
            stack: list[tuple[Slug, Iterator[Slug]]] = [(root, iter(sorted(self._deps[root])))]
            colour[root] = grey
            path.append(root)
            while stack:
                node, children = stack[-1]
                advanced = False
                for child in children:
                    if colour[child] == grey:
                        return tuple(path[path.index(child) :])
                    if colour[child] == white:
                        colour[child] = grey
                        path.append(child)
                        stack.append((child, iter(sorted(self._deps[child]))))
                        advanced = True
                        break
                if not advanced:
                    colour[node] = black
                    path.pop()
                    stack.pop()
        return None

    def generations(self) -> tuple[tuple[Slug, ...], ...]:
        """Group projects into layers that may run in parallel, dependencies first.

        Layer *n* contains every project whose dependencies all sit in layers before *n*
        (Kahn's algorithm). Raises :class:`DependencyCycleError` when no order exists.
        """
        cycle = self.find_cycle()
        if cycle is not None:
            raise DependencyCycleError(cycle)
        remaining = {n: set(self._deps[n]) for n in self._nodes}
        layers: list[tuple[Slug, ...]] = []
        while remaining:
            ready = tuple(sorted(n for n, deps in remaining.items() if not deps))
            layers.append(ready)
            for done in ready:
                del remaining[done]
            for deps in remaining.values():
                deps.difference_update(ready)
        return tuple(layers)

    def topological_order(self) -> tuple[Slug, ...]:
        return tuple(slug for layer in self.generations() for slug in layer)

    def execution_plan(
        self, targets: Iterable[Slug], *, include_dependencies: bool = False
    ) -> tuple[tuple[Slug, ...], ...]:
        """Layers to run for ``targets`` only, keeping the global dependency order.

        With ``include_dependencies`` each target's prerequisites are scheduled too
        (useful on a fresh checkout); by default only the targets run, because in this
        portfolio a project loads its siblings' *code*, not their *output*.
        """
        wanted: set[Slug] = set(targets)
        for slug in list(wanted):
            if slug not in self._deps:
                raise ManifestError(f"Unknown project {slug!r}")
            if include_dependencies:
                wanted |= self.dependencies_of(slug, transitive=True)
        layers = (tuple(s for s in layer if s in wanted) for layer in self.generations())
        return tuple(layer for layer in layers if layer)

    def impact_of(self, changed: Iterable[Slug]) -> tuple[Slug, ...]:
        """Everything affected by a change to ``changed``, in a safe re-run order.

        The changed projects themselves plus all their transitive dependents.
        """
        affected: set[Slug] = set()
        for slug in changed:
            affected.add(slug)
            affected |= self.dependents_of(slug, transitive=True)
        order = self.topological_order()
        return tuple(s for s in order if s in affected)
