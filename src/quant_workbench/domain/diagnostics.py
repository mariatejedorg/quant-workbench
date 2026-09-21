"""The vocabulary of the project doctor: findings, severities and the report."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from quant_workbench.domain.ids import Slug


class Severity(StrEnum):
    """How much a finding matters. Ordered: ``INFO < WARNING < ERROR``."""

    INFO = "info"  # worth knowing, nothing to do
    WARNING = "warning"  # probably worth fixing
    ERROR = "error"  # will break a run or violates a hard policy

    @property
    def rank(self) -> int:
        return _RANKS[self]


_RANKS = {Severity.INFO: 0, Severity.WARNING: 1, Severity.ERROR: 2}


@dataclass(frozen=True, slots=True)
class Location:
    """A place in a project's files, for "jump to source"."""

    file: str  # relative to the project root, forward slashes
    line: int | None = None

    def __str__(self) -> str:
        return self.file if self.line is None else f"{self.file}:{self.line}"


@dataclass(frozen=True, slots=True)
class Finding:
    """One problem (or observation) a checker found."""

    #: The checker that produced it, e.g. ``ssl-cert``.
    checker: str
    #: Stable identifier of the *kind* of problem, e.g. ``ssl-cert-missing``.
    code: str
    severity: Severity
    message: str
    project: Slug | None = None
    location: Location | None = None
    #: Longer explanation, shown on demand.
    detail: str = ""
    #: Id of an automatic remedy (see :mod:`quant_workbench.application.fixes`), if any.
    fix: str | None = None

    @property
    def key(self) -> str:
        """Identifies the finding across runs (project + code + place), for diffing reports."""
        where = str(self.location) if self.location else ""
        return f"{self.project or '-'}|{self.code}|{where}"


@dataclass(frozen=True, slots=True)
class ExplainabilityScore:
    """How defensible a project's code is line by line, from 0 to 100.

    The portfolio's non-negotiable rule is that the author can explain every line in an
    interview. This turns the proxies for that (documentation, comments, short and simple
    functions) into a number that can be tracked.
    """

    score: float
    docstring_coverage: float  # 0..1, public functions/classes/modules with a docstring
    comment_density: float  # comment lines per code line
    short_function_share: float  # 0..1, functions of at most ``SHORT_FUNCTION_LINES`` lines
    simple_function_share: float  # 0..1, functions of cyclomatic complexity <= the limit
    functions: int
    code_lines: int


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Everything one doctor run found."""

    findings: tuple[Finding, ...]
    scores: dict[Slug, ExplainabilityScore] = field(default_factory=dict)
    #: Checkers that did not run, with the reason (disabled, tool missing, opt-in...).
    skipped: tuple[tuple[str, str], ...] = ()

    @property
    def worst(self) -> Severity | None:
        return max((f.severity for f in self.findings), key=lambda s: s.rank, default=None)

    @property
    def ok(self) -> bool:
        """``True`` when nothing at ``ERROR`` level was found."""
        return self.worst is not Severity.ERROR

    def counts(self) -> dict[Severity, int]:
        tally = Counter(f.severity for f in self.findings)
        return {severity: tally.get(severity, 0) for severity in Severity}

    def for_project(self, slug: Slug) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.project == slug)

    def at_least(self, severity: Severity) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity.rank >= severity.rank)

    @staticmethod
    def ordered(findings: Iterable[Finding]) -> tuple[Finding, ...]:
        """Most severe first, then by project and code: a stable order for output and tests."""
        return tuple(
            sorted(findings, key=lambda f: (-f.severity.rank, f.project or "", f.code, f.key))
        )
