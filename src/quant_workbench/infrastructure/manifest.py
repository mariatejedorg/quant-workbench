"""Reading ``quant-project.toml`` files from disk."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from quant_workbench.application.manifest_schema import ManifestModel, format_validation_errors
from quant_workbench.domain.errors import ManifestError
from quant_workbench.domain.project import ProjectSpec

MANIFEST_FILENAME = "quant-project.toml"


def read_manifest(path: Path, *, folder: str | None = None) -> ProjectSpec:
    """Parse and validate one manifest file into a :class:`ProjectSpec`.

    Every failure mode (unreadable file, bad TOML, schema violation) is reported as a
    :class:`ManifestError` that names the file and the offending field, never as a raw
    pydantic or tomllib traceback.
    """
    try:
        raw: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"Cannot read manifest {path}: {exc.strerror or exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{path} is not valid TOML: {exc}") from exc

    try:
        model = ManifestModel.model_validate(raw)
    except ValidationError as exc:
        raise ManifestError(f"{path} is invalid:\n{format_validation_errors(exc)}") from exc
    return model.to_spec(folder)
