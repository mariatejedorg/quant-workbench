"""Known failure signatures: recognising *why* a run failed from its output.

A signature is data, not code (see ``data/failure_signatures/*.toml``): a regular
expression plus a diagnosis and advice. Adding knowledge about a new failure mode is a
matter of appending a TOML entry, which is how this portfolio's real bugs (SSL
inspection by an antivirus, missing packages, mismatched trading calendars) were
captured.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FailureSignature:
    id: str
    pattern: str  # regular expression searched against the run's output
    title: str
    advice: str
    #: A retry may succeed (network hiccups, rate limits), as opposed to a code or
    #: configuration problem that will fail identically every time.
    transient: bool = False
    #: Id of an automatic fix the doctor can offer, if any (see ``application/fixes``).
    fix: str | None = None

    def matches(self, text: str) -> bool:
        return re.search(self.pattern, text, flags=re.MULTILINE) is not None


def match_signatures(
    text: str, signatures: Iterable[FailureSignature]
) -> tuple[FailureSignature, ...]:
    """Every signature that matches ``text``, in the order they were declared."""
    return tuple(signature for signature in signatures if signature.matches(text))
