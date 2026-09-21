"""``qw run``, ``qw run-all``, ``qw env``, ``qw history`` and ``qw logs``."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Annotated

import typer
from rich.table import Table
from rich.text import Text

from quant_workbench.application.catalog import Catalog
from quant_workbench.application.environments import EnvironmentStatus
from quant_workbench.application.runs import BatchResult, RunOptions
from quant_workbench.bootstrap import Container
from quant_workbench.cli.catalog_commands import WorkspaceOption
from quant_workbench.cli.common import console, get_container, handled, load_catalog
from quant_workbench.cli.live import STATUS_STYLE, LiveConsole, format_duration
from quant_workbench.domain.errors import ManifestError
from quant_workbench.domain.ids import RunId, Slug
from quant_workbench.domain.runs import Run, RunStatus

EXIT_RUN_FAILED = 1
EXIT_INTERRUPTED = 130
_WIDE_TERMINAL_COLUMNS = 110

TimeoutOption = Annotated[
    int | None,
    typer.Option("--timeout", help="Seconds before a run is killed (default from settings)."),
]
RetriesOption = Annotated[
    int | None, typer.Option("--retries", help="Automatic retries for transient failures.")
]
QuietOption = Annotated[bool, typer.Option("--quiet", "-q", help="Show only the final summary.")]


def _with_concurrency(ctx: typer.Context, concurrency: int | None) -> Container:
    """A container whose executor uses ``concurrency`` slots (settings are immutable)."""
    container = get_container(ctx)
    if concurrency is None:
        return container
    settings = container.settings.model_copy(update={"max_concurrency": max(1, concurrency)})
    replaced = dataclasses.replace(container, settings=settings)
    ctx.call_on_close(replaced.close)  # the copy owns its own database connection
    return replaced


def _summary(result: BatchResult) -> Table:
    table = Table(title="Summary")
    for column in ("project", "status", "time", "attempt", "metrics", "note"):
        table.add_column(column)
    for run in sorted(result.runs, key=lambda r: r.queued_at):
        table.add_row(
            run.project,
            Text(run.status.value, style=STATUS_STYLE[run.status]),
            format_duration(run),
            str(run.attempt),
            str(len(run.metrics)),
            run.failure or "",
        )
    return table


def _execute(
    container: Container,
    catalog: Catalog,
    targets: list[Slug] | None,
    *,
    options: RunOptions,
    quiet: bool,
    include_dependencies: bool = False,
    skip_dependents: bool = False,
) -> BatchResult:
    """Run a batch with live output, and translate Ctrl+C into a clean exit."""
    detach = LiveConsole(console, show_output=not quiet).attach(container.events)
    try:
        return asyncio.run(
            container.runs.run_batch(
                catalog,
                targets,
                include_dependencies=include_dependencies,
                skip_dependents_on_failure=skip_dependents,
                options=options,
            )
        )
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted: running processes were stopped.[/]")
        raise typer.Exit(EXIT_INTERRUPTED) from None
    finally:
        detach()


def _finish(result: BatchResult) -> None:
    console.print(_summary(result))
    if not result.succeeded:
        raise typer.Exit(EXIT_RUN_FAILED)


@handled
def run(
    ctx: typer.Context,
    projects: Annotated[list[str], typer.Argument(help="Project slugs to run.")],
    *,
    workspace: WorkspaceOption = None,
    timeout: TimeoutOption = None,
    retries: RetriesOption = None,
    quiet: QuietOption = False,
    with_dependencies: Annotated[
        bool,
        typer.Option("--with-dependencies", help="Also run the projects they depend on first."),
    ] = False,
) -> None:
    """Run one or more projects (concurrently where the dependency graph allows)."""
    container = get_container(ctx)
    catalog = load_catalog(container, workspace)
    slugs = [catalog.get(name).slug for name in projects]
    options = RunOptions(timeout_seconds=timeout, max_retries=retries)
    _finish(
        _execute(
            container,
            catalog,
            slugs,
            options=options,
            quiet=quiet,
            include_dependencies=with_dependencies,
        )
    )


@handled
def run_all(
    ctx: typer.Context,
    *,
    workspace: WorkspaceOption = None,
    concurrency: Annotated[
        int | None, typer.Option("--concurrency", "-j", help="Runs at the same time.")
    ] = None,
    timeout: TimeoutOption = None,
    retries: RetriesOption = None,
    quiet: QuietOption = False,
    skip_dependents: Annotated[
        bool,
        typer.Option("--skip-dependents", help="Skip projects whose prerequisites failed."),
    ] = False,
) -> None:
    """Run every project, layer by layer through the dependency graph."""
    container = _with_concurrency(ctx, concurrency)
    catalog = load_catalog(container, workspace)
    options = RunOptions(timeout_seconds=timeout, max_retries=retries)
    _finish(
        _execute(
            container, catalog, None, options=options, quiet=quiet, skip_dependents=skip_dependents
        )
    )


# --------------------------------------------------------------------- env
env_app = typer.Typer(
    name="env", help="Inspect and set up per-project virtual environments.", no_args_is_help=True
)


@handled
def env_status(
    ctx: typer.Context,
    projects: Annotated[list[str] | None, typer.Argument(help="Slugs (default: all).")] = None,
    workspace: WorkspaceOption = None,
) -> None:
    """Report whether each environment exists and has its requirements installed."""
    container = get_container(ctx)
    catalog = load_catalog(container, workspace)
    selected = [catalog.get(p) for p in projects] if projects else list(catalog.projects)

    async def gather() -> list[tuple[str, EnvironmentStatus]]:
        return [(p.slug, await container.environments.inspect(p)) for p in selected]

    table = Table(title="Environments")
    for column in ("project", "python", "installed", "missing"):
        table.add_column(column)
    unhealthy = False
    for slug, status in asyncio.run(gather()):
        unhealthy |= not status.healthy
        missing = ", ".join(r.name for r in status.missing)
        table.add_row(
            slug,
            status.python_version or "[red]no environment[/]",
            str(len(status.installed)),
            f"[red]{missing}[/]" if status.missing else "[green]-[/]",
        )
    console.print(table)
    if unhealthy:
        raise typer.Exit(EXIT_RUN_FAILED)


@handled
def env_setup(
    ctx: typer.Context,
    projects: Annotated[list[str] | None, typer.Argument(help="Slugs to set up.")] = None,
    workspace: WorkspaceOption = None,
    all_projects: Annotated[bool, typer.Option("--all", help="Set up every project.")] = False,
    quiet: QuietOption = False,
) -> None:
    """Create a project's virtual environment and install its requirements (also repairs)."""
    container = get_container(ctx)
    catalog = load_catalog(container, workspace)
    if not projects and not all_projects:
        raise ManifestError("Name at least one project, or pass --all", hint="See `qw list`.")
    selected = list(catalog.projects) if all_projects else [catalog.get(p) for p in projects or []]

    detach = LiveConsole(console, show_output=not quiet).attach(container.events)

    async def go() -> list[Run]:
        return [await container.environments.setup(project) for project in selected]

    try:
        runs = asyncio.run(go())
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted.[/]")
        raise typer.Exit(EXIT_INTERRUPTED) from None
    finally:
        detach()
    if any(r.status is not RunStatus.SUCCEEDED for r in runs):
        raise typer.Exit(EXIT_RUN_FAILED)


