"""
viz.py — Step 5: visualization of the liquidation pressure map.

Produces three Plotly figures from a snapshot dict + the metrics history:

  * fig_ladder   — the liquidation density "ladder": liquidable notional by price
                   level, longs (flush risk, below mark) vs shorts (squeeze risk,
                   above mark), with the current mark price and the +/-5% band.
  * fig_gauge    — the Cascade Fragility Index as a 0-100 gauge with regime bands.
  * fig_history  — CFI over time with green/amber/red regime bands (fills in as the
                   forward time-series accumulates).

It also exports a combined static PNG (ladder + gauge) for the written memo.

The colour language is deliberate and matches the trader reading:
    teal  = long liquidations (downside flush fuel)
    red   = short liquidations (upside squeeze fuel)
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from liquidation_map import REGIME_BANDS

LONG_COLOR = "#14b8a6"   # teal
SHORT_COLOR = "#ef4444"  # red
GRID = "#1f2937"
PAPER = "#0b0f17"
FONT = "#e5e7eb"

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site", "assets")


def _money(x: float) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(x) >= div:
            return f"${x / div:.1f}{unit}"
    return f"${x:,.0f}"


def _base_layout(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=16, color=FONT)),
        paper_bgcolor=PAPER, plot_bgcolor=PAPER,
        font=dict(color=FONT, family="Inter, system-ui, sans-serif"),
        margin=dict(l=70, r=30, t=60, b=50),
        legend=dict(bgcolor="rgba(0,0,0,0)", orientation="h", y=1.02, x=0),
    )
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID)
    return fig


# ----------------------------------------------------------------- ladder map
def build_ladder_figure(snapshot: dict[str, Any]) -> go.Figure:
    """Horizontal density of liquidable notional by price level."""
    mark = snapshot["market"]["mark_px"]
    hist = pd.DataFrame(snapshot["histogram"])
    # keep only buckets that carry notional, for a clean axis
    hist = hist[(hist["long_notional"] > 0) | (hist["short_notional"] > 0)]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=hist["price_mid"], x=hist["long_notional"], orientation="h",
        name="Long liquidations (flush risk, below)", marker_color=LONG_COLOR,
        hovertemplate="$%{y:,.0f}<br>long liquidable %{x:$,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        y=hist["price_mid"], x=hist["short_notional"], orientation="h",
        name="Short liquidations (squeeze risk, above)", marker_color=SHORT_COLOR,
        hovertemplate="$%{y:,.0f}<br>short liquidable %{x:$,.0f}<extra></extra>",
    ))
    # current mark price
    fig.add_hline(y=mark, line=dict(color="#fbbf24", width=2, dash="solid"),
                  annotation_text=f"BTC mark ${mark:,.0f}",
                  annotation_position="top left",
                  annotation_font=dict(color="#fbbf24"))
    # +/-5% proximity band
    fig.add_hrect(y0=mark * 0.95, y1=mark * 1.05, fillcolor="#fbbf24",
                  opacity=0.06, line_width=0)

    _base_layout(fig, "Liquidation Pressure Map — BTC (open positions, where they liquidate)")
    fig.update_layout(barmode="stack", bargap=0.1)
    fig.update_xaxes(title="Liquidable notional (USD)", tickprefix="$", tickformat="~s")
    fig.update_yaxes(title="Liquidation price (USD)", tickprefix="$", tickformat=",.0f")
    return fig


# ------------------------------------------------------------------ CFI gauge
def build_gauge_figure(snapshot: dict[str, Any]) -> go.Figure:
    cfi = snapshot["signals"]["cfi"]
    calm, elev = REGIME_BANDS["calm_max"], REGIME_BANDS["elevated_max"]
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=cfi,
        number=dict(font=dict(size=40, color=FONT), suffix="/100"),
        title=dict(text="Cascade Fragility Index", font=dict(size=15, color=FONT)),
        gauge=dict(
            axis=dict(range=[0, 100], tickcolor=FONT),
            bar=dict(color="#e5e7eb", thickness=0.25),
            steps=[
                dict(range=[0, calm], color="#065f46"),    # calm  (green)
                dict(range=[calm, elev], color="#92400e"),  # elevated (amber)
                dict(range=[elev, 100], color="#7f1d1d"),   # fragile (red)
            ],
            threshold=dict(line=dict(color="#fbbf24", width=4), value=cfi),
        ),
    ))
    fig.update_layout(paper_bgcolor=PAPER, font=dict(color=FONT),
                      margin=dict(l=30, r=30, t=50, b=10), height=300)
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

    # regime bands
    fig.add_hrect(y0=0, y1=calm, fillcolor=LONG_COLOR, opacity=0.10, line_width=0)
    fig.add_hrect(y0=calm, y1=elev, fillcolor="#f59e0b", opacity=0.10, line_width=0)
    fig.add_hrect(y0=elev, y1=100, fillcolor=SHORT_COLOR, opacity=0.10, line_width=0)

    fig.add_trace(go.Scatter(
        x=ts, y=cfi, mode="lines+markers", name="CFI",
        line=dict(color="#fbbf24", width=2), marker=dict(size=6),
        hovertemplate="%{x}<br>CFI %{y:.1f}<extra></extra>",
    ))
    _base_layout(fig, "Cascade Fragility Index — history (accumulates every ~10 min)")
    fig.update_yaxes(range=[0, 100], title="CFI")
    fig.update_xaxes(title="Time (UTC)")
    if history is None or len(history) < 2:
        fig.add_annotation(text="History accumulates from first deploy — one point so far",
                           xref="paper", yref="paper", x=0.5, y=0.5,
                           showarrow=False, font=dict(color="#9ca3af", size=12))
    return fig


# ------------------------------------------------------------ combined PNG
def export_memo_png(snapshot: dict[str, Any], path: str | None = None) -> str:
    """Combined ladder + gauge static PNG for the written memo."""
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
    fig.add_hline(y=mark, line=dict(color="#fbbf24", width=2),
                  annotation_text=f"mark ${mark:,.0f}", row=1, col=1)

    cfi = snapshot["signals"]["cfi"]
    calm, elev = REGIME_BANDS["calm_max"], REGIME_BANDS["elevated_max"]
    fig.add_trace(go.Indicator(
        mode="gauge+number", value=cfi, number=dict(suffix="/100"),
        gauge=dict(axis=dict(range=[0, 100]),
                   bar=dict(color="#e5e7eb", thickness=0.25),
                   steps=[dict(range=[0, calm], color="#065f46"),
                          dict(range=[calm, elev], color="#92400e"),
                          dict(range=[elev, 100], color="#7f1d1d")],
                   threshold=dict(line=dict(color="#fbbf24", width=4), value=cfi)),
    ), row=1, col=2)

    reg = snapshot["signals"]["regime"].upper()
    asym = snapshot["signals"]["asymmetry"]
    bias = "short / upside-squeeze" if asym > 0 else "long / downside-flush"
    fig.update_layout(
        title=dict(text=f"BTC Liquidation Pressure Map — regime {reg} · "
                        f"asymmetry {asym:+.2f} ({bias})", font=dict(size=15)),
        barmode="stack", paper_bgcolor=PAPER, plot_bgcolor=PAPER,
        font=dict(color=FONT), showlegend=False, width=1200, height=600,
        margin=dict(l=70, r=30, t=80, b=50),
    )
    fig.update_xaxes(gridcolor=GRID, tickprefix="$", tickformat="~s", row=1, col=1,
                     title="Liquidable notional")
    fig.update_yaxes(gridcolor=GRID, tickprefix="$", tickformat=",.0f", row=1, col=1,
                     title="Liquidation price")
    fig.write_image(path, scale=2)
    return path


def fig_to_div(fig: go.Figure, div_id: str) -> str:
    """Render a figure as an embeddable HTML div (Plotly.js loaded once globally)."""
    return fig.to_html(full_html=False, include_plotlyjs=False, div_id=div_id,
                       config={"displayModeBar": False, "responsive": True})
