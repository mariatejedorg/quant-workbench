"""Markdown to HTML for the README viewer (Qt-free)."""

from __future__ import annotations

import re
from pathlib import Path

from markdown_it import MarkdownIt
from PIL import Image, UnidentifiedImageError

from quant_workbench.ui.theme import Tokens

# ``html=False``: a README is untrusted text as far as the viewer is concerned; raw HTML in it
# is shown as text instead of being interpreted.
_RENDERER = MarkdownIt("commonmark", {"html": False}).enable("table")

# Qt's rich text engine (used by the README's QTextBrowser) draws every <img> at its native
# pixel size — it does not shrink oversized images to fit, the way a real browser would. A
# dashboard preview screenshot is easily 1500px+ wide, which blows out the panel entirely. Only
# local images are capped: remote ones (the README's shields.io badges) are already icon-sized.
_MAX_LOCAL_IMAGE_WIDTH = 720
_IMG_TAG = re.compile(r'<img ([^>]*?)src="([^"]+)"([^>]*?)>')


def render_markdown(text: str) -> str:
    """The HTML body for ``text`` (CommonMark plus tables, raw HTML disabled)."""
    return _RENDERER.render(text)


def constrain_local_image_widths(html: str, project_root: Path) -> str:
    """Cap local ``<img>`` tags wider than :data:`_MAX_LOCAL_IMAGE_WIDTH` to that width.

    Qt scales the image's height to match automatically as long as only ``width`` is set. Images
    that are already narrow, or that fail to open (missing file, not actually an image), are left
    untouched — this only ever makes an oversized image smaller, never bigger or broken.
    """

    def _cap(match: re.Match[str]) -> str:
        before, src, after = match.group(1), match.group(2), match.group(3)
        if "://" in src:  # remote (badges): never local files, never this large
            return match.group(0)
        try:
            with Image.open(project_root / src) as image:
                width = image.width
        except (OSError, UnidentifiedImageError):
            return match.group(0)
        if width <= _MAX_LOCAL_IMAGE_WIDTH:
            return match.group(0)
        after = after.strip()
        self_closing = after.endswith("/")
        after = after[:-1].rstrip() if self_closing else after
        attrs = f'{" " + after if after else ""} width="{_MAX_LOCAL_IMAGE_WIDTH}"'
        tail = " />" if self_closing else ">"
        return f'<img {before}src="{src}"{attrs}{tail}'

    return _IMG_TAG.sub(_cap, html)


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
