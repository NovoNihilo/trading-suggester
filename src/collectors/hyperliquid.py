"""Hyperliquid read-only data collector.

Batches API calls to minimize rate-limit exposure.
Stores raw snapshots in SQLite for feature computation.

V2: Added daily, 4h, 1h candles + funding history tracking.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from src.config import HL_INFO_URL, ASSETS

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _post(client: httpx.Client, payload: dict) -> dict | list:
    resp = client.post(HL_INFO_URL, json=payload, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_all_mids(client: httpx.Client) -> dict[str, float]:
    data = _post(client, {"type": "allMids"})
    return {k: float(v) for k, v in data.items()}


def fetch_l2_book(client: httpx.Client, coin: str) -> dict:
    return _post(client, {"type": "l2Book", "coin": coin, "nSigFigs": 5})


def fetch_meta_and_asset_ctxs(client: httpx.Client) -> tuple[dict, list[dict]]:
    data = _post(client, {"type": "metaAndAssetCtxs"})
    return data[0], data[1]


def fetch_candles(
    client: httpx.Client, coin: str, interval: str, lookback_ms: int
) -> list[dict]:
    """Fetch candles for a given interval and lookback period."""
    end_time = int(time.time() * 1000)
    start_time = end_time - lookback_ms
    return _post(
        client,
        {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_time,
                "endTime": end_time,
            },
        },
    )


def collect_snapshot(client: httpx.Client) -> dict:
    """Collect one full snapshot for all tracked assets.

    API calls per cycle:
    1. metaAndAssetCtxs (1 call) — funding, OI, mark for ALL assets
    2. allMids (1 call) — mid prices for ALL assets
    3. Per asset (5 calls each):
       - l2Book
       - 15m candles (20 bars = 5h)
       - 1h candles (24 bars = 24h)
       - 4h candles (30 bars = 5 days)
       - 1d candles (7 bars = 7 days)

    Total: 2 + 5*N calls. For BTC+ETH = 12 calls/60s.
    """
    ts = datetime.now(timezone.utc).isoformat()

    meta, asset_ctxs = fetch_meta_and_asset_ctxs(client)
    all_mids = fetch_all_mids(client)

    universe = meta.get("universe", [])
    sym_to_idx = {u["name"]: i for i, u in enumerate(universe)}

    snapshot = {"timestamp": ts, "assets": {}}

    for symbol in ASSETS:
        if symbol not in sym_to_idx:
            log.warning(f"Asset {symbol} not in HL universe, skipping")
            continue

        idx = sym_to_idx[symbol]
        ctx = asset_ctxs[idx]

        # L2 book
        try:
            book_raw = fetch_l2_book(client, symbol)
            book = _parse_book(book_raw)
        except Exception as e:
            log.warning(f"L2 book failed {symbol}: {e}")
            book = None

        # Candles at multiple timeframes
        candles = {}
        candle_configs = {
            "15m": (20, 20 * 15 * 60 * 1000),      # 20 bars = 5h
            "1h":  (24, 24 * 60 * 60 * 1000),       # 24 bars = 24h
            "4h":  (30, 30 * 4 * 60 * 60 * 1000),   # 30 bars = 5 days
            "1d":  (7,  7 * 24 * 60 * 60 * 1000),   # 7 bars = 7 days
        }
        for interval, (_, lookback_ms) in candle_configs.items():
            try:
                candles[interval] = fetch_candles(client, symbol, interval, lookback_ms)
            except Exception as e:
                log.warning(f"Candle {interval} failed {symbol}: {e}")
                candles[interval] = []

        mid = all_mids.get(symbol)

        snapshot["assets"][symbol] = {
            "mid": mid,
            "mark": _float(ctx.get("markPx")),
            "funding": _float(ctx.get("funding")),
            "open_interest": _float(ctx.get("openInterest")),
            "day_ntl_vlm": _float(ctx.get("dayNtlVlm")),
            "prev_day_px": _float(ctx.get("prevDayPx")),
            "orderbook": book,
            "candles_15m": candles.get("15m", []),
            "candles_1h": candles.get("1h", []),
            "candles_4h": candles.get("4h", []),
            "candles_1d": candles.get("1d", []),
        }

    return snapshot


def _float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _parse_book(raw: dict) -> dict | None:
    levels = raw.get("levels")
    if not levels or len(levels) < 2:
        return None
    bids = [{"px": float(l["px"]), "sz": float(l["sz"]), "n": l.get("n", 0)} for l in levels[0]]
    asks = [{"px": float(l["px"]), "sz": float(l["sz"]), "n": l.get("n", 0)} for l in levels[1]]
    return {"bids": bids, "asks": asks}