"""Local cache for the Plotly script the portfolio's dashboards load from a CDN (Qt-free).

The dashboards are single HTML files that pull ``plotly.min.js`` from ``cdn.plot.ly``. Inside
the workbench they should keep working without internet after the first time, so the script is
downloaded once into the cache folder and the HTML shown to the user is a copy whose
``<script src>`` points at that local file. The original dashboard is never modified.
"""

from __future__ import annotations

import hashlib
import re
import urllib.request
from collections.abc import Callable
from pathlib import Path

#: ``<script src="https://cdn.plot.ly/plotly-2.35.2.min.js">`` (single or double quotes).
_CDN_SCRIPT = re.compile(r"""(<script[^>]*\bsrc=)(["'])(https://cdn\.plot\.ly/[^"']+)\2""", re.I)
_TIMEOUT_SECONDS = 20

Fetcher = Callable[[str], bytes]


def find_cdn_scripts(html: str) -> list[str]:
    """The CDN URLs of Plotly scripts referenced by ``html`` (deduplicated, in order)."""
    return list(dict.fromkeys(match.group(3) for match in _CDN_SCRIPT.finditer(html)))


def download(url: str) -> bytes:
    """Fetch ``url`` over HTTPS (the default :data:`Fetcher`)."""
    if not url.startswith("https://"):
        raise ValueError(f"refusing to fetch a non-HTTPS URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "quant-workbench"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
        data: bytes = response.read()
    return data


class PlotlyCache:
    """Downloads each script once and remembers where it is."""

    def __init__(self, directory: Path, fetch: Fetcher = download) -> None:
        self._directory = directory
        self._fetch = fetch

    def path_for(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()[:12]
        return self._directory / f"{digest}-{url.rsplit('/', 1)[-1]}"

    def is_cached(self, url: str) -> bool:
        return self.path_for(url).is_file()

    def ensure(self, url: str) -> Path | None:
        """The local copy of ``url``, downloading it if needed; ``None`` when offline."""
        target = self.path_for(url)
        if target.is_file():
            return target
        try:
            data = self._fetch(url)
        except (OSError, ValueError):
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".part")
        temporary.write_bytes(data)
        temporary.replace(target)  # a half-downloaded file is never mistaken for the real one
        return target

    def missing(self, html: str) -> list[str]:
        """The CDN scripts of ``html`` that are not in the cache yet."""
        return [url for url in find_cdn_scripts(html) if not self.is_cached(url)]

    def localise(self, html: str, *, download: bool = True) -> tuple[str, bool]:
        """``(html, changed)`` with every cached CDN script made local.

        With ``download`` a missing script is fetched first (blocking: call it off the GUI
        thread). Scripts that are not available are left pointing at the CDN, so the dashboard
        still works when online. ``changed`` says whether anything was rewritten.
        """
        local = {
            url: self.ensure(url)
            if download
            else (self.path_for(url) if self.is_cached(url) else None)
            for url in find_cdn_scripts(html)
        }

        def swap(match: re.Match[str]) -> str:
            path = local.get(match.group(3))
            if path is None:
                return match.group(0)
            return f"{match.group(1)}{match.group(2)}{path.as_uri()}{match.group(2)}"

        rewritten = _CDN_SCRIPT.sub(swap, html)
        return rewritten, rewritten != html
