"""The checkers shipped with the workbench."""

from __future__ import annotations

from quant_workbench.application.checkers.code_quality import (
    ExplainabilityChecker,
    StaticAnalysisChecker,
)
from quant_workbench.application.checkers.determinism import DeterminismChecker
from quant_workbench.application.checkers.environment import (
    EnvironmentChecker,
    EnvironmentHazardsChecker,
)
from quant_workbench.application.checkers.git_checks import GitHygieneChecker, GitIdentityChecker
from quant_workbench.application.checkers.imports import (
    ImportCollisionChecker,
    SiblingReferencesChecker,
)
from quant_workbench.application.checkers.outputs import OutputsFreshnessChecker
from quant_workbench.application.checkers.readme_claims import ReadmeClaimsChecker
from quant_workbench.application.checkers.ssl_cert import SslCertChecker
from quant_workbench.application.diagnostics import Checker


def default_checkers() -> tuple[Checker, ...]:
    """Every built-in checker, in the order their findings are most useful to read."""
    return (
        EnvironmentChecker(),
        EnvironmentHazardsChecker(),
        SslCertChecker(),
        ImportCollisionChecker(),
        SiblingReferencesChecker(),
        GitIdentityChecker(),
        GitHygieneChecker(),
        OutputsFreshnessChecker(),
        StaticAnalysisChecker(),
        ExplainabilityChecker(),
        ReadmeClaimsChecker(),
        DeterminismChecker(),
    )
