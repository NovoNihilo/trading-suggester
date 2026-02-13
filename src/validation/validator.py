"""Validate LLM output against schema and risk rules."""

from __future__ import annotations

import json
import logging
from typing import Tuple

from pydantic import ValidationError

from src.config import MAX_RISK_PER_TRADE_PCT, MAX_TOTAL_RISK_PCT, EQUITY_USD
from src.models.llm_output import LLMOutput

log = logging.getLogger(__name__)


def validate_llm_output(raw_json: str) -> Tuple[LLMOutput | None, list[str]]:
    """Parse and validate LLM JSON.

    Returns (parsed_output, list_of_errors).
    If errors is non-empty, output may be None or partially valid.
    """
    errors: list[str] = []

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

    # Step 3: Risk rule validation
    max_per_trade = MAX_RISK_PER_TRADE_PCT
    max_total = MAX_TOTAL_RISK_PCT
    total_risk = 0.0

    for setup in output.setups:
        if setup.direction == "no_trade":
            continue

        # Per-trade risk check
        if setup.risk.risk_pct_equity > max_per_trade:
            errors.append(
                f"Setup {setup.rank} ({setup.asset}): risk_pct_equity "
                f"{setup.risk.risk_pct_equity}% > max {max_per_trade}%"
            )

        # Max loss USD check
        expected_max_loss = EQUITY_USD * setup.risk.risk_pct_equity / 100
        if setup.risk.max_loss_usd > expected_max_loss * 1.05:  # 5% tolerance
            errors.append(
                f"Setup {setup.rank}: max_loss_usd ${setup.risk.max_loss_usd:.2f} "
                f"inconsistent with risk_pct {setup.risk.risk_pct_equity}% "
                f"(expected ~${expected_max_loss:.2f})"
            )

        # Position sizing math check
        entry_px = setup.entry.levels.trigger
        stop_px = setup.stop.level
        notional = setup.risk.position_notional_usd
        if entry_px > 0 and stop_px > 0 and notional > 0:
            stop_dist_pct = abs(entry_px - stop_px) / entry_px
            actual_loss = notional * stop_dist_pct
            stated_loss = setup.risk.max_loss_usd
            if stated_loss > 0 and abs(actual_loss - stated_loss) / stated_loss > 0.20:
                errors.append(
                    f"Setup {setup.rank}: position math mismatch — "
                    f"notional=${notional:,.0f} × stop_dist={stop_dist_pct:.4f} = "
                    f"${actual_loss:,.2f} actual loss vs ${stated_loss:,.2f} stated"
                )

        # Leverage vs margin check
        lev = setup.risk.recommended_leverage
        margin = setup.risk.margin_used_usd
        if lev > 0 and notional > 0:
            expected_margin = notional / lev
            if margin > 0 and abs(expected_margin - margin) / expected_margin > 0.20:
                errors.append(
                    f"Setup {setup.rank}: margin mismatch — "
                    f"notional=${notional:,.0f} / {lev}x = ${expected_margin:,.0f} "
                    f"expected vs ${margin:,.0f} stated"
                )

        # R:R check
        if setup.risk.rr_to_tp1 < 1.5 and setup.confidence > 30:
            errors.append(
                f"Setup {setup.rank}: R:R to TP1 is {setup.risk.rr_to_tp1} "
                f"(< 1.5) but confidence is {setup.confidence}"
            )

        # Verify R:R math
        if entry_px > 0 and stop_px > 0 and len(setup.take_profits) > 0:
            tp1 = setup.take_profits[0].level
            risk_dist = abs(entry_px - stop_px)
            reward_dist = abs(tp1 - entry_px)
            if risk_dist > 0:
                actual_rr = reward_dist / risk_dist
                stated_rr = setup.risk.rr_to_tp1
                if stated_rr > 0 and abs(actual_rr - stated_rr) / stated_rr > 0.20:
                    errors.append(
                        f"Setup {setup.rank}: R:R math wrong — "
                        f"actual={actual_rr:.2f} vs stated={stated_rr:.1f}"
                    )

        total_risk += setup.risk.risk_pct_equity

    if total_risk > max_total:
        errors.append(
            f"Total risk {total_risk:.1f}% exceeds max {max_total}%"
        )

    if errors:
        log.warning(f"Risk validation issues: {len(errors)}")

    return output, errors