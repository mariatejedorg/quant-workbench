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


# ------------------------------------------------------- log, diff and commit
def test_the_log_lists_commits_newest_first_with_their_author(repo: Path) -> None:
    (repo / "notes.txt").write_text("more\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "second: with, punctuation")

    commits = GitCli().log(repo, 10)

    assert [c.subject for c in commits] == ["second: with, punctuation", "first"]
    assert commits[0].author == "Test"
    assert commits[0].email == "t@example.org"
    assert len(commits[0].hash) == 40
    assert commits[0].short == commits[0].hash[:7]
    assert commits[0].date.tzinfo is not None
    assert len(GitCli().log(repo, 1)) == 1


def test_the_log_of_a_repository_without_commits_is_empty(accented_root: Path) -> None:
    root = accented_root / "vacio"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")

    assert GitCli().log(root) == ()


def test_the_diff_shows_uncommitted_changes_of_one_path_or_all(repo: Path) -> None:
    (repo / "notes.txt").write_text("changed\n", encoding="utf-8")
    (repo / "análisis.py").write_text("x = 2\n", encoding="utf-8")
    cli = GitCli()

    everything = cli.diff(repo)
    only_notes = cli.diff(repo, "notes.txt")

    assert "+changed" in everything
    assert "+x = 2" in everything
    assert "+changed" in only_notes
    assert "x = 2" not in only_notes


def test_the_diff_of_a_clean_tree_is_empty(repo: Path) -> None:
    assert GitCli().diff(repo) == ""


def test_commit_stages_the_given_paths_only_and_returns_the_hash(repo: Path) -> None:
    (repo / "notes.txt").write_text("staged\n", encoding="utf-8")
    (repo / "otro.txt").write_text("left alone\n", encoding="utf-8")
    git(repo, "config", "--local", "user.email", "maria@example.org")
    git(repo, "config", "--local", "user.name", "María")
    cli = GitCli()

    short = cli.commit(repo, "only notes", ["notes.txt"])

    assert short == cli.log(repo, 1)[0].short
    assert cli.log(repo, 1)[0].email == "maria@example.org"
    assert cli.state(repo).dirty == ("otro.txt",)  # the other file was not committed


def test_commit_without_paths_commits_everything_and_needs_a_message(repo: Path) -> None:
    (repo / "notes.txt").write_text("a\n", encoding="utf-8")
    (repo / "nuevo.txt").write_text("b\n", encoding="utf-8")
    git(repo, "config", "--local", "user.email", "maria@example.org")
    git(repo, "config", "--local", "user.name", "María")

    with pytest.raises(GitError, match="needs a message"):
        GitCli().commit(repo, "  ", [])
    GitCli().commit(repo, "all of it", [])

    assert GitCli().state(repo).is_clean


def test_the_hooks_of_the_repository_are_respected(repo: Path) -> None:
    """The workbench never passes --no-verify: a failing pre-commit hook blocks the commit."""
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho blocked by hook >&2\nexit 1\n", encoding="utf-8", newline="\n")
    (repo / "notes.txt").write_text("x\n", encoding="utf-8")
    git(repo, "config", "--local", "user.email", "maria@example.org")
    git(repo, "config", "--local", "user.name", "María")

    with pytest.raises(GitError, match="blocked by hook"):
        GitCli().commit(repo, "try", ["notes.txt"])

    assert [c.subject for c in GitCli().log(repo)] == ["first"]
