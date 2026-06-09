"""
rebuild_history.py — reconstruct the full CFI time-series from every source.

A point survives if it exists in ANY of these independent records, unioned by
timestamp (so nothing is lost as long as it was ever persisted):

  1. the live append-only ledger        data/cfi_history.jsonl
  2. the current CSV view               data/metrics_history.csv
  3. EVERY version of (1) and (2) ever committed to git history (all branches)

The git-history source is the strongest guarantee: every pipeline run commits both
files, so each point is snapshotted immutably in the repo's object database. Even if
the working files are deleted or corrupted, this rebuilds the complete series.

Usage:
    python rebuild_history.py            # reconstruct + atomically rewrite both files
    python rebuild_history.py --dry-run  # just report what would be recovered
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess

import pandas as pd

from liqmap.storage import (LEDGER_JSONL, MAP_HISTORY_JSONL, METRICS_CSV, _dedup_by_timestamp,
                     _persist_history, compact_histogram, iter_json_lines, load_ledger,
                     load_map_history, load_metrics_history)

# repo-relative paths as git knows them
GIT_LEDGER = "data/cfi_history.jsonl"
GIT_CSV = "data/metrics_history.csv"
GIT_MAP = "data/map_history.jsonl"
GIT_SNAPSHOT = "data/latest_snapshot.json"


def _git_versions(path: str) -> list[str]:
    """Return the file content at every commit (all branches) that touched `path`."""
    out = subprocess.run(
        ["git", "log", "--all", "--format=%H", "--", path],
        capture_output=True, text=True,
    )
    contents = []
    for commit in out.stdout.split():
        show = subprocess.run(["git", "show", f"{commit}:{path}"],
                              capture_output=True, text=True)
        if show.returncode == 0 and show.stdout.strip():
            contents.append(show.stdout)
    return contents


def collect_all_points() -> pd.DataFrame:
    """Union every point from every source, keyed by timestamp."""
    rows: dict[str, dict] = {}

    def add(df: pd.DataFrame) -> None:
        for rec in df.to_dict("records"):
            ts = rec.get("timestamp")
            if ts and ts not in rows:        # first source wins; we only ever ADD
                rows[ts] = rec

    # live working files first (most complete / most recent schema)
    add(load_ledger())
    add(load_metrics_history())

    # then walk git history of both files
    for content in _git_versions(GIT_LEDGER):
        recs = iter_json_lines(content.splitlines())
        if recs:
            add(pd.DataFrame(recs))
    for content in _git_versions(GIT_CSV):
        try:
            add(pd.read_csv(io.StringIO(content)))
        except Exception:  # noqa: BLE001
            pass

    if not rows:
        return pd.DataFrame()
    return _dedup_by_timestamp(pd.DataFrame(list(rows.values())))


def _snap_to_map_line(content: str) -> dict | None:
    """Turn a committed latest_snapshot.json into a compact map_history record."""
    try:
        snap = json.loads(content)
        return {"timestamp": snap["provenance"]["generated_at"],
                "mark": snap["market"]["mark_px"],
                "b": compact_histogram(snap.get("histogram", []))}
    except Exception:  # noqa: BLE001
        return None


def collect_map_history() -> list[dict]:
    """Union per-snapshot histograms from the live file + git history of both the map
    ledger and every committed latest_snapshot.json."""
    rows: dict[str, dict] = {}
    for rec in load_map_history():
        rows.setdefault(rec["timestamp"], rec)
    for content in _git_versions(GIT_MAP):
        for d in iter_json_lines(content.splitlines()):
            if "timestamp" in d:
                rows.setdefault(d["timestamp"], d)
    for content in _git_versions(GIT_SNAPSHOT):
        rec = _snap_to_map_line(content)
        if rec:
            rows.setdefault(rec["timestamp"], rec)
    return [rows[k] for k in sorted(rows)]


def main() -> None:
    ap = argparse.ArgumentParser(description="Reconstruct the CFI + map history from all sources.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    df = collect_all_points()
    maps = collect_map_history()
    if df.empty and not maps:
        print("No points found in any source.")
        return

    cur = len(load_metrics_history())
    print(f"Recovered {len(df)} CFI points "
          f"({df['timestamp'].min()} → {df['timestamp'].max()}); current CSV had {cur}.")
    print(f"Recovered {len(maps)} per-snapshot histograms for the heatmap.")
    if args.dry_run:
        print("[dry-run] no files written.")
        return
    _persist_history(df, METRICS_CSV, LEDGER_JSONL)
    with open(MAP_HISTORY_JSONL, "w") as f:
        for rec in maps:
            f.write(json.dumps(rec) + "\n")
    print(f"Rewrote {METRICS_CSV}, {LEDGER_JSONL} and {MAP_HISTORY_JSONL} from the union.")


if __name__ == "__main__":
    main()
