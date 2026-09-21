"""Checkers about the project's git repository: identity policy and hygiene."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import PurePosixPath

from quant_workbench.application.diagnostics import CheckContext, Checker, require_project
from quant_workbench.domain.code_analysis import find_secrets
from quant_workbench.domain.diagnostics import Finding, Location, Severity
from quant_workbench.domain.git import remote_host
from quant_workbench.domain.project import Project

_TEXT_SUFFIXES = frozenset(
    {".py", ".toml", ".txt", ".md", ".json", ".cfg", ".ini", ".yml", ".yaml", ".env", ".sh"}
)
#: Files bigger than this are not scanned for secrets (they are data, not configuration).
_MAX_SCANNED_BYTES = 512 * 1024
_MAX_LISTED = 5
_BYTES_PER_MB = 1024 * 1024


class GitIdentityChecker(Checker):
    """Commits must be authored with the portfolio's identity, and pushed through its remote.

    This guards a policy, not a preference: the repositories belong to a different person
    than the machine's global git identity, and a commit made with the wrong e-mail is
    permanent history. It reads the *effective* configuration, so a repository that relies
    on the global identity is flagged just like one configured wrongly.
    """

    id = "git-identity"
    title = "Git identity"

    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]:
        project = require_project(project)
        settings = context.settings
        git, root = context.git, project.root
        if not await asyncio.to_thread(git.is_repository, root):
            return [
                Finding(
                    self.id,
                    "not-a-repository",
                    Severity.INFO,
                    f"{project.title} is not a git repository of its own",
                    project=project.slug,
                )
            ]
        findings: list[Finding] = []
        email = await asyncio.to_thread(git.config_value, root, "user.email")
        if email != settings.expected_git_email:
            findings.append(
                Finding(
                    self.id,
                    "git-email-mismatch",
                    Severity.ERROR,
                    f"Commits here would be authored as {email or '(no e-mail configured)'}, "
                    f"not {settings.expected_git_email}",
                    project=project.slug,
                    detail="Set the repository's own user.email before committing.",
                    fix="set-git-identity",
                )
            )
        if settings.expected_git_name is not None:
            name = await asyncio.to_thread(git.config_value, root, "user.name")
            if name != settings.expected_git_name:
                findings.append(
                    Finding(
                        self.id,
                        "git-name-mismatch",
                        Severity.WARNING,
                        f"Commits here would be authored by {name or '(no name)'}, "
                        f"not {settings.expected_git_name}",
                        project=project.slug,
                        fix="set-git-identity",
                    )
                )
        expected_host = settings.expected_git_remote_host
        url = await asyncio.to_thread(git.remote_url, root)
        if expected_host and url is not None and remote_host(url) != expected_host:
            findings.append(
                Finding(
                    self.id,
                    "git-remote-host",
                    Severity.WARNING,
                    f"origin points to {remote_host(url) or url}, not {expected_host}",
                    project=project.slug,
                    detail="The SSH alias selects which account the push is made with.",
                )
            )
        return findings


class GitHygieneChecker(Checker):
    """Things that should not be in (or missing from) the repository."""

    id = "git-hygiene"
    title = "Repository hygiene"

    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]:
        project = require_project(project)
        git, root = context.git, project.root
        if not await asyncio.to_thread(git.is_repository, root):
            return []
        findings: list[Finding] = []
        findings.extend(await self._status(project, context))
        tracked = await asyncio.to_thread(git.tracked_files, root)
        findings.extend(await self._environment_ignored(project, context, tracked))
        findings.extend(self._large_files(project, context, tracked))
        findings.extend(self._secrets(project, context, tracked))
        return findings

    async def _status(self, project: Project, context: CheckContext) -> list[Finding]:
        state = await asyncio.to_thread(context.git.state, project.root)
        findings: list[Finding] = []
        if state.dirty:
            shown = ", ".join(state.dirty[:_MAX_LISTED])
            hidden = len(state.dirty) - _MAX_LISTED
            more = f" and {hidden} more" if hidden > 0 else ""
            findings.append(
                Finding(
                    self.id,
                    "uncommitted-changes",
                    Severity.INFO,
                    f"{len(state.dirty)} path(s) with uncommitted changes: {shown}{more}",
                    project=project.slug,
                )
            )
        if state.ahead:
            findings.append(
                Finding(
                    self.id,
                    "unpushed-commits",
                    Severity.INFO,
                    f"{state.ahead} commit(s) not pushed yet",
                    project=project.slug,
                    detail=f'Push with: git -C "{project.root}" push',
                )
            )
        return findings

    async def _environment_ignored(
        self, project: Project, context: CheckContext, tracked: Sequence[str]
    ) -> list[Finding]:
        venv = project.spec.venv
        if not project.venv_dir.is_dir():
            return []
        if any(path.startswith(f"{venv}/") for path in tracked):
            return [
                Finding(
                    self.id,
                    "venv-tracked",
                    Severity.ERROR,
                    f"Files of {venv}/ are committed to the repository",
                    project=project.slug,
                    detail=(
                        f"Untrack them with `git rm -r --cached {venv}` and add {venv}/ to "
                        ".gitignore. Not automatic: it changes what the repository contains."
                    ),
                )
            ]
        if await asyncio.to_thread(context.git.is_ignored, project.root, f"{venv}/"):
            return []
        return [
            Finding(
                self.id,
                "venv-not-ignored",
                Severity.WARNING,
                f"{venv}/ is not in .gitignore; the environment could be committed by accident",
                project=project.slug,
                location=Location(".gitignore"),
                fix="ignore-venv",
            )
        ]

    def _large_files(
        self, project: Project, context: CheckContext, tracked: Sequence[str]
    ) -> list[Finding]:
        limit_mb = context.settings.max_tracked_file_mb
        findings: list[Finding] = []
        for relative in tracked:
            size = context.files.size(project, relative)
            if size is not None and size > limit_mb * _BYTES_PER_MB:
                findings.append(
                    Finding(
                        self.id,
                        "large-file",
                        Severity.WARNING,
                        f"{relative} is {size / _BYTES_PER_MB:.1f} MB (limit {limit_mb} MB)",
                        project=project.slug,
                        location=Location(relative),
                        detail="Large binaries bloat the repository history for ever.",
                    )
                )
        return findings

    def _secrets(
        self, project: Project, context: CheckContext, tracked: Sequence[str]
    ) -> list[Finding]:
        findings: list[Finding] = []
        for relative in tracked:
            if PurePosixPath(relative).suffix.lower() not in _TEXT_SUFFIXES:
                continue
            data = context.files.read_bytes(project, relative)
            if data is None or len(data) > _MAX_SCANNED_BYTES:
                continue
            text = data.decode("utf-8", errors="replace")
            findings.extend(
                Finding(
                    self.id,
                    "secret-in-repository",
                    Severity.ERROR if hit.certain else Severity.WARNING,
                    f"Possible {hit.kind.replace('-', ' ')} committed in {relative}",
                    project=project.slug,
                    location=Location(relative, hit.line),
                    detail="The value is not shown here. Rotate it if it is real.",
                )
                for hit in find_secrets(text)
            )
        return findings
