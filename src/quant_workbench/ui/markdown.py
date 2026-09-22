"""Markdown to HTML for the README viewer (Qt-free)."""

from __future__ import annotations

from markdown_it import MarkdownIt

from quant_workbench.ui.theme import Tokens

# ``html=False``: a README is untrusted text as far as the viewer is concerned; raw HTML in it
# is shown as text instead of being interpreted.
_RENDERER = MarkdownIt("commonmark", {"html": False}).enable("table")


def render_markdown(text: str) -> str:
    """The HTML body for ``text`` (CommonMark plus tables, raw HTML disabled)."""
    return _RENDERER.render(text)


def page_html(text: str, tokens: Tokens) -> str:
    """A standalone page: ``render_markdown(text)`` plus a stylesheet that keeps everything —
    long lines, wide tables, oversized images — inside the page's own width rather than each
    element's natural size, the same way the dashboards' own HTML already behaves. Shown in a
    real embedded browser (see ``ReadmeView``), so this is ordinary responsive CSS: no per-image
    measuring or capping needed, unlike Qt's rich text engine, which draws every ``<img>`` at its
    native pixel size and ignores ``max-width`` entirely.
    """
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{
    color: {tokens.ink};
    background: {tokens.surface};
    font-family: -apple-system, "Segoe UI", sans-serif;
    font-size: 14px;
    margin: 16px;
    overflow-wrap: break-word;
  }}
  a {{ color: {tokens.accent}; }}
  h1, h2, h3 {{ color: {tokens.ink}; }}
  code {{
    font-family: Consolas, "SF Mono", monospace;
    background: {tokens.window};
    padding: 1px 4px;
    border-radius: 3px;
  }}
  pre {{
    font-family: Consolas, "SF Mono", monospace;
    background: {tokens.window};
    padding: 10px;
    border-radius: 6px;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }}
  pre code {{ background: none; padding: 0; }}
  blockquote {{
    color: {tokens.ink_secondary};
    margin: 0;
    padding-left: 12px;
    border-left: 3px solid {tokens.baseline};
  }}
  table {{ border-collapse: collapse; max-width: 100%; }}
  th, td {{ border: 1px solid {tokens.baseline}; padding: 4px 10px; }}
  th {{ background: {tokens.window}; }}
  img {{ max-width: 100%; height: auto; }}
</style>
</head>
<body>
{render_markdown(text)}
</body>
</html>"""
