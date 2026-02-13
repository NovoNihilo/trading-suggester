"""LLM Output schema — strict validation of what the LLM returns."""

from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator
from typing import Optional


class ConfidenceCriterion(BaseModel):
    criterion: str
    score: int

    @field_validator("score")
    @classmethod
    def score_range(cls, v: int) -> int:
        if not 0 <= v <= 10:
            raise ValueError(f"Score must be 0-10, got {v}")
        return v


class EntryLevels(BaseModel):
    trigger: float
    retest_zone_low: float
    retest_zone_high: float


class Entry(BaseModel):
    type: str  # "trigger"
    trigger_conditions: list[str]
    entry_style: str  # limit_on_retest | stop_market_on_break | limit
    levels: EntryLevels


class Stop(BaseModel):
    level: float
    why: str


class TakeProfit(BaseModel):
    level: float
    pct: int


class Risk(BaseModel):
    risk_pct_equity: float
    max_loss_usd: float
    recommended_leverage: int
    position_notional_usd: float
    margin_used_usd: float
    rr_to_tp1: float
    cancel_if_not_triggered_minutes: int
    time_stop_minutes: int
    liquidation_buffer_note: str


class Setup(BaseModel):
    rank: int
    asset: str
    direction: str  # long | short | no_trade
    playbook: str  # A | B | C | D | E
    confidence: int
    confidence_breakdown: list[ConfidenceCriterion]
    time_horizon_hours: list[int]
    entry: Entry
    stop: Stop
    take_profits: list[TakeProfit]
    risk: Risk
    invalidations: list[str]
    red_flags: list[str]
    if_not_triggered: str

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError(f"Confidence must be 0-100, got {v}")
        return v

    @field_validator("confidence_breakdown")
    @classmethod
    def exactly_10_criteria(cls, v: list[ConfidenceCriterion]) -> list[ConfidenceCriterion]:
        if len(v) != 10:
            raise ValueError(f"Must have exactly 10 criteria, got {len(v)}")
        return v

    @field_validator("playbook")
    @classmethod
    def valid_playbook(cls, v: str) -> str:
        if v not in ("A", "B", "C", "D", "E"):
            raise ValueError(f"Playbook must be A-E, got {v}")
        return v

    @field_validator("direction")
    @classmethod
    def valid_direction(cls, v: str) -> str:
        if v not in ("long", "short", "no_trade"):
            raise ValueError(f"Direction must be long/short/no_trade, got {v}")
        return v

    @model_validator(mode="after")
    def confidence_matches_breakdown(self) -> "Setup":
        weights = [2.0, 1.5, 1.5, 1.5, 1.0, 0.5, 0.5, 0.25, 0.25, 1.0]
        raw_scores = [c.score for c in self.confidence_breakdown]
        expected = round(sum(s * w for s, w in zip(raw_scores, weights)))
        # Allow ±2 tolerance for rounding
        if abs(self.confidence - expected) > 2:
            raise ValueError(
                f"confidence={self.confidence} != weighted sum={expected} "
                f"(scores={raw_scores}, weights={weights})"
            )
        return self


class LLMOutput(BaseModel):
    timestamp: str
    regime: str
    regime_note: str
    setups: list[Setup]
    no_trade_reason: str

    @field_validator("regime")
    @classmethod
    def valid_regime(cls, v: str) -> str:
        if v not in ("trend", "range", "high_vol", "low_vol", "chop"):
            raise ValueError(f"Invalid regime: {v}")
        return v

    @field_validator("setups")
    @classmethod
    def exactly_3_setups(cls, v: list[Setup]) -> list[Setup]:
        if len(v) != 3:
            raise ValueError(f"Must have exactly 3 setups, got {len(v)}")
        return v
