#!/bin/zsh
# Trading Suggester V1 — macOS/zsh setup
# Copy-paste this entire block into your terminal.

# 1. Create project (adjust path if you want it elsewhere)
cd ~/Projects 2>/dev/null || mkdir -p ~/Projects && cd ~/Projects

# 2. Clone or create directory
mkdir -p trading-suggester && cd trading-suggester

# 3. Create venv
python3 -m venv .venv
source .venv/bin/activate

# 4. Install deps
pip install httpx openai python-dotenv pydantic

# 5. Create directory structure
mkdir -p src/{collectors,features,llm,models,validation} data logs
touch src/__init__.py
touch src/{collectors,features,llm,models,validation}/__init__.py

echo "✓ Setup complete. Now:"
echo "  1. Copy all .py files from the repo into their paths"
echo "  2. cp .env.example .env && edit .env with your OPENAI_API_KEY"
echo "  3. python -m src.main collect    (start collecting in one tab)"
echo "  4. python -m src.main analyze    (run analysis in another tab)"
