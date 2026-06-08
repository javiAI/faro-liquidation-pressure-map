"""
liquidation_map.py — Step 2: the core metric.

Given a set of wallets, this builds a density map of where their currently-OPEN
BTC positions would be liquidated, split long vs short, and derives two signals:

  * Cascade Fragility Index (CFI, 0-100) — how concentrated the liquidable
    notional is NEAR the current mark price. Closer + bigger = more fragile,
    because a small move can trigger liquidations that push price further and
    trigger more (a cascade).

  * Long/Short Asymmetry (-1..+1) — which side carries more proximate liquidation
    fuel. Positive = short-side (upside-squeeze) risk dominates; negative =
    long-side (downside-flush) risk dominates.

------------------------------------------------------------------------------
DESIGN OF THE FRAGILITY INDEX (every choice here is meant to be defensible):

For each qualifying position i let
    N_i = positionValue (liquidable notional, USD)
    d_i = |liquidationPx_i - markPx| / markPx        (fractional distance to trigger)

Proximity kernel:
    K(d) = exp(-d / tau),  tau default = 0.08 (8%)

We use a smooth exponential kernel instead of a raw 1/d weight on purpose:
1/d explodes as d -> 0, so a single dust position sitting on the mark would
dominate the whole index (and Step-1 validation showed the API returns exactly
that kind of garbage). exp(-d/tau) is bounded in (0, 1], so the index is stable.

Index (notional-weighted average proximity):
    CFI = 100 * sum_i( N_i * K(d_i) ) / sum_i( N_i )

Interpretation: CFI -> 100 means the liquidable money sits right on top of the
price (maximally fragile); CFI -> 0 means it is spread far away (resilient).
Because it is notional-weighted, a tiny near-mark position cannot spike it; only
when the *big* positions are also the *near* ones does CFI climb. We deliberately
pair CFI with an ABSOLUTE magnitude (USD liquidable within +/-5%) and a COVERAGE
ratio, because the index measures STRUCTURE, not size — see the memo caveats.

Regime bands below (green/amber/red) are illustrative starting points; they must
be calibrated empirically on the forward-accumulated time-series, which is why we
store CFI every run.
------------------------------------------------------------------------------
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from hl_client import HyperliquidClient, MarketContext

# Regime thresholds — ILLUSTRATIVE, to be calibrated on accumulated history.
REGIME_BANDS = {"calm_max": 25.0, "elevated_max": 50.0}  # >50 = fragile/red


@dataclass
class MapParams:
    """Tunable parameters for the liquidation map. Defaults are sane PoC values."""

    coin: str = "BTC"
    min_notional_usd: float = 10_000.0   # drop dust (and its garbage liquidationPx)
    max_distance_pct: float = 0.60        # ignore liquidations >60% away (noise/garbage)
    tau: float = 0.08                     # proximity scale of the kernel (8%)
    hist_range_pct: float = 0.30          # histogram spans mark +/- 30%
    n_buckets: int = 120                  # ~0.5% per bucket at +/-30%
    near_bands: tuple[float, ...] = (0.02, 0.05, 0.10)  # report USD within these


@dataclass
class LiquidationMap:
    """The full, storage-ready result of one snapshot."""

    timestamp: str
    market: MarketContext
    cfi: float
    asymmetry: float
    long_pressure: float
    short_pressure: float
    near_band_usd: dict[str, dict[str, float]]   # {"0.02": {"long":..,"short":..,"total":..}}
    coverage: dict[str, float]
    quality: dict[str, int]
    histogram: pd.DataFrame = field(repr=False)
    positions: pd.DataFrame = field(repr=False)

    @property
    def regime(self) -> str:
        if self.cfi < REGIME_BANDS["calm_max"]:
            return "calm"
        if self.cfi < REGIME_BANDS["elevated_max"]:
            return "elevated"
        return "fragile"

    def summary(self) -> dict[str, Any]:
        """A compact dict suitable for a metrics time-series row."""
        m = self.market
        return {
            "timestamp": self.timestamp,
            "coin": m.coin,
            "mark_px": m.mark_px,
            "oracle_px": m.oracle_px,
            "funding_hourly": m.funding_hourly,
            "oi_btc": m.open_interest_coin,
            "oi_usd": m.open_interest_usd,
            "cfi": self.cfi,
            "regime": self.regime,
            "asymmetry": self.asymmetry,
            "long_pressure": self.long_pressure,
            "short_pressure": self.short_pressure,
            "liq_within_2pct_usd": self.near_band_usd["0.02"]["total"],
            "liq_within_5pct_usd": self.near_band_usd["0.05"]["total"],
            "liq_within_10pct_usd": self.near_band_usd["0.10"]["total"],
            "n_wallets": int(self.coverage["n_wallets_queried"]),
            "n_positions": int(self.coverage["n_btc_positions"]),
            "sampled_notional_usd": self.coverage["sampled_notional_usd"],
            "coverage_ratio": self.coverage["coverage_ratio"],
            "n_null_liqpx": self.quality["n_null_liqpx"],
            "n_dust_filtered": self.quality["n_dust_filtered"],
            "n_far_filtered": self.quality["n_far_filtered"],
        }


# ---------------------------------------------------------------- extraction
def fetch_positions(
    client: HyperliquidClient,
    addresses: list[str],
    coin: str = "BTC",
    *,
    progress_every: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch the `coin` position for each address. Returns (positions, n_errors).

    Wallets with no position are simply skipped. Per-wallet network errors are
    counted (not fatal): a liquidation map degraded by a few missing wallets is
    far better than no map, and the error count feeds observability downstream.
    """
    positions: list[dict[str, Any]] = []
    n_errors = 0
    for i, addr in enumerate(addresses, start=1):
        try:
            pos = client.get_position(addr, coin=coin)
        except Exception:  # noqa: BLE001 - counted, not fatal
            n_errors += 1
            continue
        if pos is not None:
            positions.append(pos)
        if progress_every and i % progress_every == 0:
            print(f"    …queried {i}/{len(addresses)} wallets, "
                  f"{len(positions)} {coin} positions, {n_errors} errors")
    return positions, n_errors


