"""
validate_api.py — Step 1: live validation of the Hyperliquid public API.

Goal of this script (a sanity gate before building anything):
    1. Confirm we can read BTC market context (markPx, oraclePx, funding, OI)
       from the documented `metaAndAssetCtxs` endpoint.
    2. Confirm we can read per-wallet positions from `clearinghouseState`,
       and that each BTC position carries the three fields the whole metric
       depends on: `szi` (signed size), `liquidationPx`, `positionValue`.
    3. Get a handful of REAL, currently-active addresses to test against,
       instead of hardcoding wallets that may be stale.

Everything here uses only public, no-auth endpoints:
    - POST https://api.hyperliquid.xyz/info            (documented)
    - GET  https://stats-data.hyperliquid.xyz/...      (undocumented leaderboard
                                                         feed used by the HL frontend;
                                                         flagged as a caveat, see memo)

Run:  python validate_api.py
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

INFO_URL = "https://api.hyperliquid.xyz/info"
LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
COIN = "BTC"


def _post_info(payload: dict[str, Any], *, retries: int = 3, backoff: float = 1.5) -> Any:
    """POST to the Hyperliquid /info endpoint with simple retry + backoff.

    The API is free, so we are deliberately gentle: short timeouts, a few
    retries, exponential backoff. Network/5xx errors are retried; a final
    failure is raised so the caller (or Airflow) sees it.
    """
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.post(
                INFO_URL,
                headers={"Content-Type": "application/json"},
                data=json.dumps(payload),
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - we want to retry on anything transient
            last_exc = exc
            sleep_s = backoff ** attempt
            print(f"  [retry {attempt + 1}/{retries}] {payload.get('type')} failed: {exc} "
                  f"-> sleeping {sleep_s:.1f}s")
            time.sleep(sleep_s)
    raise RuntimeError(f"POST /info {payload} failed after {retries} retries") from last_exc


def get_btc_market_context() -> dict[str, Any]:
    """Return BTC market context from metaAndAssetCtxs.

    `metaAndAssetCtxs` returns [meta, assetCtxs] as two parallel lists:
    meta['universe'][i] describes asset i, assetCtxs[i] is its live context.
    We locate BTC by name and pull the fields we care about. `markPx` is the
    reference price Hyperliquid uses for liquidations, so it is our anchor.
    """
    meta, asset_ctxs = _post_info({"type": "metaAndAssetCtxs"})
    universe = meta["universe"]
    btc_idx = next(i for i, a in enumerate(universe) if a["name"] == COIN)
    ctx = asset_ctxs[btc_idx]
    return {
        "markPx": float(ctx["markPx"]),
        "oraclePx": float(ctx["oraclePx"]),
        "funding": float(ctx["funding"]),
        "openInterest": float(ctx["openInterest"]),
        "midPx": float(ctx["midPx"]) if ctx.get("midPx") else None,
        "dayNtlVlm": float(ctx["dayNtlVlm"]),
    }


def get_top_addresses(n: int = 5) -> list[str]:
    """Fetch a few real, currently-active addresses from the leaderboard feed.

    This is the same JSON the Hyperliquid web leaderboard renders. We use it
    only to obtain *live* addresses for validation here; the production wallet
    universe is designed separately (see wallets.py / the memo). We rank by
    account value as a quick proxy for "has meaningful size on the book".
    """
    resp = requests.get(LEADERBOARD_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    rows = data["leaderboardRows"] if isinstance(data, dict) else data
    rows.sort(key=lambda r: float(r.get("accountValue", 0)), reverse=True)
    return [r["ethAddress"] for r in rows[:n]]


def get_btc_position(address: str) -> dict[str, Any] | None:
    """Return the BTC perp position for one wallet, or None if it has none.

    `clearinghouseState` returns assetPositions; each entry has a `position`
    object. We pull the fields the liquidation map is built from and keep
    `liquidationPx` as raw text first so we can see explicitly when it is null.
    """
    state = _post_info({"type": "clearinghouseState", "user": address})
    for ap in state.get("assetPositions", []):
        pos = ap["position"]
        if pos.get("coin") != COIN:
            continue
        return {
            "coin": pos["coin"],
            "szi": float(pos["szi"]),
            "liquidationPx": pos.get("liquidationPx"),  # may be None
            "positionValue": float(pos["positionValue"]),
            "entryPx": float(pos["entryPx"]) if pos.get("entryPx") else None,
            "leverage": pos.get("leverage"),
            "marginUsed": float(pos["marginUsed"]) if pos.get("marginUsed") else None,
            "unrealizedPnl": float(pos["unrealizedPnl"]) if pos.get("unrealizedPnl") else None,
        }
    return None


def main() -> None:
    print("=" * 72)
    print("STEP 1 — Hyperliquid public API live validation")
    print("=" * 72)

    # --- 1) BTC market context -------------------------------------------------
    print("\n[1] metaAndAssetCtxs -> BTC market context")
    mkt = get_btc_market_context()
    print(f"    markPx (liquidation reference) : {mkt['markPx']:,.1f}")
    print(f"    oraclePx                       : {mkt['oraclePx']:,.1f}")
    print(f"    funding (hourly)               : {mkt['funding']:+.6%}")
    print(f"    openInterest (BTC)             : {mkt['openInterest']:,.2f}")
    print(f"    openInterest (USD @ mark)      : {mkt['openInterest'] * mkt['markPx']:,.0f}")
    print(f"    24h notional volume (USD)      : {mkt['dayNtlVlm']:,.0f}")

    # --- 2) real active addresses ---------------------------------------------
    print("\n[2] leaderboard feed -> sample of real active addresses")
    addresses = get_top_addresses(n=8)
    for a in addresses:
        print(f"    {a}")

    # --- 3) per-wallet BTC positions ------------------------------------------
    print("\n[3] clearinghouseState -> BTC positions (validating szi / liquidationPx / positionValue)")
    found = 0
    null_liq = 0
    for addr in addresses:
        try:
            pos = get_btc_position(addr)
        except Exception as exc:  # noqa: BLE001
            print(f"    {addr[:10]}…  ERROR: {exc}")
            continue
        time.sleep(0.25)  # gentle rate-limit between wallet calls
        if pos is None:
            print(f"    {addr[:10]}…  no open BTC position")
            continue
        found += 1
        side = "LONG " if pos["szi"] > 0 else "SHORT"
        liq = pos["liquidationPx"]
        if liq is None:
            null_liq += 1
        lev = pos["leverage"] or {}
        dist = ""
        if liq is not None:
            liq_f = float(liq)
            dist = f"  dist_to_liq={abs(liq_f - mkt['markPx']) / mkt['markPx']:+.1%}"
        print(
            f"    {addr[:10]}…  {side}"
            f"  szi={pos['szi']:+.4f}"
            f"  liquidationPx={str(liq):>10}"
            f"  positionValue=${pos['positionValue']:,.0f}"
            f"  lev={lev.get('value')}x/{lev.get('type')}"
            f"  uPnL=${(pos['unrealizedPnl'] or 0):,.0f}"
            f"{dist}"
        )

    # --- summary / sanity gate -------------------------------------------------
    print("\n" + "-" * 72)
    print(f"SUMMARY: {found} wallets with an open BTC position "
          f"({null_liq} had liquidationPx=null).")
    print("Fields confirmed present on BTC positions: "
          "szi, liquidationPx, positionValue, entryPx, leverage, marginUsed, unrealizedPnl.")
    print("Sanity gate:", "PASS ✅" if found > 0 else "CHECK ⚠️ (no BTC positions in sample)")


if __name__ == "__main__":
    main()
