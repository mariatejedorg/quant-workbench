"""The command registry behind the command palette (Ctrl+K), and fuzzy matching.

Everything the application can do is registered here once, with a title, an optional
shortcut and an *enabled* predicate. Menus, the toolbar and the palette are all built from
the same registry, so a command can never exist in one place and be missing from another.
Qt-free: callbacks are plain callables.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Command:
    """One thing the user can ask the application to do."""

    id: str
    title: str
    callback: Callable[[], object]
    #: Text of the keyboard shortcut, in Qt's portable form (``"Ctrl+R"``), or ``""``.
    shortcut: str = ""
    #: Palette grouping (``"Run"``, ``"View"``...), shown before the title.
    category: str = ""
    #: The command is greyed out and skipped by the palette while this returns ``False``.
    enabled: Callable[[], bool] = field(default=lambda: True)

    @property
    def label(self) -> str:
        return f"{self.category}: {self.title}" if self.category else self.title


#: Bonuses and penalties of :func:`fuzzy_score`, chosen so that "run all" finds "Run: Run all
#: projects" before "Run: Run selected" and a word start beats a match in the middle of a word.
_BONUS_START = 8
_BONUS_CONSECUTIVE = 5
_BONUS_WORD_START = 6
_PENALTY_GAP = 1


def fuzzy_score(query: str, text: str) -> int | None:
    """How well ``query`` matches ``text`` as a subsequence, or ``None`` if it does not.

    Every character of the query must appear in the text, in order (``rga`` matches "Run
    all"). Higher is better: consecutive characters and characters that start a word score
    extra, gaps cost a little. Case-insensitive.
    """
    needle, haystack = query.casefold().replace(" ", ""), text.casefold()
    if not needle:
        return 0
    score = 0
    position = 0
    previous = -2
    for char in needle:
        found = haystack.find(char, position)
        if found < 0:
            return None
        if found == 0:
            score += _BONUS_START
        elif not haystack[found - 1].isalnum():
            score += _BONUS_WORD_START
        if found == previous + 1:
            score += _BONUS_CONSECUTIVE
        score -= (found - position) * _PENALTY_GAP
        previous, position = found, found + 1
    return score


class CommandRegistry:
    """The set of commands, searchable and executable by id."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> Command:
        if command.id in self._commands:
            raise ValueError(f"command {command.id!r} is already registered")
        self._commands[command.id] = command
        return command

    def get(self, command_id: str) -> Command:
        return self._commands[command_id]

    def all(self) -> tuple[Command, ...]:
        return tuple(self._commands.values())

    def __len__(self) -> int:
        return len(self._commands)

    def execute(self, command_id: str) -> bool:
        """Run a command if it is enabled; returns whether it ran."""
        command = self._commands[command_id]
        if not command.enabled():
            return False
        command.callback()
        return True

    def search(self, query: str, *, limit: int = 12) -> list[Command]:
        """The enabled commands matching ``query``, best first (all of them for an empty query)."""
        scored: list[tuple[int, str, Command]] = []
        for command in self._commands.values():
            if not command.enabled():
                continue
            score = fuzzy_score(query, command.label)
            if score is not None:
                scored.append((score, command.label, command))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [command for _, _, command in scored[:limit]]
