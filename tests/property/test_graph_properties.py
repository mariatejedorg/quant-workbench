"""Property-based tests: the scheduler's guarantees must hold for *any* dependency graph."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from quant_workbench.domain.graph import Dependency, DependencyGraph, EdgeOrigin
from quant_workbench.domain.ids import Slug

DECLARED = frozenset({EdgeOrigin.DECLARED})


@st.composite
def dags(draw: st.DrawFn) -> DependencyGraph:
    """Random DAGs: edges only go from a higher index to a lower one, so no cycle exists."""
    size = draw(st.integers(min_value=1, max_value=14))
    nodes = [Slug(f"n{i:02d}") for i in range(size)]
    edges: list[Dependency] = []
    for high in range(1, size):
        for low in range(high):
            if draw(st.booleans()) and draw(st.integers(0, 3)) == 0:
                edges.append(Dependency(nodes[high], nodes[low], DECLARED))
    return DependencyGraph(nodes, edges)


@given(dags())
def test_every_node_is_scheduled_exactly_once(graph: DependencyGraph) -> None:
    scheduled = [slug for layer in graph.generations() for slug in layer]
    assert sorted(scheduled) == sorted(graph.nodes)


@given(dags())
def test_dependencies_always_run_in_an_earlier_layer(graph: DependencyGraph) -> None:
    layer_of = {slug: i for i, layer in enumerate(graph.generations()) for slug in layer}
    for edge in graph.edges:
        assert layer_of[edge.dependency] < layer_of[edge.dependent]


@given(dags())
def test_layers_are_maximally_parallel(graph: DependencyGraph) -> None:
    """A node sits in layer n only because one of its dependencies sits in layer n-1."""
    layers = graph.generations()
    layer_of = {slug: i for i, layer in enumerate(layers) for slug in layer}
    for slug, index in layer_of.items():
        if index > 0:
            assert any(layer_of[dep] == index - 1 for dep in graph.dependencies_of(slug))


@given(dags(), st.data())
def test_impact_is_closed_under_dependents(graph: DependencyGraph, data: st.DataObject) -> None:
    changed = data.draw(st.sampled_from(graph.nodes))
    impact = set(graph.impact_of([changed]))
    assert changed in impact
    for slug in impact:
        assert graph.dependents_of(slug) <= impact


@given(dags(), st.data())
def test_adding_a_back_edge_creates_a_detectable_real_cycle(
    graph: DependencyGraph, data: st.DataObject
) -> None:
    edges_with_paths = list(graph.edges)
    if not edges_with_paths:
        return
    chosen = data.draw(st.sampled_from(edges_with_paths))
    reverse = Dependency(chosen.dependency, chosen.dependent, DECLARED)
    cyclic = DependencyGraph(graph.nodes, [*graph.edges, reverse])

    cycle = cyclic.find_cycle()

    assert cycle is not None
    edge_set = {(e.dependent, e.dependency) for e in cyclic.edges}
    for current, following in zip(cycle, (*cycle[1:], cycle[0]), strict=True):
        assert (current, following) in edge_set
