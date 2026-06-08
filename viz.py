"""
viz.py — Step 5: visualization of the liquidation pressure map.

Aesthetic: "editorial markets terminal" — warm ink background, monospace numerics,
a restrained gold accent with emerald (long) / signal-red (short). Charts are kept
transparent so they sit inside the page's card surfaces; the standalone memo PNG
gets a solid ink background.

Three figures from a snapshot dict + the metrics history:
  * fig_ladder   — liquidable notional by price level, longs (flush, below) vs
                   shorts (squeeze, above), with the mark price and the +/-5% band.
  * fig_gauge    — the Cascade Fragility Index as a 0-100 gauge with regime bands.
  * fig_history  — CFI over time with regime bands (fills in as history accumulates).
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from liquidation_map import REGIME_BANDS

# ---- palette (warm ink / editorial terminal) -------------------------------
LONG_COLOR = "#3fb98c"    # emerald  — long liquidations (downside flush fuel)
SHORT_COLOR = "#e5564e"   # red      — short liquidations (upside squeeze fuel)
ACCENT = "#e8a13a"        # gold     — mark price / signal highlight
INK = "#0b0a09"           # solid background for the PNG
FONT = "#ece6da"          # bone / parchment text
MUTED = "#9a8f7d"         # warm taupe
GRID = "rgba(150,128,92,0.13)"
MONO = "IBM Plex Mono, ui-monospace, SFMono-Regular, monospace"

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site", "assets")


def _money(x: float) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(x) >= div:
            return f"${x / div:.1f}{unit}"
    return f"${x:,.0f}"


def _base_layout(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color=FONT, family=MONO), x=0.01),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=FONT, family=MONO, size=12),
        margin=dict(l=72, r=26, t=54, b=46),
        legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h", y=1.04, x=0,
                    font=dict(size=11, color=MUTED)),
        hoverlabel=dict(bgcolor="#15120f", bordercolor=GRID,
                        font=dict(family=MONO, color=FONT)),
    )
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID,
                     tickfont=dict(color=MUTED, size=11))
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID,
                     tickfont=dict(color=MUTED, size=11))
    return fig


# ----------------------------------------------------------------- ladder map
def build_ladder_figure(snapshot: dict[str, Any]) -> go.Figure:
    """Horizontal density of liquidable notional by price level."""
    mark = snapshot["market"]["mark_px"]
    hist = pd.DataFrame(snapshot["histogram"])
    hist = hist[(hist["long_notional"] > 0) | (hist["short_notional"] > 0)]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=hist["price_mid"], x=hist["long_notional"], orientation="h",
        name="Long liquidations · flush risk (below)", marker_color=LONG_COLOR,
        marker_line_width=0,
        hovertemplate="$%{y:,.0f}<br>long liquidable %{x:$,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        y=hist["price_mid"], x=hist["short_notional"], orientation="h",
        name="Short liquidations · squeeze risk (above)", marker_color=SHORT_COLOR,
        marker_line_width=0,
        hovertemplate="$%{y:,.0f}<br>short liquidable %{x:$,.0f}<extra></extra>",
    ))
    fig.add_hline(y=mark, line=dict(color=ACCENT, width=1.6),
                  annotation_text=f"  BTC mark ${mark:,.0f}",
                  annotation_position="top left",
                  annotation_font=dict(color=ACCENT, family=MONO, size=12))
    fig.add_hrect(y0=mark * 0.95, y1=mark * 1.05, fillcolor=ACCENT,
                  opacity=0.05, line_width=0)

    _base_layout(fig, "Liquidable notional by price level — where open BTC leverage triggers")
    fig.update_layout(barmode="stack", bargap=0.12, height=520)
    fig.update_xaxes(title=dict(text="Liquidable notional (USD)",
                                font=dict(color=MUTED, size=11)),
                     tickprefix="$", tickformat="~s")
    fig.update_yaxes(title=dict(text="Liquidation price (USD)",
                                font=dict(color=MUTED, size=11)),
                     tickprefix="$", tickformat=",.0f")
    return fig


# ------------------------------------------------------------------ CFI gauge
def build_gauge_figure(snapshot: dict[str, Any]) -> go.Figure:
    cfi = snapshot["signals"]["cfi"]
    calm, elev = REGIME_BANDS["calm_max"], REGIME_BANDS["elevated_max"]
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=cfi,
        number=dict(font=dict(size=44, color=FONT, family=MONO), suffix="/100"),
        gauge=dict(
            axis=dict(range=[0, 100], tickcolor=MUTED, tickfont=dict(color=MUTED, size=10)),
            bar=dict(color=FONT, thickness=0.22),
            bgcolor="rgba(0,0,0,0)", borderwidth=0,
            steps=[
                dict(range=[0, calm], color="rgba(63,185,140,0.30)"),
                dict(range=[calm, elev], color="rgba(232,161,58,0.30)"),
                dict(range=[elev, 100], color="rgba(229,86,78,0.32)"),
            ],
            threshold=dict(line=dict(color=ACCENT, width=4), value=cfi),
        ),
    ))
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", font=dict(color=FONT, family=MONO),
                      margin=dict(l=26, r=26, t=22, b=8), height=260)
    return fig


# --------------------------------------------------------------- CFI history
def build_history_figure(history: pd.DataFrame) -> go.Figure:
    calm, elev = REGIME_BANDS["calm_max"], REGIME_BANDS["elevated_max"]
    fig = go.Figure()
    if history is None or history.empty:
        ts, cfi = [], []
    else:
        history = history.sort_values("timestamp")
        ts = pd.to_datetime(history["timestamp"])
        cfi = history["cfi"]

    fig.add_hrect(y0=0, y1=calm, fillcolor=LONG_COLOR, opacity=0.07, line_width=0)
    fig.add_hrect(y0=calm, y1=elev, fillcolor=ACCENT, opacity=0.07, line_width=0)
    fig.add_hrect(y0=elev, y1=100, fillcolor=SHORT_COLOR, opacity=0.07, line_width=0)

    fig.add_trace(go.Scatter(
        x=ts, y=cfi, mode="lines+markers", name="CFI",
        line=dict(color=ACCENT, width=2, shape="spline", smoothing=0.4),
        marker=dict(size=5, color=ACCENT),
        fill="tozeroy", fillcolor="rgba(232,161,58,0.07)",
        hovertemplate="%{x|%b %d %H:%M}<br>CFI %{y:.1f}<extra></extra>",
    ))
    _base_layout(fig, "Cascade Fragility Index — accumulating every ~10 min")
    fig.update_layout(height=260)
    fig.update_yaxes(range=[0, 100], title=dict(text="CFI", font=dict(color=MUTED, size=11)))
    fig.update_xaxes(title="")
    if history is None or len(history) < 2:
        fig.add_annotation(text="history accumulates from first deploy",
                           xref="paper", yref="paper", x=0.5, y=0.5,
                           showarrow=False, font=dict(color=MUTED, size=12, family=MONO))
    return fig


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


def fig_to_div(fig: go.Figure, div_id: str) -> str:
    """Render a figure as an embeddable HTML div (Plotly.js loaded once globally)."""
    return fig.to_html(full_html=False, include_plotlyjs=False, div_id=div_id,
                       config={"displayModeBar": False, "responsive": True})
