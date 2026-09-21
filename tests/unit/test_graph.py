from __future__ import annotations

import pytest

from quant_workbench.domain.errors import DependencyCycleError, ManifestError
from quant_workbench.domain.graph import Dependency, DependencyGraph, EdgeOrigin
from quant_workbench.domain.ids import Slug, parse_slug

DECLARED = frozenset({EdgeOrigin.DECLARED})


def s(name: str) -> Slug:
    return parse_slug(name)


def edge(dependent: str, dependency: str, origins: frozenset[EdgeOrigin] = DECLARED) -> Dependency:
    return Dependency(s(dependent), s(dependency), origins)


def portfolio() -> DependencyGraph:
    """The real shape of the portfolio: 10->{2,9}, 7->{6,3}, 6->{3}, 8->{6}."""
    nodes = [s(f"p{i}") for i in range(1, 11)]
    return DependencyGraph(
        nodes,
        [
            edge("p10", "p2"),
            edge("p10", "p9"),
            edge("p7", "p6"),
            edge("p7", "p3"),
            edge("p6", "p3"),
            edge("p8", "p6"),
        ],
    )


def test_generations_put_dependencies_before_dependents() -> None:
    layers = portfolio().generations()

    assert layers[0] == tuple(s(n) for n in ("p1", "p2", "p3", "p4", "p5", "p9"))
    # Ties are broken alphabetically, so "p10" sorts before "p6".
    assert layers[1] == (s("p10"), s("p6"))
    assert layers[2] == (s("p7"), s("p8"))
    assert sum(len(layer) for layer in layers) == 10


def test_transitive_queries() -> None:
    graph = portfolio()

    assert graph.dependencies_of(s("p7")) == {s("p6"), s("p3")}
    assert graph.dependencies_of(s("p8"), transitive=True) == {s("p6"), s("p3")}
    assert graph.dependents_of(s("p3")) == {s("p6"), s("p7")}
    assert graph.dependents_of(s("p3"), transitive=True) == {s("p6"), s("p7"), s("p8")}
    assert graph.dependents_of(s("p1"), transitive=True) == frozenset()


def test_impact_lists_the_changed_project_and_dependents_in_run_order() -> None:
    assert portfolio().impact_of([s("p3")]) == (s("p3"), s("p6"), s("p7"), s("p8"))
    assert portfolio().impact_of([s("p1")]) == (s("p1"),)
    assert portfolio().impact_of([s("p2"), s("p9")]) == (s("p2"), s("p9"), s("p10"))


def test_execution_plan_runs_only_targets_by_default() -> None:
    plan = portfolio().execution_plan([s("p7"), s("p8")])
    assert plan == ((s("p7"), s("p8")),)


def test_execution_plan_can_pull_in_prerequisites() -> None:
    plan = portfolio().execution_plan([s("p7")], include_dependencies=True)
    assert plan == ((s("p3"),), (s("p6"),), (s("p7"),))


def test_duplicate_edges_merge_their_origins() -> None:
    graph = DependencyGraph(
        [s("a"), s("b")],
        [
            edge("a", "b", frozenset({EdgeOrigin.DECLARED})),
            edge("a", "b", frozenset({EdgeOrigin.DETECTED})),
        ],
    )
    (only,) = graph.edges
    assert only.origins == {EdgeOrigin.DECLARED, EdgeOrigin.DETECTED}


def test_cycle_is_reported_as_a_real_loop() -> None:
    graph = DependencyGraph(
        [s("a"), s("b"), s("c")], [edge("a", "b"), edge("b", "c"), edge("c", "a")]
    )

    cycle = graph.find_cycle()
    assert cycle is not None
    assert set(cycle) == {s("a"), s("b"), s("c")}
    with pytest.raises(DependencyCycleError) as excinfo:
        graph.generations()
    assert excinfo.value.cycle == cycle


def test_a_self_dependency_is_a_cycle() -> None:
    with pytest.raises(DependencyCycleError):
        DependencyGraph([s("a")], [edge("a", "a")])


def test_unknown_projects_are_rejected() -> None:
    with pytest.raises(ManifestError, match="unknown project"):
        DependencyGraph([s("a")], [edge("a", "ghost")])
    with pytest.raises(ManifestError):
        DependencyGraph([s("a")]).dependents_of(s("ghost"))


def test_an_empty_graph_has_no_layers() -> None:
    assert DependencyGraph([]).generations() == ()
