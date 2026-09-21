"""The doctor: a set of checkers that look for the problems this portfolio has actually had.

A :class:`Checker` inspects one project (or the workspace as a whole) through the ports and
returns :class:`~quant_workbench.domain.diagnostics.Finding` objects. :class:`DoctorService`
runs them all, isolates their failures (a buggy checker becomes a finding, never a crash) and
assembles the :class:`~quant_workbench.domain.diagnostics.DoctorReport`.

Checkers are deliberately small and each one is the automation of a bug found by hand:
the module-name collision between sibling projects, the missing CA bundle behind the SSL
errors, the wrong git e-mail on a commit.
"""

from __future__ import annotations

import ast
import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from quant_workbench.application.catalog import Catalog
from quant_workbench.application.environments import EnvironmentService
from quant_workbench.application.runs import RunService
from quant_workbench.application.settings import Settings
from quant_workbench.domain.code_analysis import parse
from quant_workbench.domain.diagnostics import (
    DoctorReport,
    ExplainabilityScore,
    Finding,
    Severity,
)
from quant_workbench.domain.errors import WorkbenchError
from quant_workbench.domain.ids import Slug
from quant_workbench.domain.ports import GitGateway, ProjectFiles
from quant_workbench.domain.project import Project

_log = logging.getLogger(__name__)

#: How many (project, checker) pairs are examined at the same time.
_CONCURRENCY = 4


@dataclass(frozen=True, slots=True)
class SourceFile:
    """A Python file of a project, read and parsed once for all the checkers."""

    path: str
    text: str
    #: ``None`` when the file has a syntax error.
    tree: ast.Module | None


@dataclass
class CheckContext:
    """Everything a checker may look at, plus the outputs it may contribute to."""

    catalog: Catalog
    settings: Settings
    files: ProjectFiles
    git: GitGateway
    environments: EnvironmentService
    runs: RunService | None
    #: The process environment (injected so tests can pretend variables are set).
    environ: Mapping[str, str]
    #: Filled by the explainability checker during the run.
    scores: dict[Slug, ExplainabilityScore] = field(default_factory=dict)
    _sources: dict[Slug, tuple[SourceFile, ...]] = field(default_factory=dict)

    def sources(self, project: Project) -> tuple[SourceFile, ...]:
        """The project's Python files (parsed lazily, then cached for the rest of the run)."""
        if project.slug not in self._sources:
            loaded: list[SourceFile] = []
            for path in self.files.python_sources(project):
                text = self.files.read_text(project, path)
                if text is not None:
                    loaded.append(SourceFile(path, text, parse(text)))
            self._sources[project.slug] = tuple(loaded)
        return self._sources[project.slug]


Scope = Literal["project", "workspace"]


class Checker(ABC):
    """One diagnostic. ``check`` receives ``None`` as the project for workspace-wide checks.

    Subclasses set ``id`` and ``title`` and implement :meth:`check`; ``scope`` and ``opt_in``
    only need overriding when they differ from the defaults.
    """

    id: ClassVar[str]
    title: ClassVar[str]
    scope: ClassVar[Scope] = "project"
    #: Opt-in checkers are slow or have side effects and only run when asked for.
    opt_in: ClassVar[bool] = False

    @abstractmethod
    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]: ...


def require_project(project: Project | None) -> Project:
    """The project of a per-project checker (``None`` is only passed to workspace checkers)."""
    if project is None:
        raise ValueError("this checker is per-project and was called without one")
    return project


@dataclass(frozen=True, slots=True)
class DoctorOptions:
    """What to run: ``only`` (if given) minus ``skip``, plus the opt-in checkers asked for."""

    only: frozenset[str] = frozenset()
    skip: frozenset[str] = frozenset()
    #: Opt-in checkers to run in addition to the default ones.
    include: frozenset[str] = frozenset()


class DoctorService:
    def __init__(self, checkers: Sequence[Checker]) -> None:
        ids = [c.id for c in checkers]
        if len(set(ids)) != len(ids):
            raise ValueError(
                f"duplicate checker ids: {sorted({i for i in ids if ids.count(i) > 1})}"
            )
        self._checkers = tuple(checkers)

    @property
    def checkers(self) -> tuple[Checker, ...]:
        return self._checkers

    def unknown_ids(self, ids: Iterable[str]) -> list[str]:
        known = {c.id for c in self._checkers}
        return sorted(i for i in ids if i not in known)

    async def run(
        self,
        context: CheckContext,
        projects: Sequence[Project] | None = None,
        options: DoctorOptions | None = None,
    ) -> DoctorReport:
        """Run the selected checkers over ``projects`` (default: the whole catalog)."""
        chosen = options or DoctorOptions()
        targets = tuple(projects) if projects is not None else context.catalog.projects
        selected, skipped = self._select(chosen, context.settings)

        gate = asyncio.Semaphore(_CONCURRENCY)

        async def guarded(checker: Checker, project: Project | None) -> list[Finding]:
            async with gate:
                return await self._run_one(checker, project, context)

        jobs: list[asyncio.Task[list[Finding]]] = []
        async with asyncio.TaskGroup() as group:
            for checker in selected:
                if checker.scope == "workspace":
                    jobs.append(group.create_task(guarded(checker, None)))
                else:
                    jobs.extend(group.create_task(guarded(checker, p)) for p in targets)
        findings = [finding for job in jobs for finding in job.result()]
        return DoctorReport(
            findings=DoctorReport.ordered(findings),
            scores=dict(context.scores),
            skipped=tuple(skipped),
        )

    def _select(
        self, options: DoctorOptions, settings: Settings
    ) -> tuple[list[Checker], list[tuple[str, str]]]:
        selected: list[Checker] = []
        skipped: list[tuple[str, str]] = []
        disabled = set(settings.disabled_checkers)
        for checker in self._checkers:
            if checker.id in options.skip or checker.id in disabled:
                skipped.append((checker.id, "disabled"))
            elif options.only and checker.id not in options.only:
                skipped.append((checker.id, "not selected"))
            elif checker.opt_in and checker.id not in options.only | options.include:
                skipped.append((checker.id, "opt-in: request it explicitly"))
            else:
                selected.append(checker)
        return selected, skipped

    @staticmethod
    async def _run_one(
        checker: Checker, project: Project | None, context: CheckContext
    ) -> list[Finding]:
        """Run one checker; whatever goes wrong becomes a finding instead of an exception."""
        slug = project.slug if project else None
        try:
            return list(await checker.check(project, context))
        except WorkbenchError as error:
            return [
                Finding(
                    checker.id,
                    "checker-unavailable",
                    Severity.WARNING,
                    f"{checker.title} could not run: {error.message}",
                    project=slug,
                    detail=error.hint or "",
                )
            ]
        except Exception as error:  # a bug in a checker must not take the doctor down
            _log.exception("checker %s crashed on %s", checker.id, slug)
            return [
                Finding(
                    checker.id,
                    "checker-crashed",
                    Severity.WARNING,
                    f"{checker.title} crashed: {type(error).__name__}: {error}",
                    project=slug,
                    detail="This is a bug in the workbench; the details are in the log.",
                )
            ]
