"""
run_pipeline.py — the thin end-to-end runner.

This is what GitHub Actions calls every ~10 minutes (and what a human runs
locally). It reuses the exact same functions the Airflow DAG orchestrates, so
there is ONE implementation of the logic and two ways to drive it:

    extract -> validate -> transform -> load -> render

The Airflow DAG (dags/liquidation_pressure_dag.py) is the proof-of-concept
orchestration artifact; this script is the lightweight always-on runner for the
live demo. Both import wallets / liquidation_map / storage / build_site.
"""

from __future__ import annotations

import argparse
import os
import time

from hl_client import HyperliquidClient
from liquidation_map import MapParams, build_liquidation_map
from storage import persist_all
import wallets

UNIVERSE_MAX_AGE_H = 24  # refresh the wallet universe at most once a day


def ensure_universe(n: int, max_age_h: float = UNIVERSE_MAX_AGE_H) -> list[str]:
    """Return the wallet universe, refreshing from the leaderboard only if stale.

    Keeping the ~30MB leaderboard download on a slow cadence (daily) while the map
    runs every ~10 min is the whole point of caching the universe to disk.
    """
    path = wallets.UNIVERSE_CSV
    fresh = os.path.exists(path) and (time.time() - os.path.getmtime(path)) < max_age_h * 3600
    if not fresh:
        print(f"[universe] refreshing top {n} wallets from leaderboard…")
        wallets.refresh_and_save(n=n)
    else:
        age_h = (time.time() - os.path.getmtime(path)) / 3600
        print(f"[universe] using cached universe (age {age_h:.1f}h)")
    return wallets.load_universe()


def validate_map(m, *, min_positions: int = 5) -> list[str]:
    """Lightweight sanity gate. Returns a list of warnings (empty = clean).

    We do NOT hard-fail on a thin sample, but we surface it: a map built from too
    few positions should be shown with lower confidence, not silently trusted.
    """
    warnings: list[str] = []
    if m.coverage["n_btc_positions"] < min_positions:
        warnings.append(
            f"only {int(m.coverage['n_btc_positions'])} positions kept "
            f"(<{min_positions}); treat signals as low-confidence"
        )
    if m.coverage["coverage_ratio"] < 0.05:
        warnings.append(f"coverage {m.coverage['coverage_ratio']:.1%} of OI is low")
    if not (0 <= m.cfi <= 100):
        warnings.append(f"CFI out of range: {m.cfi}")
    if m.market.mark_px <= 0:
        warnings.append("non-positive mark price")
    return warnings


def run(n_wallets: int = 300, *, render: bool = True) -> None:
    t0 = time.time()
    addresses = ensure_universe(n_wallets)

    client = HyperliquidClient()
    print(f"[extract+transform] building map over {len(addresses)} wallets…")
    m = build_liquidation_map(client, addresses, MapParams(), progress_every=100)

    warnings = validate_map(m)
    for w in warnings:
        print(f"[validate] WARNING: {w}")

    persist_all(m)
    print(f"[load] persisted snapshot/history/sqlite "
          f"(CFI={m.cfi}, regime={m.regime}, asym={m.asymmetry:+.3f}, "
          f"coverage={m.coverage['coverage_ratio']:.1%})")

    if render:
        # imported lazily so the data pipeline never depends on the viz stack
        from build_site import generate_site, publish_to_docs
        out = generate_site()
        published = publish_to_docs()   # copies index.html + data.json into docs/ (served by Pages)
        print(f"[render] site -> {out} · published -> {published}")

    print(f"[done] {time.time() - t0:.1f}s")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Run the liquidation-pressure pipeline once.")
    p.add_argument("--wallets", type=int, default=300)
    p.add_argument("--no-render", action="store_true", help="skip HTML site generation")
    args = p.parse_args()
    run(n_wallets=args.wallets, render=not args.no_render)
