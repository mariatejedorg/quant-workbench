from __future__ import annotations

from pathlib import Path

import pytest

from quant_workbench.application.manifest_schema import export_json_schema
from quant_workbench.domain.errors import ManifestError
from quant_workbench.domain.graph import Dependency, DependencyGraph, EdgeOrigin
from quant_workbench.domain.project import MetricKind
from quant_workbench.infrastructure.manifest import read_manifest
from quant_workbench.infrastructure.resources import data_path
from tests.support import manifest_toml

REGISTRY_FILES = sorted(data_path("registry").glob("*.toml"))


def write(tmp: Path, text: str) -> Path:
    path = tmp / "quant-project.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_minimal_manifest_gets_sensible_defaults(accented_root: Path) -> None:
    spec = read_manifest(write(accented_root, manifest_toml("my-project")), folder="my-folder")

    assert spec.slug == "my-project"
    assert spec.folder == "my-folder"
    assert spec.entrypoint == "src/main.py"
    assert spec.outputs.dashboard == "outputs/dashboard.html"
    assert spec.venv == "venv"
    assert spec.depends_on == ()


def test_full_manifest_round_trips_into_the_domain(accented_root: Path) -> None:
    text = """
schema_version = 1
slug = "full-project"
title = "Full Project"
folder = "proyecto-x"
category = "Advanced"
order = 70
depends_on = ["other-project"]
tags = ["a", "b"]

[outputs]
dashboard = "out/index.html"
images = ["out/a.png"]

[[config_targets]]
file = "config/settings.py"
symbols = ["TICKER"]

[[metrics]]
name = "sharpe"
pattern = 'Sharpe:\\s*([\\d.]+)'
kind = "float"
"""
    spec = read_manifest(write(accented_root, text))

    assert spec.order == 70
    assert spec.outputs.dashboard == "out/index.html"
    assert spec.config_targets[0].symbols == ("TICKER",)
    assert spec.extractors[0].kind is MetricKind.FLOAT
    assert spec.depends_on == ("other-project",)


def test_registry_manifests_must_name_their_folder(accented_root: Path) -> None:
    with pytest.raises(ManifestError, match="which folder"):
        read_manifest(write(accented_root, manifest_toml("no-folder")))


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('slug = "x"\nthis is not toml', "not valid TOML"),
        (manifest_toml("x") + "surprise = 1\n", "surprise"),
        (manifest_toml("Bad_Slug"), "not a valid project slug"),
        (manifest_toml("x") + 'entrypoint = "../escape.py"\n', "entrypoint"),
        (manifest_toml("x") + 'entrypoint = "/abs/main.py"\n', "entrypoint"),
        (manifest_toml("x") + 'venv = "a\\\\b"\n', "venv"),
        (manifest_toml("x") + "schema_version = 2\n", "schema_version"),
        (
            manifest_toml("x") + '[[metrics]]\nname = "m"\npattern = "([unclosed"\n',
            "invalid regular expression",
        ),
        (
            manifest_toml("x") + '[[metrics]]\nname = "m"\npattern = "no group here"\n',
            "capture group",
        ),
        ('slug = "x"\n', "title"),
    ],
)
def test_invalid_manifests_are_reported_precisely(
    accented_root: Path, body: str, expected: str
) -> None:
    path = write(accented_root, body)

    with pytest.raises(ManifestError) as excinfo:
        read_manifest(path, folder="f")

    assert expected in str(excinfo.value)
    assert "quant-project.toml" in str(excinfo.value) or "slug" in str(excinfo.value)


def test_a_missing_file_is_a_manifest_error(accented_root: Path) -> None:
    with pytest.raises(ManifestError, match="Cannot read"):
        read_manifest(accented_root / "nope.toml")


def test_json_schema_describes_the_document() -> None:
    schema = export_json_schema()

    assert schema["title"] == "quant-project.toml"
    assert {"slug", "title", "category", "depends_on", "metrics"} <= set(schema["properties"])
    assert schema["additionalProperties"] is False


def test_the_shipped_registry_has_the_ten_portfolio_projects() -> None:
    assert len(REGISTRY_FILES) == 10


@pytest.mark.parametrize("path", REGISTRY_FILES, ids=lambda p: p.stem)
def test_every_shipped_manifest_is_valid(path: Path) -> None:
    spec = read_manifest(path)

    assert spec.folder.startswith("proyecto-")
    assert spec.repo_url == f"https://github.com/mariatejedorg/{spec.slug}"
    assert path.stem.endswith(spec.slug)
    assert spec.config_targets, "every project must expose at least one config target"


def test_the_shipped_registry_is_internally_consistent() -> None:
    specs = [read_manifest(path) for path in REGISTRY_FILES]
    slugs = {s.slug for s in specs}

    assert len(slugs) == len(specs) == len({s.folder for s in specs})
    for spec in specs:
        assert set(spec.depends_on) <= slugs
    graph = DependencyGraph(
        slugs,
        [
            Dependency(s.slug, dep, frozenset({EdgeOrigin.DECLARED}))
            for s in specs
            for dep in s.depends_on
        ],
    )
    assert graph.find_cycle() is None
