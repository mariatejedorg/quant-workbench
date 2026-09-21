"""Checkers about the machine and the project's Python environment."""

from __future__ import annotations

from collections.abc import Sequence

from quant_workbench.application.diagnostics import CheckContext, Checker, require_project
from quant_workbench.domain.diagnostics import Finding, Severity
from quant_workbench.domain.project import Project

_MAX_LISTED = 8


def _list_names(names: Sequence[str]) -> str:
    shown = ", ".join(names[:_MAX_LISTED])
    return shown if len(names) <= _MAX_LISTED else f"{shown} and {len(names) - _MAX_LISTED} more"


class EnvironmentChecker(Checker):
    """The project has a virtual environment with everything ``requirements.txt`` lists.

    Asks the environment's own ``pip list`` rather than trusting that the folder exists: an
    installation that was interrupted leaves a venv that looks fine and fails at import time.
    """

    id = "environment"
    title = "Virtual environment"

    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]:
        project = require_project(project)
        status = await context.environments.inspect(project)
        if not status.exists:
            return [
                Finding(
                    self.id,
                    "venv-missing",
                    Severity.ERROR,
                    f"No virtual environment at {project.spec.venv}/",
                    project=project.slug,
                    detail="The project cannot run until it has its own interpreter.",
                    fix="setup-environment",
                )
            ]
        findings: list[Finding] = []
        if not status.requirements_found:
            findings.append(
                Finding(
                    self.id,
                    "requirements-file-missing",
                    Severity.WARNING,
                    f"{project.spec.requirements} does not exist, "
                    "so the environment cannot be verified",
                    project=project.slug,
                )
            )
        if status.missing:
            names = [r.name for r in status.missing]
            findings.append(
                Finding(
                    self.id,
                    "requirements-missing",
                    Severity.ERROR,
                    f"{len(names)} required package(s) not installed: {_list_names(names)}",
                    project=project.slug,
                    detail="An interrupted `pip install` leaves an environment like this.",
                    fix="setup-environment",
                )
            )
        return findings


class EnvironmentHazardsChecker(Checker):
    """Settings of the machine that are known to break the portfolio, whatever the project.

    ``PYTHONUTF8=1`` makes Python hand file paths to native libraries as UTF-8, while libcurl
    reads them in the Windows ANSI code page. With a workspace path that has an accent (the
    real one is ``Quant - María``) every TLS handshake of ``yfinance`` then fails (ADR 0004).
    """

    id = "environment-hazards"
    title = "Machine environment"
    scope = "workspace"

    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]:
        findings: list[Finding] = []
        workspace = str(context.catalog.workspace)
        if not workspace.isascii() and context.environ.get("PYTHONUTF8") == "1":
            findings.append(
                Finding(
                    self.id,
                    "utf8-mode-with-non-ascii-path",
                    Severity.WARNING,
                    "PYTHONUTF8=1 is set and the workspace path contains non-ASCII characters",
                    detail=(
                        "Projects that use yfinance/curl_cffi will fail TLS setup because libcurl "
                        "cannot open the CA bundle path (see docs/adr/0004). Unset PYTHONUTF8."
                    ),
                )
            )
        if context.environ.get("PYTHONPATH"):
            findings.append(
                Finding(
                    self.id,
                    "pythonpath-set",
                    Severity.INFO,
                    "PYTHONPATH is set; it is inherited by every project run",
                    detail="It can make a project import a module from somewhere unexpected.",
                )
            )
        return findings
