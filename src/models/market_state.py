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
    atr_15m: Optional[float] = None


class KeyLevels(BaseModel):
    day_high: float
    day_low: float
    prior_day_high: Optional[float] = None
    prior_day_low: Optional[float] = None
    vwap: Optional[float] = None


class OrderbookState(BaseModel):
    spread_bps: float
    bid_depth_01pct: float  # total bid size within 0.1%
    ask_depth_01pct: float
    bid_depth_05pct: float  # within 0.5%
    ask_depth_05pct: float
    imbalance: float  # >0 = bid-heavy, <0 = ask-heavy
    best_bid: float
    best_ask: float


class FlowData(BaseModel):
    aggressive_buy_ratio: Optional[float] = None  # 0-1
    signed_volume_delta: Optional[float] = None


class FundingOI(BaseModel):
    funding_rate: float
    open_interest: float
    oi_delta_1h: Optional[float] = None


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
