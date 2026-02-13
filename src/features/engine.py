"""Feature engine: converts raw snapshots → structured MarketState.

V2: Multi-timeframe candles, floor pivots, funding trend, proper prior-day levels.
"""

from __future__ import annotations

import logging

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
    """Build MarketState from recent snapshots (newest-first order)."""
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
    mark = data.get("mark") or data.get("mid")
    mid = data.get("mid") or mark
    if not mark or not mid:
        return None

    price = PriceData(mark=mark, mid=mid, last=mid)
    bar_stats = _compute_bar_stats(symbol, data, snapshots)
    key_levels = _compute_key_levels(symbol, data)
    orderbook = _compute_orderbook_state(data.get("orderbook"), mid)

    # Flow proxy from orderbook imbalance
    flow = FlowData(aggressive_buy_ratio=None, signed_volume_delta=None)
    if orderbook:
        ratio = max(0.0, min(1.0, 0.5 + (orderbook.imbalance * 0.5)))
        flow = FlowData(aggressive_buy_ratio=round(ratio, 3), signed_volume_delta=None)

    funding_oi = _compute_funding_oi(symbol, data, snapshots)

    if orderbook is None:
        orderbook = OrderbookState(
            spread_bps=0, bid_depth_01pct=0, ask_depth_01pct=0,
            bid_depth_05pct=0, ask_depth_05pct=0, imbalance=0,
            best_bid=mid, best_ask=mid,
        )

    return AssetState(
        symbol=symbol, timestamp=ts, price=price, bar_stats=bar_stats,
        key_levels=key_levels, orderbook=orderbook, flow=flow, funding_oi=funding_oi,
    )


# ---------- BAR STATS ----------

def _compute_bar_stats(symbol: str, data: dict, snapshots: list[dict]) -> BarStats:
    """Compute returns from snapshot history + ATR from multi-TF candles."""
    # Returns from snapshot mids (60s apart)
    mids = []
    for snap in snapshots:
        m = snap.get("assets", {}).get(symbol, {}).get("mid")
        if m:
            mids.append(m)

    ret_1m = ret_5m = ret_15m = None
    if len(mids) >= 2:
        current = mids[0]
        def _ret(idx):
            i = min(idx, len(mids) - 1)
            return round((current - mids[i]) / mids[i] * 100, 4) if mids[i] else None
        ret_1m = _ret(1)
        ret_5m = _ret(5)
        ret_15m = _ret(15)

    # Returns from candle closes for longer TFs
    ret_1h = _return_from_candles(data.get("candles_1h", []), 1)
    ret_4h = _return_from_candles(data.get("candles_4h", []), 1)

    # ATR from each timeframe
    atr_15m = _compute_atr(data.get("candles_15m", []), 14)
    atr_1h = _compute_atr(data.get("candles_1h", []), 14)
    atr_4h = _compute_atr(data.get("candles_4h", []), 14)

    return BarStats(
        ret_1m=ret_1m, ret_5m=ret_5m, ret_15m=ret_15m,
        ret_1h=ret_1h, ret_4h=ret_4h,
        atr_15m=atr_15m, atr_1h=atr_1h, atr_4h=atr_4h,
    )


def _return_from_candles(candles: list[dict], periods_back: int) -> float | None:
    """Return % change from N candles back to latest."""
    if len(candles) < periods_back + 1:
        return None
    current = float(candles[-1].get("c", 0))
    prev = float(candles[-(periods_back + 1)].get("c", 0))
    if not current or not prev:
        return None
    return round((current - prev) / prev * 100, 4)


def _compute_atr(candles: list[dict], period: int = 14) -> float | None:
    """Compute ATR from candle data."""
    if not candles or len(candles) < 3:
        return None
    trs = []
    for i, c in enumerate(candles[-period:]):
        h, l = float(c.get("h", 0)), float(c.get("l", 0))
        if not h or not l:
            continue
        if i > 0:
            prev_c = float(candles[max(0, len(candles) - period + i - 1)].get("c", 0))
            if prev_c:
                tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            else:
                tr = h - l
        else:
            tr = h - l
        trs.append(tr)
    if not trs:
        return None
    return round(sum(trs) / len(trs), 4)


# ---------- KEY LEVELS ----------

