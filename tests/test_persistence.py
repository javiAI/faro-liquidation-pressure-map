"""Unit tests for the storage layer (storage.py).

The memo's headline claim about persistence is that the history can only grow: a
stale or empty read can ADD a point but never DROP one, and a single corrupt line
never loses the rest. These pin exactly that.
"""
import pandas as pd

import storage
from storage import iter_json_lines, _dedup_by_timestamp


class FakeMap:
    """storage only needs `.summary()` -> a dict with a 'timestamp'."""
    def __init__(self, ts: str, cfi: float = 10.0):
        self._d = {"timestamp": ts, "cfi": cfi, "mark_px": 50000.0, "asymmetry": 0.0}

    def summary(self) -> dict:
        return dict(self._d)


def test_iter_json_lines_skips_corrupt_and_blank():
    lines = ['{"timestamp":"t1","cfi":1}', '', '   ', 'NOT json {oops', '{"timestamp":"t2","cfi":2}']
    out = iter_json_lines(lines)
    assert len(out) == 2 and {r["timestamp"] for r in out} == {"t1", "t2"}


def test_dedup_keeps_last_and_sorts_by_timestamp():
    df = pd.DataFrame([
        {"timestamp": "t2", "cfi": 20},
        {"timestamp": "t1", "cfi": 10},
        {"timestamp": "t2", "cfi": 99},   # later duplicate wins
    ])
    d = _dedup_by_timestamp(df)
    assert list(d["timestamp"]) == ["t1", "t2"]                 # time-sorted, one row per ts
    assert d.loc[d["timestamp"] == "t2", "cfi"].iloc[0] == 99   # keep="last"


def test_history_union_never_drops_a_point(tmp_path, monkeypatch):
    led = str(tmp_path / "ledger.jsonl")
    csv = str(tmp_path / "metrics.csv")
    # redirect every default path the public function touches into the tmp dir
    monkeypatch.setattr(storage.append_ledger, "__defaults__", (led,))
    monkeypatch.setattr(storage.load_ledger, "__defaults__", (led,))
    monkeypatch.setattr(storage._persist_history, "__defaults__", (csv, led))
    monkeypatch.setattr(storage.append_metrics_history, "__defaults__", (csv,))

    storage.append_metrics_history(FakeMap("2026-01-01T00:00:00+00:00", 10))
    df = storage.append_metrics_history(FakeMap("2026-01-01T00:10:00+00:00", 20))
    assert set(df["timestamp"]) == {"2026-01-01T00:00:00+00:00", "2026-01-01T00:10:00+00:00"}

    # Wipe the CSV view (simulate a stale / empty / half-written read). The next run must
    # still recover the earlier points from the append-only ledger and only ADD the new one.
    open(csv, "w").close()
    df = storage.append_metrics_history(FakeMap("2026-01-01T00:20:00+00:00", 30))
    assert set(df["timestamp"]) == {
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:10:00+00:00",
        "2026-01-01T00:20:00+00:00",
    }
