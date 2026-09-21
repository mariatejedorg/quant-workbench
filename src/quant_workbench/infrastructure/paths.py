"""Resolving and creating the workbench's per-user directories."""

from __future__ import annotations

from pathlib import Path

import platformdirs

from quant_workbench.domain.paths import AppPaths

_APP_NAME = "quant-workbench"


def default_app_paths() -> AppPaths:
    """OS-conventional locations (``%APPDATA%``, ``~/.local/share``, ``~/Library``...)."""
    return AppPaths(
        config_dir=Path(platformdirs.user_config_dir(_APP_NAME, appauthor=False)),
        data_dir=Path(platformdirs.user_data_dir(_APP_NAME, appauthor=False)),
        cache_dir=Path(platformdirs.user_cache_dir(_APP_NAME, appauthor=False)),
        log_dir=Path(platformdirs.user_log_dir(_APP_NAME, appauthor=False)),
    )


def ensure_app_dirs(paths: AppPaths) -> None:
    """Create the directories if they do not exist yet."""
    for directory in paths.all_dirs():
        directory.mkdir(parents=True, exist_ok=True)
