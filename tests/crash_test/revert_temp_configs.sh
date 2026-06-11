#!/bin/bash
# Revert all TEMP crash test configs to production values
# Run after 15:17 IST on 2026-06-10

set -e
cd ~/systems/trading-system

echo "Reverting TEMP configs to production values..."
echo "================================================"

# 1. max_daily_trades: 500 → 20
sed -i 's/max_daily_trades: 500.*/max_daily_trades: 20        # hard cap on new trades per day (>= 1)/' config/system_config.yaml
echo "[1] max_daily_trades: 500 → 20"

# 2. max_consecutive_losses: 100 → 4
sed -i 's/max_consecutive_losses: 100.*/max_consecutive_losses: 4   # Production threshold/' config/system_config.yaml
echo "[2] max_consecutive_losses: 100 → 4"

# 3. min_pass_score: 30 → 60
sed -i 's/min_pass_score: 30/min_pass_score: 60/' config/scoring_weights.yaml
echo "[3] min_pass_score: 30 → 60"

# Verify
echo ""
echo "Verification:"
grep max_daily_trades config/system_config.yaml | head -1
grep max_consecutive_losses config/system_config.yaml | head -1
grep min_pass_score config/scoring_weights.yaml | head -1

echo ""
echo "TEMP configs reverted. System restart NOT needed (configs read at startup only)."
echo "Next restart will pick up production values."
