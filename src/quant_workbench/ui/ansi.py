"""ANSI escape sequences (colours, bold, underline) turned into styled text runs.

Qt-free on purpose: the console widget only maps :class:`AnsiStyle` to a text format, so the
parsing, which is the part with edge cases (sequences split across chunks, 256-colour and
true-colour codes, cursor movement that must simply disappear), is testable without a display.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace

#: The 16 standard terminal colours (normal, then bright), as hex.
_BASIC = (
    "#1c1c1c",
    "#cd3131",
    "#0dbc79",
    "#e5e510",
    "#2472c8",
    "#bc3fbc",
    "#11a8cd",
    "#e5e5e5",
    "#666666",
    "#f14c4c",
    "#23d18b",
    "#f5f543",
    "#3b8eea",
    "#d670d6",
    "#29b8db",
    "#ffffff",
)

#: SGR codes that select one of the 16 basic colours: 30-37 / 90-97 (foreground), 40-47 / 100-107.
_FOREGROUND = (*range(30, 38), *range(90, 98))
_BACKGROUND = (*range(40, 48), *range(100, 108))
_SET_FOREGROUND, _SET_BACKGROUND = 38, 48
_MODE_256, _MODE_TRUE_COLOUR = 5, 2

_SEQUENCE = re.compile(r"\x1b\[([0-9;:?]*)([@-~])")
#: An escape sequence that started but has not finished yet (the chunk ended in the middle).
_INCOMPLETE = re.compile(r"\x1b(?:\[[0-9;:?]*)?$")
_XTERM_LEVELS = (0, 95, 135, 175, 215, 255)
_LAST_BASIC = 15
_FIRST_GRAY = 232
_LAST_CUBE = 231
_FIRST_CUBE = 16


@dataclass(frozen=True, slots=True)
class AnsiStyle:
    """How a run of text is drawn. ``None`` colours mean "the widget's default"."""

    fg: str | None = None
    bg: str | None = None
    bold: bool = False
    dim: bool = False
    underline: bool = False


_SIMPLE_CODES: dict[int, Callable[[AnsiStyle], AnsiStyle]] = {
    0: lambda style: AnsiStyle(),
    1: lambda style: replace(style, bold=True),
    2: lambda style: replace(style, dim=True),
    4: lambda style: replace(style, underline=True),
    22: lambda style: replace(style, bold=False, dim=False),
    24: lambda style: replace(style, underline=False),
    39: lambda style: replace(style, fg=None),
    49: lambda style: replace(style, bg=None),
}


def _xterm_256(index: int) -> str:
    """The colour of entry ``index`` of the xterm 256-colour palette."""
    if index <= _LAST_BASIC:
        return _BASIC[index]
    if index <= _LAST_CUBE:
        cube = index - _FIRST_CUBE
        red, green, blue = cube // 36, (cube // 6) % 6, cube % 6
        return "#{:02x}{:02x}{:02x}".format(*(_XTERM_LEVELS[c] for c in (red, green, blue)))
    level = 8 + 10 * (index - _FIRST_GRAY)
    return f"#{level:02x}{level:02x}{level:02x}"


class AnsiParser:
    """Splits text into ``(text, style)`` runs, remembering the style between calls.

    A process writes colour codes in one line and expects them to hold for the next ones, and
    a read from a pipe can end in the middle of an escape sequence; both are handled here.
    """

    def __init__(self) -> None:
        self._style = AnsiStyle()
        self._pending = ""

    @property
    def style(self) -> AnsiStyle:
        return self._style

    def reset(self) -> None:
        self._style = AnsiStyle()
        self._pending = ""

    def feed(self, text: str) -> list[tuple[str, AnsiStyle]]:
        """The styled runs of ``text`` (empty runs are dropped)."""
        data = self._pending + text
        self._pending = ""
        incomplete = _INCOMPLETE.search(data)
        if incomplete:
            self._pending = incomplete.group(0)
            data = data[: incomplete.start()]

        runs: list[tuple[str, AnsiStyle]] = []
        position = 0
        for match in _SEQUENCE.finditer(data):
            if match.start() > position:
                runs.append((data[position : match.start()], self._style))
            position = match.end()
            if match.group(2) == "m":  # only SGR sequences change how text looks
                self._apply(match.group(1))
        if position < len(data):
            runs.append((data[position:], self._style))
        return runs

    # ------------------------------------------------------------------ SGR
    def _apply(self, parameters: str) -> None:
        codes = [int(p) if p.isdigit() else 0 for p in re.split(r"[;:]", parameters)] or [0]
        index = 0
        while index < len(codes):
            index += self._apply_one(codes, index)

    def _apply_one(self, codes: list[int], index: int) -> int:
        """Apply the code at ``index``; returns how many codes it consumed."""
        code = codes[index]
        if code in _SIMPLE_CODES:
            self._style = _SIMPLE_CODES[code](self._style)
        elif code in _FOREGROUND:
            self._style = replace(self._style, fg=_BASIC[_FOREGROUND.index(code)])
        elif code in _BACKGROUND:
            self._style = replace(self._style, bg=_BASIC[_BACKGROUND.index(code)])
        elif code in (_SET_FOREGROUND, _SET_BACKGROUND):
            return self._extended_colour(codes, index)
        return 1

    def _extended_colour(self, codes: list[int], index: int) -> int:
        """``38;5;n`` (256 colours) and ``38;2;r;g;b`` (true colour), foreground or background."""
        mode = codes[index + 1] if index + 1 < len(codes) else None
        colour: str | None = None
        consumed = 1
        if mode == _MODE_256 and index + 2 < len(codes):
            colour, consumed = _xterm_256(min(codes[index + 2], 255)), 3
        elif mode == _MODE_TRUE_COLOUR and index + 4 < len(codes):
            red, green, blue = (min(c, 255) for c in codes[index + 2 : index + 5])
            colour, consumed = f"#{red:02x}{green:02x}{blue:02x}", 5
        if colour is not None:
            if codes[index] == _SET_FOREGROUND:
                self._style = replace(self._style, fg=colour)
            else:
                self._style = replace(self._style, bg=colour)
        return consumed
