# Trading Suggester V1

Read-only Hyperliquid data collector + LLM-powered trade plan generator.
Collects market snapshots every 60s, builds structured Market State,
and on-demand calls an LLM to produce 3 bracketed trade setups from
a fixed playbook menu.

## Architecture

```
Hyperliquid (read-only)
    │ 60s poll
    ▼
Collector ──► SQLite (snapshots)
                  │
                  ▼ on-demand
           Feature Engine (levels, vol, flow)
                  │
                  ▼
           LLM Analyst (strict JSON, schema-validated)
                  │
                  ▼
           Terminal Output (ranked setups)
```

## Setup (macOS / zsh)

```zsh
cd trading-suggester
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env → add your OPENAI_API_KEY and adjust settings
```

## Commands

```zsh
# Start data collection (runs continuously, Ctrl+C to stop):
python -m src.main collect

# Trigger LLM analysis (run in a separate terminal tab):
python -m src.main analyze

# Dry-run — prints Market State JSON, no LLM call:
python -m src.main analyze --dry-run

# Check how many snapshots are stored:
python -m src.main status

# Check if any signals occured today:
python3 -m src.main signals
```

## Adding Assets

Edit `ASSETS` in `.env` (comma-separated): `ASSETS=BTC,ETH,SOL,HYPE`

## Future: Multi-LLM

The LLM layer is modular. To add a second provider, create a new class
in `src/llm/` implementing the `BaseLLMClient` interface and register
it in the factory. Config selects provider via `LLM_PROVIDER` env var.
