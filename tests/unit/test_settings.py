from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from quant_workbench.application.settings import DEFAULT_GIT_EMAIL, Settings, load_settings


def test_defaults_encode_the_portfolio_policy() -> None:
    settings = Settings()
    assert settings.expected_git_email == DEFAULT_GIT_EMAIL == "mariatg.invers@gmail.com"
    assert settings.max_concurrency == 3
    assert settings.workspace_root is None
    assert settings.disabled_checkers == ()


def test_settings_are_immutable() -> None:
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.max_concurrency = 9  # type: ignore[misc]


@pytest.mark.parametrize("field", ["max_concurrency", "run_timeout_seconds"])
def test_out_of_range_values_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: 0})


def test_file_values_are_loaded(accented_root: Path) -> None:
    file = accented_root / "settings.toml"
    file.write_text(
        'max_concurrency = 5\ntheme = "dark"\ndisabled_checkers = ["determinism"]\n'
        f'workspace_root = "{(accented_root / "ws").as_posix()}"\n',
        encoding="utf-8",
    )

    settings = load_settings(file)

    assert settings.max_concurrency == 5
    assert settings.theme == "dark"
    assert settings.disabled_checkers == ("determinism",)
    assert settings.workspace_root == accented_root / "ws"


def test_environment_overrides_the_file(
    accented_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = accented_root / "settings.toml"
    file.write_text("max_concurrency = 5\n", encoding="utf-8")
    monkeypatch.setenv("QW_MAX_CONCURRENCY", "7")

    assert load_settings(file).max_concurrency == 7


def test_a_missing_file_falls_back_to_defaults(accented_root: Path) -> None:
    assert load_settings(accented_root / "nope.toml") == Settings()
    assert load_settings(None) == Settings()
