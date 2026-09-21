"""The project catalog: turns a workspace folder into resolved projects and a dependency graph."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from quant_workbench.domain.errors import DiscoveryError, ManifestError
from quant_workbench.domain.graph import Dependency, DependencyGraph, EdgeOrigin
from quant_workbench.domain.ids import Slug
from quant_workbench.domain.ports import WorkspaceGateway
from quant_workbench.domain.project import ManifestSource, Project, ProjectSpec


@dataclass(frozen=True, slots=True)
class Catalog:
    """A snapshot of the workspace: what is on disk, how it fits together, what is missing."""

    workspace: Path
    projects: tuple[Project, ...]
    graph: DependencyGraph
    #: Registry entries whose folder is absent from the workspace (candidates for ``qw sync``).
    missing: tuple[ProjectSpec, ...] = ()

    def get(self, slug: str) -> Project:
        """Look a project up by slug, suggesting near matches when it does not exist."""
        for project in self.projects:
            if project.slug == slug:
                return project
        close = difflib.get_close_matches(slug, [p.slug for p in self.projects], n=3, cutoff=0.5)
        raise ManifestError(
            f"No project with slug {slug!r} in {self.workspace}",
            hint=f"Did you mean: {', '.join(close)}?" if close else "Run `qw list` to see them.",
        )

    def by_folder(self, folder: str) -> Project | None:
        return next((p for p in self.projects if p.root.name == folder), None)

    @property
    def slugs(self) -> tuple[Slug, ...]:
        return tuple(p.slug for p in self.projects)


class ProjectCatalog:
    """Use case: discover projects and resolve their descriptions.

    Precedence for a folder's description: a manifest inside the project, then the
    registry shipped with the workbench, then an inference from the layout. Dependencies
    are the union of what manifests *declare* and what static analysis *detects*; edges
    keep their origin so the doctor can flag any disagreement between the two.
    """

    def __init__(self, gateway: WorkspaceGateway) -> None:
        self._gateway = gateway

    def detect_workspace(self, start: Path) -> Path | None:
        """Walk up from ``start`` and return the first folder that looks like a workspace."""
        for candidate in (start, *start.parents):
            if self._gateway.looks_like_workspace(candidate):
                return candidate
        return None

    def load(self, workspace: Path) -> Catalog:
        directories = self._gateway.candidate_directories(workspace)
        registry = {spec.folder: spec for spec in self._gateway.read_registry()}

        resolved: list[Project] = []
        for directory in directories:
            in_repo = self._gateway.read_manifest(directory)
            if in_repo is not None:
                resolved.append(Project(in_repo, directory, ManifestSource.IN_REPO))
            elif directory.name in registry:
                resolved.append(
                    Project(registry[directory.name], directory, ManifestSource.REGISTRY)
                )
            else:
                inferred = self._gateway.infer_spec(directory)
                if inferred is not None:
                    resolved.append(Project(inferred, directory, ManifestSource.INFERRED))

        self._ensure_unique_slugs(resolved)
        projects = tuple(sorted(resolved, key=lambda p: (p.spec.order, p.slug)))
        graph = self._build_graph(projects)

        present = {p.root.name for p in projects}
        missing = tuple(
            sorted(
                (spec for folder, spec in registry.items() if folder not in present),
                key=lambda s: (s.order, s.slug),
            )
        )
        return Catalog(workspace=workspace, projects=projects, graph=graph, missing=missing)

    @staticmethod
    def _ensure_unique_slugs(projects: list[Project]) -> None:
        seen: dict[Slug, Path] = {}
        for project in projects:
            if project.slug in seen:
                raise DiscoveryError(
                    f"Slug {project.slug!r} is used by both {seen[project.slug].name!r} "
                    f"and {project.root.name!r}",
                    hint="Slugs must be unique; change one in its manifest.",
                )
            seen[project.slug] = project.root

    def _build_graph(self, projects: tuple[Project, ...]) -> DependencyGraph:
        by_slug = {p.slug: p for p in projects}
        by_folder = {p.root.name: p for p in projects}
        edges: list[Dependency] = []

        for project in projects:
            for dependency in project.spec.depends_on:
                if dependency not in by_slug:
                    raise ManifestError(
                        f"{project.slug!r} declares a dependency on unknown project {dependency!r}",
                        hint="Check the spelling, or add the project to the workspace.",
                    )
                edges.append(Dependency(project.slug, dependency, frozenset({EdgeOrigin.DECLARED})))

            referenced = self._gateway.sibling_references(project.root, frozenset(by_folder))
            for folder in sorted(referenced):
                edges.append(
                    Dependency(
                        project.slug, by_folder[folder].slug, frozenset({EdgeOrigin.DETECTED})
                    )
                )

        return DependencyGraph((p.slug for p in projects), edges)
