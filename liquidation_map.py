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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from hl_client import HyperliquidClient, MarketContext

# Regime thresholds — ILLUSTRATIVE, to be calibrated on accumulated history.
REGIME_BANDS = {"calm_max": 25.0, "elevated_max": 50.0}  # >50 = fragile/red

# Validation-gate thresholds — single source shared by the runner and the Airflow DAG,
# so "what counts as low-confidence" can never drift between the two drivers.
MIN_POSITIONS = 5       # fewer kept positions → flag the reading low-confidence
COVERAGE_FLOOR = 0.05   # coverage below this fraction of OI → flag low-confidence


@dataclass
class MapParams:
    """Tunable parameters for the liquidation map. Defaults are sane PoC values."""

    coin: str = "BTC"
    min_notional_usd: float = 10_000.0   # drop dust (and its garbage liquidationPx)
    max_distance_pct: float = 0.60        # ignore liquidations >60% away (noise/garbage)
    tau: float = 0.08                     # proximity scale of the kernel (8%)
    hist_range_pct: float = 0.60          # histogram spans mark +/- 60% (same as the keep filter,
                                          # so every kept position is on the map; the chart opens
                                          # zoomed to +/-30% and you can pan/zoom out to the rest)
    n_buckets: int = 240                  # ~0.5% per bucket across +/-60%
    # Fixed contract, not freely tunable: these three bands back the summary keys
    # ("0.02"/"0.05"/"0.10") and the SQLite columns liq_within_{2,5,10}pct_usd. Changing
    # them requires updating summary()/SCHEMA_SQL/the KPI keys in lockstep.
    near_bands: tuple[float, ...] = (0.02, 0.05, 0.10)


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
    max_workers: int = 16,
    progress_every: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch the `coin` position for each address concurrently. Returns (positions, n_errors).

    Concurrency only hides network latency — the client's shared token bucket caps the
    actual request rate, so we never exceed the API budget no matter how many workers.
    Wallets with no position are skipped; per-wallet errors are counted (not fatal):
    a map degraded by a few missing wallets beats no map, and the count feeds
    observability downstream.
    """
    positions: list[dict[str, Any]] = []
    n_errors = 0
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(client.get_position, a, coin): a for a in addresses}
        for fut in as_completed(futures):
            done += 1
            try:
                pos = fut.result()
                if pos is not None:
                    positions.append(pos)
            except Exception:  # noqa: BLE001 - counted, not fatal
                n_errors += 1
            if progress_every and done % progress_every == 0:
                print(f"    …queried {done}/{len(addresses)} wallets, "
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
    """Aggregate liquidable notional into price buckets, split long vs short.

    Single vectorized pass (pd.cut + groupby) instead of re-scanning the frame once per
    bucket, then reindexed onto the full bucket grid so empty buckets are present as zeros.
    """
    n = params.n_buckets
    lo = mark_px * (1 - params.hist_range_pct)
    hi = mark_px * (1 + params.hist_range_pct)
    edges = [lo + (hi - lo) * k / n for k in range(n + 1)]
    mids = [(edges[b] + edges[b + 1]) / 2 for b in range(n)]
    out = pd.DataFrame({
        "price_low": edges[:-1],
        "price_high": edges[1:],
        "price_mid": mids,
        "distance_pct": [(m - mark_px) / mark_px * 100.0 for m in mids],
        "long_notional": 0.0,
        "short_notional": 0.0,
    })
    if not df.empty:
        # bucket index per position; positions outside [lo, hi) become NaN and drop out
        bucket = pd.cut(df["liquidation_px"], bins=edges, right=False,
                        include_lowest=True, labels=False)
        grouped = (df.assign(_bucket=bucket).dropna(subset=["_bucket"])
                     .groupby(["_bucket", "side"])["notional_usd"].sum())
        for (b, side), notional in grouped.items():
            out.at[int(b), f"{side}_notional"] = float(notional)
    return out


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
        weighted_proximity = df["notional_usd"] * df["proximity"]   # one pass, no re-slicing
        cfi = 100.0 * weighted_proximity.sum() / df["notional_usd"].sum()
        by_side = weighted_proximity.groupby(df["side"]).sum()
        long_pressure = float(by_side.get("long", 0.0))
        short_pressure = float(by_side.get("short", 0.0))

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


def validate_map(m: LiquidationMap) -> list[str]:
    """Lightweight sanity gate. Returns a list of warnings (empty = clean).

    We do NOT hard-fail on a thin sample, but we surface it: a map built from too few
    positions or low coverage should be shown with lower confidence, not silently
    trusted. Shared by the runner and the DAG so both judge confidence identically.
    """
    warnings: list[str] = []
    n = m.coverage["n_btc_positions"]
    if n < MIN_POSITIONS:
        warnings.append(f"only {int(n)} positions kept (<{MIN_POSITIONS}); "
                        f"treat signals as low-confidence")
    if m.coverage["coverage_ratio"] < COVERAGE_FLOOR:
        warnings.append(f"coverage {m.coverage['coverage_ratio']:.1%} of OI is low")
    if not (0 <= m.cfi <= 100):
        warnings.append(f"CFI out of range: {m.cfi}")
    if m.market.mark_px <= 0:
        warnings.append("non-positive mark price")
    return warnings
