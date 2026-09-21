"""Reading the fixed structure the portfolio's READMEs share (pure text processing).

All ten READMEs have the same sections: ``Results`` (a table of the numbers the run produced),
``Key findings`` and ``Concepts to be able to explain in an interview`` (bullets of the form
``- **Title**: explanation``). That regularity is what lets the workbench turn them into study
cards and check them against the latest run.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_BULLET = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(.*)$")
_BOLD_LEAD = re.compile(
    r"^\*\*(?P<title>.+?)\*\*\s*[:\-" + chr(0x2014) + chr(0x2013) + r"]?\s*(?P<rest>.*)$"
)
#: A number as a README writes it: ``1,234.5``, ``-3.62``, ``26.97``, ``1.5e-6``.
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w])")

CONCEPTS_HEADING = "Concepts to be able to explain in an interview"
FINDINGS_HEADING = "Key findings"
RESULTS_HEADING = "Results"


def section(markdown: str, heading: str) -> tuple[int, str] | None:
    """``(first line number, body)`` of the section titled ``heading``, or ``None``.

    The body runs to the next heading of the same or a higher level. Headings inside fenced
    code blocks are not headings.
    """
    lines = markdown.replace("\r\n", "\n").split("\n")
    in_fence = False
    start: int | None = None
    level = 0
    body: list[str] = []
    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match and start is not None and len(match.group(1)) <= level:
            break
        if match and start is None and match.group(2).strip().casefold() == heading.casefold():
            start, level = number, len(match.group(1))
            continue
        if start is not None:
            body.append(line)
    return None if start is None else (start, "\n".join(body))


@dataclass(frozen=True, slots=True)
class StudyCard:
    """A question the author must be able to answer, taken from a README bullet."""

    project: str
    kind: str  # "concept" or "finding"
    prompt: str
    answer: str

    @property
    def id(self) -> str:
        """Stable across README edits that do not change the bullet's title."""
        digest = hashlib.sha1(
            f"{self.kind}|{self.prompt}".encode(), usedforsecurity=False
        ).hexdigest()[:12]
        return f"{self.project}:{digest}"


def _bullets(body: str) -> list[str]:
    """The bullets of a section, each with its continuation lines joined."""
    items: list[str] = []
    for line in body.split("\n"):
        match = _BULLET.match(line)
        if match:
            items.append(match.group(1).strip())
        elif items and line.strip() and line.startswith((" ", "\t")):
            items[-1] += " " + line.strip()
    return items


def study_cards(project: str, markdown: str) -> tuple[StudyCard, ...]:
    """Cards for the *concepts* and *key findings* of a README."""
    cards: list[StudyCard] = []
    for heading, kind, template in (
        (CONCEPTS_HEADING, "concept", "Explain: {title}"),
        (FINDINGS_HEADING, "finding", "What did this project find? {title}"),
    ):
        found = section(markdown, heading)
        if found is None:
            continue
        for bullet in _bullets(found[1]):
            lead = _BOLD_LEAD.match(bullet)
            if lead:
                title, rest = lead.group("title").strip(" :"), lead.group("rest").strip()
            else:
                title, rest = bullet, ""
            if kind == "finding":
                # the finding's title is its answer: ask for it, show the whole statement
                cards.append(
                    StudyCard(project, kind, f"State a key finding about: {_topic(title)}", bullet)
                )
                continue
            cards.append(StudyCard(project, kind, template.format(title=title), rest or title))
    return tuple(cards)


def _topic(text: str, limit: int = 60) -> str:
    """A short topic hint from a finding's title (its first words)."""
    words = re.sub(r"[`*_]", "", text).split()
    shown = " ".join(words[:8])
    return shown if len(shown) <= limit else shown[: limit - 1] + "…"


# ------------------------------------------------------------------------- numbers
@dataclass(frozen=True, slots=True)
class ClaimedNumber:
    """A number written in the README, with the precision it was written at."""

    text: str
    value: float
    decimals: int
    line: int

    def matches(self, value: float, *, percent: bool = False) -> bool:
        """Whether ``value`` rounds to what the README says (also as a percentage)."""
        candidates = (value, value * 100) if percent else (value,)
        return any(
            round(candidate, self.decimals) == round(self.value, self.decimals)
            for candidate in candidates
        )


def claimed_numbers(markdown: str, heading: str = RESULTS_HEADING) -> tuple[ClaimedNumber, ...]:
    """Every number in the ``Results`` section, with its line in the file."""
    found = section(markdown, heading)
    if found is None:
        return ()
    first, body = found
    numbers: list[ClaimedNumber] = []
    for offset, line in enumerate(body.split("\n"), start=1):
        for match in _NUMBER.finditer(line):
            text = match.group(0)
            try:
                value = float(text.replace(",", ""))
            except ValueError:
                continue
            decimals = len(text.split(".")[1]) if "." in text and "e" not in text.lower() else 0
            numbers.append(ClaimedNumber(text, value, decimals, first + offset))
    return tuple(numbers)
