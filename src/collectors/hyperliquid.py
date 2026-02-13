"""Hyperliquid read-only data collector.

Batches API calls to minimize rate-limit exposure.
Stores raw snapshots in SQLite for feature computation.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import httpx

from src.config import HL_INFO_URL, ASSETS

log = logging.getLogger(__name__)

# Hyperliquid info endpoint — all POST with {"type": ...}
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _post(client: httpx.Client, payload: dict) -> dict | list:
    resp = client.post(HL_INFO_URL, json=payload, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_meta(client: httpx.Client) -> dict:
    """Fetch universe metadata (asset list, szDecimals, etc.)."""
    return _post(client, {"type": "meta"})


def fetch_all_mids(client: httpx.Client) -> dict[str, float]:
    """Fetch mid prices for all assets. Returns {symbol: mid_price}."""
    data = _post(client, {"type": "allMids"})
    return {k: float(v) for k, v in data.items()}


def fetch_l2_book(client: httpx.Client, coin: str, n_levels: int = 20) -> dict:
    """Fetch L2 order book for a single asset."""
    return _post(client, {"type": "l2Book", "coin": coin, "nSigFigs": 5})


def fetch_meta_and_asset_ctxs(client: httpx.Client) -> tuple[dict, list[dict]]:
    """Fetch meta + per-asset context (funding, OI, mark, etc.) in one call."""
    data = _post(client, {"type": "metaAndAssetCtxs"})
    # Returns [meta_dict, [asset_ctx_0, asset_ctx_1, ...]]
    return data[0], data[1]


def fetch_candle_snapshot(
    client: httpx.Client, coin: str, interval: str = "15m", limit: int = 20
) -> list[dict]:
    """Fetch recent candles for a single asset."""
    end_time = int(time.time() * 1000)
    start_time = end_time - (limit * 15 * 60 * 1000)  # rough for 15m candles
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

    Batches calls to minimize API hits:
    1. metaAndAssetCtxs (1 call) — gives funding, OI, mark for ALL assets
    2. allMids (1 call) — gives mid prices for ALL assets
    3. Per asset: l2Book + candleSnapshot (2 calls each)

    Total: 2 + 2*N calls per snapshot (N = number of assets).
    For BTC+ETH that's 6 calls/60s — well within limits.
    """
    ts = datetime.now(timezone.utc).isoformat()

    meta, asset_ctxs = fetch_meta_and_asset_ctxs(client)
    all_mids = fetch_all_mids(client)

    # Build symbol -> index mapping from meta
    universe = meta.get("universe", [])
    sym_to_idx = {u["name"]: i for i, u in enumerate(universe)}

    snapshot = {"timestamp": ts, "assets": {}}

    for symbol in ASSETS:
        if symbol not in sym_to_idx:
            log.warning(f"Asset {symbol} not in Hyperliquid universe, skipping")
            continue

        idx = sym_to_idx[symbol]
        ctx = asset_ctxs[idx]

        # L2 book
        try:
            book_raw = fetch_l2_book(client, symbol)
            book = _parse_book(book_raw)
        except Exception as e:
            log.warning(f"L2 book fetch failed for {symbol}: {e}")
            book = None

        # Candles
        try:
            candles = fetch_candle_snapshot(client, symbol, "15m", 20)
        except Exception as e:
            log.warning(f"Candle fetch failed for {symbol}: {e}")
            candles = []

        mid = all_mids.get(symbol)

        snapshot["assets"][symbol] = {
            "mid": mid,
            "mark": _float(ctx.get("markPx")),
            "funding": _float(ctx.get("funding")),
            "open_interest": _float(ctx.get("openInterest")),
            "day_ntl_vlm": _float(ctx.get("dayNtlVlm")),
            "prev_day_px": _float(ctx.get("prevDayPx")),
            "orderbook": book,
            "candles": candles,
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
    """Parse L2 book into structured format."""
    levels = raw.get("levels")
    if not levels or len(levels) < 2:
        return None

    bids = [{"px": float(l["px"]), "sz": float(l["sz"]), "n": l.get("n", 0)} for l in levels[0]]
    asks = [{"px": float(l["px"]), "sz": float(l["sz"]), "n": l.get("n", 0)} for l in levels[1]]

    return {"bids": bids, "asks": asks}
