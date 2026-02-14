"""Anchoring context builder — feeds the LLM a concise thesis summary
instead of the full previous output, with staleness decay.

Solves: LLM copy-pasting previous analysis verbatim instead of re-evaluating.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# After this many minutes with no new signals, stop anchoring entirely
STALE_THRESHOLD_MINUTES = 90

# After this many minutes, downgrade anchoring from "maintain" to "re-evaluate"
SOFT_STALE_MINUTES = 45


def build_anchoring_context(
    prev_analysis: dict | None,
    signals: list[dict],
) -> str | None:
    """Build a concise anchoring summary from the previous analysis.

    Returns None if:
    - No previous analysis exists
    - Previous analysis is too stale (>90min with no confirming signals)

    Returns a SHORT text summary (not raw JSON) otherwise.
    """
    if not prev_analysis:
        return None

    prev_ts = prev_analysis.get("timestamp")
    if not prev_ts:
        return None

    # --- Staleness check ---
    try:
        prev_dt = datetime.fromisoformat(prev_ts)
        now = datetime.now(timezone.utc)
        age_minutes = (now - prev_dt).total_seconds() / 60
    except Exception:
        age_minutes = 999

    # Count signals that fired AFTER the previous analysis
    signals_since = _count_signals_since(signals, prev_ts)

    # Hard stale: old analysis + no market activity confirming it
    if age_minutes > STALE_THRESHOLD_MINUTES and signals_since == 0:
        log.info(
            f"Previous analysis is {age_minutes:.0f}min old with 0 signals since. "
            f"Dropping anchor — forcing fresh analysis."
        )
        return None

    # --- Extract thesis summary from raw JSON ---
    raw = prev_analysis.get("raw", "")
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    setups = parsed.get("setups", [])
    regime = parsed.get("regime", "unknown")
    regime_note = parsed.get("regime_note", "")

    lines = []
    lines.append(f"Previous regime: {regime} ({regime_note})")
    lines.append(f"Age: {age_minutes:.0f} minutes ago | Signals since: {signals_since}")

    # Determine anchor strength
    if age_minutes > SOFT_STALE_MINUTES and signals_since == 0:
        lines.append(
            "ANCHOR STRENGTH: WEAK — analysis is aging with no confirming signals. "
            "Re-evaluate from scratch. Only maintain if data still strongly supports it."
        )
    elif age_minutes > SOFT_STALE_MINUTES and signals_since > 0:
        lines.append(
            "ANCHOR STRENGTH: MODERATE — thesis is aging but market has been active. "
            "Check whether signals confirm or contradict the previous thesis."
        )
    else:
        lines.append(
            "ANCHOR STRENGTH: STRONG — recent analysis. Maintain unless a key level "
            "was breached or an invalidation condition was met."
        )

    for s in setups:
        direction = s.get("direction", "?")
        asset = s.get("asset", "?")
        playbook = s.get("playbook", "?")
        confidence = s.get("confidence", 0)

        if direction == "no_trade":
            lines.append(f"  • {asset}: NO_TRADE (playbook {playbook})")
            continue

        entry = s.get("entry", {})
        levels = entry.get("levels", {})
        trigger = levels.get("trigger", 0)
        stop = s.get("stop", {}).get("level", 0)

        tps = s.get("take_profits", [])
        tp1 = tps[0].get("level", 0) if tps else 0

        invalidations = s.get("invalidations", [])
        inv_str = "; ".join(invalidations[:2]) if invalidations else "none specified"

        lines.append(
            f"  • {asset} {direction.upper()} (playbook {playbook}, conf={confidence}): "
            f"trigger={trigger}, stop={stop}, TP1={tp1}"
        )
        lines.append(f"    Invalidated if: {inv_str}")

    # Add what signals have happened since
    if signals_since > 0:
        recent = _get_signals_since(signals, prev_ts)
        # Deduplicate and summarize
        summary = _summarize_signals(recent)
        if summary:
            lines.append(f"  Signals since previous analysis:")
            for s_line in summary:
                lines.append(f"    {s_line}")

    return "\n".join(lines)


def _count_signals_since(signals: list[dict], prev_ts: str) -> int:
    """Count signals that occurred after the previous analysis timestamp."""
    count = 0
    for sig in signals:
        sig_ts = sig.get("ts", "")
        if sig_ts > prev_ts:
            count += 1
    return count


def _get_signals_since(signals: list[dict], prev_ts: str) -> list[dict]:
    """Get signals that occurred after the previous analysis."""
    return [s for s in signals if s.get("ts", "") > prev_ts]


def _summarize_signals(signals: list[dict]) -> list[str]:
    """Summarize signals into readable lines, deduplicating noise."""
    # Group by (asset, event, level) and keep the latest
    seen: dict[tuple, dict] = {}
    for sig in signals:
        key = (sig.get("asset"), sig.get("event"), sig.get("level", ""))
        seen[key] = sig  # last one wins

    lines = []
    for (asset, event, level), sig in sorted(seen.items()):
        price = sig.get("price", "?")
        level_val = sig.get("level_value", "")
        pct = sig.get("pct")

        if pct is not None:
            lines.append(f"{asset}: {event} {pct:+.2f}% @ {price}")
        elif level:
            lines.append(f"{asset}: {event} {level} ({level_val}) @ {price}")
        else:
            lines.append(f"{asset}: {event} @ {price}")

    return lines