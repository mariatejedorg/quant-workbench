"""The CA bundle that Yahoo Finance access needs on this portfolio's machines."""

from __future__ import annotations

from collections.abc import Sequence

from quant_workbench.application.diagnostics import CheckContext, Checker, require_project
from quant_workbench.domain.code_analysis import imports_module
from quant_workbench.domain.diagnostics import Finding, Location, Severity
from quant_workbench.domain.project import Project

CA_BUNDLE = ".certs/cacert.pem"


class SslCertChecker(Checker):
    """A project that downloads market data ships the CA bundle its session verifies with.

    On the author's machines ``yfinance`` (through ``curl_cffi``) only verifies TLS when
    given an explicit bundle; without ``.certs/cacert.pem`` the failure looks like
    ``CERTIFICATE_VERIFY_FAILED`` or, worse, like a delisted ticker.
    """

    id = "ssl-cert"
    title = "TLS certificate bundle"

    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]:
        project = require_project(project)
        user = next(
            (
                source
                for source in context.sources(project)
                if source.tree is not None and imports_module(source.tree, "yfinance")
            ),
            None,
        )
        if user is None or context.files.exists(project, CA_BUNDLE):
            return []
        return [
            Finding(
                self.id,
                "ssl-cert-missing",
                Severity.ERROR,
                f"{project.title} uses yfinance but has no {CA_BUNDLE}",
                project=project.slug,
                location=Location(user.path),
                detail="Market-data requests will fail TLS verification.",
                fix="copy-ca-bundle",
            )
        ]
