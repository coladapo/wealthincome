#!/bin/bash
# WealthIncome production launcher — single blessed entrypoint.
#
# Contract:
#   1. Refuse to start a service if a healthy version is already running.
#   2. Detect and clean up wedged duplicates (port held but unresponsive)
#      before starting fresh.
#   3. Always print a single STATUS line per service so every caller
#      (dashboard, routine, terminal) sees the same truth.
#
# Idempotent and safe to re-run.

set -euo pipefail

# Single source of truth — every port, path, and timeout lives in wealthincome.toml.
# shellcheck source=/Users/wivak/puo-jects/____active/wealthincome-unified/scripts/_config.sh
source "$(dirname "$0")/_config.sh"

PROJECT="$WI_PROJECT"
LOGDIR="$WI_LOG_DIR"
mkdir -p "$LOGDIR"

API_PORT="$WI_API_PORT"
DASH_PORT="$WI_DASH_PORT"
API_HEALTH_URL="$WI_API_HEALTH_URL"
DASH_URL="$WI_DASH_URL"

PLIST_API="$HOME/Library/LaunchAgents/com.wealthincome.api.plist"
TRADER_PID_FILE="$PROJECT/trader.pid"
DASH_PID_FILE="$PROJECT/dashboard.pid"

# ── helpers ──────────────────────────────────────────────────────────────────
status() { printf "STATUS: %-9s %s\n" "$1" "$2"; }

is_healthy_url() {
    # 200 = healthy, anything else = wedged or down
    local code
    code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 3 "$1" 2>/dev/null || echo "000")
    [ "$code" = "200" ]
}

port_in_use() {
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

free_port() {
    # Kill anything listening on $1 (used only to clear wedged state).
    local port="$1"
    local pids
    pids=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $2}' | sort -u || true)
    if [ -n "$pids" ]; then
        echo "  cleaning wedged process(es) on port $port: $pids" >&2
        echo "$pids" | xargs -r kill -9 2>/dev/null || true
        sleep 1
    fi
}

wait_healthy() {
    local url="$1" tries="${2:-15}"
    for _ in $(seq 1 "$tries"); do
        is_healthy_url "$url" && return 0
        sleep 1
    done
    return 1
}

# ── 1. Backend API (launchd-managed) ─────────────────────────────────────────
launch_api() {
    if is_healthy_url "$API_HEALTH_URL"; then
        status "api"       "healthy on :${API_PORT} (no action)"
        return 0
    fi

    if port_in_use "$API_PORT"; then
        echo "  api port :${API_PORT} held but not healthy — wedged. Resetting." >&2
        launchctl unload "$PLIST_API" 2>/dev/null || true
        sleep 1
        free_port "$API_PORT"
    fi

    # Make sure launchd entry is unloaded before reload (prevents duplicate plists).
    launchctl unload "$PLIST_API" 2>/dev/null || true
    sleep 1
    launchctl load "$PLIST_API"

    if wait_healthy "$API_HEALTH_URL" 20; then
        status "api"       "started on :${API_PORT}"
    else
        status "api"       "FAILED — see logs/api.log"
        return 1
    fi
}

# ── 2. Streamlit dashboard ───────────────────────────────────────────────────
launch_dashboard() {
    if is_healthy_url "$DASH_URL"; then
        status "dashboard" "healthy on :${DASH_PORT} (no action)"
        return 0
    fi

    if port_in_use "$DASH_PORT"; then
        echo "  dashboard port :${DASH_PORT} held but not healthy — wedged. Resetting." >&2
        if [ -f "$DASH_PID_FILE" ]; then
            kill -9 "$(cat "$DASH_PID_FILE")" 2>/dev/null || true
            rm -f "$DASH_PID_FILE"
        fi
        free_port "$DASH_PORT"
    fi

    cd "$PROJECT"
    nohup "$PROJECT/venv/bin/streamlit" run app.py \
        --server.port "$DASH_PORT" \
        --server.headless true \
        >> "$LOGDIR/dashboard.log" 2>&1 &
    echo $! > "$DASH_PID_FILE"

    if wait_healthy "$DASH_URL" 15; then
        status "dashboard" "started on :${DASH_PORT}"
    else
        status "dashboard" "FAILED — see logs/dashboard.log"
        return 1
    fi
}

# ── 3. Trader daemon ─────────────────────────────────────────────────────────
launch_trader() {
    if [ -f "$TRADER_PID_FILE" ] && kill -0 "$(cat "$TRADER_PID_FILE")" 2>/dev/null; then
        status "trader"    "healthy (PID $(cat "$TRADER_PID_FILE"), no action)"
        return 0
    fi

    # Stale PID file — remove and check for any orphan trader.
    rm -f "$TRADER_PID_FILE"
    local orphans
    orphans=$(pgrep -f "python -m backend\\.trader" || true)
    if [ -n "$orphans" ]; then
        echo "  found orphan trader process(es): $orphans — terminating." >&2
        echo "$orphans" | xargs -r kill -9 2>/dev/null || true
        sleep 1
    fi

    cd "$PROJECT"
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT/.env"
    set +a
    nohup "$PROJECT/venv/bin/python" -m backend.trader \
        >> "$LOGDIR/trader.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$TRADER_PID_FILE"
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        status "trader"    "started (PID $pid)"
    else
        status "trader"    "FAILED — see logs/trader.log"
        return 1
    fi
}

# ── run all ──────────────────────────────────────────────────────────────────
echo "WealthIncome launch — $(date '+%Y-%m-%d %H:%M:%S')"
launch_api
launch_dashboard
launch_trader
echo "Launch complete."
