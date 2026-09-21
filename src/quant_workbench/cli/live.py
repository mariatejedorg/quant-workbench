"""Live console rendering of run events."""

from __future__ import annotations

from collections.abc import Callable
from itertools import cycle

from rich.console import Console
from rich.text import Text

from quant_workbench.domain.ports import EventBus
from quant_workbench.domain.run_events import RunFinished, RunOutput
from quant_workbench.domain.runs import LogLevel, LogStream, Run, RunStatus

_PALETTE = (
    "cyan",
    "magenta",
    "green",
    "yellow",
    "blue",
    "bright_red",
    "bright_cyan",
    "bright_magenta",
)
_LEVEL_STYLE = {LogLevel.ERROR: "red", LogLevel.WARNING: "yellow", LogLevel.INFO: ""}
STATUS_STYLE = {
    RunStatus.SUCCEEDED: "bold green",
    RunStatus.FAILED: "bold red",
    RunStatus.TIMED_OUT: "bold red",
    RunStatus.CANCELLED: "bold yellow",
    RunStatus.SKIPPED: "dim",
    RunStatus.QUEUED: "dim",
    RunStatus.RUNNING: "bold",
}


_SECONDS_PER_DISPLAYED_MINUTE = 120  # below two minutes, seconds read better than m:ss


def format_duration(run: Run) -> str:
    duration = run.duration
    if duration is None:
        return "-"
    seconds = duration.total_seconds()
    return (
        f"{seconds:.1f}s"
        if seconds < _SECONDS_PER_DISPLAYED_MINUTE
        else f"{int(seconds // 60)}m{int(seconds % 60):02d}s"
    )


class LiveConsole:
    """Prints every run's output as it arrives, each line prefixed by its project.

    Lines are built as :class:`rich.text.Text` (never parsed as markup), so program
    output containing ``[brackets]`` cannot be mistaken for styling instructions.
    """

    def __init__(self, console: Console, *, show_output: bool = True) -> None:
        self._console = console
        self._show_output = show_output
        self._colours: dict[str, str] = {}
        self._palette = cycle(_PALETTE)

    def _colour(self, project: str) -> str:
        if project not in self._colours:
            self._colours[project] = next(self._palette)
        return self._colours[project]

    def attach(self, events: EventBus) -> Callable[[], None]:
        """Subscribe to the bus; returns a function that detaches the console again."""
        cancel_output = events.subscribe(RunOutput, self._on_output)
        cancel_finished = events.subscribe(RunFinished, self._on_finished)

        def detach() -> None:
            cancel_output()
            cancel_finished()

        return detach

    def _prefix(self, project: str) -> Text:
        return Text(f"[{project}] ", style=self._colour(project))

    def _on_output(self, event: RunOutput) -> None:
        if not self._show_output:
            return
        line = event.line
        style = "dim" if line.stream is LogStream.SYSTEM else _LEVEL_STYLE[line.level]
        self._console.print(
            self._prefix(event.project) + Text(line.text, style=style),
            soft_wrap=True,
            highlight=False,
        )

    def _on_finished(self, event: RunFinished) -> None:
        run = event.run
        label = Text(run.status.value.upper(), style=STATUS_STYLE[run.status])
        message = Text(f" ({format_duration(run)})")
        if run.failure and run.status is not RunStatus.SUCCEEDED:
            message.append(f" {run.failure}", style="red")
        self._console.print(
            self._prefix(run.project) + label + message, soft_wrap=True, highlight=False
        )
