"""``qw new``: create a project that follows the portfolio's conventions and already runs."""

from __future__ import annotations

import re
from dataclasses import dataclass

from quant_workbench.application.catalog import Catalog
from quant_workbench.domain.errors import WorkbenchError
from quant_workbench.domain.ids import parse_slug
from quant_workbench.domain.ports import ProjectFiles, ProjectTemplates
from quant_workbench.domain.project import ManifestSource, Project, ProjectSpec

_FOLDER_NUMBER = re.compile(r"^proyecto-(\d+)-")
CA_BUNDLE = ".certs/cacert.pem"
_ORDER_STEP = 10  # registry orders are 10, 20, ... so a new project fits between two of them


class ScaffoldError(WorkbenchError):
    """The project cannot be created (name taken, folder exists...)."""


@dataclass(frozen=True, slots=True)
class ScaffoldResult:
    project: Project
    files: tuple[str, ...]
    #: Where the CA bundle was copied from, if a sibling had one.
    ca_bundle_from: str | None


class ScaffoldService:
    def __init__(self, *, files: ProjectFiles, templates: ProjectTemplates) -> None:
        self._files = files
        self._templates = templates

    def next_folder(self, catalog: Catalog, slug: str) -> str:
        """``proyecto-<n>-<slug>``, with ``n`` one past the highest number in the workspace."""
        numbers = [
            int(m.group(1))
            for p in catalog.projects
            if (m := _FOLDER_NUMBER.match(p.root.name)) is not None
        ]
        return f"proyecto-{max(numbers, default=len(catalog.projects)) + 1}-{slug}"

    def create(
        self,
        catalog: Catalog,
        slug: str,
        *,
        title: str | None = None,
        category: str = "New projects",
        description: str | None = None,
        folder: str | None = None,
    ) -> ScaffoldResult:
        """Write a new project into the workspace of ``catalog`` and return it."""
        valid = parse_slug(slug)
        if any(p.slug == valid for p in catalog.projects):
            raise ScaffoldError(f"A project called {valid!r} already exists in this workspace")
        chosen = folder or self.next_folder(catalog, valid)
        root = catalog.workspace / chosen
        if root.exists():
            raise ScaffoldError(f"{root} already exists", hint="Choose another slug or --folder.")

        nice_title = title or valid.replace("-", " ").title()
        order = max((p.spec.order for p in catalog.projects), default=0) + _ORDER_STEP
        context: dict[str, str | int] = {
            "slug": valid,
            "title": nice_title,
            "module": valid.replace("-", "_"),
            "folder": chosen,
            "category": category,
            "description": description or f"{nice_title}: a Monte Carlo simulation of a price.",
            "order": order,
        }
        spec = ProjectSpec(
            slug=valid, title=nice_title, folder=chosen, category=category, order=order
        )
        project = Project(spec, root, ManifestSource.IN_REPO)
        rendered = self._templates.render(context)
        for relative, text in rendered.items():
            self._files.write_text(project, relative, text)
        donor = self._copy_ca_bundle(catalog, project)
        return ScaffoldResult(project, tuple(rendered), donor)

    def _copy_ca_bundle(self, catalog: Catalog, project: Project) -> str | None:
        """New projects start with the same TLS bundle as their siblings (see the doctor)."""
        for other in catalog.projects:
            data = self._files.read_bytes(other, CA_BUNDLE)
            if data is not None:
                self._files.write_bytes(project, CA_BUNDLE, data)
                return other.slug
        return None
