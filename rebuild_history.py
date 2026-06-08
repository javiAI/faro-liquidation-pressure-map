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

from storage import (LEDGER_JSONL, METRICS_CSV, _persist_history, load_ledger,
                     load_metrics_history)

# repo-relative paths as git knows them
GIT_LEDGER = "data/cfi_history.jsonl"
GIT_CSV = "data/metrics_history.csv"


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
        recs = []
        for line in content.splitlines():
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        if recs:
            add(pd.DataFrame(recs))
    for content in _git_versions(GIT_CSV):
        try:
            add(pd.read_csv(io.StringIO(content)))
        except Exception:  # noqa: BLE001
            pass

    if not rows:
        return pd.DataFrame()
    return (pd.DataFrame(list(rows.values()))
            .drop_duplicates("timestamp").sort_values("timestamp")
            .reset_index(drop=True))


def main() -> None:
    ap = argparse.ArgumentParser(description="Reconstruct the CFI history from all sources.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    df = collect_all_points()
    if df.empty:
        print("No points found in any source.")
        return

    cur = len(load_metrics_history())
    print(f"Recovered {len(df)} unique points "
          f"({df['timestamp'].min()} → {df['timestamp'].max()}); "
          f"current CSV had {cur}.")
    if args.dry_run:
        print("[dry-run] no files written.")
        return
    _persist_history(df, METRICS_CSV, LEDGER_JSONL)
    print(f"Rewrote {METRICS_CSV} and {LEDGER_JSONL} from the union.")


if __name__ == "__main__":
    main()
