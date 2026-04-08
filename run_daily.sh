#!/usr/bin/env bash
# run_daily.sh — Daily weather report automation
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') | Starting daily weather report ==="

# Step 1: Fetch homepage forecasts
echo "[1/2] Fetching homepage forecasts..."
conda run -n env_nlnz python src/fetch_homepage_forecasts.py --output daily_weather.json
echo "      -> daily_weather.json created."

# Step 2: Clean up temp images and run wxcharts
echo "[2/2] Removing temp_images and running wxcharts..."
rm -rf temp_images
wxcharts run
echo "      -> wxcharts finished."

echo "=== Done ==="
