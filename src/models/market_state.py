"""Market State schema — the structured input the LLM receives."""

from __future__ import annotations

from pydantic import BaseModel
from typing import Optional


class PriceData(BaseModel):
    mark: float
    mid: float
    last: float


class BarStats(BaseModel):
    ret_1m: Optional[float] = None
    ret_5m: Optional[float] = None
    ret_15m: Optional[float] = None
    ret_1h: Optional[float] = None
    ret_4h: Optional[float] = None
    atr_15m: Optional[float] = None
    atr_1h: Optional[float] = None
    atr_4h: Optional[float] = None


class KeyLevels(BaseModel):
    day_high: float
    day_low: float
    prior_day_high: Optional[float] = None
    prior_day_low: Optional[float] = None
    prior_day_close: Optional[float] = None
    vwap: Optional[float] = None
    # Classic floor pivots from prior day OHLC
    pivot_pp: Optional[float] = None
    pivot_r1: Optional[float] = None
    pivot_r2: Optional[float] = None
    pivot_s1: Optional[float] = None
    pivot_s2: Optional[float] = None
    # Multi-day context
    week_high: Optional[float] = None
    week_low: Optional[float] = None


class OrderbookState(BaseModel):
    spread_bps: float
    bid_depth_01pct: float
    ask_depth_01pct: float
    bid_depth_05pct: float
    ask_depth_05pct: float
    imbalance: float
    best_bid: float
    best_ask: float


class FlowData(BaseModel):
    aggressive_buy_ratio: Optional[float] = None
    signed_volume_delta: Optional[float] = None


class FundingOI(BaseModel):
    funding_rate: float
    open_interest: float
    oi_delta_1h: Optional[float] = None
    funding_1h_ago: Optional[float] = None
    funding_trend: Optional[str] = None  # "rising", "falling", "stable", "extreme_long", "extreme_short"


class RiskContext(BaseModel):
    equity_usd: float
    max_loss_per_trade_usd: float
    max_total_risk_usd: float
    min_leverage: int
    max_leverage: int


class AssetState(BaseModel):
    symbol: str
    timestamp: str
    price: PriceData
    bar_stats: BarStats
    key_levels: KeyLevels
    orderbook: OrderbookState
    flow: FlowData
    funding_oi: FundingOI


class MarketState(BaseModel):
    timestamp: str
    assets: list[AssetState]
    risk_context: RiskContext