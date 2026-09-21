"""What the workbench knows about a project's git repository."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GitState:
    """A snapshot of a working tree (the result of ``git status``)."""

    #: The checked-out branch, or ``None`` on a detached HEAD.
    branch: str | None
    #: Paths with uncommitted changes, including untracked files, relative to the repo root.
    dirty: tuple[str, ...]
    #: Commits not yet on the upstream branch / upstream commits not yet here.
    ahead: int = 0
    behind: int = 0
    #: Whether the branch tracks an upstream at all (without one, ahead/behind are 0).
    has_upstream: bool = False

    @property
    def is_clean(self) -> bool:
        return not self.dirty


def remote_host(url: str) -> str | None:
    """The host of a git remote URL, in any of its usual spellings.

    ``git@github.com-maria:owner/repo.git`` -> ``github.com-maria`` (an SSH alias from
    ``~/.ssh/config``, which is how the portfolio selects the right identity),
    ``ssh://git@host/owner/repo`` and ``https://host/owner/repo`` -> ``host``.
    """
    text = url.strip()
    if "://" in text:
        authority = text.split("://", 1)[1].split("/", 1)[0]
        return authority.rsplit("@", 1)[-1].split(":", 1)[0] or None
    if "@" in text and ":" in text:  # scp-like: user@host:path
        return text.split("@", 1)[1].split(":", 1)[0] or None
    return None
