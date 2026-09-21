"""Opt-in check: does running the project twice give the same numbers?"""

from __future__ import annotations

from collections.abc import Sequence

from quant_workbench.application.diagnostics import CheckContext, Checker, require_project
from quant_workbench.application.runs import RunOptions
from quant_workbench.domain.diagnostics import Finding, Severity
from quant_workbench.domain.errors import ExecutionError
from quant_workbench.domain.project import Project
from quant_workbench.domain.runs import Run


class DeterminismChecker(Checker):
    """Two runs with the same code and configuration must report the same metrics.

    Randomness without a seed is the classic way a quantitative result becomes impossible to
    defend ("it was 1.4 yesterday"). Runs the project twice and compares the extracted
    metrics. Opt-in: it executes the project (twice), and live market data can legitimately
    move between the runs, so a difference is a warning to look at, not proof of a bug.
    """

    id = "determinism"
    title = "Determinism"
    opt_in = True

    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]:
        project = require_project(project)
        if context.runs is None:
            raise ExecutionError("The run engine is not available to the doctor")
        options = RunOptions(max_retries=0)
        first = await context.runs.run(project, options)
        second = await context.runs.run(project, options)

        if not (first.status.is_success and second.status.is_success):
            return [
                Finding(
                    self.id,
                    "determinism-unverifiable",
                    Severity.WARNING,
                    "Could not compare two runs because one of them failed",
                    project=project.slug,
                    detail=f"first: {first.status.value}, second: {second.status.value}",
                )
            ]
        differing = _differences(first, second)
        if not differing:
            return []
        return [
            Finding(
                self.id,
                "non-deterministic",
                Severity.WARNING,
                f"{len(differing)} metric(s) changed between two identical runs: "
                + ", ".join(differing),
                project=project.slug,
                detail=(
                    "Seed the random generators (or fix the market-data window). If the data is "
                    "live, a small difference may come from the data, not the code."
                ),
            )
        ]


def _differences(first: Run, second: Run) -> list[str]:
    left = {m.name: m.value for m in first.metrics}
    right = {m.name: m.value for m in second.metrics}
    names = sorted(left.keys() | right.keys())
    return [
        f"{name} ({left.get(name, '-')} vs {right.get(name, '-')})"
        for name in names
        if left.get(name) != right.get(name)
    ]
