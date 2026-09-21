"""``qw new``: create a project that follows the portfolio's conventions."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from quant_workbench.cli.catalog_commands import WorkspaceOption
from quant_workbench.cli.common import console, get_container, handled, load_catalog
from quant_workbench.cli.doctor_commands import print_report
from quant_workbench.domain.diagnostics import Severity


@handled
def new(
    ctx: typer.Context,
    slug: Annotated[str, typer.Argument(help="Short lowercase name, e.g. 'pairs-trading'.")],
    *,
    workspace: WorkspaceOption = None,
    title: Annotated[str | None, typer.Option(help="Human title (default: from the slug).")] = None,
    category: Annotated[str, typer.Option(help="Group in the project list.")] = "New projects",
    description: Annotated[str | None, typer.Option(help="One-sentence description.")] = None,
    folder: Annotated[
        str | None, typer.Option(help="Folder name (default: proyecto-<n>-<slug>).")
    ] = None,
) -> None:
    """Create a new project that already runs, then check it with the doctor."""
    container = get_container(ctx)
    catalog = load_catalog(container, workspace)
    result = container.scaffold.create(
        catalog, slug, title=title, category=category, description=description, folder=folder
    )
    console.print(f"[green]Created[/] {result.project.root}")
    for relative in result.files:
        console.print(f"  {relative}", highlight=False)
    if result.ca_bundle_from:
        console.print(f"  .certs/cacert.pem  [dim](copied from {result.ca_bundle_from})[/]")

    # Verify what we just wrote: a generator whose output fails the doctor is a bug.
    fresh = load_catalog(container, workspace)
    context = container.check_context(fresh)
    report = asyncio.run(container.doctor.run(context, [fresh.get(result.project.slug)]))
    console.print()
    print_report(report, fresh, Severity.WARNING)
    console.print(
        f"\nNext: [bold]qw doctor {result.project.slug} --fix[/] creates the environment, "
        f"then [bold]qw run {result.project.slug}[/] runs it."
    )


def register(app: typer.Typer) -> None:
    app.command("new")(new)
