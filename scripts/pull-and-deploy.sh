#!/usr/bin/env bash
# =============================================================================
# KnowledgeForge AI: Pull Latest Code, Test & Deploy Pipeline (Bash)
# Modeled after the-visualizer/scripts/pull-and-deploy.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=========================================================="
echo " KnowledgeForge AI: Pull, Test & Deploy Pipeline (Bash)"
echo "=========================================================="

# 1. Stash changes if any
if [ -n "$(git status --porcelain)" ]; then
    echo "[1/4] Stashing local changes..."
    git stash push -m "pull-and-deploy-auto-stash-$(date +%Y%m%d-%H%M%S)"
else
    echo "[1/4] Working tree clean, skipping stash."
fi

# 2. Pull latest
echo "[2/4] Pulling latest code from origin..."
git pull --rebase origin main

# 3. Test (lint, types, unit suite — same commands CI runs)
echo "[3/4] Running lint, type check, and unit tests..."
cd "$REPO_ROOT"
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m "not integration and not live"

# 4. Deploy
echo "[4/4] Executing Cloud Run deployment..."
bash "$SCRIPT_DIR/deploy-cloudrun.sh" "$@"
