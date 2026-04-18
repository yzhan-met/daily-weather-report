#!/usr/bin/env bash
# run_daily.sh — Daily weather report automation
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') | Starting daily weather report ==="

# Step 1: Fetch homepage forecasts
echo "[1/4] Fetching homepage forecasts..."
conda run -n env_nlnz python src/fetch_homepage_forecasts.py --output daily_weather.json
echo "      -> daily_weather.json created."

# Step 2: Clean up temp images and run wxcharts
echo "[2/4] Removing temp_images and running wxcharts..."
rm -rf temp_images
wxcharts run
echo "      -> wxcharts finished."

# Add a pause and allow user to review wxcharts output before proceeding
read -p "Press Enter to continue to WeChat article generation (or Ctrl+C to abort)"

# Step 3: Generate WeChat article via Gemini API
echo "[3/4] Generating WeChat article..."
conda run -n env_nlnz python src/generate_wechat_article.py \
    --input daily_weather.json \
    --output wechat_article.md
echo "      -> wechat_article.md created."

# Step 4: Upload draft to WeChat Official Account (best-effort)
echo "[4/4] Uploading draft to WeChat Official Account..."
if conda run -n env_nlnz python src/upload_wechat_draft.py \
    --input wechat_article.md; then
    echo "      -> Draft uploaded successfully."
else
    echo "      ⚠ Draft upload failed (check WECHAT_APP_ID / WECHAT_APP_SECRET and IP whitelist). Continuing."
fi

echo "=== Done ==="
