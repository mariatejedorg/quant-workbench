"""``qw config get | diff | set``: read and edit a project's configuration constants."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.syntax import Syntax
from rich.table import Table

from quant_workbench.application.config import ConfigPlan
from quant_workbench.cli.catalog_commands import WorkspaceOption
from quant_workbench.cli.common import console, get_container, handled, load_catalog
from quant_workbench.domain.errors import UnsafeEditError

config_app = typer.Typer(
    name="config",
    help="Read and edit a project's configuration constants, preserving comments and layout.",
    no_args_is_help=True,
)

AssignmentsArgument = Annotated[
    list[str],
    typer.Argument(help="One or more NAME=VALUE (or file:NAME=VALUE) assignments."),
]


def _first_line(source: str) -> str:
    lines = source.replace("\r", "").split("\n")
    return lines[0] if len(lines) == 1 else f"{lines[0]} … ({len(lines)} lines)"


def _split_assignments(assignments: list[str]) -> dict[str, str]:
    """``["A=1", "B=x=y"]`` -> ``{"A": "1", "B": "x=y"}`` (only the first ``=`` splits)."""
    parsed: dict[str, str] = {}
    for item in assignments:
        name, separator, text = item.partition("=")
        if not separator or not name.strip():
            raise UnsafeEditError(
                f"'{item}' is not an assignment", hint="Use NAME=VALUE, e.g. RISK_FREE_RATE=0.03"
            )
        parsed[name.strip()] = text
    return parsed


def _print_diff(plan: ConfigPlan) -> None:
    if plan.is_empty:
        console.print("[yellow]No changes: the requested values are already set.[/]")
        return
    console.print(Syntax(plan.diff.replace("\r", ""), "diff", theme="ansi_dark"))


@handled
def config_get(
    ctx: typer.Context,
    project: Annotated[str, typer.Argument(help="Project slug.")],
    name: Annotated[str | None, typer.Argument(help="Show only this constant.")] = None,
    workspace: WorkspaceOption = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """List the editable constants of a project with their type, value and description."""
    container = get_container(ctx)
    catalog = load_catalog(container, workspace)
    entries = container.config.constants(catalog.get(project))
    if name is not None:
        entries = tuple(e for e in entries if name in (e.constant.name, e.qualified_name))
        if not entries:
            raise UnsafeEditError(f"No editable constant named {name}")

    if as_json:
        payload = [
            {
                "file": e.file,
                "name": e.constant.name,
                "type": e.constant.kind.value,
                "source": e.constant.source,
                "description": e.constant.description,
                "line": e.constant.line,
            }
            for e in entries
        ]
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    table = Table(title=f"{len(entries)} editable constants in {project}")
    for column in ("file", "name", "type", "value", "description"):
        table.add_column(column, overflow="fold", no_wrap=column == "name")
    for entry in entries:
        constant = entry.constant
        table.add_row(
            entry.file,
            f"[bold]{constant.name}[/]",
            constant.kind.value,
            _first_line(constant.source),
            constant.description,
        )
    console.print(table)


@handled
def config_diff(
    ctx: typer.Context,
    project: Annotated[str, typer.Argument(help="Project slug.")],
    assignments: AssignmentsArgument,
    workspace: WorkspaceOption = None,
) -> None:
    """Show what ``qw config set`` would change, without writing anything."""
    container = get_container(ctx)
    target = load_catalog(container, workspace).get(project)
    values = container.config.parse_assignments(target, _split_assignments(assignments))
    _print_diff(container.config.plan(target, values))


@handled
def config_set(
    ctx: typer.Context,
    project: Annotated[str, typer.Argument(help="Project slug.")],
    assignments: AssignmentsArgument,
    workspace: WorkspaceOption = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the diff and stop, like `qw config diff`.")
    ] = False,
) -> None:
    """Set constants, print the diff, and write it (a backup of each file is kept)."""
    container = get_container(ctx)
    target = load_catalog(container, workspace).get(project)
    values = container.config.parse_assignments(target, _split_assignments(assignments))
    plan = container.config.plan(target, values)
    _print_diff(plan)
    if plan.is_empty or dry_run:
        return
    result = container.config.apply(target, plan)
    console.print(f"[green]Updated[/] {', '.join(result.files)}")
    for backup in result.backups:
        console.print(f"[dim]backup: {backup}[/]")


def register(app: typer.Typer) -> None:
    config_app.command("get")(config_get)
    config_app.command("diff")(config_diff)
    config_app.command("set")(config_set)
    app.add_typer(config_app)
