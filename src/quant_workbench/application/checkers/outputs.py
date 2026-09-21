"""Are the files a project is supposed to produce there, and up to date?"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from quant_workbench.application.diagnostics import CheckContext, Checker, require_project
from quant_workbench.domain.diagnostics import Finding, Location, Severity
from quant_workbench.domain.project import Project

#: File-system timestamps are only precise to about a second, and a run can rewrite a
#: config-derived output right after a source was saved.
_TOLERANCE = timedelta(seconds=2)


class OutputsFreshnessChecker(Checker):
    """Declared outputs exist, and the dashboard is not older than the code that makes it."""

    id = "outputs"
    title = "Generated outputs"

    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]:
        project = require_project(project)
        spec = project.spec.outputs
        declared = [
            *([spec.dashboard] if spec.dashboard else []),
            *spec.images,
            *spec.artifacts,
        ]
        findings: list[Finding] = [
            Finding(
                self.id,
                "output-missing",
                Severity.WARNING,
                f"Declared output {relative} does not exist",
                project=project.slug,
                location=Location(relative),
                detail="Run the project to generate it.",
            )
            for relative in dict.fromkeys(declared)
            if not context.files.exists(project, relative)
        ]
        findings.extend(self._stale(project, context))
        return findings

    def _stale(self, project: Project, context: CheckContext) -> list[Finding]:
        dashboard = project.spec.outputs.dashboard
        generated = context.files.modified_at(project, dashboard) if dashboard else None
        if dashboard is None or generated is None:
            return []
        inputs = [
            *context.files.python_sources(project),
            *(target.file for target in project.spec.config_targets),
        ]
        newest: tuple[str, datetime] | None = None
        for relative in dict.fromkeys(inputs):
            changed = context.files.modified_at(project, relative)
            if (
                changed is not None
                and changed > generated + _TOLERANCE
                and (newest is None or changed > newest[1])
            ):
                newest = (relative, changed)
        if newest is None:
            return []
        return [
            Finding(
                self.id,
                "outputs-stale",
                Severity.INFO,
                f"{newest[0]} changed after {dashboard} was generated",
                project=project.slug,
                location=Location(newest[0]),
                detail="The dashboard may not reflect the current code or configuration.",
            )
        ]
