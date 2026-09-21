"""Exception hierarchy.

Every error the workbench raises on purpose derives from :class:`WorkbenchError`, so the
delivery layers (CLI, GUI) can distinguish *expected* failures (bad manifest, unsafe
config edit, policy violation) from bugs, and present the former without a traceback.
"""

from __future__ import annotations


class WorkbenchError(Exception):
    """Base class for every deliberate error raised by the workbench."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        #: Optional, human-oriented suggestion on how to recover.
        self.hint = hint

    def __str__(self) -> str:
        return self.message if self.hint is None else f"{self.message} (hint: {self.hint})"


class InvalidSlugError(WorkbenchError):
    """A project identifier does not follow the kebab-case convention."""


class ManifestError(WorkbenchError):
    """A ``quant-project.toml`` is missing, malformed or contradicts another source."""


class DiscoveryError(WorkbenchError):
    """The workspace could not be scanned or contains an unresolvable layout."""


class DependencyCycleError(WorkbenchError):
    """The project dependency graph contains a cycle, so no execution order exists."""

    def __init__(self, cycle: tuple[str, ...]) -> None:
        super().__init__(
            "Dependency cycle detected: " + " -> ".join((*cycle, cycle[0])),
            hint="Break the cycle by removing one of the listed 'depends_on' edges.",
        )
        self.cycle = cycle


class ExecutionError(WorkbenchError):
    """A job could not be started, or terminated abnormally."""


class EnvironmentSetupError(WorkbenchError):
    """A project's virtual environment is missing, broken or could not be built."""


class UnsafeEditError(WorkbenchError):
    """A configuration edit was refused because it could not be applied safely.

    The config editor prefers refusing to corrupting a file: when a value cannot be
    rewritten while preserving the surrounding source, this is raised and the UI falls
    back to the manual editor.
    """


class GitError(WorkbenchError):
    """A git operation failed or the repository is in an unexpected state."""


class PolicyViolationError(WorkbenchError):
    """An action was blocked by a configured policy (e.g. wrong git identity)."""


class PluginError(WorkbenchError):
    """A third-party plugin failed to load or violated its contract."""
