"""Checkers about how projects load code from each other."""

from __future__ import annotations

import re
from collections.abc import Sequence

from quant_workbench.application.diagnostics import CheckContext, Checker, require_project
from quant_workbench.domain.code_analysis import string_literals, uncleaned_foreign_path_inserts
from quant_workbench.domain.diagnostics import Finding, Location, Severity
from quant_workbench.domain.graph import EdgeOrigin
from quant_workbench.domain.project import Project

#: The naming convention of the portfolio's folders (``proyecto-6-opciones-...``): a string
#: literal shaped like this is a reference to a sibling project even when the registry has
#: never heard of that folder.
_SIBLING_SHAPE = re.compile(r"^proyecto-\d+-[\w-]+$")


class ImportCollisionChecker(Checker):
    """Loading a sibling's code must not leave its ``config`` package in ``sys.modules``.

    Every project has a top-level ``config`` package. Putting a sibling's folder on
    ``sys.path`` and importing from it makes ``import config`` return whichever one was
    imported first: silently the wrong parameters. The portfolio's loader saves and restores
    ``sys.modules`` around the import; this checker finds the places that do not.
    """

    id = "import-collision"
    title = "Sibling imports"

    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]:
        project = require_project(project)
        findings: list[Finding] = []
        for source in context.sources(project):
            if source.tree is None:
                continue
            findings.extend(
                Finding(
                    self.id,
                    "import-collision-risk",
                    Severity.WARNING,
                    f"`{mutation.scope}` puts another folder on sys.path without restoring "
                    "sys.modules",
                    project=project.slug,
                    location=Location(source.path, mutation.line),
                    detail=(
                        "A later `import config` (or `data`) may resolve to the other project's "
                        "module. Save and restore the `config` entries of sys.modules around the "
                        "import, as the sibling loader in the portfolio does."
                    ),
                )
                for mutation in uncleaned_foreign_path_inserts(source.tree)
            )
        return findings


class SiblingReferencesChecker(Checker):
    """References to sibling projects must resolve, and agree with the declared dependencies."""

    id = "sibling-references"
    title = "Sibling project references"

    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]:
        project = require_project(project)
        catalog = context.catalog
        present = {p.root.name for p in catalog.projects}
        registered = present | {spec.folder for spec in catalog.missing}

        findings: list[Finding] = []
        reported: set[str] = set()
        for source in context.sources(project):
            if source.tree is None:
                continue
            for text, line in string_literals(source.tree):
                is_reference = text in registered or _SIBLING_SHAPE.match(text) is not None
                if (
                    is_reference
                    and text != project.root.name
                    and text not in present
                    and text not in reported
                ):
                    reported.add(text)
                    findings.append(
                        Finding(
                            self.id,
                            "sibling-missing",
                            Severity.ERROR,
                            f"Refers to sibling project '{text}', which is not in the workspace",
                            project=project.slug,
                            location=Location(source.path, line),
                            detail="Clone it next to this project (`qw sync`) or fix the name.",
                        )
                    )
        findings.extend(self._declared_vs_detected(project, context))
        return findings

    def _declared_vs_detected(self, project: Project, context: CheckContext) -> list[Finding]:
        findings: list[Finding] = []
        for edge in context.catalog.graph.edges:
            if edge.dependent != project.slug:
                continue
            if edge.origins == {EdgeOrigin.DETECTED}:
                findings.append(
                    Finding(
                        self.id,
                        "dependency-undeclared",
                        Severity.WARNING,
                        f"The code loads '{edge.dependency}' but the manifest does not declare it",
                        project=project.slug,
                        detail="Add it to depends_on so run-all orders and re-runs correctly.",
                    )
                )
            elif edge.origins == {EdgeOrigin.DECLARED}:
                findings.append(
                    Finding(
                        self.id,
                        "dependency-not-detected",
                        Severity.INFO,
                        f"The manifest declares '{edge.dependency}' but no code refers to it",
                        project=project.slug,
                    )
                )
        return findings
