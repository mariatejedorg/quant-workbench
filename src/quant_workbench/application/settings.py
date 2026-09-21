"""User-configurable settings.

Resolution order (highest priority first): explicit constructor arguments, ``QW_*``
environment variables, then the ``settings.toml`` file in the user's config directory.
Everything has a sensible default, so the workbench runs with no configuration at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

#: The identity every commit in this portfolio must be authored with (see the
#: ``GitIdentity`` checker). Overridable, but the default encodes the project policy.
DEFAULT_GIT_EMAIL = "mariatg.invers@gmail.com"


class Settings(BaseSettings):
    """Validated, immutable application settings."""

    model_config = SettingsConfigDict(
        env_prefix="QW_",
        frozen=True,
        extra="ignore",
        toml_file=None,
    )

    #: Folder that contains the projects. ``None`` means "auto-detect from the cwd".
    workspace_root: Path | None = None

    #: Git identity policy enforced before any commit made through the workbench.
    expected_git_email: str = DEFAULT_GIT_EMAIL
    expected_git_name: str | None = None

    #: How many project runs may execute at the same time.
    max_concurrency: int = Field(default=3, ge=1, le=16)
    #: Wall-clock limit per run, in seconds, before the process tree is killed.
    run_timeout_seconds: int = Field(default=900, ge=10)
    #: Automatic retries for failures classified as transient (e.g. API rate limits).
    max_retries: int = Field(default=1, ge=0, le=5)

    theme: Literal["system", "light", "dark"] = "system"

    #: Checker ids that must not run.
    disabled_checkers: tuple[str, ...] = ()

    #: Optional trailer appended to commits created from the Git panel (empty = none).
    commit_trailer: str = ""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Order the sources: init > env > TOML file. Dotenv and secrets are not used."""
        toml_path = settings_cls.model_config.get("toml_file")
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        if toml_path is not None:
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=toml_path))
        return tuple(sources)


def load_settings(settings_file: Path | None = None) -> Settings:
    """Load settings, reading ``settings_file`` when it exists.

    A subclass is created on the fly to bind the file path: pydantic-settings resolves
    the TOML location at class level, and this keeps :class:`Settings` itself free of
    global state (important for tests that load several configurations in one process).
    """
    if settings_file is None or not settings_file.is_file():
        return Settings()

    class _FileBackedSettings(Settings):
        model_config = SettingsConfigDict(
            env_prefix="QW_",
            frozen=True,
            extra="ignore",
            toml_file=str(settings_file),
        )

    return _FileBackedSettings()