# ----------------------------------------------------------------- history
@handled
def history(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Argument(help="Only this project.")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many runs to show.")] = 20,
    workspace: WorkspaceOption = None,
) -> None:
    """Show recent runs, newest first."""
    container = get_container(ctx)
    slug = load_catalog(container, workspace).get(project).slug if project else None
    runs = container.repository.list_runs(slug, limit=limit)

    # Narrow terminals get a compact table: the id (needed by `qw logs`) and the status are
    # what matter, so the less useful columns are dropped rather than squeezed to nothing.
    wide = console.width >= _WIDE_TERMINAL_COLUMNS
    columns = ["id", "project", "status", "started", "time", "exit"]
    if wide:
        columns += ["kind", "metrics"]
    table = Table(title=f"Last {len(runs)} runs")
    for column in columns:
        # The id must never be truncated, however narrow the terminal.
        table.add_column(column, no_wrap=(column == "id"))
    for run in runs:
        cells: dict[str, str | Text] = {
            "id": run.id,
            "project": run.project,
            "status": Text(run.status.value, style=STATUS_STYLE[run.status]),
            "started": (
                run.started_at.astimezone().strftime("%m-%d %H:%M") if run.started_at else "-"
            ),
            "time": format_duration(run),
            "exit": "-" if run.exit_code is None else str(run.exit_code),
            "kind": run.kind.value,
            "metrics": str(len(run.metrics)),
        }
        table.add_row(*(cells[column] for column in columns))
    console.print(table)


@handled
def logs(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run id from `qw history`.")],
    tail: Annotated[int | None, typer.Option("--tail", help="Only the last N lines.")] = None,
) -> None:
    """Print the recorded output of a run, and its extracted metrics."""
    container = get_container(ctx)
    run = container.repository.get(RunId(run_id))
    if run is None:
        raise ManifestError(f"No run with id {run_id!r}", hint="Use `qw history` to list run ids.")

    lines = container.repository.logs(run.id)
    for line in lines[-tail:] if tail else lines:
        console.print(Text(line.text), soft_wrap=True, highlight=False)
    console.print(
        Text(
            f"-- {run.project}: {run.status.value}, {format_duration(run)}",
            style=STATUS_STYLE[run.status],
        )
    )
    for metric in run.metrics:
        console.print(Text(f"   {metric.name} = {metric.value} {metric.unit}".rstrip()))


def register(app: typer.Typer) -> None:
    app.command("run")(run)
    app.command("run-all")(run_all)
    app.command("history")(history)
    app.command("logs")(logs)
    env_app.command("status")(env_status)
    env_app.command("setup")(env_setup)
    app.add_typer(env_app)
