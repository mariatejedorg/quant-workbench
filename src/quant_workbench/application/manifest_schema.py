"""The ``quant-project.toml`` contract: pydantic models, validation rules, JSON Schema.

This is the wire format of a manifest. It lives in the application layer (it is a data
contract, not I/O); reading a file from disk is the infrastructure adapter's job. The rest
of the system only ever sees the plain domain dataclasses it converts to
(:class:`~quant_workbench.domain.project.ProjectSpec`).
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from quant_workbench.domain.errors import InvalidSlugError, ManifestError
from quant_workbench.domain.ids import parse_slug
from quant_workbench.domain.project import (
    ConfigTarget,
    MetricExtractorSpec,
    MetricKind,
    MetricPick,
    OutputSpec,
    ProjectSpec,
)

SCHEMA_VERSION: Literal[1] = 1


def _validate_relative_path(value: str) -> str:
    """Manifest paths are project-relative, forward-slashed and may not escape the root."""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or not value:
        raise ValueError(f"{value!r} must be a relative path with forward slashes and no '..'")
    return value


RelativePath = Annotated[str, Field(min_length=1, description="Path relative to the project root.")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetricModel(_Strict):
    """One value to extract from a run's console output."""

    name: str = Field(min_length=1, description="Metric key, e.g. 'sharpe_strategy'.")
    pattern: str = Field(
        description="Regular expression searched line by line; group 1 is the value."
    )
    kind: MetricKind = MetricKind.FLOAT
    unit: str = ""
    pick: MetricPick = Field(
        default=MetricPick.LAST,
        description="Which match wins when several lines match: 'last' (default) or 'first'.",
    )

    @field_validator("pattern")
    @classmethod
    def _compiles_with_a_group(cls, value: str) -> str:
        try:
            compiled = re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        if compiled.groups < 1:
            raise ValueError("pattern needs at least one capture group holding the value")
        return value


class ConfigTargetModel(_Strict):
    file: RelativePath
    symbols: list[str] = Field(
        default_factory=list,
        description="Constants to expose; empty means every literal constant in the file.",
    )

    _check_file = field_validator("file")(_validate_relative_path)


class OutputsModel(_Strict):
    dashboard: RelativePath | None = "outputs/dashboard.html"
    images: list[RelativePath] = Field(default_factory=list)
    artifacts: list[RelativePath] = Field(default_factory=list)

    @field_validator("dashboard")
    @classmethod
    def _check_dashboard(cls, value: str | None) -> str | None:
        return None if value is None else _validate_relative_path(value)

    @field_validator("images", "artifacts")
    @classmethod
    def _check_lists(cls, values: list[str]) -> list[str]:
        return [_validate_relative_path(v) for v in values]


class ManifestModel(_Strict):
    """The complete manifest document."""

    schema_version: Literal[1] = SCHEMA_VERSION
    slug: str = Field(description="Stable kebab-case identifier; usually the repository name.")
    title: str = Field(min_length=1)
    folder: str | None = Field(
        default=None,
        description="Local folder name. Required in the registry; inferred for in-repo manifests.",
    )
    category: str = Field(min_length=1)
    order: int = Field(default=1000, ge=0)
    description: str = ""
    entrypoint: RelativePath = "src/main.py"
    requirements: RelativePath = "requirements.txt"
    venv: RelativePath = "venv"
    outputs: OutputsModel = Field(default_factory=OutputsModel)
    config_targets: list[ConfigTargetModel] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    metrics: list[MetricModel] = Field(default_factory=list)
    repo_url: str | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("entrypoint", "requirements", "venv")
    @classmethod
    def _check_paths(cls, value: str) -> str:
        return _validate_relative_path(value)

    def to_spec(self, folder: str | None = None) -> ProjectSpec:
        """Convert to the domain object. ``folder`` overrides/supplies the local folder."""
        resolved_folder = folder or self.folder
        if not resolved_folder:
            raise ManifestError(
                f"Manifest for {self.slug!r} does not say which folder it describes",
                hint="Registry manifests must set 'folder'.",
            )
        try:
            slug = parse_slug(self.slug)
            depends_on = tuple(parse_slug(dep) for dep in self.depends_on)
        except InvalidSlugError as exc:
            raise ManifestError(str(exc.message), hint=exc.hint) from exc
        return ProjectSpec(
            slug=slug,
            title=self.title,
            folder=resolved_folder,
            category=self.category,
            order=self.order,
            description=self.description,
            entrypoint=self.entrypoint,
            requirements=self.requirements,
            venv=self.venv,
            outputs=OutputSpec(
                dashboard=self.outputs.dashboard,
                images=tuple(self.outputs.images),
                artifacts=tuple(self.outputs.artifacts),
            ),
            config_targets=tuple(
                ConfigTarget(file=t.file, symbols=tuple(t.symbols)) for t in self.config_targets
            ),
            depends_on=depends_on,
            extractors=tuple(
                MetricExtractorSpec(
                    name=m.name, pattern=m.pattern, kind=m.kind, unit=m.unit, pick=m.pick
                )
                for m in self.metrics
            ),
            repo_url=self.repo_url,
            tags=tuple(self.tags),
        )


def export_json_schema() -> dict[str, Any]:
    """The JSON Schema of the manifest, for editor completion (``qw schema``)."""
    schema = ManifestModel.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "quant-project.toml"
    return schema


def format_validation_errors(exc: ValidationError) -> str:
    """One ``- field.path: message`` line per problem, for human-readable error output."""
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(document)"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)
