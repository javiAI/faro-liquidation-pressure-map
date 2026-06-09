"""
storage.py — the storage / output-table layer for the PoC.

We persist three things, with intentionally different formats matched to use:

  1. data/latest_snapshot.json   — the current map (market + signals + histogram).
       Small, git-friendly, read directly by the visualization / site. This is the
       "agent-facing / product-facing" artifact: everything a chart or an LLM needs
       in one place, with provenance + freshness baked in.

  2. data/metrics_history.csv    — one row per run = the forward-accumulating
       time-series of the Cascade Fragility Index and friends. Append-only,
       git-friendly small diffs. This is what makes regime calibration possible.

  3. data/warehouse.sqlite       — a relational mirror with explicit table schemas
       (snapshot header / histogram / positions). This stands in for the production
       warehouse table design; it shows how the data would be modelled for SQL
       access and reconciliation, without dragging in a real warehouse.

Production note: in a real deployment (2) and (3) would be warehouse tables
(e.g. partitioned parquet on object storage + a query engine), and (1) would be a
materialized "latest" view. The shapes here are deliberately the same.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

import pandas as pd

from liqmap.liquidation_map import LiquidationMap
from liqmap.paths import DATA_DIR   # repo-root data/ dir (robust to the src/ layout)

LATEST_SNAPSHOT_JSON = os.path.join(DATA_DIR, "latest_snapshot.json")
METRICS_CSV = os.path.join(DATA_DIR, "metrics_history.csv")
LEDGER_JSONL = os.path.join(DATA_DIR, "cfi_history.jsonl")  # append-only canonical store
MAP_HISTORY_JSONL = os.path.join(DATA_DIR, "map_history.jsonl")  # per-snapshot histograms (heatmap)
SQLITE_DB = os.path.join(DATA_DIR, "warehouse.sqlite")


# ---------------------------------------------------------------- serialization
def map_to_dict(m: LiquidationMap) -> dict[str, Any]:
    """Flatten a LiquidationMap into a JSON-serializable snapshot dict.

    Includes provenance + freshness fields so any consumer (chart, agent, audit)
    can judge trust without going back to the pipeline.
    """
    s = m.summary()
    return {
        "schema_version": 1,
        "provenance": {
            "source": "Hyperliquid public API (info + leaderboard feed)",
            "method": "aggregate open BTC perp positions of top-activity wallets",
            "generated_at": m.timestamp,
            "coverage_ratio_vs_oi": m.coverage["coverage_ratio"],
            "caveat": ("Approximate market coverage from a wallet sample; "
                       "not the full order book. liquidationPx is exchange-provided."),
        },
        "market": {
            "coin": m.market.coin,
            "mark_px": m.market.mark_px,
            "oracle_px": m.market.oracle_px,
            "funding_hourly": m.market.funding_hourly,
            "oi_btc": m.market.open_interest_coin,
            "oi_usd": m.market.open_interest_usd,
        },
        "signals": {
            "cfi": m.cfi,
            "regime": m.regime,
            "asymmetry": m.asymmetry,
            "long_pressure": m.long_pressure,
            "short_pressure": m.short_pressure,
            "near_band_usd": m.near_band_usd,
        },
        "coverage": m.coverage,
        "quality": m.quality,
        "histogram": m.histogram.to_dict(orient="records"),
        "summary_row": s,
    }


def write_latest_snapshot(m: LiquidationMap, path: str = LATEST_SNAPSHOT_JSON) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(map_to_dict(m), f, indent=2)


def _atomic_write(path: str, text: str) -> None:
    """Write via a temp file + rename so a crash mid-write can't truncate the target."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_ledger(m: LiquidationMap, path: str = LEDGER_JSONL) -> None:
    """Append one immutable JSON line to the CFI ledger. Append-only = loss-resistant:
    existing points are never rewritten, so a bad run cannot erase history."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(m.summary()) + "\n")


def iter_json_lines(src: Any) -> list[dict]:
    """Parse JSONL from any iterable of strings, skipping blank/corrupt lines.

    One definition of "read append-only JSONL tolerantly", shared by the ledger, the
    map history, and rebuild_history's git-replay — so the skip-corrupt rule can't drift.
    """
    out: list[dict] = []
    for line in src:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _dedup_by_timestamp(df: pd.DataFrame, keep: str = "last") -> pd.DataFrame:
    """Canonical history ordering: one row per timestamp, time-sorted, fresh index."""
    return (df.drop_duplicates("timestamp", keep=keep)
              .sort_values("timestamp").reset_index(drop=True))


def load_ledger(path: str = LEDGER_JSONL) -> pd.DataFrame:
    """Read the ledger, skipping any corrupt line (a damaged line never loses the rest)."""
    if not os.path.exists(path):
        return pd.DataFrame()
    with open(path) as f:
        rows = iter_json_lines(f)
    return _dedup_by_timestamp(pd.DataFrame(rows)) if rows else pd.DataFrame()


def _persist_history(df: pd.DataFrame, csv: str = METRICS_CSV,
                     jsonl: str = LEDGER_JSONL) -> pd.DataFrame:
    """Atomically write the deduped/sorted history to BOTH the CSV view and the ledger."""
    df = _dedup_by_timestamp(df)
    _atomic_write(csv, df.to_csv(index=False))
    lines = "".join(
        json.dumps({k: (None if pd.isna(v) else v) for k, v in rec.items()}) + "\n"
        for rec in df.to_dict("records")
    )
    _atomic_write(jsonl, lines)
    return df


def append_metrics_history(m: LiquidationMap, path: str = METRICS_CSV) -> pd.DataFrame:
    """Persist this run's point with UNION semantics so history can only grow.

    1) append the point to the append-only ledger first (immutable record);
    2) rebuild the CSV/ledger as the UNION of (ledger ∪ existing CSV ∪ this row),
       de-duped by timestamp — we never read-then-overwrite, so a stale or empty
       read can add nothing but can never *drop* an existing point.

    Combined with git history (every push snapshots both files) and rebuild_history.py,
    the full series is reconstructable from multiple independent sources.
    """
    append_ledger(m)
    frames = [load_ledger(), pd.DataFrame([m.summary()])]
    if os.path.exists(path):
        try:
            frames.append(pd.read_csv(path))
        except Exception:  # noqa: BLE001 - a damaged CSV must not block the run
            pass
    merged = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    return _persist_history(merged, path)


def load_metrics_history(path: str = METRICS_CSV) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def _nonzero_bucket(r: Any) -> bool:
    """A price bucket worth persisting: it carries some long or short liquidable notional."""
    return r["long_notional"] > 0 or r["short_notional"] > 0


def compact_histogram(rows: Any) -> list[list[float]]:
    """The compact map-history bucket form: non-zero [price, long, short], rounded.

    One definition of the serialization shape, shared by append_map_history and the
    git-replay in rebuild_history (which reads it back from committed snapshots).
    `rows` is any iterable of mappings with price_mid / long_notional / short_notional.
    """
    return [
        [round(float(r["price_mid"]), 1), round(float(r["long_notional"])),
         round(float(r["short_notional"]))]
        for r in rows
        if _nonzero_bucket(r)
    ]


def append_map_history(m: LiquidationMap, path: str = MAP_HISTORY_JSONL) -> None:
    """Append this snapshot's (compact) liquidation histogram for the time-heatmap.

    Pure append-only: one line per run holding the timestamp, mark price, and the
    non-zero price buckets as [price, long_notional, short_notional]. Existing lines
    are never rewritten, so the per-snapshot map history is loss-resistant by
    construction (and git history is a second copy).
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rec = {"timestamp": m.timestamp, "mark": m.market.mark_px,
           "b": compact_histogram(m.histogram.to_dict("records"))}
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")


