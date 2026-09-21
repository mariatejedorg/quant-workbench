from __future__ import annotations

from pathlib import Path

import pytest

from quant_workbench.application.catalog import ProjectCatalog
from quant_workbench.domain.errors import DiscoveryError, ManifestError
from quant_workbench.domain.graph import EdgeOrigin
from quant_workbench.domain.project import ManifestSource
from quant_workbench.infrastructure.manifest import read_manifest
from quant_workbench.infrastructure.resources import data_path
from quant_workbench.infrastructure.workspace import FileSystemWorkspace
from tests.support import manifest_toml, write_project

pytestmark = pytest.mark.integration


def make_catalog(registry_dir: Path | None = None) -> ProjectCatalog:
    return ProjectCatalog(FileSystemWorkspace(registry_dir=registry_dir))


def test_precedence_is_in_repo_then_registry_then_inference(accented_root: Path) -> None:
    registry = accented_root / "registry"
    registry.mkdir()
    (registry / "reg.toml").write_text(
        manifest_toml("from-registry", folder="beta", title="Registry Title"), encoding="utf-8"
    )
    write_project(accented_root, "alpha", manifest=manifest_toml("from-repo", title="Repo Title"))
    write_project(accented_root, "beta")
    write_project(accented_root, "gamma", readme="# 📈 Gamma Project\n\ntext\n")

    catalog = make_catalog(registry).load(accented_root)

    by_folder = {p.root.name: p for p in catalog.projects}
    assert by_folder["alpha"].source is ManifestSource.IN_REPO
    assert by_folder["alpha"].slug == "from-repo"
    assert by_folder["beta"].source is ManifestSource.REGISTRY
    assert by_folder["beta"].title == "Registry Title"
    assert by_folder["gamma"].source is ManifestSource.INFERRED
    assert by_folder["gamma"].slug == "gamma"
    assert by_folder["gamma"].title == "Gamma Project"


def test_folders_that_are_not_projects_are_ignored(accented_root: Path) -> None:
    write_project(accented_root, "real")
    (accented_root / "docs").mkdir()
    (accented_root / "venv").mkdir()
    (accented_root / ".hidden").mkdir()
    write_project(accented_root, "no-entrypoint", src_main=False)

    catalog = make_catalog().load(accented_root)

    assert [p.root.name for p in catalog.projects] == ["real"]


def test_references_in_code_become_detected_edges_but_prose_does_not(accented_root: Path) -> None:
    write_project(accented_root, "engine")
    write_project(accented_root, "consumer", refs=["engine"])
    write_project(accented_root, "chatty", docstring_mention="engine")
    write_project(accented_root, "self-aware", refs=["self-aware"])

    catalog = make_catalog().load(accented_root)

    edges = {(e.dependent, e.dependency): e.origins for e in catalog.graph.edges}
    assert edges == {("consumer", "engine"): {EdgeOrigin.DETECTED}}


def test_declared_and_detected_edges_merge(accented_root: Path) -> None:
    write_project(accented_root, "engine", manifest=manifest_toml("engine"))
    write_project(
        accented_root,
        "consumer",
        refs=["engine"],
        manifest=manifest_toml("consumer", deps=["engine"]),
    )

    (edge,) = make_catalog().load(accented_root).graph.edges

    assert edge.origins == {EdgeOrigin.DECLARED, EdgeOrigin.DETECTED}


def test_a_declared_dependency_on_a_missing_project_is_rejected(accented_root: Path) -> None:
    write_project(accented_root, "lonely", manifest=manifest_toml("lonely", deps=["ghost"]))

    with pytest.raises(ManifestError, match="unknown project 'ghost'"):
        make_catalog().load(accented_root)


def test_duplicate_slugs_are_rejected(accented_root: Path) -> None:
    write_project(accented_root, "one", manifest=manifest_toml("same"))
    write_project(accented_root, "two", manifest=manifest_toml("same"))

    with pytest.raises(DiscoveryError, match="used by both"):
        make_catalog().load(accented_root)


def test_registry_entries_without_a_folder_are_reported_as_missing(accented_root: Path) -> None:
    registry = accented_root / "registry"
    registry.mkdir()
    (registry / "a.toml").write_text(
        manifest_toml("absent", folder="not-cloned-yet"), encoding="utf-8"
    )
    write_project(accented_root, "present")

    catalog = make_catalog(registry).load(accented_root)

    assert [s.slug for s in catalog.missing] == ["absent"]


def test_lookup_suggests_close_matches(accented_root: Path) -> None:
    write_project(accented_root, "monte-carlo-simulator")
    catalog = make_catalog().load(accented_root)

    with pytest.raises(ManifestError) as excinfo:
        catalog.get("monte-carlo")

    assert "monte-carlo-simulator" in (excinfo.value.hint or "")
    assert catalog.get("monte-carlo-simulator").root.name == "monte-carlo-simulator"
    assert catalog.by_folder("monte-carlo-simulator") is not None
    assert catalog.by_folder("nope") is None


def test_a_file_that_does_not_parse_does_not_break_discovery(accented_root: Path) -> None:
    write_project(accented_root, "engine")
    write_project(
        accented_root,
        "broken",
        refs=["engine"],
        extra_files={"src/bad.py": "def oops(:\n", "src/latin1.py": "x = '\xe9'\n"},
    )

    catalog = make_catalog().load(accented_root)

    assert {(e.dependent, e.dependency) for e in catalog.graph.edges} == {("broken", "engine")}


def test_workspace_is_found_by_walking_up_from_a_nested_directory(accented_root: Path) -> None:
    write_project(accented_root, "a")
    write_project(accented_root, "b")
    nested = accented_root / "a" / "src"

    catalog = make_catalog()

    assert catalog.detect_workspace(nested) == accented_root
    assert catalog.detect_workspace(accented_root.parent) is None


def test_a_workspace_that_is_not_a_directory_is_a_clear_error(accented_root: Path) -> None:
    with pytest.raises(DiscoveryError, match="not a directory"):
        make_catalog().load(accented_root / "does-not-exist")


def test_registry_ships_with_the_expected_folder_names() -> None:
    folders = {read_manifest(p).folder for p in data_path("registry").glob("*.toml")}

    assert "proyecto-6-opciones-volatilidad-implicita" in folders
