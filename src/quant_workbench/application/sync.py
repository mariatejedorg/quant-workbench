"""``qw sync``: rebuild the portfolio's workspace by cloning the registry's repositories."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from quant_workbench.application.catalog import Catalog
from quant_workbench.domain.errors import ManifestError, WorkbenchError
from quant_workbench.domain.ports import GitGateway
from quant_workbench.domain.project import ProjectSpec

#: Only HTTPS is cloned by default: public repositories need no credentials, and it rules out
#: exotic git transports (``ext::``, local paths) that a registry entry could otherwise name.
DEFAULT_TRUSTED_PREFIXES = ("https://",)


class SyncStatus(StrEnum):
    CLONED = "cloned"
    WOULD_CLONE = "would clone"
    PRESENT = "already present"
    REFUSED = "refused"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    spec: ProjectSpec
    status: SyncStatus
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status not in {SyncStatus.REFUSED, SyncStatus.FAILED}


class SyncService:
    """Use case: clone what the registry lists and the workspace lacks.

    It never overwrites or updates an existing folder: a project you already have is left
    exactly as it is, so syncing is safe to run at any time.
    """

    def __init__(
        self, git: GitGateway, trusted_prefixes: Sequence[str] = DEFAULT_TRUSTED_PREFIXES
    ) -> None:
        self._git = git
        self._trusted = tuple(trusted_prefixes)

    def sync(
        self,
        catalog: Catalog,
        slugs: Sequence[str] = (),
        *,
        dry_run: bool = False,
        on_outcome: Callable[[SyncOutcome], None] = lambda _: None,
    ) -> tuple[SyncOutcome, ...]:
        """Clone the missing projects (all, or only ``slugs``); one failure never stops the rest."""
        outcomes: list[SyncOutcome] = []
        for spec, present in self._selection(catalog, slugs):
            outcome = (
                SyncOutcome(spec, SyncStatus.PRESENT)
                if present
                else self._clone_one(catalog, spec, dry_run=dry_run)
            )
            outcomes.append(outcome)
            on_outcome(outcome)
        return tuple(outcomes)

    def _selection(self, catalog: Catalog, slugs: Sequence[str]) -> list[tuple[ProjectSpec, bool]]:
        """``(spec, already_in_workspace)`` for what was asked (default: everything missing)."""
        if not slugs:
            return [(spec, False) for spec in catalog.missing]
        by_slug: dict[str, ProjectSpec] = {spec.slug: spec for spec in catalog.missing}
        chosen: list[tuple[ProjectSpec, bool]] = []
        for slug in slugs:
            if slug in by_slug:
                chosen.append((by_slug[slug], False))
            elif any(p.slug == slug for p in catalog.projects):
                chosen.append((catalog.get(slug).spec, True))
            else:
                raise ManifestError(
                    f"{slug!r} is not in the registry", hint="Run `qw list` to see what is known."
                )
        return chosen

    def _clone_one(self, catalog: Catalog, spec: ProjectSpec, *, dry_run: bool) -> SyncOutcome:
        url = spec.repo_url
        if not url:
            return SyncOutcome(spec, SyncStatus.REFUSED, "the registry has no repository URL")
        if not url.startswith(self._trusted):
            return SyncOutcome(spec, SyncStatus.REFUSED, f"{url} is not an HTTPS URL")
        if dry_run:
            return SyncOutcome(spec, SyncStatus.WOULD_CLONE, url)
        try:
            self._git.clone(url, catalog.workspace / spec.folder)
        except WorkbenchError as error:
            return SyncOutcome(spec, SyncStatus.FAILED, error.message)
        return SyncOutcome(spec, SyncStatus.CLONED, url)
