"""Concise terminal output for trade setups."""

from __future__ import annotations

import json
from src.models.llm_output import LLMOutput
from src.models.market_state import MarketState


def print_market_state(state: MarketState) -> None:
    """Print compact Market State summary."""
    print(f"\n{'='*60}")
    print(f"  MARKET STATE  |  {state.timestamp}")
    print(f"{'='*60}")

    for a in state.assets:
        spread = a.orderbook.spread_bps
        imb = a.orderbook.imbalance
        print(
            f"  {a.symbol:>5}  "
            f"mid={a.price.mid:>10,.2f}  "
            f"mark={a.price.mark:>10,.2f}  "
            f"spread={spread:.1f}bps  "
            f"imb={imb:+.2f}"
        )
        print(
            f"         "
            f"dayH={a.key_levels.day_high:>10,.2f}  "
            f"dayL={a.key_levels.day_low:>10,.2f}  "
            f"vwap={a.key_levels.vwap or 0:>10,.2f}"
        )
        rets = a.bar_stats
        print(
            f"         "
            f"ret1m={_pct(rets.ret_1m)}  "
            f"ret5m={_pct(rets.ret_5m)}  "
            f"ret15m={_pct(rets.ret_15m)}  "
            f"atr15m={rets.atr_15m or 0:.2f}"
        )
        print(
            f"         "
            f"funding={a.funding_oi.funding_rate:+.6f}  "
            f"OI={a.funding_oi.open_interest:,.0f}  "
            f"OIΔ1h={a.funding_oi.oi_delta_1h or 0:+,.0f}"
        )

    rc = state.risk_context
    print(f"\n  Risk: equity=${rc.equity_usd:,.0f}  "
          f"max/trade=${rc.max_loss_per_trade_usd:,.0f}  "
          f"max/total=${rc.max_total_risk_usd:,.0f}  "
          f"lev={rc.min_leverage}-{rc.max_leverage}x")
    print(f"{'='*60}\n")


def print_setups(output: LLMOutput, errors: list[str]) -> None:
    """Print ranked trade setups."""
    print(f"\n{'='*60}")
    print(f"  TRADE PLAN  |  {output.timestamp}")
    print(f"  Regime: {output.regime} — {output.regime_note}")
    print(f"{'='*60}")

    if output.no_trade_reason:
        print(f"\n  ⚠ NO TRADE: {output.no_trade_reason}\n")

    for s in output.setups:
        _print_setup(s)

    if errors:
        print(f"\n{'─'*60}")
        print(f"  ⚠ VALIDATION ISSUES:")
        for e in errors:
            print(f"    • {e}")
        print(f"{'─'*60}")

    print()


def _print_setup(s) -> None:
    """Print a single setup."""
    if s.direction == "no_trade":
        print(f"\n  #{s.rank} {'NO TRADE':>10}  playbook={s.playbook}  "
              f"conf={s.confidence}/100")
        if s.red_flags:
            print(f"    Reason: {'; '.join(s.red_flags)}")
        return

    arrow = "▲" if s.direction == "long" else "▼"
    print(
        f"\n  #{s.rank} {arrow} {s.direction.upper():>5} {s.asset}  "
        f"playbook={s.playbook}  conf={s.confidence}/100"
    )
    print(
        f"    Entry: {s.entry.entry_style} @ trigger={s.entry.levels.trigger:,.2f}  "
        f"zone=[{s.entry.levels.retest_zone_low:,.2f}, "
        f"{s.entry.levels.retest_zone_high:,.2f}]"
    )
    print(f"    Stop:  {s.stop.level:,.2f} ({s.stop.why})")
    tp_str = "  ".join(
        f"TP{i+1}={tp.level:,.2f}({tp.pct}%)" for i, tp in enumerate(s.take_profits)
    )
    print(f"    TPs:   {tp_str}")
    print(
        f"    Risk:  {s.risk.risk_pct_equity:.1f}% equity  "
        f"maxloss=${s.risk.max_loss_usd:,.0f}  "
        f"lev={s.risk.recommended_leverage}x  "
        f"notional=${s.risk.position_notional_usd:,.0f}  "
        f"R:R={s.risk.rr_to_tp1:.1f}"
    )
    print(
        f"    Time:  cancel={s.risk.cancel_if_not_triggered_minutes}min  "
        f"stop={s.risk.time_stop_minutes}min  "
        f"horizon={s.time_horizon_hours[0]}-{s.time_horizon_hours[1]}h"
    )
    if s.invalidations:
        print(f"    Invalidations: {'; '.join(s.invalidations[:3])}")
    if s.red_flags:
        print(f"    Red flags: {'; '.join(s.red_flags[:3])}")


def print_market_state_json(state: MarketState) -> None:
    """Print full Market State as formatted JSON (for dry-run)."""
    print(json.dumps(state.model_dump(), indent=2, default=str))


def _pct(v: float | None) -> str:
    if v is None:
        return "  n/a  "
    return f"{v:+.3f}%"
