"""The "Overview" page of a project, as HTML (Qt-free, so its content is unit-testable)."""

from __future__ import annotations

from datetime import timedelta
from html import escape

from quant_workbench.application.catalog import Catalog
from quant_workbench.domain.diagnostics import ExplainabilityScore, Finding, Severity
from quant_workbench.domain.project import Project
from quant_workbench.domain.runs import MetricValue, Run
from quant_workbench.ui.theme import Tokens, severity_colour, status_colour

_MEGABYTE = 1024 * 1024
_MINUTE = 60


def format_duration(duration: timedelta | None) -> str:
    """``12.3 s`` or ``2 min 05 s``; ``-`` when unknown."""
    if duration is None:
        return "-"
    seconds = duration.total_seconds()
    if seconds < _MINUTE:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(int(seconds), _MINUTE)
    return f"{minutes} min {rest:02d} s"


def format_metric(metric: MetricValue) -> str:
    value = f"{metric.value:,.4g}" if isinstance(metric.value, float) else str(metric.value)
    return f"{value} {metric.unit}".strip()


def project_summary_html(
    project: Project,
    catalog: Catalog,
    *,
    last_run: Run | None,
    score: ExplainabilityScore | None,
    findings: tuple[Finding, ...],
    tokens: Tokens,
) -> str:
    """A compact report on one project: what it is, how it last ran, what the doctor thinks."""
    spec = project.spec
    needs = sorted(catalog.graph.dependencies_of(project.slug))
    used_by = sorted(catalog.graph.dependents_of(project.slug))

    def row(label: str, value: str) -> str:
        return (
            f"<tr><td style='color:{tokens.ink_muted}; padding:2px 16px 2px 0'>{escape(label)}</td>"
            f"<td>{value}</td></tr>"
        )

    rows = [
        row("Category", escape(spec.category)),
        row("Folder", escape(project.root.name)),
        row("Manifest", escape(project.source.value)),
        row("Needs", escape(", ".join(needs)) or "-"),
        row("Used by", escape(", ".join(used_by)) or "-"),
    ]
    if spec.description:
        description = f"<p style='color:{tokens.ink_secondary}'>{escape(spec.description)}</p>"
    else:
        description = ""

    parts = [
        f"<h2 style='margin-bottom:2px'>{escape(project.title)}</h2>",
        f"<div style='color:{tokens.ink_muted}'>{escape(project.slug)}</div>",
        description,
        f"<table>{''.join(rows)}</table>",
        "<h3>Last run</h3>",
        _run_section(last_run, tokens),
        "<h3>Health</h3>",
        _health_section(score, findings, tokens),
    ]
    return "".join(parts)


def _run_section(run: Run | None, tokens: Tokens) -> str:
    if run is None:
        return f"<p style='color:{tokens.ink_muted}'>This project has not been run yet.</p>"
    colour = status_colour(run.status, tokens)
    lines = [
        f"<p><b style='color:{colour}'>{escape(run.status.value.upper())}</b> "
        f"<span style='color:{tokens.ink_secondary}'>in {format_duration(run.duration)}"
        f"{f', attempt {run.attempt}' if run.attempt > 1 else ''}</span></p>"
    ]
    if run.failure:
        lines.append(f"<p style='color:{tokens.error}'>{escape(run.failure)}</p>")
    if run.metrics:
        items = "".join(
            f"<tr><td style='color:{tokens.ink_muted}; padding-right:16px'>{escape(m.name)}</td>"
            f"<td><b>{escape(format_metric(m))}</b></td></tr>"
            for m in run.metrics
        )
        lines.append(f"<table>{items}</table>")
    if run.peak_rss_bytes:
        lines.append(
            f"<p style='color:{tokens.ink_muted}'>peak memory "
            f"{run.peak_rss_bytes / _MEGABYTE:.0f} MB, CPU {run.avg_cpu_percent:.0f}%</p>"
        )
    return "".join(lines)


def _health_section(
    score: ExplainabilityScore | None, findings: tuple[Finding, ...], tokens: Tokens
) -> str:
    if score is None and not findings:
        return (
            f"<p style='color:{tokens.ink_muted}'>Run the doctor to see this project's health.</p>"
        )
    parts: list[str] = []
    if score is not None:
        parts.append(
            f"<p>Explainability <b>{score.score:.0f}/100</b> "
            f"<span style='color:{tokens.ink_muted}'>(docstrings {score.docstring_coverage:.0%}, "
            f"{score.functions} functions)</span></p>"
        )
    counts = {s: sum(1 for f in findings if f.severity is s) for s in Severity}
    summary = ", ".join(
        f"<span style='color:{severity_colour(s, tokens)}'>{n} {s.value}</span>"
        for s, n in counts.items()
        if n
    )
    parts.append(f"<p>{summary or 'No findings.'}</p>")
    return "".join(parts)
