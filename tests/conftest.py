"""Shared fixtures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quant_workbench.domain.paths import AppPaths
from quant_workbench.infrastructure.paths import ensure_app_dirs


class FakeClock:
    """Deterministic clock: time only moves when the test says so."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 9, 21, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def accented_root(tmp_path: Path) -> Path:
    """A directory whose name has a space and an accent, like the real workspace.

    The real workspace lives in ``Quant - María``; every path-handling bug that this
    name would expose (bad quoting, cp1252 mojibake) must be caught by the test suite,
    not by the user.
    """
    root = tmp_path / "Quant - María"
    root.mkdir()
    return root


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths.under(tmp_path / "app-home")
    ensure_app_dirs(paths)
    return paths


# The desktop UI tests need PySide6 (the ``gui`` extra); the headless core job runs without it.
import importlib.util  # noqa: E402

collect_ignore = [] if importlib.util.find_spec("PySide6") else ["gui"]
