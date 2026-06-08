"""
hl_client.py — a small, defensive, rate-limited client for the Hyperliquid public API.

Single place that talks to the network. Everything else imports from here so retry /
rate-limit / parsing logic lives in exactly one spot.

Rate limiting (verified against the docs): the /info endpoint shares an aggregated
budget of 1200 request-weight per minute per IP. `clearinghouseState` costs weight 2
(→ 600 calls/min ceiling); `metaAndAssetCtxs` costs 20. We stay well under that with a
thread-safe token bucket (default ~7.5 calls/s ≈ 450/min ≈ 75% of the ceiling) so we
can fetch many wallets concurrently without ever tripping the limit. The leaderboard
lives on a different host and does not count against this budget.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests

INFO_URL = "https://api.hyperliquid.xyz/info"
LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
DEFAULT_COIN = "BTC"

# clearinghouseState weight = 2, limit = 1200/min → 600 calls/min ceiling.
# Default to ~75% of that, expressed as calls/second.
DEFAULT_RATE_PER_SEC = 7.5


@dataclass
class MarketContext:
    """BTC market context snapshot from metaAndAssetCtxs.

    `mark_px` is the price Hyperliquid uses as the liquidation reference, the anchor
    for every distance-to-liquidation computation downstream.
    """

    coin: str
    mark_px: float
    oracle_px: float
    funding_hourly: float
    open_interest_coin: float
    day_ntl_vlm: float
    mid_px: float | None

    @property
    def open_interest_usd(self) -> float:
        return self.open_interest_coin * self.mark_px


class _TokenBucket:
    """A simple thread-safe token bucket: at most `rate` permits granted per second."""

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = float(rate)
        self.capacity = float(capacity if capacity is not None else max(rate, 1.0))
        self.tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
                self._last = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait = (1.0 - self.tokens) / self.rate
            time.sleep(wait)


class HyperliquidClient:
    """Thin, rate-limited, thread-safe wrapper around the public Hyperliquid endpoints."""

    def __init__(
        self,
        *,
        rate_per_sec: float = DEFAULT_RATE_PER_SEC,
        retries: int = 4,
        backoff: float = 1.6,
        timeout_s: float = 10.0,
    ) -> None:
        self._limiter = _TokenBucket(rate_per_sec)
        self._retries = retries
        self._backoff = backoff
        self._timeout_s = timeout_s
        self._local = threading.local()      # one requests.Session per worker thread

    # ------------------------------------------------------------------ internal
    def _session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers.update({"Content-Type": "application/json"})
            adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4)
            s.mount("https://", adapter)
            self._local.session = s
        return s

    def _post_info(self, payload: dict[str, Any]) -> Any:
        """POST to /info with global rate-limiting + retry/backoff. Raises on final failure."""
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            self._limiter.acquire()
            try:
                resp = self._session().post(
                    INFO_URL, data=json.dumps(payload), timeout=self._timeout_s
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise requests.HTTPError(f"status {resp.status_code}")
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001 - retry on anything transient
                last_exc = exc
                time.sleep(self._backoff ** attempt)
        raise RuntimeError(
            f"/info {payload.get('type')} failed after {self._retries} retries: {last_exc}"
        ) from last_exc

    # -------------------------------------------------------------------- public
    def get_market_context(self, coin: str = DEFAULT_COIN) -> MarketContext:
        """Return market context for `coin` from metaAndAssetCtxs."""
        meta, asset_ctxs = self._post_info({"type": "metaAndAssetCtxs"})
        universe = meta["universe"]
        idx = next(i for i, a in enumerate(universe) if a["name"] == coin)
        ctx = asset_ctxs[idx]
        return MarketContext(
            coin=coin,
            mark_px=float(ctx["markPx"]),
            oracle_px=float(ctx["oraclePx"]),
            funding_hourly=float(ctx["funding"]),
            open_interest_coin=float(ctx["openInterest"]),
            day_ntl_vlm=float(ctx["dayNtlVlm"]),
            mid_px=float(ctx["midPx"]) if ctx.get("midPx") else None,
        )

    def get_position(self, address: str, coin: str = DEFAULT_COIN) -> dict[str, Any] | None:
        """Return the raw `coin` perp position for one wallet, or None. Thread-safe."""
        state = self._post_info({"type": "clearinghouseState", "user": address})
        for ap in state.get("assetPositions", []):
            pos = ap.get("position", {})
            if pos.get("coin") != coin:
                continue
            return {
                "address": address,
                "coin": pos["coin"],
                "szi": float(pos["szi"]),
                "liquidationPx": (
                    float(pos["liquidationPx"]) if pos.get("liquidationPx") else None
                ),
                "positionValue": float(pos["positionValue"]),
                "entryPx": float(pos["entryPx"]) if pos.get("entryPx") else None,
                "leverage_value": (pos.get("leverage") or {}).get("value"),
                "leverage_type": (pos.get("leverage") or {}).get("type"),
                "marginUsed": float(pos["marginUsed"]) if pos.get("marginUsed") else None,
                "unrealizedPnl": (
                    float(pos["unrealizedPnl"]) if pos.get("unrealizedPnl") else None
                ),
            }
        return None

    def fetch_leaderboard(self) -> list[dict[str, Any]]:
        """Fetch the raw leaderboard rows (undocumented HL frontend feed, separate host).

        Each row: ethAddress, accountValue, displayName, windowPerformances
        (list of [window, {pnl, roi, vlm}] for day/week/month/allTime).
        ~30MB payload, so callers should fetch this rarely (e.g. on universe refresh).
        """
        resp = self._session().get(LEADERBOARD_URL, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data["leaderboardRows"] if isinstance(data, dict) else data
