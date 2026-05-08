#!/bin/bash
# WealthIncome production shutdown — stops trader + dashboard, keeps backend.
#
# Contract:
#   1. Stop the trader daemon gracefully (SIGTERM, then SIGKILL if needed).
#   2. Stop the dashboard (Streamlit) gracefully.
#   3. Leave the backend API running — it's always-on by design.
#   4. Always print a single STATUS line per service.
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

TRADER_PID_FILE="$PROJECT/trader.pid"
DASH_PID_FILE="$PROJECT/dashboard.pid"

# ── helpers ──────────────────────────────────────────────────────────────────
status() { printf "STATUS: %-9s %s\n" "$1" "$2"; }

is_healthy_url() {
    local code
    code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 3 "$1" 2>/dev/null || echo "000")
    [ "$code" = "200" ]
}

is_healthy_url_with_retry() {
    # Retry up to 5 times over ~10s — covers brief blips during sibling shutdowns.
    local url="$1"
    for _ in 1 2 3 4 5; do
        is_healthy_url "$url" && return 0
        sleep 2
    done
    return 1
}

stop_pid_file() {
    # Graceful stop of a process tracked by a PID file.
    local label="$1" pid_file="$2"
    if [ ! -f "$pid_file" ]; then
        status "$label" "no PID file (already stopped)"
        return 0
    fi

    local pid
    pid=$(cat "$pid_file")
    if ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$pid_file"
        status "$label" "stale PID $pid (cleaned up)"
        return 0
    fi

    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
        status "$label" "force-stopped (PID $pid)"
    else
        status "$label" "stopped gracefully (PID $pid)"
    fi
    rm -f "$pid_file"
}

stop_orphans_by_pattern() {
    # Catch any orphan processes matching a pattern (no PID file).
    local label="$1" pattern="$2"
    local pids
    pids=$(pgrep -f "$pattern" || true)
    if [ -n "$pids" ]; then
        echo "  found orphan $label process(es): $pids — terminating." >&2
        echo "$pids" | xargs -r kill 2>/dev/null || true
        sleep 2
        pids=$(pgrep -f "$pattern" || true)
        if [ -n "$pids" ]; then
            echo "$pids" | xargs -r kill -9 2>/dev/null || true
        fi
    fi
}

# ── 1. Trader daemon ─────────────────────────────────────────────────────────
stop_pid_file "trader" "$TRADER_PID_FILE"
stop_orphans_by_pattern "trader" "python -m backend\\.trader"

# ── 2. Streamlit dashboard ───────────────────────────────────────────────────
stop_pid_file "dashboard" "$DASH_PID_FILE"
stop_orphans_by_pattern "dashboard" "streamlit run app\\.py"

# ── 3. Backend API — leave running, just report status ───────────────────────
# Give it a beat to recover from any sibling-shutdown blip, then retry.
sleep 1
if is_healthy_url_with_retry "$API_HEALTH_URL"; then
    status "api" "left running on :${API_PORT} (always-on by design)"
else
    status "api" "WARN — not healthy after retries. Run scripts/launch.sh to recover."
fi

echo "Shutdown complete — $(date '+%Y-%m-%d %H:%M:%S')"
