"""``qw doctor``: diagnose the projects and (optionally) fix what can be fixed safely."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer
from rich.table import Table

from quant_workbench.application.catalog import Catalog
from quant_workbench.application.diagnostics import CheckContext, DoctorOptions
from quant_workbench.bootstrap import Container
from quant_workbench.cli.catalog_commands import WorkspaceOption
from quant_workbench.cli.common import console, get_container, handled, load_catalog
from quant_workbench.domain.diagnostics import DoctorReport, Finding, Severity
from quant_workbench.domain.errors import WorkbenchError

EXIT_ERRORS_FOUND = 1

_STYLE = {Severity.ERROR: "bold red", Severity.WARNING: "yellow", Severity.INFO: "dim"}
_ICON = {Severity.ERROR: "x", Severity.WARNING: "!", Severity.INFO: "i"}
#: Explainability scores are coloured by these thresholds (green / yellow / red).
_SCORE_GOOD = 85
_SCORE_OK = 70


def _validate_ids(container: Container, *groups: list[str] | None) -> None:
    unknown = container.doctor.unknown_ids(i for group in groups for i in group or [])
    if unknown:
        known = ", ".join(c.id for c in container.doctor.checkers)
        raise WorkbenchError(f"Unknown checker(s): {', '.join(unknown)}", hint=f"Known: {known}")


def _print_checks(container: Container) -> None:
    table = Table(title="Checkers")
    for column in ("id", "title", "scope", "runs by default"):
        table.add_column(column)
    for checker in container.doctor.checkers:
        table.add_row(
            checker.id, checker.title, checker.scope, "no (opt-in)" if checker.opt_in else "yes"
        )
    console.print(table)


def _print_finding(finding: Finding) -> None:
    style = _STYLE[finding.severity]
    where = f" [dim]{finding.location}[/]" if finding.location else ""
    fixable = " [cyan](fix available)[/]" if finding.fix else ""
    console.print(
        f"  [{style}]{_ICON[finding.severity]} {finding.code}[/]{where}{fixable}\n"
        f"    {finding.message}",
        highlight=False,
    )
    if finding.detail:
        console.print(f"    [dim]{finding.detail}[/]", highlight=False)


def _print_report(report: DoctorReport, catalog: Catalog, minimum: Severity) -> None:
    shown = report.at_least(minimum)
    workspace_level = [f for f in shown if f.project is None]
    if workspace_level:
        console.print("[bold]Workspace[/]")
        for finding in workspace_level:
            _print_finding(finding)
    for project in catalog.projects:
        findings = [f for f in shown if f.project == project.slug]
        if findings:
            console.print(f"[bold]{project.slug}[/]")
            for finding in findings:
                _print_finding(finding)
    if report.scores:
        table = Table(title="Explainability (0-100)")
        for column in ("project", "score", "docstrings", "comments/line", "functions"):
            table.add_column(column)
        for slug, score in sorted(report.scores.items()):
            colour = (
                "green"
                if score.score >= _SCORE_GOOD
                else "yellow"
                if score.score >= _SCORE_OK
                else "red"
            )
            table.add_row(
                slug,
                f"[{colour}]{score.score:.0f}[/]",
                f"{score.docstring_coverage:.0%}",
                f"{score.comment_density:.2f}",
                str(score.functions),
            )
        console.print(table)
    counts = report.counts()
    console.print(
        f"[bold]{counts[Severity.ERROR]} error(s), {counts[Severity.WARNING]} warning(s), "
        f"{counts[Severity.INFO]} note(s)[/]"
        + ("" if report.ok else " [red]- fix the errors before running[/]")
    )
    for checker_id, reason in report.skipped:
        if reason.startswith("opt-in"):
            console.print(f"[dim]skipped {checker_id}: {reason}[/]")


def _report_json(report: DoctorReport) -> str:
    return json.dumps(
        {
            "ok": report.ok,
            "counts": {severity.value: n for severity, n in report.counts().items()},
            "findings": [
                {
                    "checker": f.checker,
                    "code": f.code,
                    "severity": f.severity.value,
                    "project": f.project,
                    "message": f.message,
                    "location": str(f.location) if f.location else None,
                    "detail": f.detail,
                    "fix": f.fix,
                }
                for f in report.findings
            ],
            "explainability": {
                slug: round(score.score, 1) for slug, score in sorted(report.scores.items())
            },
        },
        indent=2,
        ensure_ascii=False,
    )


async def _fix_all(
    container: Container, context: CheckContext, report: DoctorReport, *, assume_yes: bool
) -> int:
    """Preview and apply the fixes the report offers; returns how many were applied."""
    seen: set[tuple[str | None, str | None]] = set()
    applied = 0
    for finding in report.findings:
        marker = (finding.project, finding.fix)
        if not container.fixes.can_fix(finding) or marker in seen:
            continue
        seen.add(marker)
        preview = await container.fixes.preview(finding, context)
        console.print(f"\n[bold cyan]{preview.summary}[/]  [dim]({finding.code})[/]")
        for action in preview.actions:
            console.print(f"  - {action}", highlight=False)
        if preview.diff:
            console.print(preview.diff.replace("\r", ""), highlight=False)
        if not assume_yes and not typer.confirm("Apply this fix?", default=False):
            console.print("[dim]skipped[/]")
            continue
        console.print(f"[green]{await container.fixes.apply(finding, context)}[/]")
        applied += 1
    return applied


@handled
def doctor(
    ctx: typer.Context,
    projects: Annotated[list[str] | None, typer.Argument(help="Slugs (default: all).")] = None,
    *,
    workspace: WorkspaceOption = None,
    only: Annotated[
        list[str] | None, typer.Option("--only", help="Run only these checkers.")
    ] = None,
    skip: Annotated[list[str] | None, typer.Option("--skip", help="Do not run these.")] = None,
    min_severity: Annotated[
        Severity, typer.Option("--min-severity", help="Hide findings below this level.")
    ] = Severity.INFO,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    fix: Annotated[
        bool, typer.Option("--fix", help="Preview each available fix and offer to apply it.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="With --fix: apply without asking.")
    ] = False,
    deterministic: Annotated[
        bool, typer.Option("--deterministic", help="Also run each project twice and compare.")
    ] = False,
    list_checks: Annotated[
        bool, typer.Option("--list", help="List the available checkers and exit.")
    ] = False,
) -> None:
    """Look for the problems this portfolio has had: environment, TLS, imports, git, code."""
    container = get_container(ctx)
    if list_checks:
        _print_checks(container)
        return
    _validate_ids(container, only, skip)

    catalog = load_catalog(container, workspace)
    targets = [catalog.get(p) for p in projects] if projects else None
    options = DoctorOptions(
        only=frozenset(only or ()),
        skip=frozenset(skip or ()),
        include=frozenset({"determinism"}) if deterministic else frozenset(),
    )

    async def diagnose() -> tuple[DoctorReport, CheckContext]:
        context = container.check_context(catalog, with_runs=deterministic)
        return await container.doctor.run(context, targets, options), context

    report, context = asyncio.run(diagnose())
    if as_json and not fix:
        typer.echo(_report_json(report))
    else:
        _print_report(report, catalog, min_severity)

    if fix:
        applied = asyncio.run(_fix_all(container, context, report, assume_yes=yes))
        if applied:
            console.print("\n[bold]Re-checking after the fixes[/]")
            report, _ = asyncio.run(diagnose())
            _print_report(report, catalog, min_severity)
        elif not any(container.fixes.can_fix(f) for f in report.findings):
            console.print("[dim]Nothing here has an automatic fix.[/]")
    if not report.ok:
        raise typer.Exit(EXIT_ERRORS_FOUND)


def register(app: typer.Typer) -> None:
    app.command("doctor")(doctor)
