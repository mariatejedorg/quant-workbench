"""``qw sync``: clone the portfolio's repositories into the workspace."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from quant_workbench.application.sync import SyncOutcome, SyncStatus
from quant_workbench.cli.catalog_commands import WorkspaceOption
from quant_workbench.cli.common import (
    console,
    get_container,
    handled,
    load_catalog,
    resolve_workspace,
)
from quant_workbench.cli.doctor_commands import print_report
from quant_workbench.domain.diagnostics import Severity
from quant_workbench.domain.runs import RunStatus

EXIT_SYNC_FAILED = 1

_STYLE = {
    SyncStatus.CLONED: "green",
    SyncStatus.WOULD_CLONE: "cyan",
    SyncStatus.PRESENT: "dim",
    SyncStatus.REFUSED: "yellow",
    SyncStatus.FAILED: "bold red",
}


def _show(outcome: SyncOutcome) -> None:
    style = _STYLE[outcome.status]
    detail = f"  [dim]{outcome.detail}[/]" if outcome.detail else ""
    console.print(
        f"[{style}]{outcome.status.value:<15}[/] {outcome.spec.slug}{detail}", highlight=False
    )


@handled
def sync(
    ctx: typer.Context,
    projects: Annotated[
        list[str] | None, typer.Argument(help="Slugs (default: all that are missing).")
    ] = None,
    *,
    workspace: WorkspaceOption = None,
    setup: Annotated[
        bool, typer.Option("--setup", help="Also create each new environment and install it.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Only show what would be cloned.")
    ] = False,
) -> None:
    """Clone the registry's projects that the workspace lacks (existing ones are never touched)."""
    container = get_container(ctx)
    # The target may be a brand-new folder: sync is how an empty workspace gets filled.
    resolve_workspace(container, workspace).mkdir(parents=True, exist_ok=True)
    catalog = load_catalog(container, workspace)
    if not catalog.missing and not projects:
        console.print("[green]Nothing to sync:[/] every project of the registry is present.")
        return

    outcomes = container.sync.sync(catalog, projects or (), dry_run=dry_run, on_outcome=_show)
    problems = not all(o.ok for o in outcomes)
    cloned = [o.spec.slug for o in outcomes if o.status is SyncStatus.CLONED]
    if not cloned:
        raise typer.Exit(EXIT_SYNC_FAILED if problems else 0)

    fresh = load_catalog(container, workspace)
    targets = [fresh.get(slug) for slug in cloned]
    if setup:
        console.print("\n[bold]Setting up environments[/] (this downloads the dependencies)...")

        async def build() -> list[RunStatus]:
            return [(await container.environments.setup(p)).status for p in targets]

        broken = [s for s in asyncio.run(build()) if s is not RunStatus.SUCCEEDED]
        if broken:
            console.print(f"[red]{len(broken)} environment(s) failed to install.[/]")
            raise typer.Exit(EXIT_SYNC_FAILED)

    report = asyncio.run(container.doctor.run(container.check_context(fresh), targets))
    console.print()
    print_report(report, fresh, Severity.WARNING)
    if not setup:
        console.print("\nNext: [bold]qw env setup --all[/] creates the environments.")
    if problems:
        raise typer.Exit(EXIT_SYNC_FAILED)


def register(app: typer.Typer) -> None:
    app.command("sync")(sync)
