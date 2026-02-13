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

        # R:R check
        if setup.risk.rr_to_tp1 < 1.5 and setup.confidence > 30:
            errors.append(
                f"Setup {setup.rank}: R:R to TP1 is {setup.risk.rr_to_tp1} "
                f"(< 1.5) but confidence is {setup.confidence}"
            )

        total_risk += setup.risk.risk_pct_equity

    if total_risk > max_total:
        errors.append(
            f"Total risk {total_risk:.1f}% exceeds max {max_total}%"
        )

    if errors:
        log.warning(f"Risk validation issues: {len(errors)}")

    return output, errors
