"""Centralized config loaded from .env — single source of truth."""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")


def _csv(key: str, default: str = "") -> list[str]:
    return [s.strip() for s in os.getenv(key, default).split(",") if s.strip()]


# --- LLM ---
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")
LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "4000"))
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# --- Assets ---
ASSETS: list[str] = _csv("ASSETS", "BTC,ETH")

# --- Risk ---
EQUITY_USD: float = float(os.getenv("EQUITY_USD", "10000"))
MAX_RISK_PER_TRADE_PCT: float = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "1.0"))
MAX_TOTAL_RISK_PCT: float = float(os.getenv("MAX_TOTAL_RISK_PCT", "2.0"))
MIN_LEVERAGE: int = int(os.getenv("MIN_LEVERAGE", "1"))
MAX_LEVERAGE: int = int(os.getenv("MAX_LEVERAGE", "6"))

# --- Collection ---
POLL_INTERVAL_SECONDS: int = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

# --- DB ---
DB_PATH: Path = _ROOT / os.getenv("DB_PATH", "data/snapshots.db")

# --- Logging ---
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# --- Hyperliquid ---
HL_INFO_URL: str = "https://api.hyperliquid.xyz/info"
