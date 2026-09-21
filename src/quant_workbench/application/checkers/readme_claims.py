"""Does the README's Results table still describe what the project produces?"""

from __future__ import annotations

from collections.abc import Sequence

from quant_workbench.application.diagnostics import CheckContext, Checker, require_project
from quant_workbench.domain.diagnostics import Finding, Location, Severity
from quant_workbench.domain.project import Project
from quant_workbench.domain.readme import claimed_numbers
from quant_workbench.domain.runs import RunStatus

_LOOKBACK_RUNS = 20
_MAX_LISTED = 5


class ReadmeClaimsChecker(Checker):
    """Every extracted metric of the last successful run should appear in the README.

    The portfolio's READMEs quote the numbers a run printed (``Results`` table). Live-data
    projects drift, and code changes shift results; a README that still shows last month's
    Sharpe is a claim the author cannot defend. A number counts as *present* when it rounds to
    what the README shows at the precision the README uses (``26.97`` matches ``26.9712``), as a
    percentage or as a fraction. Reported as information: a stale README is a to-do, not an error,
    and it needs a stored run to compare with.
    """

    id = "readme-claims"
    title = "README claims"

    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]:
        project = require_project(project)
        text = context.files.read_text(project, "README.md")
        numbers = claimed_numbers(text) if text else ()
        if not numbers or context.repository is None:
            return []
        run = next(
            (
                r
                for r in context.repository.list_runs(project.slug, limit=_LOOKBACK_RUNS)
                if r.status is RunStatus.SUCCEEDED and r.metrics
            ),
            None,
        )
        if run is None:
            return []
        missing = [
            f"{metric.name} = {metric.value:g}"
            for metric in run.metrics
            if isinstance(metric.value, int | float)
            and not any(n.matches(float(metric.value), percent=True) for n in numbers)
        ]
        if not missing:
            return []
        listed = ", ".join(missing[:_MAX_LISTED])
        more = len(missing) - _MAX_LISTED
        return [
            Finding(
                self.id,
                "readme-metric-not-found",
                Severity.INFO,
                f"The Results of the README do not show the last run's {listed}"
                + (f" and {more} more" if more > 0 else ""),
                project=project.slug,
                location=Location("README.md", numbers[0].line),
                detail="Update the table, or run the project again if the README is the newer one.",
            )
        ]
