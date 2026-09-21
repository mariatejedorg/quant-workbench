"""The project model: what a quantitative project *is*, independent of how it was found."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from quant_workbench.domain.ids import Slug


class ManifestSource(StrEnum):
    """Where a project's description came from, in decreasing order of precedence."""

    IN_REPO = "in-repo"  # a quant-project.toml inside the project folder
    REGISTRY = "registry"  # a manifest shipped with the workbench
    INFERRED = "inferred"  # guessed from the folder layout (convention over configuration)


class MetricKind(StrEnum):
    FLOAT = "float"
    INT = "int"
    STR = "str"
    PERCENT = "percent"  # "12.3%" -> 12.3


@dataclass(frozen=True, slots=True)
class MetricExtractorSpec:
    """How to pull one named metric out of a run's console output.

    ``pattern`` is a regular expression searched line by line; its first capture group
    is the value. Legacy projects print free-form Spanish text, so extractors are
    declared per project instead of assuming a machine-readable format.
    """

    name: str
    pattern: str
    kind: MetricKind = MetricKind.FLOAT
    unit: str = ""


@dataclass(frozen=True, slots=True)
class ConfigTarget:
    """A source file holding editable constants, and optionally which of them."""

    file: str  # relative to the project root, always with forward slashes
    symbols: tuple[str, ...] = ()  # empty means "every literal constant in the file"


@dataclass(frozen=True, slots=True)
class OutputSpec:
    """Where a project writes what it produces, relative to its root."""

    dashboard: str | None = "outputs/dashboard.html"
    images: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectSpec:
    """The declarative description of a project (a parsed manifest)."""

    slug: Slug
    title: str
    folder: str
    category: str
    order: int
    description: str = ""
    entrypoint: str = "src/main.py"
    requirements: str = "requirements.txt"
    venv: str = "venv"
    outputs: OutputSpec = field(default_factory=OutputSpec)
    config_targets: tuple[ConfigTarget, ...] = ()
    depends_on: tuple[Slug, ...] = ()
    extractors: tuple[MetricExtractorSpec, ...] = ()
    repo_url: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Project:
    """A :class:`ProjectSpec` resolved to a real folder on disk."""

    spec: ProjectSpec
    root: Path
    source: ManifestSource

    @property
    def slug(self) -> Slug:
        return self.spec.slug

    @property
    def title(self) -> str:
        return self.spec.title

    @property
    def entrypoint_path(self) -> Path:
        return self.root / self.spec.entrypoint

    @property
    def requirements_path(self) -> Path:
        return self.root / self.spec.requirements

    @property
    def dashboard_path(self) -> Path | None:
        return self.root / self.spec.outputs.dashboard if self.spec.outputs.dashboard else None

    @property
    def venv_dir(self) -> Path:
        return self.root / self.spec.venv

    @property
    def venv_python(self) -> Path:
        """The interpreter of the project's own virtual environment (may not exist yet)."""
        if os.name == "nt":
            return self.venv_dir / "Scripts" / "python.exe"
        return self.venv_dir / "bin" / "python"
