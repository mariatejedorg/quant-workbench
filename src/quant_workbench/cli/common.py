"""Helpers shared by the CLI commands: error presentation and workspace resolution."""

from __future__ import annotations

import functools
import sys
from collections.abc import Callable
from pathlib import Path
from typing import ParamSpec, TypeVar

import typer
from rich.console import Console

from quant_workbench.application.catalog import Catalog
from quant_workbench.bootstrap import Container
from quant_workbench.domain.errors import DiscoveryError, WorkbenchError

P = ParamSpec("P")
R = TypeVar("R")

console = Console()
err_console = Console(stderr=True)

#: Exit status for failures the workbench anticipated (bad input, policy, environment).
EXIT_EXPECTED_FAILURE = 2


def configure_stdio() -> None:
    """Emit UTF-8 regardless of the console code page.

    Windows consoles default to cp1252 in pipes, which turns accented text (the real
    workspace is called ``Quant - María``) into mojibake or raises UnicodeEncodeError.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def handled(func: Callable[P, R]) -> Callable[P, R]:
    """Turn expected :class:`WorkbenchError` failures into a clean message and exit code 2.

    A bug (any other exception) still surfaces with a full traceback: only failures the
    workbench *predicted* are presented without one.
    """

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except WorkbenchError as exc:
            err_console.print(f"[bold red]error:[/] {exc.message}")
            if exc.hint:
                err_console.print(f"[yellow]hint:[/] {exc.hint}")
            raise typer.Exit(EXIT_EXPECTED_FAILURE) from exc

    return wrapper


def get_container(ctx: typer.Context) -> Container:
    """The container built once by the root callback (see ``cli/main.py``)."""
    container = ctx.obj
    if not isinstance(container, Container):  # pragma: no cover - programming error
        raise RuntimeError("CLI context has no container; the root callback did not run")
    return container


def resolve_workspace(container: Container, explicit: Path | None) -> Path:
    """``--workspace`` wins, then ``workspace_root`` from settings, then auto-detection."""
    if explicit is not None:
        return explicit.expanduser().resolve()
    if container.settings.workspace_root is not None:
        return container.settings.workspace_root
    detected = container.catalog.detect_workspace(Path.cwd())
    if detected is None:
        raise DiscoveryError(
            "Could not find a workspace containing projects from the current directory",
            hint="Run from inside the workspace, pass --workspace, or set 'workspace_root'.",
        )
    return detected


def load_catalog(container: Container, explicit: Path | None) -> Catalog:
    return container.catalog.load(resolve_workspace(container, explicit))
