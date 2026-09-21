"""Git access through the ``git`` command line (the same tool the user runs by hand).

Shelling out instead of using a library keeps behaviour identical to the terminal: same
config resolution (local, global, system), same SSH aliases, same ignore rules.

Two safety properties are enforced here rather than left to callers:

* the only write is ``set_local_config`` and it is restricted to identity keys and to the
  repository's own config file (``--local``), so the user's global git configuration cannot
  be changed through the workbench;
* commands never prompt (``GIT_TERMINAL_PROMPT=0``) and never run without a timeout, so a
  credential prompt or a hung network filesystem cannot freeze the application.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from quant_workbench.domain.errors import GitError
from quant_workbench.domain.git import GitState

_TIMEOUT_SECONDS = 30
#: The only keys ``set_local_config`` accepts.
_WRITABLE_KEYS = frozenset({"user.email", "user.name"})
_AHEAD_BEHIND = re.compile(r"\+(\d+) -(\d+)")
#: Windows: do not flash a console window for every git call made from the GUI.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class GitCli:
    """Implements :class:`~quant_workbench.domain.ports.GitGateway`."""

    def __init__(self, executable: str = "git") -> None:
        self._executable = executable

    # ---------------------------------------------------------------- reading
    def is_repository(self, root: Path) -> bool:
        result = self._run(root, "rev-parse", "--show-toplevel", check=False)
        if result.returncode != 0:
            return False
        top = Path(result.stdout.strip()).resolve()
        return top == root.resolve()

    def state(self, root: Path) -> GitState:
        output = self._run(root, "status", "--porcelain=v2", "--branch", "-z").stdout
        branch: str | None = None
        ahead = behind = 0
        has_upstream = False
        dirty: list[str] = []
        entries = output.split("\0")
        skip_next = False
        for entry in entries:
            if skip_next:  # the original path of a rename/copy record
                skip_next = False
                continue
            if entry.startswith("# branch.head "):
                head = entry.removeprefix("# branch.head ")
                branch = None if head == "(detached)" else head
            elif entry.startswith("# branch.ab "):
                has_upstream = True
                match = _AHEAD_BEHIND.search(entry)
                if match:
                    ahead, behind = int(match.group(1)), int(match.group(2))
            elif entry.startswith(("1 ", "u ")):
                dirty.append(entry.split(" ", 8 if entry[0] == "1" else 10)[-1])
            elif entry.startswith("2 "):
                dirty.append(entry.split(" ", 9)[-1])
                skip_next = True
            elif entry.startswith("? "):
                dirty.append(entry[2:])
        return GitState(branch, tuple(dirty), ahead, behind, has_upstream)

    def config_value(self, root: Path, key: str) -> str | None:
        result = self._run(root, "config", "--get", key, check=False)
        value = result.stdout.strip()
        return value if result.returncode == 0 and value else None

    def remote_url(self, root: Path, name: str = "origin") -> str | None:
        result = self._run(root, "remote", "get-url", name, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def tracked_files(self, root: Path) -> tuple[str, ...]:
        output = self._run(root, "ls-files", "-z").stdout
        return tuple(path for path in output.split("\0") if path)

    def is_ignored(self, root: Path, relative: str) -> bool:
        return self._run(root, "check-ignore", "-q", "--", relative, check=False).returncode == 0

    # ---------------------------------------------------------------- writing
    def set_local_config(self, root: Path, key: str, value: str) -> None:
        if key not in _WRITABLE_KEYS:
            raise GitError(f"Refusing to set git config key {key!r}", hint="Only identity keys.")
        self._run(root, "config", "--local", key, value)

    # --------------------------------------------------------------- internals
    def _run(self, root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
        command = [self._executable, "-C", str(root), "-c", "core.quotepath=off", *args]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_TIMEOUT_SECONDS,
                env=environment,
                creationflags=_NO_WINDOW,
                check=False,
            )
        except FileNotFoundError as error:
            raise GitError(
                "git is not installed or not on PATH", hint="Install git and restart."
            ) from error
        except subprocess.TimeoutExpired as error:
            raise GitError(f"git {args[0]} timed out after {_TIMEOUT_SECONDS}s") from error
        if check and result.returncode != 0:
            raise GitError(
                f"git {args[0]} failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        return result