def load_map_history(path: str = MAP_HISTORY_JSONL) -> list[dict[str, Any]]:
    """Load per-snapshot histograms, de-duped by timestamp (keep last), time-sorted."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        seen = {d["timestamp"]: d for d in iter_json_lines(f) if "timestamp" in d}
    return [seen[k] for k in sorted(seen)]


# ------------------------------------------------------------ relational mirror
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS liq_map_snapshot (
    timestamp           TEXT PRIMARY KEY,   -- run time (UTC, ISO8601)
    coin                TEXT NOT NULL,
    mark_px             REAL NOT NULL,
    oracle_px           REAL,
    funding_hourly      REAL,
    oi_usd              REAL,
    cfi                 REAL NOT NULL,       -- Cascade Fragility Index 0-100
    regime              TEXT NOT NULL,       -- calm | elevated | fragile
    asymmetry           REAL NOT NULL,       -- -1..+1
    liq_within_2pct_usd  REAL,
    liq_within_5pct_usd  REAL,
    liq_within_10pct_usd REAL,
    n_wallets           INTEGER,
    n_positions         INTEGER,
    sampled_notional_usd REAL,
    coverage_ratio      REAL,
    n_null_liqpx        INTEGER,
    n_dust_filtered     INTEGER,
    n_far_filtered      INTEGER
);
CREATE TABLE IF NOT EXISTS liq_map_histogram (
    timestamp       TEXT NOT NULL,           -- FK -> liq_map_snapshot.timestamp
    price_mid       REAL NOT NULL,
    distance_pct    REAL NOT NULL,
    long_notional   REAL NOT NULL,
    short_notional  REAL NOT NULL,
    PRIMARY KEY (timestamp, price_mid)
);
"""


