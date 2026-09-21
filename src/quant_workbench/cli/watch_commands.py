"""``qw watch``: run a project again every time its code or configuration is saved."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from rich.text import Text

from quant_workbench.application.runs import RunOptions
from quant_workbench.application.watch import WatchedRun
from quant_workbench.cli.catalog_commands import WorkspaceOption
from quant_workbench.cli.common import console, get_container, handled, load_catalog
from quant_workbench.cli.live import STATUS_STYLE, LiveConsole, format_duration
from quant_workbench.domain.errors import WorkbenchError


def _report(item: WatchedRun) -> None:
    trigger = f"changed: {', '.join(item.changed)}" if item.changed else "first run"
    console.print(
        Text.assemble(
            (f"\nrun #{item.number} ", "bold"),
            (item.run.status.value, STATUS_STYLE[item.run.status]),
            (f" in {format_duration(item.run)} ", ""),
            (f"({trigger})", "dim"),
        )
    )
    console.print("[dim]Waiting for changes (Ctrl+C to stop)...[/]")


@handled
def watch(
    ctx: typer.Context,
    project: Annotated[str, typer.Argument(help="Slug of the project to watch.")],
    *,
    workspace: WorkspaceOption = None,
    initial: Annotated[
        bool, typer.Option("--initial/--no-initial", help="Run once before waiting.")
    ] = True,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Hide the program's output.")
    ] = False,
    timeout: Annotated[int | None, typer.Option(help="Seconds before a run is killed.")] = None,
) -> None:
    """Re-run a project whenever a Python file in `src/` or `config/` is saved."""
    container = get_container(ctx)
    catalog = load_catalog(container, workspace)
    target = catalog.get(project)
    directories = container.watch.watched_directories(target)
    if not directories:
        raise WorkbenchError(f"{target.slug} has no src/ or config folder to watch")
    console.print(
        "Watching " + ", ".join(str(d.relative_to(target.root)) for d in directories) + "/ ..."
    )

    detach = LiveConsole(console, show_output=not quiet).attach(container.events)
    try:
        asyncio.run(
            container.watch.watch(
                target,
                options=RunOptions(timeout_seconds=timeout),
                initial=initial,
                on_run=_report,
            )
        )
    except KeyboardInterrupt:
        console.print("[yellow]Stopped.[/]")
    finally:
        detach()


def register(app: typer.Typer) -> None:
    app.command("watch")(watch)
