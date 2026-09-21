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


def stylesheet(tokens: Tokens) -> str:
    """The default style of a rendered README, from the theme."""
    return (
        f"body {{ color: {tokens.ink}; }} "
        f"a {{ color: {tokens.accent}; }} "
        f"h1, h2, h3 {{ color: {tokens.ink}; }} "
        f"code, pre {{ font-family: Consolas, monospace; background: {tokens.window}; }} "
        f"blockquote {{ color: {tokens.ink_secondary}; }} "
        f"th {{ background: {tokens.window}; }}"
    )
