"""Self-contained HTML dashboard with the portfolio's palette (no third-party libraries).

The page is one file with inline SVG, so it opens anywhere and needs no internet. The colours
are the ones every dashboard of the portfolio uses, which keeps the projects visually alike.
"""

from html import escape

# Palette shared by the portfolio's dashboards.
BLUE = "#2a78d6"
ORANGE = "#e3a648"
SURFACE = "#fcfcfb"
PAGE_PLANE = "#f9f9f7"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
GRIDLINE = "#e1e0d9"

CHART_WIDTH = 900
CHART_HEIGHT = 340
MARGIN = 40
SAMPLE_PATHS = 25  # how many individual paths are drawn faintly behind the median


def _polyline(
    values: list[float], low: float, high: float, colour: str, opacity: float, width: float
) -> str:
    """One SVG polyline for ``values``, scaled into the chart area."""
    inner_w = CHART_WIDTH - 2 * MARGIN
    inner_h = CHART_HEIGHT - 2 * MARGIN
    span = (high - low) or 1.0
    points = " ".join(
        f"{MARGIN + i / (len(values) - 1) * inner_w:.1f},"
        f"{CHART_HEIGHT - MARGIN - (v - low) / span * inner_h:.1f}"
        for i, v in enumerate(values)
    )
    return (
        f'<polyline fill="none" stroke="{colour}" stroke-opacity="{opacity}" '
        f'stroke-width="{width}" points="{points}"/>'
    )


def build_dashboard(
    *, title: str, paths: list[list[float]], median_path: list[float], kpis: list[tuple[str, str]]
) -> str:
    """The dashboard page: KPI tiles and a chart of sample paths with the median highlighted."""
    every = [price for path in paths for price in path]
    low, high = min(every), max(every)
    faint = "".join(_polyline(p, low, high, BLUE, 0.18, 1.0) for p in paths[:SAMPLE_PATHS])
    median = _polyline(median_path, low, high, ORANGE, 1.0, 2.5)
    axis = "".join(
        f'<line x1="{MARGIN}" x2="{CHART_WIDTH - MARGIN}" y1="{y}" y2="{y}" stroke="{GRIDLINE}"/>'
        for y in (MARGIN, CHART_HEIGHT / 2, CHART_HEIGHT - MARGIN)
    )
    tiles = "".join(
        f'<div class="tile"><span>{escape(label)}</span><b>{escape(value)}</b></div>'
        for label, value in kpis
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{escape(title)}</title>
<style>
body {{ margin: 0; background: {PAGE_PLANE}; color: {INK_PRIMARY}; font-family: system-ui, sans-serif; }}
main {{ max-width: 960px; margin: 0 auto; padding: 32px 24px; }}
.tiles {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 24px 0; }}
.tile {{ background: {SURFACE}; border: 1px solid {GRIDLINE}; border-radius: 12px; padding: 16px 20px; min-width: 180px; }}
.tile span {{ display: block; color: {INK_SECONDARY}; font-size: 13px; }}
.tile b {{ font-size: 26px; }}
svg {{ background: {SURFACE}; border: 1px solid {GRIDLINE}; border-radius: 12px; width: 100%; height: auto; }}
</style></head><body><main>
<h1>{escape(title)}</h1>
<div class="tiles">{tiles}</div>
<svg viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" role="img" aria-label="Simulated price paths">
{axis}{faint}{median}
<text x="{MARGIN}" y="20" fill="{INK_SECONDARY}" font-size="13">Price ({low:.0f} to {high:.0f}); median path in orange</text>
</svg>
</main></body></html>
"""
