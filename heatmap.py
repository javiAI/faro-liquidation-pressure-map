"""
heatmap.py — build the time × price liquidity heatmap from per-snapshot histograms.

Each snapshot stores where open BTC leverage would liquidate (notional per price
bucket). Because the mark price moves, every snapshot's buckets sit at different
absolute prices, so we rebin them onto ONE fixed absolute-price grid and group
snapshots by hour. The cell value is the MEAN liquidable notional at that price
during that hour — a mean (not a sum) because positions are a stock, not a flow:
summing snapshots within an hour would double-count the same open positions.

Returns a dict ready for a Plotly heatmap: {x: hour labels, y: price grid,
z: matrix[price][hour], mark: hourly mark price} — or None if there is no data.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd


def build_heatmap(map_history: list[dict[str, Any]], n_bins: int = 64,
                  max_hours: int = 336) -> dict[str, Any] | None:
    if not map_history:
        return None

    # global price range from non-zero buckets (clip extreme tails for a tight axis)
    prices: list[float] = []
    for snap in map_history:
        for p, lo, sh in snap["b"]:
            if lo + sh > 0:
                prices.append(p)
    if len(prices) < 2:
        return None
    pmin, pmax = float(np.percentile(prices, 0.5)), float(np.percentile(prices, 99.5))
    if pmax <= pmin:
        pmin, pmax = min(prices), max(prices) + 1
    edges = np.linspace(pmin, pmax, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2

    # group snapshots by hour
    groups: dict[pd.Timestamp, list[dict]] = defaultdict(list)
    for snap in map_history:
        hour = pd.Timestamp(snap["timestamp"]).tz_convert("UTC").floor("h")
        groups[hour].append(snap)
    hours = sorted(groups)[-max_hours:]

    z = np.zeros((n_bins, len(hours)))
    marks: list[float] = []
    for j, hour in enumerate(hours):
        snaps = groups[hour]
        acc = np.zeros(n_bins)
        for snap in snaps:
            for p, lo, sh in snap["b"]:
                idx = int(np.searchsorted(edges, p, side="right") - 1)
                if 0 <= idx < n_bins:
                    acc[idx] += lo + sh
        z[:, j] = acc / len(snaps)          # mean across snapshots in the hour
        marks.append(float(np.mean([s["mark"] for s in snaps])))

    return {
        "x": [h.isoformat() for h in hours],
        "y": centers.tolist(),
        "z": z.tolist(),
        "mark": marks,
    }
