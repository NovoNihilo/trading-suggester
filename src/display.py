"""Concise terminal output for trade setups."""

from __future__ import annotations

import json
from src.models.llm_output import LLMOutput
from src.models.market_state import MarketState


def print_market_state(state: MarketState) -> None:
    print(f"\n{'='*70}")
    print(f"  MARKET STATE  |  {state.timestamp}")
    print(f"{'='*70}")

    for a in state.assets:
        spread = a.orderbook.spread_bps
        imb = a.orderbook.imbalance
        print(
            f"\n  {a.symbol:>5}  "
            f"mid={a.price.mid:>10,.2f}  "
            f"mark={a.price.mark:>10,.2f}  "
            f"spread={spread:.1f}bps  "
            f"imb={imb:+.2f}"
        )

        kl = a.key_levels
        print(
            f"         dayH={kl.day_high:>10,.2f}  "
            f"dayL={kl.day_low:>10,.2f}  "
            f"vwap={kl.vwap or 0:>10,.2f}"
        )
        if kl.prior_day_high:
            print(
                f"         pdH={kl.prior_day_high:>11,.2f}  "
                f"pdL={kl.prior_day_low or 0:>11,.2f}  "
                f"pdC={kl.prior_day_close or 0:>11,.2f}"
            )
        if kl.pivot_pp:
            print(
                f"         PP={kl.pivot_pp:>12,.2f}  "
                f"R1={kl.pivot_r1:>11,.2f}  "
                f"S1={kl.pivot_s1:>11,.2f}"
            )
            print(
                f"         R2={kl.pivot_r2:>12,.2f}  "
                f"S2={kl.pivot_s2:>11,.2f}"
            )
        if kl.week_high:
            print(
                f"         wkH={kl.week_high:>11,.2f}  "
                f"wkL={kl.week_low or 0:>11,.2f}"
            )

        b = a.bar_stats
        print(
            f"         ret: 1m={_pct(b.ret_1m)} 5m={_pct(b.ret_5m)} "
            f"15m={_pct(b.ret_15m)} 1h={_pct(b.ret_1h)} 4h={_pct(b.ret_4h)}"
        )
        print(
            f"         atr: 15m={b.atr_15m or 0:>8.2f}  "
            f"1h={b.atr_1h or 0:>8.2f}  "
            f"4h={b.atr_4h or 0:>8.2f}"
        )

        f = a.funding_oi
        print(
            f"         funding={f.funding_rate:+.6f} ({f.funding_trend or '?'})  "
            f"OI={f.open_interest:,.0f}  "
            f"OIΔ1h={f.oi_delta_1h or 0:+,.0f}"
        )

    rc = state.risk_context
    print(f"\n  Risk: equity=${rc.equity_usd:,.0f}  "
          f"max/trade=${rc.max_loss_per_trade_usd:,.0f}  "
          f"max/total=${rc.max_total_risk_usd:,.0f}  "
          f"lev={rc.min_leverage}-{rc.max_leverage}x")
    print(f"{'='*70}\n")


def print_setups(output: LLMOutput, errors: list[str]) -> None:
    print(f"\n{'='*70}")
    print(f"  TRADE PLAN  |  {output.timestamp}")
    print(f"  Regime: {output.regime} — {output.regime_note}")
    print(f"{'='*70}")

    if output.no_trade_reason:
        print(f"\n  ⚠ NO TRADE: {output.no_trade_reason}\n")

    for s in output.setups:
        _print_setup(s)

    if errors:
        print(f"\n{'─'*70}")
        corrections = [e for e in errors if e.startswith("✓")]
        real_errors = [e for e in errors if not e.startswith("✓")]
        if corrections:
            print(f"  ✓ AUTO-CORRECTIONS ({len(corrections)}):")
            for c in corrections:
                print(f"    {c}")
        if real_errors:
            print(f"  ⚠ ISSUES ({len(real_errors)}):")
            for e in real_errors:
                print(f"    • {e}")
        print(f"{'─'*70}")
    print()


def _print_setup(s) -> None:
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
    print(json.dumps(state.model_dump(), indent=2, default=str))


def _pct(v: float | None) -> str:
    if v is None:
        return "  n/a  "
    return f"{v:+.3f}%"