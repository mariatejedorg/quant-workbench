"""``qw list``, ``qw graph`` and ``qw schema``."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from quant_workbench.application.catalog import Catalog
from quant_workbench.application.manifest_schema import export_json_schema
from quant_workbench.cli.common import console, get_container, handled, load_catalog
from quant_workbench.domain.graph import EdgeOrigin
from quant_workbench.domain.ids import parse_slug

WorkspaceOption = Annotated[
    Path | None,
    typer.Option("--workspace", "-w", help="Folder that contains the projects (auto-detected)."),
]


class GraphFormat(StrEnum):
    TEXT = "text"
    MERMAID = "mermaid"
    JSON = "json"


def _origin_label(origins: frozenset[EdgeOrigin]) -> str:
    if origins == {EdgeOrigin.DECLARED, EdgeOrigin.DETECTED}:
        return "declared + detected"
    return next(iter(origins)).value


def _catalog_json(catalog: Catalog) -> list[dict[str, object]]:
    return [
        {
            "slug": p.slug,
            "title": p.title,
            "folder": p.root.name,
            "category": p.spec.category,
            "source": p.source.value,
            "depends_on": sorted(catalog.graph.dependencies_of(p.slug)),
            "has_venv": p.venv_python.is_file(),
            "has_dashboard": bool(p.dashboard_path and p.dashboard_path.is_file()),
        }
        for p in catalog.projects
    ]


@handled
def list_projects(
    ctx: typer.Context,
    workspace: WorkspaceOption = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """List the projects found in the workspace."""
    catalog = load_catalog(get_container(ctx), workspace)

    if as_json:
        typer.echo(json.dumps(_catalog_json(catalog), indent=2, ensure_ascii=False))
        return

    table = Table(title=f"{len(catalog.projects)} projects in {catalog.workspace}")
    for column in ("#", "slug", "folder", "category", "source", "needs", "venv", "dashboard"):
        table.add_column(column)
    for index, project in enumerate(catalog.projects, start=1):
        needs = ", ".join(sorted(catalog.graph.dependencies_of(project.slug))) or "-"
        table.add_row(
            str(index),
            project.slug,
            project.root.name,
            project.spec.category,
            project.source.value,
            needs,
            "[green]yes[/]" if project.venv_python.is_file() else "[red]no[/]",
            "[green]yes[/]"
            if project.dashboard_path and project.dashboard_path.is_file()
            else "[red]no[/]",
        )
    console.print(table)
    if catalog.missing:
        console.print(
            f"[yellow]{len(catalog.missing)} registry project(s) not in this workspace:[/] "
            + ", ".join(spec.slug for spec in catalog.missing)
        )


@handled
def graph(
    ctx: typer.Context,
    workspace: WorkspaceOption = None,
    fmt: Annotated[
        GraphFormat, typer.Option("--format", "-f", help="Output format.")
    ] = GraphFormat.TEXT,
    impact: Annotated[
        str | None,
        typer.Option("--impact", help="Show what is affected if this project changes."),
    ] = None,
) -> None:
    """Show the dependency graph: execution layers, edges and their origin."""
    catalog = load_catalog(get_container(ctx), workspace)
    dep_graph = catalog.graph

    if impact is not None:
        slug = catalog.get(impact).slug
        order = dep_graph.impact_of([slug])
        typer.echo(f"Changing '{slug}' affects, in re-run order:")
        for item in order:
            typer.echo(f"  {'*' if item == slug else '-'} {item}")
        return

    if fmt is GraphFormat.MERMAID:
        typer.echo("flowchart LR")
        for project in catalog.projects:
            typer.echo(f'    {_node_id(project.slug)}["{project.spec.title}"]')
        for edge in dep_graph.edges:
            arrow = "-->" if EdgeOrigin.DECLARED in edge.origins else "-.->"
            typer.echo(f"    {_node_id(edge.dependent)} {arrow} {_node_id(edge.dependency)}")
    elif fmt is GraphFormat.JSON:
        payload = {
            "layers": [list(layer) for layer in dep_graph.generations()],
            "edges": [
                {
                    "from": e.dependent,
                    "to": e.dependency,
                    "origins": sorted(o.value for o in e.origins),
                }
                for e in dep_graph.edges
            ],
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo("Execution layers (each layer can run in parallel):")
        for index, layer in enumerate(dep_graph.generations(), start=1):
            typer.echo(f"  {index}. {', '.join(layer)}")
        typer.echo("\nDependencies (X -> Y means X needs Y):")
        for edge in dep_graph.edges:
            typer.echo(f"  {edge.dependent} -> {edge.dependency}   [{_origin_label(edge.origins)}]")
        if not dep_graph.edges:
            typer.echo("  (none)")


def _node_id(slug: str) -> str:
    # Mermaid ids cannot contain hyphens reliably across renderers.
    return "n_" + parse_slug(slug).replace("-", "_")


@handled
def schema(
    write: Annotated[
        Path | None, typer.Option("--write", help="Write the schema to this file.")
    ] = None,
) -> None:
    """Print the JSON Schema of ``quant-project.toml`` (for editor completion)."""
    text = json.dumps(export_json_schema(), indent=2)
    if write is None:
        typer.echo(text)
    else:
        write.write_text(text + "\n", encoding="utf-8")
        typer.echo(f"Wrote {write}")


def register(app: typer.Typer) -> None:
    app.command("list")(list_projects)
    app.command("graph")(graph)
    app.command("schema")(schema)
