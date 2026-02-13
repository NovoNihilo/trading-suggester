"""Feature engine: converts raw snapshots → structured MarketState.

Takes the last N snapshots from SQLite and computes:
- Returns (1m, 5m, 15m approximations from snapshot cadence)
- ATR proxy from 15m candles
- Key levels (day hi/lo, prior day hi/lo)
- Orderbook features (spread, depth, imbalance)
- Funding/OI
- Flow proxy (orderbook imbalance as stand-in for aggressive flow)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.config import (
    ASSETS,
    EQUITY_USD,
    MAX_RISK_PER_TRADE_PCT,
    MAX_TOTAL_RISK_PCT,
    MIN_LEVERAGE,
    MAX_LEVERAGE,
)
from src.models.market_state import (
    AssetState,
    BarStats,
    FlowData,
    FundingOI,
    KeyLevels,
    MarketState,
    OrderbookState,
    PriceData,
    RiskContext,
)

log = logging.getLogger(__name__)


def build_market_state(snapshots: list[dict]) -> MarketState | None:
    """Build MarketState from recent snapshots (newest-first order).

    Needs at least 1 snapshot. More snapshots = better return estimates.
    """
    if not snapshots:
        log.error("No snapshots available")
        return None

    latest = snapshots[0]
    ts = latest["timestamp"]

    assets: list[AssetState] = []
    for symbol in ASSETS:
        asset_data = latest.get("assets", {}).get(symbol)
        if not asset_data:
            log.warning(f"No data for {symbol} in latest snapshot")
            continue

        asset_state = _build_asset_state(symbol, ts, asset_data, snapshots)
        if asset_state:
            assets.append(asset_state)

    if not assets:
        log.error("No valid asset states built")
        return None

    risk_ctx = RiskContext(
        equity_usd=EQUITY_USD,
        max_loss_per_trade_usd=EQUITY_USD * MAX_RISK_PER_TRADE_PCT / 100,
        max_total_risk_usd=EQUITY_USD * MAX_TOTAL_RISK_PCT / 100,
        min_leverage=MIN_LEVERAGE,
        max_leverage=MAX_LEVERAGE,
    )

    return MarketState(timestamp=ts, assets=assets, risk_context=risk_ctx)


def _build_asset_state(
    symbol: str, ts: str, data: dict, snapshots: list[dict]
) -> AssetState | None:
    """Build AssetState for a single asset."""
    mark = data.get("mark") or data.get("mid")
    mid = data.get("mid") or mark
    if not mark or not mid:
        return None

    # --- Price ---
    price = PriceData(mark=mark, mid=mid, last=mid)

    # --- Bar stats from snapshot history ---
    bar_stats = _compute_bar_stats(symbol, snapshots)

    # --- Key levels from candles ---
    key_levels = _compute_key_levels(symbol, data, snapshots)

    # --- Orderbook ---
    orderbook = _compute_orderbook_state(data.get("orderbook"), mid)

    # --- Flow (use orderbook imbalance as proxy) ---
    flow = FlowData(
        aggressive_buy_ratio=None,
        signed_volume_delta=None,
    )
    if orderbook:
        # Rough proxy: imbalance > 0 = more bids = buyers
        ratio = 0.5 + (orderbook.imbalance * 0.5)  # map [-1,1] → [0,1]
        ratio = max(0.0, min(1.0, ratio))
        flow = FlowData(aggressive_buy_ratio=round(ratio, 3), signed_volume_delta=None)

    # --- Funding / OI ---
    funding_oi = FundingOI(
        funding_rate=data.get("funding") or 0.0,
        open_interest=data.get("open_interest") or 0.0,
        oi_delta_1h=_compute_oi_delta(symbol, snapshots),
    )

    # Default orderbook if None
    if orderbook is None:
        orderbook = OrderbookState(
            spread_bps=0,
            bid_depth_01pct=0,
            ask_depth_01pct=0,
            bid_depth_05pct=0,
            ask_depth_05pct=0,
            imbalance=0,
            best_bid=mid,
            best_ask=mid,
        )

    return AssetState(
        symbol=symbol,
        timestamp=ts,
        price=price,
        bar_stats=bar_stats,
        key_levels=key_levels,
        orderbook=orderbook,
        flow=flow,
        funding_oi=funding_oi,
    )


def _compute_bar_stats(symbol: str, snapshots: list[dict]) -> BarStats:
    """Compute return approximations from snapshot mid prices."""
    mids = []
    for snap in snapshots:
        asset = snap.get("assets", {}).get(symbol, {})
        m = asset.get("mid")
        if m:
            mids.append(m)

    if len(mids) < 2:
        return BarStats()

    current = mids[0]

    def _ret(idx: int) -> float | None:
        if idx < len(mids) and mids[idx] != 0:
            return round((current - mids[idx]) / mids[idx] * 100, 4)
        return None

    # Snapshots are 60s apart, so index 1 ≈ 1m, 5 ≈ 5m, 15 ≈ 15m
    ret_1m = _ret(1)
    ret_5m = _ret(min(5, len(mids) - 1))
    ret_15m = _ret(min(15, len(mids) - 1))

    # ATR proxy from 15m candles in latest snapshot
    atr = _compute_atr_from_candles(symbol, snapshots[0])

    return BarStats(ret_1m=ret_1m, ret_5m=ret_5m, ret_15m=ret_15m, atr_15m=atr)


def _compute_atr_from_candles(symbol: str, snap: dict) -> float | None:
    """Compute ATR(14) from 15m candles stored in the snapshot."""
    asset = snap.get("assets", {}).get(symbol, {})
    candles = asset.get("candles", [])
    if not candles or len(candles) < 5:
        return None

    trs = []
    for c in candles[-14:]:
        h = float(c.get("h", 0))
        l = float(c.get("l", 0))
        o = float(c.get("o", 0))
        if h and l:
            trs.append(h - l)

    if not trs:
        return None
    return round(sum(trs) / len(trs), 4)


def _compute_key_levels(symbol: str, data: dict, snapshots: list[dict]) -> KeyLevels:
    """Extract key levels from candle data and snapshot history."""
    mid = data.get("mid") or data.get("mark") or 0

    # Day high/low from candles
    candles = data.get("candles", [])
    day_high = mid
    day_low = mid

    if candles:
        highs = [float(c.get("h", 0)) for c in candles if c.get("h")]
        lows = [float(c.get("l", 0)) for c in candles if c.get("l")]
        if highs:
            day_high = max(highs)
        if lows:
            day_low = min(l for l in lows if l > 0)

    # Prior day high/low: use prevDayPx as a rough reference
    prev_day_px = data.get("prev_day_px")

    # VWAP approximation from candle volume-weighted average
    vwap = _compute_vwap(candles)

    return KeyLevels(
        day_high=day_high,
        day_low=day_low,
        prior_day_high=None,  # Would need daily candles
        prior_day_low=None,
        vwap=vwap,
    )


def _compute_vwap(candles: list[dict]) -> float | None:
    """Simple VWAP from candle data."""
    if not candles:
        return None

    total_vp = 0.0
    total_v = 0.0
    for c in candles:
        h = float(c.get("h", 0))
        l = float(c.get("l", 0))
        close = float(c.get("c", 0))
        vol = float(c.get("v", 0))
        if h and l and close and vol > 0:
            typical = (h + l + close) / 3
            total_vp += typical * vol
            total_v += vol

    if total_v == 0:
        return None
    return round(total_vp / total_v, 2)


def _compute_orderbook_state(book: dict | None, mid: float) -> OrderbookState | None:
    """Parse orderbook into structured state."""
    if not book or not book.get("bids") or not book.get("asks"):
        return None

    bids = book["bids"]
    asks = book["asks"]

    best_bid = bids[0]["px"] if bids else mid
    best_ask = asks[0]["px"] if asks else mid

    spread_bps = 0.0
    if best_bid > 0:
        spread_bps = round((best_ask - best_bid) / best_bid * 10000, 2)

    def _depth(levels: list[dict], ref_px: float, pct: float) -> float:
        total = 0.0
        for lvl in levels:
            if abs(lvl["px"] - ref_px) / ref_px <= pct / 100:
                total += lvl["sz"] * lvl["px"]  # notional
        return round(total, 2)

    bid_d01 = _depth(bids, best_bid, 0.1)
    ask_d01 = _depth(asks, best_ask, 0.1)
    bid_d05 = _depth(bids, best_bid, 0.5)
    ask_d05 = _depth(asks, best_ask, 0.5)

    total_near = bid_d01 + ask_d01
    imbalance = 0.0
    if total_near > 0:
        imbalance = round((bid_d01 - ask_d01) / total_near, 3)

    return OrderbookState(
        spread_bps=spread_bps,
        bid_depth_01pct=bid_d01,
        ask_depth_01pct=ask_d01,
        bid_depth_05pct=bid_d05,
        ask_depth_05pct=ask_d05,
        imbalance=imbalance,
        best_bid=best_bid,
        best_ask=best_ask,
    )


def _compute_oi_delta(symbol: str, snapshots: list[dict]) -> float | None:
    """OI change over ~1h of snapshots (60 snapshots at 60s cadence)."""
    if len(snapshots) < 2:
        return None

    latest_oi = snapshots[0].get("assets", {}).get(symbol, {}).get("open_interest")
    # Look ~60 snapshots back for 1h
    back_idx = min(60, len(snapshots) - 1)
    old_oi = snapshots[back_idx].get("assets", {}).get(symbol, {}).get("open_interest")

    if latest_oi is not None and old_oi is not None:
        return round(latest_oi - old_oi, 2)
    return None
