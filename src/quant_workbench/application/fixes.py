"""Automatic remedies for the doctor's findings.

Every fix has two steps, like the config editor: :meth:`FixService.preview` says exactly what
would happen (commands, files, a diff) without doing it, and :meth:`FixService.apply` does it.
Fixes are *safe by construction*: they only add or copy, or change the repository's own
config; none deletes or overwrites user content.
"""

from __future__ import annotations

import difflib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from quant_workbench.application.checkers.ssl_cert import CA_BUNDLE
from quant_workbench.application.diagnostics import CheckContext
from quant_workbench.domain.diagnostics import Finding
from quant_workbench.domain.errors import WorkbenchError
from quant_workbench.domain.project import Project


class FixError(WorkbenchError):
    """A fix cannot be previewed or applied (no source for a file, unknown id, ...)."""


@dataclass(frozen=True, slots=True)
class FixPreview:
    """What a fix will do, in words and (when it edits a file) as a diff."""

    fix: str
    project: Project
    summary: str
    actions: tuple[str, ...]
    diff: str = ""


class Fix(ABC):
    """One automatic remedy, registered under the finding ``fix`` ids it handles."""

    ids: ClassVar[tuple[str, ...]]

    @abstractmethod
    async def preview(self, project: Project, context: CheckContext) -> FixPreview: ...

    @abstractmethod
    async def apply(self, project: Project, context: CheckContext) -> str:
        """Do it. Returns a one-line description of what was done."""


class CopyCaBundle(Fix):
    """Copy the CA bundle from a sibling project that has it (they are all identical)."""

    ids = ("copy-ca-bundle",)

    def _donor(self, project: Project, context: CheckContext) -> Project:
        for other in context.catalog.projects:
            if other.slug != project.slug and context.files.exists(other, CA_BUNDLE):
                return other
        raise FixError(
            f"No project in the workspace has a {CA_BUNDLE} to copy",
            hint="Create one from your system's or certifi's CA bundle.",
        )

    async def preview(self, project: Project, context: CheckContext) -> FixPreview:
        donor = self._donor(project, context)
        return FixPreview(
            "copy-ca-bundle",
            project,
            f"Add {CA_BUNDLE} to {project.title}",
            (f"copy {donor.root / CA_BUNDLE} -> {project.root / CA_BUNDLE}",),
        )

    async def apply(self, project: Project, context: CheckContext) -> str:
        donor = self._donor(project, context)
        data = context.files.read_bytes(donor, CA_BUNDLE)
        if data is None:  # pragma: no cover - _donor just saw it
            raise FixError(f"{donor.title}'s {CA_BUNDLE} disappeared")
        context.files.write_bytes(project, CA_BUNDLE, data)
        return f"Copied {CA_BUNDLE} from {donor.title}"


class SetUpEnvironment(Fix):
    """Create the virtual environment and/or install the missing requirements."""

    ids = ("setup-environment",)

    async def preview(self, project: Project, context: CheckContext) -> FixPreview:
        creating = not project.venv_dir.is_dir()
        actions = [f"python -m venv {project.venv_dir}"] if creating else []
        actions.append(f"{project.venv_python} -m pip install -r {project.spec.requirements}")
        return FixPreview(
            "setup-environment",
            project,
            f"{'Create' if creating else 'Repair'} the environment of {project.title}",
            tuple(actions),
        )

    async def apply(self, project: Project, context: CheckContext) -> str:
        run = await context.environments.setup(project)
        if not run.status.is_success:
            raise FixError(
                f"Environment setup {run.status.value}: {run.failure or 'see the run log'}",
                hint=f"Inspect it with `qw logs {run.id}`.",
            )
        return f"Environment of {project.title} is ready (run {run.id})"


class SetGitIdentity(Fix):
    """Give the repository its own author identity (never the global one)."""

    ids = ("set-git-identity",)

    @staticmethod
    def _settings(context: CheckContext) -> list[tuple[str, str]]:
        pairs = [("user.email", context.settings.expected_git_email)]
        if context.settings.expected_git_name is not None:
            pairs.append(("user.name", context.settings.expected_git_name))
        return pairs

    async def preview(self, project: Project, context: CheckContext) -> FixPreview:
        actions = tuple(
            f'git -C "{project.root}" config --local {key} "{value}"'
            for key, value in self._settings(context)
        )
        return FixPreview(
            "set-git-identity", project, f"Set the git identity of {project.title}", actions
        )

    async def apply(self, project: Project, context: CheckContext) -> str:
        for key, value in self._settings(context):
            context.git.set_local_config(project.root, key, value)
        return (
            f"Set {', '.join(key for key, _ in self._settings(context))} in the repository config"
        )


class IgnoreEnvironment(Fix):
    """Add the virtual-environment folder to ``.gitignore``."""

    ids = ("ignore-venv",)
    _FILE = ".gitignore"

    def _new_text(self, project: Project, context: CheckContext) -> tuple[str, str]:
        before = context.files.read_text(project, self._FILE) or ""
        entry = f"{project.spec.venv}/"
        separator = "" if before == "" or before.endswith("\n") else "\n"
        return before, f"{before}{separator}{entry}\n"

    async def preview(self, project: Project, context: CheckContext) -> FixPreview:
        before, after = self._new_text(project, context)
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{self._FILE}",
                tofile=f"b/{self._FILE}",
            )
        )
        return FixPreview(
            "ignore-venv",
            project,
            f"Ignore {project.spec.venv}/ in {project.title}",
            (f"append '{project.spec.venv}/' to {self._FILE}",),
            diff,
        )

    async def apply(self, project: Project, context: CheckContext) -> str:
        _, after = self._new_text(project, context)
        context.files.write_text(project, self._FILE, after)
        return f"Added {project.spec.venv}/ to {self._FILE}"


class FixService:
    """Looks up the fix a finding asks for and previews or applies it."""

    def __init__(self, fixes: tuple[Fix, ...] | None = None) -> None:
        available = fixes or (
            CopyCaBundle(),
            SetUpEnvironment(),
            SetGitIdentity(),
            IgnoreEnvironment(),
        )
        self._by_id: dict[str, Fix] = {fix_id: fix for fix in available for fix_id in fix.ids}

    def can_fix(self, finding: Finding) -> bool:
        return finding.fix in self._by_id and finding.project is not None

    async def preview(self, finding: Finding, context: CheckContext) -> FixPreview:
        fix, project = self._resolve(finding, context)
        return await fix.preview(project, context)

    async def apply(self, finding: Finding, context: CheckContext) -> str:
        fix, project = self._resolve(finding, context)
        return await fix.apply(project, context)

    def _resolve(self, finding: Finding, context: CheckContext) -> tuple[Fix, Project]:
        if finding.fix is None or finding.fix not in self._by_id:
            raise FixError(f"Finding {finding.code} has no automatic fix")
        if finding.project is None:
            raise FixError(f"Finding {finding.code} is not tied to a project")
        return self._by_id[finding.fix], context.catalog.get(finding.project)
