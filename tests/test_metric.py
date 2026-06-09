"""Unit tests for the metric core (liquidation_map.py).

These pin the behaviour the memo claims: the CFI is bounded and notional-weighted
(so near-mark dust can't spike it), the asymmetry follows proximity rather than raw
size, and the cleaning filters drop exactly the junk the real API returns.
"""
import math

from hl_client import MarketContext
from liquidation_map import (
    MapParams, compute_metrics, clean_positions, build_histogram, validate_map,
)

MARK = 50_000.0


def mkt(mark: float = MARK, oi_coin: float = 1_000.0) -> MarketContext:
    return MarketContext(coin="BTC", mark_px=mark, oracle_px=mark,
                         funding_hourly=0.0, open_interest_coin=oi_coin,
                         day_ntl_vlm=0.0, mid_px=mark)


def pos(addr: str, szi: float, liq, notional: float) -> dict:
    # szi > 0 -> long (liquidates below), szi < 0 -> short (liquidates above)
    return {"address": addr, "szi": szi, "liquidationPx": liq,
            "positionValue": notional, "leverage_value": 5.0}


# ----------------------------------------------------------------- CFI
def test_cfi_zero_when_no_positions():
    m = compute_metrics([], mkt(), 0)
    assert m.cfi == 0.0 and m.asymmetry == 0.0


def test_cfi_is_100_for_a_position_on_the_mark():
    # trigger == mark -> distance 0 -> proximity 1 -> CFI 100 (the upper bound)
    m = compute_metrics([pos("a", 1.0, MARK, 1_000_000)], mkt(), 1)
    assert abs(m.cfi - 100.0) < 1e-6


def test_cfi_falls_off_with_distance():
    # one position at d = tau (8%) -> CFI = 100 * exp(-1) ~= 36.79
    m = compute_metrics([pos("a", -1.0, MARK * 1.08, 1_000_000)], mkt(), 1,
                        MapParams(tau=0.08))
    assert abs(m.cfi - 100.0 * math.exp(-1)) < 0.5


def test_cfi_bounded_0_100():
    ps = [pos("a", 1.0, MARK * 0.98, 5e5),
          pos("b", -1.0, MARK * 1.20, 2e6),
          pos("c", 1.0, MARK * 0.60, 8e5)]
    m = compute_metrics(ps, mkt(), 3)
    assert 0.0 <= m.cfi <= 100.0


def test_near_mark_dust_does_not_dominate_cfi():
    # The reason for the exponential kernel: a small ($10k) position sitting ON the mark
    # must NOT drag CFI up against a huge ($10M) position sitting far away. With a raw 1/d
    # weight the near one would blow the index up; notional-weighting keeps it honest.
    near_small = pos("a", 1.0, MARK, 10_000)              # d=0, proximity 1, tiny
    far_big = pos("b", -1.0, MARK * 1.16, 10_000_000)     # d=16%, proximity exp(-2)
    m = compute_metrics([near_small, far_big], mkt(), 2, MapParams(tau=0.08))
    assert m.cfi < 25.0   # ~13.6, dominated by the big-but-far position, not the near dust


# ----------------------------------------------------------------- asymmetry
def test_asymmetry_bounded_and_balanced():
    ps = [pos("a", 1.0, MARK * 0.95, 1e6), pos("b", -1.0, MARK * 1.05, 1e6)]  # mirror image
    m = compute_metrics(ps, mkt(), 2)
    assert -1.0 <= m.asymmetry <= 1.0
    assert abs(m.asymmetry) < 1e-6


def test_asymmetry_sign_convention():
    shorts = compute_metrics([pos("a", -1.0, MARK * 1.05, 1e6)], mkt(), 1)
    longs = compute_metrics([pos("b", 1.0, MARK * 0.95, 1e6)], mkt(), 1)
    assert shorts.asymmetry > 0.99    # all short fuel -> short-side (+1)
    assert longs.asymmetry < -0.99    # all long fuel -> long-side (-1)


def test_asymmetry_follows_proximity_not_size():
    # Longs carry MORE notional but sit FAR; shorts carry less but sit NEAR.
    # Proximity-weighting should still tilt the asymmetry to the (near) short side.
    long_far = pos("a", 1.0, MARK * 0.80, 5_000_000)      # d=20%, big
    short_near = pos("b", -1.0, MARK * 1.02, 1_000_000)   # d=2%, smaller
    m = compute_metrics([long_far, short_near], mkt(), 2, MapParams(tau=0.08))
    assert m.asymmetry > 0   # short-side, despite the longs being larger


# ----------------------------------------------------------------- cleaning filters
def test_filters_drop_null_dust_and_far():
    ps = [
        pos("null", 1.0, None, 1e6),          # cross-margin null liqPx -> dropped
        pos("dust", 1.0, MARK * 0.99, 5_000), # < $10k dust -> dropped
        pos("far", -1.0, MARK * 1.8, 1e6),    # 80% away -> dropped
        pos("ok", 1.0, MARK * 0.95, 1e6),     # kept
    ]
    df, q = clean_positions(ps, MARK, MapParams())
    assert q["n_null_liqpx"] == 1
    assert q["n_dust_filtered"] == 1
    assert q["n_far_filtered"] == 1
    assert len(df) == 1 and df.iloc[0]["address"] == "ok"


def test_garbage_liqpx_dropped_as_dust_before_distance():
    # Real-data anecdote: a $1 position reporting liqPx = $1.8e12 is removed by the dust
    # rule (notional < $10k), so the absurd price never reaches the distance/map logic.
    df, q = clean_positions([pos("g", 1.0, 1.8e12, 1.0)], MARK, MapParams())
    assert q["n_dust_filtered"] == 1 and q["n_far_filtered"] == 0 and df.empty


def test_kept_within_60_appears_on_the_60pct_map_not_the_30pct_one():
    # A position 50% from the mark is kept by the filter (within 60%). With the +/-60%
    # histogram it now lands on the map; under the old +/-30% range it would not have.
    p = pos("a", -1.0, MARK * 1.50, 1e6)   # 50% above the mark
    df, q = clean_positions([p], MARK, MapParams())
    assert q["n_far_filtered"] == 0 and len(df) == 1
    assert build_histogram(df, MARK, MapParams())["short_notional"].sum() > 0
    assert build_histogram(df, MARK, MapParams(hist_range_pct=0.30))["short_notional"].sum() == 0


def test_validate_map_flags_thin_sample():
    m = compute_metrics([pos("a", 1.0, MARK * 0.99, 1e6)], mkt(), 1)  # 1 position
    assert any("low-confidence" in w for w in validate_map(m))
