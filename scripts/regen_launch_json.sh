#!/bin/bash
# Regenerate .claude/launch.json from wealthincome.toml.
# Run this whenever ports or paths change in the config.
#
# wealthincome.toml is the source of truth. .claude/launch.json is generated
# (Claude Code requires JSON in this exact path — we can't make it read TOML).

set -euo pipefail

# shellcheck source=/Users/wivak/puo-jects/____active/wealthincome-unified/scripts/_config.sh
source "$(dirname "$0")/_config.sh"

OUT="$WI_PROJECT/.claude/launch.json"
mkdir -p "$(dirname "$OUT")"

VENV_PY="$WI_PROJECT/venv/bin/python"
VENV_STREAMLIT="$WI_PROJECT/venv/bin/streamlit"

cat > "$OUT" <<EOF
{
  "_comment": "GENERATED from wealthincome.toml by scripts/regen_launch_json.sh — do not edit by hand. Run that script after changing the config.",
  "version": "0.0.1",
  "configurations": [
    {
      "name": "backend-api",
      "runtimeExecutable": "$VENV_PY",
      "runtimeArgs": ["-m", "uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "$WI_API_PORT"],
      "port": $WI_API_PORT
    },
    {
      "name": "dashboard-preview",
      "runtimeExecutable": "$VENV_STREAMLIT",
      "runtimeArgs": ["run", "app.py", "--server.port", "$WI_DASH_PREVIEW_PORT", "--server.headless", "true"],
      "port": $WI_DASH_PREVIEW_PORT
    },
    {
      "name": "trader",
      "runtimeExecutable": "$VENV_PY",
      "runtimeArgs": ["-m", "backend.trader"],
      "port": null
    }
  ]
}
EOF

echo "Regenerated $OUT (api=$WI_API_PORT, preview=$WI_DASH_PREVIEW_PORT)"