_SNAPSHOT_COLS = [
    "timestamp", "coin", "mark_px", "oracle_px", "funding_hourly", "oi_usd",
    "cfi", "regime", "asymmetry", "liq_within_2pct_usd", "liq_within_5pct_usd",
    "liq_within_10pct_usd", "n_wallets", "n_positions", "sampled_notional_usd",
    "coverage_ratio", "n_null_liqpx", "n_dust_filtered", "n_far_filtered",
]


def write_sqlite_from_snapshot(snap: dict[str, Any], path: str = SQLITE_DB) -> None:
    """Upsert the snapshot header + histogram rows into the relational mirror.

    Keyed on the snapshot dict (summary_row + histogram records) so a single code path
    serves both the in-process pipeline and the Airflow `load` task — which only has the
    re-read JSON — without forking the column list or the SQL.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    s, ts = snap["summary_row"], snap["summary_row"]["timestamp"]
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            f"INSERT OR REPLACE INTO liq_map_snapshot ({','.join(_SNAPSHOT_COLS)}) "
            f"VALUES ({','.join('?' for _ in _SNAPSHOT_COLS)})",
            [s[c] for c in _SNAPSHOT_COLS],
        )
        conn.execute("DELETE FROM liq_map_histogram WHERE timestamp = ?", (ts,))
        conn.executemany(
            "INSERT INTO liq_map_histogram "
            "(timestamp, price_mid, distance_pct, long_notional, short_notional) "
            "VALUES (?,?,?,?,?)",
            [(ts, h["price_mid"], h["distance_pct"], h["long_notional"], h["short_notional"])
             for h in snap["histogram"]
             if _nonzero_bucket(h)],
        )
        conn.commit()
    finally:
        conn.close()


def write_sqlite(m: LiquidationMap, path: str = SQLITE_DB) -> None:
    """Upsert one LiquidationMap into the relational mirror (delegates to the dict writer)."""
    write_sqlite_from_snapshot(map_to_dict(m), path)


def persist_all(m: LiquidationMap) -> None:
    """Write all artifacts for one run."""
    write_latest_snapshot(m)
    append_metrics_history(m)
    append_map_history(m)
    write_sqlite(m)
