"""Trading Suggester V1 — CLI entry point.

Commands:
    collect           Start continuous data collection (60s intervals)
    analyze           Run LLM analysis on current data
    analyze --dry-run Print Market State without calling LLM
    status            Show snapshot count and latest data age
    signals           Show today's detected intraday signals
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone

import httpx

from src import config
from src.collectors.hyperliquid import collect_snapshot
from src.collectors.storage import get_conn, store_snapshot, get_latest_snapshots, get_snapshot_count
from src.display import print_market_state, print_setups, print_market_state_json
from src.features.engine import build_market_state
from src.llm.factory import get_llm_client
from src.llm.prompt import SYSTEM_PROMPT
from src.validation.validator import validate_llm_output

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("main")

_running = True

# Track the current day for signal reset
_current_day: str | None = None


def _load_previous_analysis() -> dict | None:
    """Load the most recent LLM output for anchoring."""
    log_path = config.DB_PATH.parent.parent / "logs" / "llm_outputs.jsonl"
    if not log_path.exists():
        return None
    try:
        with open(log_path, "r") as f:
            lines = f.readlines()
        if not lines:
            return None
        for line in reversed(lines):
            line = line.strip()
            if line:
                return json.loads(line)
    except Exception as e:
        log.warning(f"Could not load previous analysis: {e}")
    return None


def _check_daily_reset() -> None:
    """Reset signal log at the start of each new UTC day."""
    global _current_day
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _current_day is None:
        _current_day = today
    elif today != _current_day:
        from src.features.signals import reset_signals
        reset_signals()
        log.info(f"New day {today} — reset intraday signals")
        _current_day = today


def _handle_sigint(sig, frame):
    global _running
    _running = False
    print("\nShutting down...")


def cmd_collect(args: argparse.Namespace) -> None:
    """Continuous data collection loop."""
    signal.signal(signal.SIGINT, _handle_sigint)

    conn = get_conn()
    client = httpx.Client()
    interval = config.POLL_INTERVAL_SECONDS

    log.info(f"Collecting {config.ASSETS} every {interval}s. Ctrl+C to stop.")

    while _running:
        try:
            # Check for daily signal reset
            _check_daily_reset()

            t0 = time.time()
            snapshot = collect_snapshot(client)
            store_snapshot(conn, snapshot)
            count = get_snapshot_count(conn)

            # Track intraday signals
            from src.features.signals import track_signals
            prev_snaps = get_latest_snapshots(conn, n=2)
            prev_snap = prev_snaps[1] if len(prev_snaps) >= 2 else None
            track_signals(snapshot, prev_snap)
            elapsed = time.time() - t0

            prices = "  ".join(
                f"{sym}={snapshot['assets'].get(sym, {}).get('mid', '?')}"
                for sym in config.ASSETS
            )
            log.info(f"#{count} {prices} ({elapsed:.1f}s)")

        except httpx.HTTPError as e:
            log.error(f"HTTP error: {e}")
        except Exception as e:
            log.error(f"Collection error: {e}", exc_info=True)

        elapsed = time.time() - t0
        sleep_time = max(0, interval - elapsed)
        if _running and sleep_time > 0:
            time.sleep(sleep_time)

    client.close()
    conn.close()
    log.info("Collector stopped.")


def cmd_analyze(args: argparse.Namespace) -> None:
    """Run analysis on current data."""
    conn = get_conn()
    count = get_snapshot_count(conn)

    if count == 0:
        print("No snapshots yet. Run 'collect' first and wait a few minutes.")
        conn.close()
        return

    snapshots = get_latest_snapshots(conn, n=60)
    log.info(f"Loaded {len(snapshots)} snapshots (total in DB: {count})")

    # Cooldown check
    prev = _load_previous_analysis()
    if prev and prev.get("timestamp"):
        try:
            prev_dt = datetime.fromisoformat(prev["timestamp"])
            now = datetime.now(timezone.utc)
            minutes_since = (now - prev_dt).total_seconds() / 60
            if minutes_since < 30 and not args.dry_run:
                log.warning(
                    f"Last analysis was {minutes_since:.0f}min ago. "
                    f"Running again in a range market generates noise. "
                    f"Consider waiting at least 30min between calls."
                )
        except Exception:
            pass

    market_state = build_market_state(snapshots)
    if not market_state:
        print("Failed to build Market State from snapshots.")
        conn.close()
        return

    if args.dry_run:
        print("\n--- DRY RUN: Market State (no LLM call) ---")
        print_market_state(market_state)
        print("\n--- Full JSON ---")
        print_market_state_json(market_state)
        conn.close()
        return

    print_market_state(market_state)

    try:
        llm = get_llm_client()
    except ValueError as e:
        print(f"LLM setup error: {e}")
        conn.close()
        return

    state_json = json.dumps(market_state.model_dump(), default=str)

    # Load previous analysis for anchoring
    prev_analysis = _load_previous_analysis()

    # Load intraday signals
    from src.features.signals import load_todays_signals
    signals = load_todays_signals(max_signals=30)

    anchored_state = state_json

    if signals:
        signals_text = json.dumps(signals, default=str)
        anchored_state += (
            f"\n\nINTRADAY SIGNALS (objective events detected today):\n"
            f"{signals_text}"
        )

    # Build concise anchoring summary (replaces raw JSON dump)
    from src.anchoring import build_anchoring_context
    anchor_summary = build_anchoring_context(prev_analysis, signals)

    if anchor_summary:
        anchored_state += (
            f"\n\nPREVIOUS ANALYSIS SUMMARY (do NOT copy — re-evaluate using current data):\n"
            f"{anchor_summary}"
        )
    else:
        log.info("No anchoring context — fresh analysis")

    try:
        raw_response = llm.analyze(anchored_state, SYSTEM_PROMPT)
    except Exception as e:
        log.error(f"LLM call failed: {e}")
        print(f"LLM call failed: {e}")
        conn.close()
        return

    output, errors = validate_llm_output(raw_response)

    if output is None:
        print("LLM output failed validation:")
        for e in errors:
            print(f"  ✗ {e}")
        print(f"\nRaw response:\n{raw_response[:2000]}")
        conn.close()
        return

    print_setups(output, errors)

    log_path = config.DB_PATH.parent.parent / "logs" / "llm_outputs.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps({
            "timestamp": output.timestamp,
            "raw": raw_response,
            "errors": errors,
        }) + "\n")
    log.info(f"Output logged to {log_path}")

    conn.close()


def cmd_status(args: argparse.Namespace) -> None:
    """Show collection status."""
    conn = get_conn()
    count = get_snapshot_count(conn)

    if count == 0:
        print("No snapshots collected yet.")
        conn.close()
        return

    snapshots = get_latest_snapshots(conn, n=1)
    latest_ts = snapshots[0]["timestamp"]
    now = datetime.now(timezone.utc)

    try:
        snap_dt = datetime.fromisoformat(latest_ts)
        age_sec = (now - snap_dt).total_seconds()
        age_str = f"{age_sec:.0f}s ago"
    except Exception:
        age_str = "unknown"

    print(f"Snapshots: {count}")
    print(f"Latest: {latest_ts} ({age_str})")
    print(f"Assets: {config.ASSETS}")
    print(f"DB: {config.DB_PATH}")
    conn.close()


def cmd_signals(args: argparse.Namespace) -> None:
    """Show today's detected intraday signals."""
    from src.features.signals import load_todays_signals, SIGNALS_PATH

    signals = load_todays_signals(max_signals=100)

    if not signals:
        print(f"\nNo signals detected today.")
        print(f"Signals fire when price crosses key levels (prior day high/low/close,")
        print(f"new intraday high/low, or >1% moves between snapshots).")
        print(f"\nSignals file: {SIGNALS_PATH}")
        print(f"File exists: {SIGNALS_PATH.exists()}")
        if SIGNALS_PATH.exists():
            import os
            size = os.path.getsize(SIGNALS_PATH)
            print(f"File size: {size} bytes")
        return

    # Group by asset
    by_asset: dict[str, list[dict]] = {}
    for s in signals:
        asset = s.get("asset", "?")
        by_asset.setdefault(asset, []).append(s)

    print(f"\n{'='*70}")
    print(f"  INTRADAY SIGNALS  |  {len(signals)} events today")
    print(f"{'='*70}")

    for asset in sorted(by_asset.keys()):
        sigs = by_asset[asset]
        print(f"\n  {asset}:")
        for s in sigs:
            ts = s.get("ts", "?")
            # Parse to show just time
            try:
                dt = datetime.fromisoformat(ts)
                time_str = dt.strftime("%H:%M:%S UTC")
            except Exception:
                time_str = ts[:19] if len(ts) > 19 else ts

            event = s.get("event", "?")
            level = s.get("level", "")
            price = s.get("price", "?")
            level_val = s.get("level_value", "")
            pct = s.get("pct")

            if pct is not None:
                print(f"    {time_str}  {event}  {pct:+.2f}%  @ {price}")
            elif level:
                print(f"    {time_str}  {event} {level} ({level_val})  @ {price}")
            else:
                print(f"    {time_str}  {event}  @ {price}")

    print(f"\n  File: {SIGNALS_PATH}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trading Suggester V1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("collect", help="Start continuous data collection")

    analyze_p = sub.add_parser("analyze", help="Run LLM trade analysis")
    analyze_p.add_argument(
        "--dry-run", action="store_true",
        help="Print Market State without calling LLM"
    )

    sub.add_parser("status", help="Show collection status")
    sub.add_parser("signals", help="Show today's intraday signals")

    args = parser.parse_args()

    if args.command == "collect":
        cmd_collect(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "signals":
        cmd_signals(args)


if __name__ == "__main__":
    main()