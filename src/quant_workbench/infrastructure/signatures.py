"""Loading the failure-signature knowledge base from TOML."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from quant_workbench.application.manifest_schema import format_validation_errors
from quant_workbench.domain.errors import ManifestError
from quant_workbench.domain.failures import FailureSignature


class _SignatureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=1)
    pattern: str
    advice: str = ""
    transient: bool = False
    fix: str | None = None

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        return value


class _SignatureFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signature: list[_SignatureModel] = Field(default_factory=list)


def load_signatures(directory: Path) -> tuple[FailureSignature, ...]:
    """Read every ``*.toml`` in ``directory`` (sorted by name) into signatures.

    Ids must be unique across files, so a signature can be referenced (by a fix, or from
    a stored run) without ambiguity.
    """
    signatures: list[FailureSignature] = []
    seen: dict[str, Path] = {}
    for path in sorted(directory.glob("*.toml")):
        try:
            document = _SignatureFile.model_validate(
                tomllib.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ManifestError(f"Cannot read failure signatures from {path}: {exc}") from exc
        except ValidationError as exc:
            raise ManifestError(f"{path} is invalid:\n{format_validation_errors(exc)}") from exc
        for entry in document.signature:
            if entry.id in seen:
                raise ManifestError(
                    f"Failure signature {entry.id!r} is defined in both {seen[entry.id]} and {path}"
                )
            seen[entry.id] = path
            signatures.append(
                FailureSignature(
                    id=entry.id,
                    pattern=entry.pattern,
                    title=entry.title,
                    advice=entry.advice,
                    transient=entry.transient,
                    fix=entry.fix,
                )
            )
    return tuple(signatures)
