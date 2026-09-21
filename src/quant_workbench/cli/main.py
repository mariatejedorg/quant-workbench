"""``qw`` command line entry point."""

from __future__ import annotations

import importlib.util
import platform
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from quant_workbench import __version__
from quant_workbench.bootstrap import build_container
from quant_workbench.domain.paths import AppPaths

app = typer.Typer(
    name="qw",
    help="Quant Workbench: run, inspect, debug and extend a portfolio of quantitative projects.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"quant-workbench {__version__}")
        raise typer.Exit


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Quant Workbench command line."""


@app.command()
def info(
    home: Annotated[
        Path | None,
        typer.Option("--home", help="Keep all workbench files under this directory."),
    ] = None,
) -> None:
    """Show the resolved environment: versions, directories and settings."""
    container = build_container(paths=AppPaths.under(home) if home else None)
    settings = container.settings

    table = Table(title="Quant Workbench", show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold cyan")
    table.add_column()
    table.add_row("version", __version__)
    table.add_row("python", f"{platform.python_version()} ({sys.executable})")
    table.add_row("platform", platform.platform())
    table.add_row(
        "desktop UI", "available" if importlib.util.find_spec("PySide6") else "not installed"
    )
    table.add_row("config dir", str(container.paths.config_dir))
    table.add_row("data dir", str(container.paths.data_dir))
    table.add_row("log dir", str(container.paths.log_dir))
    table.add_row("workspace", str(settings.workspace_root or "(auto-detect)"))
    table.add_row("git identity", settings.expected_git_email)
    table.add_row("concurrency", str(settings.max_concurrency))
    console.print(table)
