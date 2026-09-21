"""Parsing ``requirements.txt`` and comparing it with what is actually installed."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
_EXTRAS = re.compile(r"\[[^\]]*\]")
_SEPARATORS = re.compile(r"[-_.]+")


def normalize_name(name: str) -> str:
    """PEP 503 normalisation: case-insensitive, runs of ``-_.`` are one ``-``."""
    return _SEPARATORS.sub("-", name).lower()


@dataclass(frozen=True, slots=True)
class Requirement:
    name: str  # normalised
    specifier: str = ""  # e.g. ">=2.0,<3"; kept as text, only the name is compared
    raw: str = ""


def parse_requirements(text: str) -> tuple[Requirement, ...]:
    """Parse the *named* requirements of a ``requirements.txt``.

    Comments, blank lines, option lines (``-r``, ``--index-url``...), and direct URL/path
    installs are skipped: they cannot be checked by name. Environment markers
    (``; python_version < "3.12"``) are stripped from the specifier.
    """
    requirements: list[Requirement] = []
    for raw_line in text.splitlines():
        line = raw_line.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "-", ".", "/")) or "://" in line or "@" in line:
            continue
        line = line.split(";", 1)[0].strip()
        match = _NAME.match(line)
        if match is None:
            continue
        remainder = _EXTRAS.sub("", line[match.end() :]).strip()
        requirements.append(
            Requirement(normalize_name(match.group()), remainder.replace(" ", ""), raw_line.strip())
        )
    return tuple(requirements)


def missing_requirements(
    required: Iterable[Requirement], installed: Mapping[str, str]
) -> tuple[Requirement, ...]:
    """Requirements whose package is not installed. ``installed`` maps name -> version."""
    have = {normalize_name(name) for name in installed}
    return tuple(req for req in required if req.name not in have)
