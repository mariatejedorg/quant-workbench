"""What the config editor exposes about a project's editable constants."""

from __future__ import annotations

from dataclasses import dataclass

from quant_workbench.domain.literals import LiteralValue, ValueKind


@dataclass(frozen=True, slots=True)
class Constant:
    """A module-level ``NAME = <literal>`` assignment found in a source file."""

    name: str
    kind: ValueKind
    value: LiteralValue
    #: The value exactly as written in the file (``10_000.0``, a multi-line dict, ...).
    source: str
    #: The comment block right above the assignment plus any trailing comment, as prose.
    description: str
    #: 1-based, inclusive line range of the whole statement, for "jump to source".
    line: int
    end_line: int

    @property
    def is_multiline(self) -> bool:
        return self.end_line > self.line
