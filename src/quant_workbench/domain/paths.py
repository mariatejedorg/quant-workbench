"""Locations of the workbench's own files (never inside the user's projects)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Per-user directories. A pure value object: creating or resolving them is I/O and
    lives in :mod:`quant_workbench.infrastructure.paths`.
    """

    config_dir: Path
    data_dir: Path
    cache_dir: Path
    log_dir: Path

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.toml"

    @property
    def database_file(self) -> Path:
        return self.data_dir / "workbench.sqlite3"

    @classmethod
    def under(cls, root: Path) -> AppPaths:
        """Place every directory below ``root`` (used by tests and ``qw --home``)."""
        return cls(
            config_dir=root / "config",
            data_dir=root / "data",
            cache_dir=root / "cache",
            log_dir=root / "logs",
        )

    def all_dirs(self) -> tuple[Path, ...]:
        return (self.config_dir, self.data_dir, self.cache_dir, self.log_dir)
