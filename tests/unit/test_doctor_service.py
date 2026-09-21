"""The doctor's orchestration: selection, isolation of failures, ordering and the report."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from quant_workbench.application.catalog import Catalog
from quant_workbench.application.diagnostics import (
    CheckContext,
    Checker,
    DoctorOptions,
    DoctorService,
    require_project,
)
from quant_workbench.application.settings import Settings
from quant_workbench.domain.diagnostics import DoctorReport, Finding, Location, Severity
from quant_workbench.domain.errors import GitError
from quant_workbench.domain.graph import DependencyGraph
from quant_workbench.domain.ids import Slug
from quant_workbench.domain.project import Project
from tests.fakes import FakeGit, MultiFiles, make_project

ROOT = Path("workspace")


class Fixed(Checker):
    """Reports one finding per project (or one for the workspace)."""

    def __init__(self, checker_id: str, severity: Severity, **flags: object) -> None:
        self.id = checker_id
        self.title = checker_id.title()
        self.severity = severity
        self.scope = flags.get("scope", "project")  # type: ignore[assignment]
        self.opt_in = bool(flags.get("opt_in", False))
        self.calls: list[str | None] = []

    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]:
        self.calls.append(project.slug if project else None)
        return [
            Finding(
                self.id,
                f"{self.id}-code",
                self.severity,
                f"{self.id} on {project.slug if project else 'workspace'}",
                project=project.slug if project else None,
            )
        ]


class Exploding(Checker):
    id = "boom"
    title = "Boom"

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def check(self, project: Project | None, context: CheckContext) -> Sequence[Finding]:
        raise self.error


def context_for(*slugs: str, settings: Settings | None = None) -> CheckContext:
    projects = tuple(make_project(ROOT, s) for s in slugs)
    catalog = Catalog(ROOT, projects, DependencyGraph([p.slug for p in projects]))
    return CheckContext(
        catalog=catalog,
        settings=settings or Settings(),
        files=MultiFiles(),  # type: ignore[arg-type]
        git=FakeGit(),  # type: ignore[arg-type]
        environments=None,  # type: ignore[arg-type]
        runs=None,
        environ={},
    )


async def test_every_checker_runs_on_every_project_and_workspace_checkers_run_once() -> None:
    per_project = Fixed("a", Severity.INFO)
    workspace = Fixed("w", Severity.INFO, scope="workspace")

    report = await DoctorService([per_project, workspace]).run(context_for("one", "two"))

    assert sorted(c for c in per_project.calls if c) == ["one", "two"]
    assert workspace.calls == [None]
    assert len(report.findings) == 3


async def test_a_subset_of_projects_can_be_selected() -> None:
    checker = Fixed("a", Severity.INFO)
    context = context_for("one", "two", "three")

    await DoctorService([checker]).run(context, [context.catalog.get("two")])

    assert checker.calls == ["two"]


async def test_only_skip_and_settings_choose_the_checkers() -> None:
    a, b, c = Fixed("a", Severity.INFO), Fixed("b", Severity.INFO), Fixed("c", Severity.INFO)
    service = DoctorService([a, b, c])

    only = await service.run(context_for("p"), options=DoctorOptions(only=frozenset({"a"})))
    skip = await service.run(context_for("p"), options=DoctorOptions(skip=frozenset({"a"})))
    disabled = await service.run(context_for("p", settings=Settings(disabled_checkers=("c",))))

    assert {f.checker for f in only.findings} == {"a"}
    assert {f.checker for f in skip.findings} == {"b", "c"}
    assert {f.checker for f in disabled.findings} == {"a", "b"}
    assert ("a", "disabled") in skip.skipped
    assert ("c", "disabled") in disabled.skipped
    assert ("b", "not selected") in only.skipped


async def test_opt_in_checkers_need_to_be_asked_for() -> None:
    slow = Fixed("slow", Severity.INFO, opt_in=True)
    service = DoctorService([slow])

    default = await service.run(context_for("p"))
    included = await service.run(
        context_for("p"), options=DoctorOptions(include=frozenset({"slow"}))
    )
    named = await service.run(context_for("p"), options=DoctorOptions(only=frozenset({"slow"})))

    assert default.findings == ()
    assert any(reason.startswith("opt-in") for _, reason in default.skipped)
    assert len(included.findings) == 1
    assert len(named.findings) == 1


async def test_a_crashing_checker_becomes_a_finding_and_the_others_still_run() -> None:
    healthy = Fixed("ok", Severity.INFO)
    service = DoctorService([Exploding(RuntimeError("kaput")), healthy])

    report = await service.run(context_for("p"))

    crashed = next(f for f in report.findings if f.code == "checker-crashed")
    assert "RuntimeError: kaput" in crashed.message
    assert crashed.severity is Severity.WARNING
    assert crashed.project == "p"
    assert any(f.checker == "ok" for f in report.findings)


async def test_an_expected_error_is_reported_as_unavailable_with_its_hint() -> None:
    error = GitError("git is not installed", hint="Install git.")

    report = await DoctorService([Exploding(error)]).run(context_for("p"))

    (finding,) = report.findings
    assert finding.code == "checker-unavailable"
    assert "git is not installed" in finding.message
    assert finding.detail == "Install git."


def test_duplicate_checker_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate checker ids: \\['a'\\]"):
        DoctorService([Fixed("a", Severity.INFO), Fixed("a", Severity.INFO)])


def test_unknown_ids_are_reported() -> None:
    service = DoctorService([Fixed("a", Severity.INFO)])

    assert service.unknown_ids(["a", "zzz", "b"]) == ["b", "zzz"]


async def test_findings_are_ordered_most_severe_first() -> None:
    service = DoctorService(
        [
            Fixed("info", Severity.INFO),
            Fixed("err", Severity.ERROR),
            Fixed("warn", Severity.WARNING),
        ]
    )

    report = await service.run(context_for("p"))

    assert [f.severity for f in report.findings] == [
        Severity.ERROR,
        Severity.WARNING,
        Severity.INFO,
    ]


def test_require_project_rejects_none() -> None:
    project = make_project(ROOT, "p")

    assert require_project(project) is project
    with pytest.raises(ValueError, match="per-project"):
        require_project(None)


# ---------------------------------------------------------------- the report itself
def finding(severity: Severity, code: str = "c", project: str | None = "p") -> Finding:
    return Finding(
        "x",
        code,
        severity,
        "m",
        project=Slug(project) if project else None,
        location=Location("f.py", 3),
    )


def test_a_report_summarises_its_findings() -> None:
    report = DoctorReport(
        (finding(Severity.ERROR), finding(Severity.WARNING), finding(Severity.WARNING, "d", "q"))
    )

    assert report.worst is Severity.ERROR
    assert not report.ok
    assert report.counts() == {Severity.INFO: 0, Severity.WARNING: 2, Severity.ERROR: 1}
    assert len(report.for_project(Slug("p"))) == 2
    assert len(report.at_least(Severity.WARNING)) == 3
    assert len(report.at_least(Severity.ERROR)) == 1


def test_an_empty_report_is_ok() -> None:
    report = DoctorReport(())

    assert report.ok
    assert report.worst is None
    assert DoctorReport((finding(Severity.WARNING),)).ok  # warnings do not fail the doctor


def test_finding_keys_identify_the_same_problem_across_runs() -> None:
    first = finding(Severity.ERROR)
    second = Finding(
        "x",
        "c",
        Severity.WARNING,
        "different words",
        project=Slug("p"),
        location=Location("f.py", 3),
    )

    assert first.key == second.key == "p|c|f.py:3"
    assert finding(Severity.INFO, project=None).key.startswith("-|")
    assert str(Location("f.py")) == "f.py"


def test_severity_ordering() -> None:
    assert Severity.INFO.rank < Severity.WARNING.rank < Severity.ERROR.rank
