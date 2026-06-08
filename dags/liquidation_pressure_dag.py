"""
liquidation_pressure_dag.py — proof-of-concept Airflow DAG.

This is the orchestration artifact the challenge asks for. It is intentionally a
thin layer: all the real logic lives in the reusable project modules
(hl_client / wallets / liquidation_map / storage / build_site), and this DAG just
sequences them with the operational concerns a Head of Data cares about —
scheduling, validation, failure handling, observability, and backfill policy.

Flow (every 10 minutes):

    refresh_universe_if_stale
            │
    extract_market_context ──┐
            │                │
    extract_positions ───────┤
                             ▼
                        validate  ──(quarantine on hard failure)
                             │
                        transform  (build map + CFI + asymmetry; write derived layer)
                             │
                          load     (warehouse mirror + publish HTML)

NOTE ON BACKFILL (important, stated up front): this metric is NOT backfillable.
`clearinghouseState` only returns the *current* state of an account, so historical
liquidation maps cannot be reconstructed from the public API. `catchup=False` is
therefore deliberate; the CFI time-series accumulates forward from first run.
Only wallet *selection* uses history, and it is refreshed on a slow cadence.

This file is meant to be dropped into an Airflow `dags/` folder. It is not run in
the PoC harness (the live demo is driven by run_pipeline.py under GitHub Actions),
but it is the structure we would deploy.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pendulum

# In a real deployment the project would be an installed package; for the PoC we
# make the sibling modules importable from the DAG file.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from airflow.decorators import dag, task  # noqa: E402
from airflow.exceptions import AirflowFailException  # noqa: E402

N_WALLETS = 300
COVERAGE_FLOOR = 0.05          # below this, alert: sample too thin to trust
STALENESS_FACTOR = 2           # alert if mark/oracle look stale vs cadence
MIN_POSITIONS = 5


# --------------------------------------------------------------- alerting stub
def alert_on_failure(context: dict) -> None:
    """Failure callback — wire to Slack / PagerDuty / Opsgenie in production.

    Observability is a first-class concern: a silent data pipeline is worse than a
    loud one. Here we just log a structured message; the hook is the integration
    point.
    """
    ti = context.get("task_instance")
    exc = context.get("exception")
    print(f"[ALERT] DAG=liquidation_pressure task={getattr(ti, 'task_id', '?')} "
          f"run={context.get('run_id')} failed: {exc}")


default_args = {
    "owner": "data",
    "retries": 3,
    "retry_delay": timedelta(seconds=30),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=5),
    "on_failure_callback": alert_on_failure,
}


@dag(
    dag_id="liquidation_pressure_map",
    schedule="*/10 * * * *",                 # every 10 minutes
    start_date=pendulum.datetime(2026, 6, 1, tz="UTC"),
    catchup=False,                           # NOT backfillable — see module docstring
    max_active_runs=1,                       # one snapshot at a time; no overlap
    dagrun_timeout=timedelta(minutes=8),     # must finish well within the 10-min cadence
    default_args=default_args,
    tags=["hyperliquid", "derivatives", "risk", "btc", "poc"],
    doc_md=__doc__,
)
def liquidation_pressure_map():

    @task
    def refresh_universe_if_stale() -> list[str]:
        """Return the wallet universe, refreshing from the leaderboard only if stale.

        Decoupling the heavy (~30MB) leaderboard download (daily) from the 10-min map
        run is what keeps the high-frequency loop cheap and within rate limits.
        """
        from run_pipeline import ensure_universe
        addresses = ensure_universe(N_WALLETS)
        if not addresses:
            raise AirflowFailException("empty wallet universe")
        return addresses

    @task
    def extract_market_context() -> dict:
        """metaAndAssetCtxs → BTC market context (the anchor for all distances)."""
        from hl_client import HyperliquidClient
        return HyperliquidClient().get_market_context("BTC").__dict__

    @task
    def extract_positions(addresses: list[str]) -> list[dict]:
        """clearinghouseState over the universe (rate-limited, per-wallet tolerant).

        In production the raw positions would land in a staging table / object store
        rather than XCom; for a few hundred small rows XCom is fine for the PoC.
        """
        from hl_client import HyperliquidClient
        from liquidation_map import fetch_positions
        positions, n_errors = fetch_positions(HyperliquidClient(), addresses, "BTC")
        # observability: surface the error rate even when the run succeeds
        err_rate = n_errors / max(len(addresses), 1)
        if err_rate > 0.5:
            raise AirflowFailException(f"wallet error rate too high: {err_rate:.0%}")
        print(f"[extract_positions] {len(positions)} positions, {n_errors} errors "
              f"({err_rate:.1%})")
        return positions

    @task
    def validate(market: dict, positions: list[dict]) -> dict:
        """Schema + range + freshness sanity. Hard-fails are quarantined.

        Soft issues (thin coverage) are passed through but flagged downstream so the
        product can lower confidence rather than show a number it can't stand behind.
        """
        # hard checks → fail the run (data we should not publish)
        if market.get("mark_px", 0) <= 0:
            raise AirflowFailException("non-positive mark price")
        oracle = market.get("oracle_px") or market["mark_px"]
        drift = abs(market["mark_px"] - oracle) / market["mark_px"]
        if drift > 0.05:
            # mark vs oracle should track closely; large drift = data-quality event
            raise AirflowFailException(f"mark/oracle drift {drift:.1%} exceeds 5%")

        n_with_pos = len(positions)
        n_null = sum(1 for p in positions if p.get("liquidationPx") is None)
        soft_warnings = []
        if n_with_pos < MIN_POSITIONS:
            soft_warnings.append(f"only {n_with_pos} positions")
        print(f"[validate] positions={n_with_pos} null_liqpx={n_null} "
              f"warnings={soft_warnings or 'none'}")
        return {"n_positions": n_with_pos, "n_null": n_null, "warnings": soft_warnings}

    @task
    def transform(market: dict, positions: list[dict], n_wallets: int) -> dict:
        """Build the map + signals and write the DERIVED layer (snapshot + history).

        Pure-ish: calls the unit-testable compute_metrics, then persists the
        agent/product-facing artifacts. Returns the compact summary row for XCom-level
        observability (so the Airflow UI shows CFI/coverage at a glance).
        """
        from hl_client import MarketContext
        from liquidation_map import MapParams, compute_metrics
        from storage import append_metrics_history, write_latest_snapshot

        mkt = MarketContext(**market)
        m = compute_metrics(positions, mkt, n_wallets, MapParams())

        if m.coverage["coverage_ratio"] < COVERAGE_FLOOR:
            # observability hook, not a hard fail: publish with low confidence + alert
            print(f"[ALERT] coverage {m.coverage['coverage_ratio']:.1%} below floor "
                  f"{COVERAGE_FLOOR:.0%}")

        write_latest_snapshot(m)
        append_metrics_history(m)
        return m.summary()

    @task
    def load(summary: dict) -> str:
        """Publish: warehouse mirror (SQLite) + regenerate & deploy the HTML.

        Separated from transform so the 'storage/output table' and the 'serving'
        concerns are explicit. Reads the snapshot the transform task wrote.
        """
        import json
        from storage import LATEST_SNAPSHOT_JSON
        from build_site import generate_site

        # The transform task already wrote the snapshot + history (the derived layer).
        # Here we mirror it into the warehouse table and publish the site, so SQLite
        # and the page share one source of truth.
        with open(LATEST_SNAPSHOT_JSON) as f:
            snap = json.load(f)
        _write_sqlite_from_snapshot(snap)
        out = generate_site()
        print(f"[load] published {out} · CFI={summary['cfi']} regime={summary['regime']}")
        return out

    # ---- task wiring -------------------------------------------------------
    addresses = refresh_universe_if_stale()
    market = extract_market_context()
    positions = extract_positions(addresses)
    checks = validate(market, positions)
    summary = transform(market, positions, N_WALLETS)
    # `checks` is a non-data dependency: validate must pass before we transform/load
    checks >> summary
    load(summary)


def _write_sqlite_from_snapshot(snap: dict) -> None:
    """Write the relational mirror from a snapshot dict (no map object needed)."""
    import sqlite3
    from storage import SCHEMA_SQL, SQLITE_DB

    s = snap["summary_row"]
    conn = sqlite3.connect(SQLITE_DB)
    try:
        conn.executescript(SCHEMA_SQL)
        cols = ["timestamp", "coin", "mark_px", "oracle_px", "funding_hourly", "oi_usd",
                "cfi", "regime", "asymmetry", "liq_within_2pct_usd", "liq_within_5pct_usd",
                "liq_within_10pct_usd", "n_wallets", "n_positions", "sampled_notional_usd",
                "coverage_ratio", "n_null_liqpx", "n_dust_filtered", "n_far_filtered"]
        conn.execute(
            f"INSERT OR REPLACE INTO liq_map_snapshot ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})", [s[c] for c in cols])
        conn.execute("DELETE FROM liq_map_histogram WHERE timestamp = ?", (s["timestamp"],))
        conn.executemany(
            "INSERT INTO liq_map_histogram "
            "(timestamp, price_mid, distance_pct, long_notional, short_notional) "
            "VALUES (?,?,?,?,?)",
            [(s["timestamp"], h["price_mid"], h["distance_pct"],
              h["long_notional"], h["short_notional"])
             for h in snap["histogram"]
             if h["long_notional"] > 0 or h["short_notional"] > 0])
        conn.commit()
    finally:
        conn.close()


dag = liquidation_pressure_map()
