"""Intraday signal tracker — records objective level tests and outcomes.

Runs inside the collector loop, watches for key events, stores them
in a compact format the LLM can consume as context.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.config import DB_PATH

log = logging.getLogger(__name__)

SIGNALS_PATH = DB_PATH.parent.parent / "logs" / "signals_today.jsonl"

# Thresholds
LEVEL_PROXIMITY_PCT = 0.15  # within 0.15% of a level = "testing"


def track_signals(snapshot: dict, prev_snapshot: dict | None) -> None:
    """Compare current snapshot to previous and record events."""
    if not prev_snapshot:
        return

    ts = snapshot["timestamp"]
    signals = []

    for symbol, data in snapshot.get("assets", {}).items():
        prev_data = prev_snapshot.get("assets", {}).get(symbol)
        if not prev_data:
            continue

        mid = data.get("mid")
        prev_mid = prev_data.get("mid")
        if not mid or not prev_mid:
            continue

        # Detect level breaks from candle data
        candles_1d = data.get("candles_1d", [])
        if len(candles_1d) >= 2:
            prev_day = candles_1d[-2]
            pd_high = float(prev_day.get("h", 0))
            pd_low = float(prev_day.get("l", 0))
            pd_close = float(prev_day.get("c", 0))

            # Broke above prior day high
            if prev_mid <= pd_high and mid > pd_high and pd_high > 0:
                signals.append({
                    "ts": ts, "asset": symbol, "event": "broke_above",
                    "level": "prior_day_high", "price": round(mid, 2),
                    "level_value": pd_high
                })

            # Broke below prior day low
            if prev_mid >= pd_low and mid < pd_low and pd_low > 0:
                signals.append({
                    "ts": ts, "asset": symbol, "event": "broke_below",
                    "level": "prior_day_low", "price": round(mid, 2),
                    "level_value": pd_low
                })

            # Broke above prior day close
            if prev_mid <= pd_close and mid > pd_close and pd_close > 0:
                signals.append({
                    "ts": ts, "asset": symbol, "event": "broke_above",
                    "level": "prior_day_close", "price": round(mid, 2),
                    "level_value": pd_close
                })

            # Broke below prior day close
            if prev_mid >= pd_close and mid < pd_close and pd_close > 0:
                signals.append({
                    "ts": ts, "asset": symbol, "event": "broke_below",
                    "level": "prior_day_close", "price": round(mid, 2),
                    "level_value": pd_close
                })

        # Detect intraday high/low breaks from 15m candles
        candles_15m = data.get("candles_15m", [])
        if candles_15m:
            highs = [float(c["h"]) for c in candles_15m[:-1] if c.get("h")]
            lows = [float(c["l"]) for c in candles_15m[:-1] if c.get("l") and float(c["l"]) > 0]
            if highs:
                day_high = max(highs)
                if prev_mid <= day_high and mid > day_high:
                    signals.append({
                        "ts": ts, "asset": symbol, "event": "new_day_high",
                        "price": round(mid, 2), "level_value": round(day_high, 2)
                    })
            if lows:
                day_low = min(lows)
                if prev_mid >= day_low and mid < day_low:
                    signals.append({
                        "ts": ts, "asset": symbol, "event": "new_day_low",
                        "price": round(mid, 2), "level_value": round(day_low, 2)
                    })

        # Detect large moves (> 1% in 5 minutes)
        pct_change = (mid - prev_mid) / prev_mid * 100
        if abs(pct_change) > 1.0:
            signals.append({
                "ts": ts, "asset": symbol,
                "event": "large_move_up" if pct_change > 0 else "large_move_down",
                "pct": round(pct_change, 3),
                "price": round(mid, 2)
            })

    # Write signals
    if signals:
        SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SIGNALS_PATH, "a") as f:
            for sig in signals:
                f.write(json.dumps(sig) + "\n")
        log.debug(f"Recorded {len(signals)} signals")


def load_todays_signals(max_signals: int = 50) -> list[dict]:
    """Load today's signals for LLM context."""
    if not SIGNALS_PATH.exists():
        return []
    try:
        with open(SIGNALS_PATH, "r") as f:
            lines = f.readlines()
        signals = []
        for line in lines:
            line = line.strip()
            if line:
                signals.append(json.loads(line))
        # Return most recent N
        return signals[-max_signals:]
    except Exception as e:
        log.warning(f"Could not load signals: {e}")
        return []


def reset_signals() -> None:
    """Call at start of new trading day to clear signal log."""
    if SIGNALS_PATH.exists():
        SIGNALS_PATH.unlink()