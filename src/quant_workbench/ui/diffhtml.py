"""A unified diff as coloured HTML (Qt-free, so the colouring rules are unit-testable)."""

from __future__ import annotations

from html import escape

from quant_workbench.ui.theme import Tokens


def diff_html(diff: str, tokens: Tokens) -> str:
    """``<pre>`` block with added lines in green, removed in red, hunk headers in the accent."""
    if not diff.strip():
        return f"<p style='color:{tokens.ink_muted}'>No differences.</p>"
    lines: list[str] = []
    for raw in diff.replace("\r", "").split("\n"):
        colour = _colour_of(raw, tokens)
        text = escape(raw) or "&nbsp;"
        lines.append(f"<span style='color:{colour}'>{text}</span>" if colour else text)
    return (
        "<pre style='font-family:Consolas,monospace; font-size:12px; margin:0'>"
        + "\n".join(lines)
        + "</pre>"
    )


def _colour_of(line: str, tokens: Tokens) -> str | None:
    if line.startswith(("+++", "---")):
        return tokens.ink_muted
    if line.startswith("@@"):
        return tokens.accent
    if line.startswith("+"):
        return tokens.success
    if line.startswith("-"):
        return tokens.error
    return None
