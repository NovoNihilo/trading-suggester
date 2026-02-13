"""Validate LLM output against schema and risk rules.

V3: Auto-corrects position sizing from LLM using deterministic math.
"""

from __future__ import annotations

import json
import logging
from typing import Tuple

from pydantic import ValidationError

from src.config import MAX_RISK_PER_TRADE_PCT, MAX_TOTAL_RISK_PCT, EQUITY_USD, MAX_LEVERAGE
from src.models.llm_output import LLMOutput

log = logging.getLogger(__name__)


def validate_llm_output(raw_json: str) -> Tuple[LLMOutput | None, list[str]]:
    """Parse, validate, and auto-correct LLM JSON."""
    errors: list[str] = []
    corrections: list[str] = []

    # Step 1: Parse JSON
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        return None, [f"Invalid JSON: {e}"]

    # Step 2: Schema validation via Pydantic
    try:
        output = LLMOutput(**data)
    except ValidationError as e:
        err_lines = []
        for err in e.errors():
            loc = " → ".join(str(l) for l in err["loc"])
            err_lines.append(f"  {loc}: {err['msg']}")
        return None, [f"Schema validation failed:\n" + "\n".join(err_lines)]

    # Step 3: Auto-correct position sizing + validate risk rules
    max_per_trade = MAX_RISK_PER_TRADE_PCT
    max_total = MAX_TOTAL_RISK_PCT
    total_risk = 0.0

    for setup in output.setups:
        if setup.direction == "no_trade":
            continue

        # Cap risk_pct_equity
        if setup.risk.risk_pct_equity > max_per_trade:
            corrections.append(
                f"Setup {setup.rank}: capped risk_pct from "
                f"{setup.risk.risk_pct_equity}% to {max_per_trade}%"
            )
            setup.risk.risk_pct_equity = max_per_trade

        # Deterministic position sizing override
        entry_px = setup.entry.levels.trigger
        stop_px = setup.stop.level

        if entry_px > 0 and stop_px > 0 and entry_px != stop_px:
            max_loss_usd = EQUITY_USD * setup.risk.risk_pct_equity / 100
            stop_dist_pct = abs(entry_px - stop_px) / entry_px
            notional = max_loss_usd / stop_dist_pct

            # Compute leverage needed, cap at MAX_LEVERAGE
            margin_budget = EQUITY_USD * 0.5  # use up to 50% of equity as margin
            raw_leverage = notional / margin_budget
            leverage = min(int(raw_leverage) + 1, MAX_LEVERAGE)

            # If leverage needed exceeds max, reduce notional to fit
            if raw_leverage > MAX_LEVERAGE:
                notional = margin_budget * MAX_LEVERAGE
                max_loss_usd = notional * stop_dist_pct
                corrections.append(
                    f"Setup {setup.rank}: leverage capped at {MAX_LEVERAGE}x, "
                    f"reduced notional to ${notional:,.0f}, max_loss=${max_loss_usd:,.2f}"
                )

            margin_used = notional / leverage

            # Compute actual R:R
            if len(setup.take_profits) > 0:
                tp1 = setup.take_profits[0].level
                risk_dist = abs(entry_px - stop_px)
                reward_dist = abs(tp1 - entry_px)
                rr_to_tp1 = round(reward_dist / risk_dist, 2) if risk_dist > 0 else 0
            else:
                rr_to_tp1 = 0

            # Log corrections if values changed significantly
            old_notional = setup.risk.position_notional_usd
            if old_notional > 0 and abs(notional - old_notional) / old_notional > 0.1:
                corrections.append(
                    f"Setup {setup.rank} ({setup.asset}): corrected notional "
                    f"${old_notional:,.0f} → ${notional:,.0f}"
                )

            old_rr = setup.risk.rr_to_tp1
            if old_rr > 0 and abs(rr_to_tp1 - old_rr) / old_rr > 0.1:
                corrections.append(
                    f"Setup {setup.rank}: corrected R:R {old_rr:.2f} → {rr_to_tp1:.2f}"
                )

            # Overwrite with correct values
            setup.risk.max_loss_usd = round(max_loss_usd, 2)
            setup.risk.position_notional_usd = round(notional, 2)
            setup.risk.recommended_leverage = leverage
            setup.risk.margin_used_usd = round(margin_used, 2)
            setup.risk.rr_to_tp1 = rr_to_tp1

            # Liquidation buffer note
            liq_dist_pct = (1 / leverage) * 100  # rough liq distance
            setup.risk.liquidation_buffer_note = (
                f"Liq ~{liq_dist_pct:.1f}% from entry, "
                f"stop at {stop_dist_pct*100:.2f}% ({liq_dist_pct - stop_dist_pct*100:.1f}% buffer)"
            )

        # R:R hard gate: if < 1.5, downgrade to warning
        if setup.risk.rr_to_tp1 < 1.5:
            errors.append(
                f"Setup {setup.rank} ({setup.asset}): R:R to TP1 = "
                f"{setup.risk.rr_to_tp1:.2f} (< 1.5 minimum). "
                f"Consider NO_TRADE or wider targets."
            )

        total_risk += setup.risk.risk_pct_equity

    # Total risk check
    if total_risk > max_total:
        # Scale down proportionally
        scale = max_total / total_risk
        for setup in output.setups:
            if setup.direction != "no_trade":
                old_pct = setup.risk.risk_pct_equity
                setup.risk.risk_pct_equity = round(old_pct * scale, 2)
                corrections.append(
                    f"Setup {setup.rank}: scaled risk {old_pct:.1f}% → "
                    f"{setup.risk.risk_pct_equity:.1f}% (total risk cap)"
                )

    if corrections:
        log.info(f"Auto-corrections applied: {len(corrections)}")
        for c in corrections:
            log.info(f"  ✓ {c}")

    # Combine corrections into errors for display
    all_issues = [f"✓ CORRECTED: {c}" for c in corrections] + errors

    if errors:
        log.warning(f"Remaining validation issues: {len(errors)}")

    return output, all_issues