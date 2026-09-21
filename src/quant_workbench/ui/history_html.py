"""HTML for comparing two runs (Qt-free)."""

from __future__ import annotations

from html import escape

from quant_workbench.domain.history import MetricChange, RunComparison
from quant_workbench.ui.diffhtml import diff_html
from quant_workbench.ui.summary import format_duration
from quant_workbench.ui.theme import Tokens, status_colour


def _value(value: float | int | str | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.6g}" if isinstance(value, float) else str(value)


def _delta(change: MetricChange) -> str:
    delta = change.delta
    if delta is None:
        return ""
    return f"{delta:+,.4g}"


def comparison_html(comparison: RunComparison, tokens: Tokens) -> str:
    """What changed between the older run and the newer one, section by section."""
    before, after = comparison.before, comparison.after
    parts = [
        f"<h3>{escape(before.id)} &rarr; {escape(after.id)}</h3>",
        "<p>"
        f"<b style='color:{status_colour(before.status, tokens)}'>{before.status.value}</b> "
        f"&rarr; <b style='color:{status_colour(after.status, tokens)}'>{after.status.value}</b>"
        f"<span style='color:{tokens.ink_muted}'> &nbsp; {format_duration(before.duration)}"
        f" &rarr; {format_duration(after.duration)}</span></p>",
        "<h4>Configuration</h4>",
    ]
    if comparison.config_changed:
        parts += [diff_html(text, tokens) for _, text in comparison.config_diffs]
    else:
        parts.append(f"<p style='color:{tokens.ink_muted}'>The configuration is identical.</p>")

    parts.append("<h4>Metrics</h4>")
    changed = comparison.changed_metrics
    if changed:
        rows = "".join(
            f"<tr><td style='padding-right:14px'>{escape(m.name)}</td>"
            f"<td>{escape(_value(m.before))}</td><td style='padding:0 8px'>&rarr;</td>"
            f"<td><b>{escape(_value(m.after))}</b></td>"
            f"<td style='padding-left:14px; color:{tokens.accent}'>{escape(_delta(m))}</td></tr>"
            for m in changed
        )
        parts.append(f"<table>{rows}</table>")
    else:
        parts.append(f"<p style='color:{tokens.ink_muted}'>No metric changed.</p>")

    parts.append("<h4>Outputs</h4>")
    outputs = comparison.changed_outputs
    if outputs:
        parts.append(
            "<ul>"
            + "".join(f"<li>{escape(o.path)}: <b>{o.kind}</b></li>" for o in outputs)
            + "</ul>"
        )
    else:
        parts.append(f"<p style='color:{tokens.ink_muted}'>Every output file is identical.</p>")
    return "".join(parts)
