"""Git use cases for the Git panel: overview, diff and a *guarded* commit. Never a push.

The workbench does not push, ever (a push changes shared state on a remote); it shows the
command and leaves the decision to the user. Committing is allowed, but only with the identity
the portfolio's policy requires: a commit made with the wrong e-mail is permanent history.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from quant_workbench.application.settings import Settings
from quant_workbench.domain.errors import GitError, PolicyViolationError
from quant_workbench.domain.git import CommitInfo, GitState
from quant_workbench.domain.ports import GitGateway
from quant_workbench.domain.project import Project

_DEFAULT_LOG_LENGTH = 15


@dataclass(frozen=True, slots=True)
class GitOverview:
    """Everything the Git panel shows about one project's repository."""

    state: GitState
    commits: tuple[CommitInfo, ...]
    #: The e-mail commits would be authored with, and whether it is the required one.
    email: str | None
    identity_ok: bool
    remote: str | None
    push_command: str


class GitService:
    def __init__(self, git: GitGateway, settings: Settings) -> None:
        self._git = git
        self._settings = settings

    def overview(self, project: Project) -> GitOverview | None:
        """The repository's state, or ``None`` when the project is not a git repository."""
        root = project.root
        if not self._git.is_repository(root):
            return None
        email = self._git.config_value(root, "user.email")
        return GitOverview(
            state=self._git.state(root),
            commits=self._git.log(root, _DEFAULT_LOG_LENGTH),
            email=email,
            identity_ok=email == self._settings.expected_git_email,
            remote=self._git.remote_url(root),
            push_command=self.push_command(project),
        )

    def diff(self, project: Project, relative: str | None = None) -> str:
        return self._git.diff(project.root, relative)

    def push_command(self, project: Project) -> str:
        """The command the user would run to publish the commits (the app never runs it)."""
        return f'git -C "{project.root}" push'

    def commit(self, project: Project, message: str, paths: Sequence[str] = ()) -> str:
        """Commit ``paths`` (all changes when empty) after checking the identity policy."""
        self._require_identity(project)
        text = message.strip()
        if not text:
            raise GitError("A commit needs a message")
        trailer = self._settings.commit_trailer.strip()
        if trailer and trailer not in text:
            text = f"{text}\n\n{trailer}"
        return self._git.commit(project.root, text, paths)

    def _require_identity(self, project: Project) -> None:
        email = self._git.config_value(project.root, "user.email")
        if email != self._settings.expected_git_email:
            raise PolicyViolationError(
                f"Commits in {project.title} would be authored as "
                f"{email or '(no e-mail configured)'}, not {self._settings.expected_git_email}",
                hint="Use the doctor's fix (`qw doctor --fix`) to set the repo's own identity.",
            )
        expected_name = self._settings.expected_git_name
        if (
            expected_name is not None
            and self._git.config_value(project.root, "user.name") != expected_name
        ):
            raise PolicyViolationError(
                f"The git user.name of {project.title} is not {expected_name}",
                hint="Set it with the doctor's fix before committing.",
            )
