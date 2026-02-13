"""SQLite storage for market snapshots."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from src.config import DB_PATH

log = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(timestamp);
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(_DDL)
    return conn


def store_snapshot(conn: sqlite3.Connection, snapshot: dict) -> None:
    conn.execute(
        "INSERT INTO snapshots (timestamp, data) VALUES (?, ?)",
        (snapshot["timestamp"], json.dumps(snapshot)),
    )
    conn.commit()


def get_latest_snapshots(conn: sqlite3.Connection, n: int = 60) -> list[dict]:
    """Retrieve the last N snapshots (most recent first)."""
    rows = conn.execute(
        "SELECT data FROM snapshots ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


def get_snapshot_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()
    return row[0] if row else 0