def _compute_key_levels(symbol: str, data: dict) -> KeyLevels:
    """Compute key levels from multi-TF candle data."""
    mid = data.get("mid") or data.get("mark") or 0
    candles_15m = data.get("candles_15m", [])
    candles_1d = data.get("candles_1d", [])

    # Intraday high/low from 15m candles
    day_high, day_low = mid, mid
    if candles_15m:
        highs = [float(c["h"]) for c in candles_15m if c.get("h")]
        lows = [float(c["l"]) for c in candles_15m if c.get("l") and float(c["l"]) > 0]
        if highs:
            day_high = max(highs)
        if lows:
            day_low = min(lows)

    # Prior day OHLC from daily candles
    prior_day_high = prior_day_low = prior_day_close = None
    pivot_pp = pivot_r1 = pivot_r2 = pivot_s1 = pivot_s2 = None
    week_high = week_low = None

    if len(candles_1d) >= 2:
        # Second-to-last daily candle = prior day
        prev = candles_1d[-2]
        prior_day_high = float(prev.get("h", 0)) or None
        prior_day_low = float(prev.get("l", 0)) or None
        prior_day_close = float(prev.get("c", 0)) or None

        # Classic floor pivots: PP = (H + L + C) / 3
        if prior_day_high and prior_day_low and prior_day_close:
            pp = (prior_day_high + prior_day_low + prior_day_close) / 3
            pivot_pp = round(pp, 2)
            pivot_r1 = round(2 * pp - prior_day_low, 2)
            pivot_s1 = round(2 * pp - prior_day_high, 2)
            pivot_r2 = round(pp + (prior_day_high - prior_day_low), 2)
            pivot_s2 = round(pp - (prior_day_high - prior_day_low), 2)

    # Week high/low from all daily candles
    if candles_1d:
        week_highs = [float(c["h"]) for c in candles_1d if c.get("h")]
        week_lows = [float(c["l"]) for c in candles_1d if c.get("l") and float(c["l"]) > 0]
        if week_highs:
            week_high = max(week_highs)
        if week_lows:
            week_low = min(week_lows)

    # VWAP from 15m candles
    vwap = _compute_vwap(candles_15m)

    return KeyLevels(
        day_high=day_high, day_low=day_low,
        prior_day_high=prior_day_high, prior_day_low=prior_day_low,
        prior_day_close=prior_day_close, vwap=vwap,
        pivot_pp=pivot_pp, pivot_r1=pivot_r1, pivot_r2=pivot_r2,
        pivot_s1=pivot_s1, pivot_s2=pivot_s2,
        week_high=week_high, week_low=week_low,
    )


def _compute_vwap(candles: list[dict]) -> float | None:
    if not candles:
        return None
    total_vp = 0.0
    total_v = 0.0
    for c in candles:
        h, l = float(c.get("h", 0)), float(c.get("l", 0))
        close, vol = float(c.get("c", 0)), float(c.get("v", 0))
        if h and l and close and vol > 0:
            total_vp += ((h + l + close) / 3) * vol
            total_v += vol
    if total_v == 0:
        return None
    return round(total_vp / total_v, 2)


# ---------- ORDERBOOK ----------

def _compute_orderbook_state(book: dict | None, mid: float) -> OrderbookState | None:
    if not book or not book.get("bids") or not book.get("asks"):
        return None
    bids, asks = book["bids"], book["asks"]
    best_bid = bids[0]["px"] if bids else mid
    best_ask = asks[0]["px"] if asks else mid
    spread_bps = round((best_ask - best_bid) / best_bid * 10000, 2) if best_bid > 0 else 0

    def _depth(levels, ref_px, pct):
        return round(sum(l["sz"] * l["px"] for l in levels if abs(l["px"] - ref_px) / ref_px <= pct / 100), 2)

    bid_d01, ask_d01 = _depth(bids, best_bid, 0.1), _depth(asks, best_ask, 0.1)
    bid_d05, ask_d05 = _depth(bids, best_bid, 0.5), _depth(asks, best_ask, 0.5)
    total_near = bid_d01 + ask_d01
    imbalance = round((bid_d01 - ask_d01) / total_near, 3) if total_near > 0 else 0

    return OrderbookState(
        spread_bps=spread_bps, bid_depth_01pct=bid_d01, ask_depth_01pct=ask_d01,
        bid_depth_05pct=bid_d05, ask_depth_05pct=ask_d05, imbalance=imbalance,
        best_bid=best_bid, best_ask=best_ask,
    )


# ---------- FUNDING / OI ----------

def _compute_funding_oi(symbol: str, data: dict, snapshots: list[dict]) -> FundingOI:
    """Compute funding with trend from snapshot history."""
    current_funding = data.get("funding") or 0.0
    current_oi = data.get("open_interest") or 0.0

    # OI delta from ~1h ago
    oi_delta = None
    if len(snapshots) >= 2:
        back_idx = min(60, len(snapshots) - 1)
        old_oi = snapshots[back_idx].get("assets", {}).get(symbol, {}).get("open_interest")
        if old_oi is not None:
            oi_delta = round(current_oi - old_oi, 2)

    # Funding from ~1h ago
    funding_1h_ago = None
    if len(snapshots) >= 2:
        back_idx = min(60, len(snapshots) - 1)
        funding_1h_ago = snapshots[back_idx].get("assets", {}).get(symbol, {}).get("funding")

    # Classify funding trend
    funding_trend = _classify_funding_trend(current_funding, funding_1h_ago)

    return FundingOI(
        funding_rate=current_funding,
        open_interest=current_oi,
        oi_delta_1h=oi_delta,
        funding_1h_ago=funding_1h_ago,
        funding_trend=funding_trend,
    )


def _classify_funding_trend(current: float, hour_ago: float | None) -> str:
    """Classify funding trend for the LLM."""
    # Extreme thresholds (annualized ~100%+)
    if current > 0.01:
        return "extreme_long"
    if current < -0.01:
        return "extreme_short"

    if hour_ago is None:
        return "stable"

    delta = current - hour_ago
    if abs(delta) < 0.0001:
        return "stable"
    elif delta > 0:
        return "rising"
    else:
        return "falling"