# ---------------------------------------------------------------- computation
def _proximity(distance_frac: float, tau: float) -> float:
    """Bounded proximity weight K(d) = exp(-d/tau) in (0, 1]."""
    return math.exp(-distance_frac / tau)


def clean_positions(
    positions: list[dict[str, Any]], mark_px: float, params: MapParams
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Validate/clean raw positions; return (clean_df, quality_counts).

    Filters applied, each motivated by Step-1 findings:
      * liquidationPx is None  -> cross-margin; not placeable on the map.
      * |positionValue| < min  -> dust; its liquidationPx is unreliable garbage.
      * distance > max_distance -> noise / numerically degenerate liquidationPx.
    """
    quality = {"n_raw": len(positions), "n_null_liqpx": 0, "n_dust_filtered": 0,
               "n_far_filtered": 0}
    clean_rows = []
    for p in positions:
        liq = p["liquidationPx"]
        notional = abs(p["positionValue"])
        if liq is None:
            quality["n_null_liqpx"] += 1
            continue
        if notional < params.min_notional_usd:
            quality["n_dust_filtered"] += 1
            continue
        distance = abs(liq - mark_px) / mark_px
        if distance > params.max_distance_pct:
            quality["n_far_filtered"] += 1
            continue
        side = "long" if p["szi"] > 0 else "short"
        clean_rows.append(
            {
                "address": p["address"],
                "side": side,
                "szi": p["szi"],
                "liquidation_px": liq,
                "notional_usd": notional,
                "distance_frac": distance,
                "distance_pct": distance * 100.0,
                "proximity": _proximity(distance, params.tau),
                "leverage": p.get("leverage_value"),
            }
        )
    df = pd.DataFrame(clean_rows)
    return df, quality


def build_histogram(df: pd.DataFrame, mark_px: float, params: MapParams) -> pd.DataFrame:
    """Aggregate liquidable notional into price buckets, split long vs short."""
    lo = mark_px * (1 - params.hist_range_pct)
    hi = mark_px * (1 + params.hist_range_pct)
    edges = [lo + (hi - lo) * k / params.n_buckets for k in range(params.n_buckets + 1)]
    rows = []
    for b in range(params.n_buckets):
        b_lo, b_hi = edges[b], edges[b + 1]
        in_bucket = df[(df["liquidation_px"] >= b_lo) & (df["liquidation_px"] < b_hi)] \
            if not df.empty else df
        long_n = float(in_bucket[in_bucket["side"] == "long"]["notional_usd"].sum()) \
            if not df.empty else 0.0
        short_n = float(in_bucket[in_bucket["side"] == "short"]["notional_usd"].sum()) \
            if not df.empty else 0.0
        mid = (b_lo + b_hi) / 2
        rows.append(
            {
                "price_low": b_lo,
                "price_high": b_hi,
                "price_mid": mid,
                "distance_pct": (mid - mark_px) / mark_px * 100.0,
                "long_notional": long_n,
                "short_notional": short_n,
            }
        )
    return pd.DataFrame(rows)


def compute_metrics(
    positions: list[dict[str, Any]],
    market: MarketContext,
    n_wallets_queried: int,
    params: MapParams | None = None,
) -> LiquidationMap:
    """Turn raw positions + market context into the full LiquidationMap result.

    Pure function (no network), so it is unit-testable and the Airflow `transform`
    task can call it directly on extracted data.
    """
    params = params or MapParams()
    mark = market.mark_px
    df, quality = clean_positions(positions, mark, params)

    # --- Cascade Fragility Index: notional-weighted average proximity ---------
    if df.empty or df["notional_usd"].sum() == 0:
        cfi = 0.0
        long_pressure = short_pressure = 0.0
    else:
        weighted = (df["notional_usd"] * df["proximity"]).sum()
        cfi = 100.0 * weighted / df["notional_usd"].sum()
        long_pressure = float(
            (df[df["side"] == "long"]["notional_usd"]
             * df[df["side"] == "long"]["proximity"]).sum()
        )
        short_pressure = float(
            (df[df["side"] == "short"]["notional_usd"]
             * df[df["side"] == "short"]["proximity"]).sum()
        )

    # --- Long/Short Asymmetry -------------------------------------------------
    denom = long_pressure + short_pressure
    asymmetry = (short_pressure - long_pressure) / denom if denom > 0 else 0.0

    # --- Absolute liquidable notional within near bands (magnitude context) ---
    near_band_usd: dict[str, dict[str, float]] = {}
    for band in params.near_bands:
        if df.empty:
            near_band_usd[f"{band:.2f}"] = {"long": 0.0, "short": 0.0, "total": 0.0}
            continue
        within = df[df["distance_frac"] <= band]
        long_u = float(within[within["side"] == "long"]["notional_usd"].sum())
        short_u = float(within[within["side"] == "short"]["notional_usd"].sum())
        near_band_usd[f"{band:.2f}"] = {
            "long": long_u, "short": short_u, "total": long_u + short_u
        }

    sampled_notional = float(df["notional_usd"].sum()) if not df.empty else 0.0
    coverage = {
        "n_wallets_queried": float(n_wallets_queried),
        "n_btc_positions": float(len(df)),
        "sampled_notional_usd": sampled_notional,
        "coverage_ratio": (sampled_notional / market.open_interest_usd
                           if market.open_interest_usd else 0.0),
    }

    histogram = build_histogram(df, mark, params)

    return LiquidationMap(
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        market=market,
        cfi=round(cfi, 2),
        asymmetry=round(asymmetry, 4),
        long_pressure=long_pressure,
        short_pressure=short_pressure,
        near_band_usd=near_band_usd,
        coverage=coverage,
        quality=quality,
        histogram=histogram,
        positions=df,
    )


def build_liquidation_map(
    client: HyperliquidClient,
    addresses: list[str],
    params: MapParams | None = None,
    *,
    progress_every: int = 0,
) -> LiquidationMap:
    """End-to-end convenience: fetch market context + positions, then compute."""
    params = params or MapParams()
    market = client.get_market_context(params.coin)
    positions, _ = fetch_positions(
        client, addresses, coin=params.coin, progress_every=progress_every
    )
    return compute_metrics(positions, market, len(addresses), params)
