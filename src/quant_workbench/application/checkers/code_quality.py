"""Checkers about the code itself: obvious bugs, and how defensible it is line by line."""

from __future__ import annotations

import ast
from collections.abc import Sequence

from quant_workbench.application.diagnostics import CheckContext, Checker, require_project
from quant_workbench.domain.code_analysis import (
    COMPLEXITY_LIMIT,
    SHORT_FUNCTION_LINES,
    FileMetrics,
    explainability,
    lint,
    measure,
)
from quant_workbench.domain.diagnostics import Finding, Location, Severity
from quant_workbench.domain.project import Project

#: Below this score the project is flagged: too little documentation to defend in an interview.
EXPLAINABILITY_TARGET = 70.0
#: A function this long is flagged on its own (the score already counts anything over 50).
_VERY_LONG_FUNCTION = 2 * SHORT_FUNCTION_LINES
_MAX_LISTED = 5

_LINT_SEVERITY = {
    "bare-except": Severity.WARNING,
    "mutable-default": Severity.WARNING,
    "silent-except": Severity.INFO,
}


class StaticAnalysisChecker(Checker):
    """Files that do not even parse, plus a few high-signal bug patterns.

    Deliberately small: it is not a linter (ruff does that better). It reports the things
    that are bugs waiting to happen and needs nothing installed in the project's environment.
    """

    id = "static-analysis"
    title = "Static analysis"

    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]:
        project = require_project(project)
        findings: list[Finding] = []
        for source in context.sources(project):
            if source.tree is None:
                findings.append(self._syntax_error(project, source.path, source.text))
                continue
            unused: list[str] = []
            for issue in lint(source.tree):
                if issue.code == "unused-import":
                    unused.append(issue.message.split("`")[1])
                    continue
                findings.append(
                    Finding(
                        self.id,
                        issue.code,
                        _LINT_SEVERITY[issue.code],
                        issue.message,
                        project=project.slug,
                        location=Location(source.path, issue.line),
                    )
                )
            if unused:
                findings.append(
                    Finding(
                        self.id,
                        "unused-imports",
                        Severity.INFO,
                        f"Unused import(s) in {source.path}: {', '.join(unused)}",
                        project=project.slug,
                        location=Location(source.path),
                    )
                )
        return findings

    def _syntax_error(self, project: Project, path: str, text: str) -> Finding:
        try:
            ast.parse(text)
            line, reason = None, "the file could not be parsed"
        except SyntaxError as error:
            line, reason = error.lineno, error.msg
        except (ValueError, RecursionError) as error:
            line, reason = None, str(error)
        return Finding(
            self.id,
            "syntax-error",
            Severity.ERROR,
            f"{path} does not parse: {reason}",
            project=project.slug,
            location=Location(path, line),
        )


class ExplainabilityChecker(Checker):
    """How defensible the code is line by line (the portfolio's non-negotiable rule).

    Every project is meant to be explainable by its author in an interview. The score
    combines documentation coverage, comment density, function length and complexity (see
    :func:`~quant_workbench.domain.code_analysis.explainability`); the findings point at the
    functions that drag it down.
    """

    id = "explainability"
    title = "Explainability"

    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]:
        project = require_project(project)
        per_file: list[tuple[str, FileMetrics]] = []
        for source in context.sources(project):
            metrics = measure(source.text) if source.tree is not None else None
            if metrics is not None:
                per_file.append((source.path, metrics))
        if not per_file:
            return []

        score = explainability(metrics for _, metrics in per_file)
        context.scores[project.slug] = score
        findings: list[Finding] = []
        if score.score < EXPLAINABILITY_TARGET:
            findings.append(
                Finding(
                    self.id,
                    "explainability-low",
                    Severity.WARNING,
                    f"Explainability score {score.score:.0f}/100 is below "
                    f"{EXPLAINABILITY_TARGET:.0f}",
                    project=project.slug,
                    detail=(
                        f"docstrings {score.docstring_coverage:.0%}, "
                        f"comments {score.comment_density:.2f}/line, "
                        f"functions <= {SHORT_FUNCTION_LINES} lines "
                        f"{score.short_function_share:.0%}, "
                        f"complexity <= {COMPLEXITY_LIMIT} {score.simple_function_share:.0%}"
                    ),
                )
            )
        findings.extend(self._hard_to_explain(project, per_file))
        return findings

    def _hard_to_explain(
        self, project: Project, per_file: list[tuple[str, FileMetrics]]
    ) -> list[Finding]:
        every = [(path, fn) for path, metrics in per_file for fn in metrics.functions]
        complex_first = sorted(
            (item for item in every if item[1].complexity > COMPLEXITY_LIMIT),
            key=lambda item: -item[1].complexity,
        )
        long_first = sorted(
            (item for item in every if item[1].length > _VERY_LONG_FUNCTION),
            key=lambda item: -item[1].length,
        )
        findings = [
            Finding(
                self.id,
                "function-too-complex",
                Severity.INFO,
                f"`{fn.name}` has cyclomatic complexity {fn.complexity} (limit {COMPLEXITY_LIMIT})",
                project=project.slug,
                location=Location(path, fn.line),
            )
            for path, fn in complex_first[:_MAX_LISTED]
        ]
        findings.extend(
            Finding(
                self.id,
                "function-too-long",
                Severity.INFO,
                f"`{fn.name}` is {fn.length} lines long",
                project=project.slug,
                location=Location(path, fn.line),
            )
            for path, fn in long_first[:_MAX_LISTED]
        )
        undocumented = [(p, fn) for p, fn in every if fn.public and not fn.documented]
        if undocumented:
            names = ", ".join(fn.name for _, fn in undocumented[:_MAX_LISTED])
            more = len(undocumented) - _MAX_LISTED
            findings.append(
                Finding(
                    self.id,
                    "missing-docstrings",
                    Severity.INFO,
                    f"{len(undocumented)} public function(s) without a docstring: {names}"
                    + (f" and {more} more" if more > 0 else ""),
                    project=project.slug,
                    location=Location(undocumented[0][0], undocumented[0][1].line),
                )
            )
        return findings
