"""``qw study``: practise the README concepts with spaced repetition."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.panel import Panel
from rich.table import Table

from quant_workbench.bootstrap import Container
from quant_workbench.cli.catalog_commands import WorkspaceOption
from quant_workbench.cli.common import console, get_container, handled, load_catalog
from quant_workbench.domain.project import Project
from quant_workbench.domain.study import Grade

_GRADE_HELP = "0 blackout, 1 wrong, 2 wrong but familiar, 3 hard, 4 good, 5 easy (q to stop)"


def _print_stats(container: Container, projects: list[Project]) -> None:
    table = Table(title="Study progress")
    for column in ("project", "cards", "new", "due today", "learned"):
        table.add_column(column)
    for slug, stat in container.study.summary(projects).items():
        table.add_row(slug, str(stat.total), str(stat.new), str(stat.due), str(stat.learned))
    console.print(table)


def _ask_grade() -> Grade | None:
    """A grade from the user, or ``None`` to stop the session."""
    while True:
        raw = typer.prompt(f"Grade ({_GRADE_HELP})", default="", show_default=False).strip().lower()
        if raw in {"q", "quit"}:
            return None
        if raw.isdigit() and int(raw) in Grade._value2member_map_:
            return Grade(int(raw))
        console.print("[yellow]Type a number from 0 to 5, or q.[/]")


@handled
def study(
    ctx: typer.Context,
    projects: Annotated[list[str] | None, typer.Argument(help="Slugs (default: all).")] = None,
    *,
    workspace: WorkspaceOption = None,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, help="Cards per session.")] = 10,
    stats_only: Annotated[
        bool, typer.Option("--stats", help="Only show the progress, do not study.")
    ] = False,
) -> None:
    """Practise the 'Concepts to be able to explain' and 'Key findings' of the READMEs."""
    container = get_container(ctx)
    catalog = load_catalog(container, workspace)
    selected = [catalog.get(p) for p in projects] if projects else list(catalog.projects)
    if stats_only:
        _print_stats(container, selected)
        return

    queue = container.study.queue(selected, limit)
    if not queue:
        console.print("[green]Nothing due today.[/] Come back tomorrow, or study a project again.")
        _print_stats(container, selected)
        return
    done = 0
    for number, item in enumerate(queue, start=1):
        console.print(f"\n[bold]{number}/{len(queue)}[/]  [dim]{item.card.project}[/]")
        console.print(Panel(item.card.prompt, title="Question" + (" (new)" if item.is_new else "")))
        typer.prompt("Answer it out loud, then press Enter", default="", show_default=False)
        console.print(Panel(item.card.answer, title="Answer", border_style="green"))
        grade = _ask_grade()
        if grade is None:
            break
        state = container.study.answer(item, grade)
        console.print(f"[dim]next review in {state.interval_days} day(s)[/]")
        done += 1
    console.print(f"\n[bold]{done} card(s) reviewed.[/]")
    _print_stats(container, selected)


def register(app: typer.Typer) -> None:
    app.command("study")(study)
