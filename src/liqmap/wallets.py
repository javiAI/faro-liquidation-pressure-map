"""
wallets.py — Step 3: the wallet universe the liquidation map is built from.

THE COVERAGE PROBLEM (stated honestly, because it is the central caveat):
There is no public Hyperliquid endpoint that returns every open position in the
market. So, exactly like Glassnode/Coinglass-style liquidation maps, we APPROXIMATE
the market by aggregating the positions of the most active wallets, which
concentrate the majority of liquidable open interest.

How we pick "representative" wallets here (PoC):
  We rank the public leaderboard feed by RECENT TRADING ACTIVITY (volume over the
  week/month windows), not by account value. Step-1 validation showed ranking by
  accountValue surfaces spot holders/vaults with no BTC perp exposure, while
  ranking by recent volume surfaces the directional perp traders we actually want.

How we WOULD pick them in production (documented, not built here):
  Continuous discovery from the Hyperliquid fills WebSocket: subscribe to the
  trade/fill feed, maintain a rolling set of the wallets responsible for the most
  BTC perp volume over a trailing window (e.g. 90-180 days), and refresh the
  universe incrementally. The leaderboard feed used here is undocumented and a
  point-in-time snapshot, so it is a PoC stand-in, not the production source.

The selected universe is cached to data/wallet_universe.csv so the high-frequency
pipeline (every ~10 min) does NOT re-download the ~30MB leaderboard each run; the
universe is refreshed on a slower cadence (e.g. daily).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from liqmap.hl_client import HyperliquidClient
from liqmap.paths import DATA_DIR   # repo-root data/ dir (robust to the src/ layout)

UNIVERSE_CSV = os.path.join(DATA_DIR, "wallet_universe.csv")
# A sidecar with the logical build time. We rely on this (not the file mtime) because
# git checkouts on CI reset mtimes, which would make the universe look forever-fresh
# and never refresh — letting the wallet list silently rot. With built_at persisted,
# the daily refresh works regardless of how the file got onto disk.
UNIVERSE_META = os.path.join(DATA_DIR, "wallet_universe_meta.json")

DEFAULT_N = 2000  # ~47% of BTC OI coverage at ~7.5 req/s, ~4.5 min/run (measured)


def _window_metric(row: dict[str, Any], window: str, field: str) -> float:
    """Pull one numeric field (pnl/roi/vlm) for one window from a leaderboard row."""
    for w in row.get("windowPerformances", []):
        # each entry looks like ["week", {"pnl": "...", "roi": "...", "vlm": "..."}]
        if w and w[0] == window:
            try:
                return float(w[1].get(field, 0) or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def build_universe(n: int = DEFAULT_N, client: HyperliquidClient | None = None) -> pd.DataFrame:
    """Select the top-N most active wallets and return them as a DataFrame.

    SELECTION CRITERION (this is the answer to "how are the wallets chosen?"):
    we rank every leaderboard row by **activity_score = week volume + month volume**
    and keep the top N. We weight recent turnover (volume) — not PnL or account size —
    because turnover is the best proxy for "currently holding and rotating directional
    perp risk", which is exactly the population a liquidation map needs. (Of these N,
    only those that *currently* hold a qualifying open BTC position — notional ≥ $10k,
    liquidationPx present, within 60% of mark — actually land on the map each run; that
    is why ~250 of 2000 appear, and it changes every snapshot as positions open/close.)
    """
    client = client or HyperliquidClient()
    rows = client.fetch_leaderboard()

    records = []
    for r in rows:
        vlm_week = _window_metric(r, "week", "vlm")
        vlm_month = _window_metric(r, "month", "vlm")
        records.append(
            {
                "address": r["ethAddress"],
                "display_name": r.get("displayName") or "",
                "account_value": float(r.get("accountValue", 0) or 0),
                "vlm_week": vlm_week,
                "vlm_month": vlm_month,
                "pnl_month": _window_metric(r, "month", "pnl"),
                "activity_score": vlm_week + vlm_month,
            }
        )

    df = pd.DataFrame.from_records(records)
    df = df.sort_values("activity_score", ascending=False).head(n).reset_index(drop=True)
    df.insert(0, "rank", df.index + 1)
    return df


def save_universe(df: pd.DataFrame, path: str = UNIVERSE_CSV) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    with open(UNIVERSE_META, "w") as f:
        json.dump({"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "n": int(len(df))}, f)


def universe_age_hours(meta_path: str = UNIVERSE_META) -> float | None:
    """Hours since the universe was built (from the sidecar), or None if unknown."""
    if not os.path.exists(meta_path):
        return None
    try:
        built = datetime.fromisoformat(json.load(open(meta_path))["built_at"])
        return (datetime.now(timezone.utc) - built).total_seconds() / 3600.0
    except Exception:  # noqa: BLE001
        return None


def load_universe(path: str = UNIVERSE_CSV) -> list[str]:
    """Return the list of addresses from the cached universe.

    Raises a clear error if the cache is missing, so the pipeline fails loudly
    rather than silently running on an empty wallet set.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Wallet universe cache not found at {path}. "
            f"Run `python wallets.py --refresh` (or build_universe) first."
        )
    return pd.read_csv(path)["address"].astype(str).tolist()


def refresh_and_save(n: int = DEFAULT_N) -> pd.DataFrame:
    """Convenience: build the universe and persist it. Returns the DataFrame."""
    df = build_universe(n=n)
    save_universe(df)
    return df


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build/refresh the wallet universe.")
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="number of wallets to keep")
    parser.add_argument("--refresh", action="store_true", help="rebuild from leaderboard")
    args = parser.parse_args()

    if args.refresh or not os.path.exists(UNIVERSE_CSV):
        frame = refresh_and_save(n=args.n)
        print(f"Saved {len(frame)} wallets -> {UNIVERSE_CSV}")
        print(frame.head(10).to_string(index=False))
    else:
        print(f"Universe already cached at {UNIVERSE_CSV} "
              f"({len(load_universe())} wallets). Use --refresh to rebuild.")
