"""
viz.py — Step 5 helpers: the smoothing kernel + the static memo PNG.

The live page renders its charts client-side (in build_site.py's embedded JS) from a
compact data.json, so this module is intentionally small: it owns (1) the gaussian
smoothing used to precompute the density profile served to the page, and (2) the
combined ladder + gauge PNG exported for the written memo. Aesthetic: "editorial
markets terminal" — warm ink, monospace numerics, gold accent with emerald (long) /
red (short).
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from liqmap.liquidation_map import REGIME_BANDS

# ---- palette (warm ink / editorial terminal) -------------------------------
LONG_COLOR = "#3fb98c"    # emerald  — long liquidations (downside flush fuel)
SHORT_COLOR = "#e5564e"   # red      — short liquidations (upside squeeze fuel)
ACCENT = "#e8a13a"        # gold     — mark price / signal highlight
INK = "#0b0a09"           # solid background for the PNG
FONT = "#ece6da"          # bone / parchment text
MUTED = "#9a8f7d"         # warm taupe
GRID = "rgba(150,128,92,0.13)"
MONO = "IBM Plex Mono, ui-monospace, SFMono-Regular, monospace"

from liqmap.paths import SITE_DIR
OUT_DIR = os.path.join(SITE_DIR, "assets")   # repo-root site/assets (robust to the src/ layout)


def _gaussian_smooth(y: np.ndarray, sigma: float = 1.8) -> np.ndarray:
    """Light gaussian smoothing for a clean density profile (peak positions preserved)."""
    if len(y) == 0:
        return y
    radius = max(1, int(sigma * 3))
    k = np.exp(-(np.arange(-radius, radius + 1) ** 2) / (2 * sigma ** 2))
    k /= k.sum()
    return np.convolve(y, k, mode="same")


# ------------------------------------------------------------ combined PNG
def export_memo_png(snapshot: dict[str, Any], path: str | None = None) -> str:
    """Combined ladder + gauge static PNG for the written memo (solid ink bg)."""
    path = path or os.path.join(OUT_DIR, "liquidation_map.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    mark = snapshot["market"]["mark_px"]
    hist = pd.DataFrame(snapshot["histogram"])
    hist = hist[(hist["long_notional"] > 0) | (hist["short_notional"] > 0)]

    fig = make_subplots(
        rows=1, cols=2, column_widths=[0.68, 0.32],
        specs=[[{"type": "xy"}, {"type": "indicator"}]],
        subplot_titles=("Liquidation density by price level", "Cascade Fragility Index"),
        horizontal_spacing=0.10,
    )
    fig.add_trace(go.Bar(y=hist["price_mid"], x=hist["long_notional"], orientation="h",
                         name="Long", marker_color=LONG_COLOR), row=1, col=1)
    fig.add_trace(go.Bar(y=hist["price_mid"], x=hist["short_notional"], orientation="h",
                         name="Short", marker_color=SHORT_COLOR), row=1, col=1)
    fig.add_hline(y=mark, line=dict(color=ACCENT, width=1.6),
                  annotation_text=f"mark ${mark:,.0f}",
                  annotation_font=dict(color=ACCENT), row=1, col=1)

    cfi = snapshot["signals"]["cfi"]
    calm, elev = REGIME_BANDS["calm_max"], REGIME_BANDS["elevated_max"]
    fig.add_trace(go.Indicator(
        mode="gauge+number", value=cfi, number=dict(suffix="/100", font=dict(color=FONT)),
        gauge=dict(axis=dict(range=[0, 100]),
                   bar=dict(color=FONT, thickness=0.22), bgcolor="rgba(0,0,0,0)",
                   steps=[dict(range=[0, calm], color="rgba(63,185,140,0.35)"),
                          dict(range=[calm, elev], color="rgba(232,161,58,0.35)"),
                          dict(range=[elev, 100], color="rgba(229,86,78,0.38)")],
                   threshold=dict(line=dict(color=ACCENT, width=4), value=cfi)),
    ), row=1, col=2)

    reg = snapshot["signals"]["regime"].upper()
    asym = snapshot["signals"]["asymmetry"]
    bias = "short / upside-squeeze" if asym > 0 else "long / downside-flush"
    fig.update_layout(
        title=dict(text=f"BTC Liquidation Pressure Map — regime {reg} · "
                        f"asymmetry {asym:+.2f} ({bias})",
                   font=dict(size=15, color=FONT, family=MONO)),
        barmode="stack", paper_bgcolor=INK, plot_bgcolor=INK,
        font=dict(color=FONT, family=MONO), showlegend=False, width=1200, height=600,
        margin=dict(l=70, r=30, t=80, b=50),
    )
    fig.update_xaxes(gridcolor=GRID, tickprefix="$", tickformat="~s", row=1, col=1,
                     title="Liquidable notional", tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, tickprefix="$", tickformat=",.0f", row=1, col=1,
                     title="Liquidation price", tickfont=dict(color=MUTED))
    fig.write_image(path, scale=2)
    return path
