"""
heatmap.py — build the time × price liquidity heatmap from per-snapshot histograms.

Each snapshot stores where open BTC leverage would liquidate (notional per price
bucket). Because the mark price moves, every snapshot's buckets sit at different
absolute prices, so we rebin them onto ONE fixed absolute-price grid. We then
aggregate over time at three resolutions (10-min / hourly / daily); the cell value
is the MEAN liquidable notional at that price during that period — a mean (not a
sum) because positions are a stock, not a flow (summing snapshots within a period
would double-count the same open positions).

Returns {res: {x, y, z, mark}} for res in {"min10","hour","day"} (shared price grid
y), or None if there is no data. The mark line shares each resolution's x, so it
spans the full width; in the 10-min view each column is one snapshot, so the mark
reaches the latest reading.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

# (pandas floor alias, max columns kept) per resolution
_RESOLUTIONS = {"min10": ("10min", 432), "hour": ("h", 336), "day": ("D", 120)}


def build_heatmap(map_history: list[dict[str, Any]], n_bins: int = 64) -> dict[str, Any] | None:
    if not map_history:
        return None

    # shared absolute-price grid from non-zero buckets (clip tails for a tight axis)
    prices = [p for snap in map_history for p, lo, sh in snap["b"] if lo + sh > 0]
    if len(prices) < 2:
        return None
    pmin, pmax = float(np.percentile(prices, 0.5)), float(np.percentile(prices, 99.5))
    if pmax <= pmin:
        pmin, pmax = min(prices), max(prices) + 1
    edges = np.linspace(pmin, pmax, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2

    # rebin each snapshot onto the grid once (reused by every resolution)
    cols: list[tuple[pd.Timestamp, float, np.ndarray]] = []
    for snap in map_history:
        col = np.zeros(n_bins)
        for p, lo, sh in snap["b"]:
            idx = int(np.searchsorted(edges, p, side="right") - 1)
            if 0 <= idx < n_bins:
                col[idx] += lo + sh
        cols.append((pd.Timestamp(snap["timestamp"]).tz_convert("UTC"), float(snap["mark"]), col))
    cols.sort(key=lambda c: c[0])

    def aggregate(freq: str, cap: int) -> dict[str, Any]:
        groups: dict[pd.Timestamp, list[tuple[float, np.ndarray]]] = defaultdict(list)
        for ts, mark, col in cols:
            groups[ts.floor(freq)].append((mark, col))
        keys = sorted(groups)[-cap:]
        z = np.zeros((n_bins, len(keys)))
        marks: list[float] = []
        for j, k in enumerate(keys):
            members = groups[k]
            z[:, j] = np.mean([c for _, c in members], axis=0)
            marks.append(float(np.mean([m for m, _ in members])))
        return {"x": [k.isoformat() for k in keys], "y": centers.tolist(),
                "z": z.tolist(), "mark": marks}

    return {res: aggregate(freq, cap) for res, (freq, cap) in _RESOLUTIONS.items()}
