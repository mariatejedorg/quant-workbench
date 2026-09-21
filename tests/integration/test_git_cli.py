"""The git adapter against real repositories (never the user's own git configuration)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from quant_workbench.domain.errors import GitError
from quant_workbench.infrastructure.git_cli import GitCli

pytestmark = pytest.mark.integration

GLOBAL_EMAIL = "global@example.org"


@pytest.fixture(autouse=True)
def isolated_git_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point git at a throw-away global config so no test can read or change the real one."""
    global_config = tmp_path / "global-gitconfig"
    global_config.write_text(f"[user]\n\temail = {GLOBAL_EMAIL}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return global_config


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=t@example.org", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


@pytest.fixture
def repo(accented_root: Path) -> Path:
    root = accented_root / "proyecto"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "análisis.py").write_text("x = 1\n", encoding="utf-8")
    (root / "notes.txt").write_text("hello\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "first")
    return root


def test_only_the_top_level_of_a_repository_counts(repo: Path, accented_root: Path) -> None:
    (repo / "sub").mkdir()
    plain = accented_root / "plain"
    plain.mkdir()

    cli = GitCli()

    assert cli.is_repository(repo)
    assert not cli.is_repository(repo / "sub")  # inside the repo, but not its root
    assert not cli.is_repository(plain)


def test_a_clean_repository(repo: Path) -> None:
    state = GitCli().state(repo)

    assert state.branch == "main"
    assert state.is_clean
    assert (state.ahead, state.behind, state.has_upstream) == (0, 0, False)


def test_modified_untracked_and_renamed_paths_are_reported_with_accents(repo: Path) -> None:
    (repo / "notes.txt").write_text("changed\n", encoding="utf-8")
    (repo / "nuevo módulo.py").write_text("y = 2\n", encoding="utf-8")
    git(repo, "mv", "análisis.py", "analisis_renombrado.py")

    state = GitCli().state(repo)

    assert sorted(state.dirty) == ["analisis_renombrado.py", "notes.txt", "nuevo módulo.py"]
    assert not state.is_clean


def test_ahead_and_behind_are_counted_against_the_upstream(repo: Path, accented_root: Path) -> None:
    remote = accented_root / "remote.git"
    git(accented_root, "init", "-q", "--bare", "-b", "main", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    (repo / "notes.txt").write_text("local work\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "local")

    state = GitCli().state(repo)

    assert (state.ahead, state.behind, state.has_upstream) == (1, 0, True)
    assert GitCli().remote_url(repo) == str(remote)


def test_a_detached_head_has_no_branch(repo: Path) -> None:
    git(repo, "checkout", "-q", "--detach")

    assert GitCli().state(repo).branch is None


def test_config_falls_back_to_the_global_identity_and_local_overrides_it(repo: Path) -> None:
    cli = GitCli()
    assert cli.config_value(repo, "user.email") == GLOBAL_EMAIL
    assert cli.config_value(repo, "user.name") is None

    git(repo, "config", "--local", "user.email", "local@example.org")

    assert cli.config_value(repo, "user.email") == "local@example.org"


def test_setting_the_local_identity_never_touches_the_global_config(
    repo: Path, isolated_git_config: Path
) -> None:
    before = isolated_git_config.read_text(encoding="utf-8")

    GitCli().set_local_config(repo, "user.email", "maria@example.org")
    GitCli().set_local_config(repo, "user.name", "María T")

    assert isolated_git_config.read_text(encoding="utf-8") == before
    assert GitCli().config_value(repo, "user.email") == "maria@example.org"
    assert "maria@example.org" in (repo / ".git" / "config").read_text(encoding="utf-8")


@pytest.mark.parametrize("key", ["core.hooksPath", "user.signingkey", "remote.origin.url", ""])
def test_only_identity_keys_can_be_written(repo: Path, key: str) -> None:
    with pytest.raises(GitError, match="Refusing to set git config key"):
        GitCli().set_local_config(repo, key, "x")


def test_a_missing_remote_is_none(repo: Path) -> None:
    assert GitCli().remote_url(repo) is None


def test_tracked_files_keep_their_accents(repo: Path) -> None:
    assert sorted(GitCli().tracked_files(repo)) == ["análisis.py", "notes.txt"]


def test_ignore_rules_are_honoured(repo: Path) -> None:
    (repo / ".gitignore").write_text("venv/\n*.log\n", encoding="utf-8")
    cli = GitCli()

    assert cli.is_ignored(repo, "venv/")
    assert cli.is_ignored(repo, "run.log")
    assert not cli.is_ignored(repo, "notes.txt")


def test_a_missing_git_executable_is_a_clear_error(repo: Path) -> None:
    with pytest.raises(GitError, match="git is not installed"):
        GitCli("git-that-does-not-exist-xyz").state(repo)


def test_a_failing_command_reports_git_s_own_message(accented_root: Path) -> None:
    with pytest.raises(GitError, match="git status failed"):
        GitCli().state(accented_root / "nowhere")


def test_the_environment_of_the_test_is_isolated(repo: Path) -> None:
    """Guard for the fixture itself: git must be reading the throw-away config."""
    assert os.environ["GIT_CONFIG_GLOBAL"].endswith("global-gitconfig")
