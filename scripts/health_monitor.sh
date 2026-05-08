#!/bin/bash
# WealthIncome health monitor — runs every minute, logs anomalies, auto-recovers wedged backends.
#
# Reads ports from wealthincome.toml. Designed to run from cron or launchd.
# Single-pass (exits after one check) so the scheduler controls cadence.
#
# What it watches:
#   1. Backend API health endpoint must return 200.
#   2. Backend port must not be held without responding (wedged).
#   3. Trader daemon must be running (PID file alive).
#   4. Each anomaly logs to logs/health.log AND triggers ONE auto-recovery via
#      scripts/launch.sh. Subsequent anomalies in the same minute are recorded
#      but don't double-recover.

set -euo pipefail

# shellcheck source=/Users/wivak/puo-jects/____active/wealthincome-unified/scripts/_config.sh
source "$(dirname "$0")/_config.sh"

HEALTH_LOG="$WI_LOG_DIR/health.log"
RECOVERY_LOCK="$WI_LOG_DIR/.health_recovery.lock"
mkdir -p "$WI_LOG_DIR"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "$(ts) [$1] $2" >> "$HEALTH_LOG"; }

is_healthy_url() {
    local code
    code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 3 "$1" 2>/dev/null || echo "000")
    [ "$code" = "200" ]
}

port_in_use() {
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

trader_alive() {
    local pid_file="$WI_PROJECT/trader.pid"
    [ -f "$pid_file" ] || return 1
    local pid
    pid=$(cat "$pid_file")
    kill -0 "$pid" 2>/dev/null
}

# Don't recover more than once per 5 minutes — avoid recovery storms.
should_attempt_recovery() {
    if [ -f "$RECOVERY_LOCK" ]; then
        local last_recovery age
        last_recovery=$(stat -f %m "$RECOVERY_LOCK" 2>/dev/null || echo 0)
        age=$(( $(date +%s) - last_recovery ))
        [ "$age" -lt 300 ] && return 1
    fi
    touch "$RECOVERY_LOCK"
    return 0
}

ANOMALY=""
RECOVER_NEEDED=0

# Check 1 — backend health
if is_healthy_url "$WI_API_HEALTH_URL"; then
    : # healthy
elif port_in_use "$WI_API_PORT"; then
    ANOMALY="api_wedged"
    RECOVER_NEEDED=1
    log WARN "api wedged on port $WI_API_PORT (held but unresponsive)"
else
    ANOMALY="api_down"
    RECOVER_NEEDED=1
    log ERROR "api not running on port $WI_API_PORT"
fi

# Check 2 — trader
if ! trader_alive; then
    if [ -z "$ANOMALY" ]; then
        ANOMALY="trader_down"
        RECOVER_NEEDED=1
    fi
    log WARN "trader daemon not alive (PID file missing or stale)"
fi

# Auto-recovery — best-effort, rate-limited
if [ "$RECOVER_NEEDED" -eq 1 ]; then
    if should_attempt_recovery; then
        log INFO "auto-recovering via scripts/launch.sh ($ANOMALY)"
        if bash "$WI_PROJECT/scripts/launch.sh" >> "$HEALTH_LOG" 2>&1; then
            log INFO "auto-recovery succeeded"
        else
            log ERROR "auto-recovery FAILED — manual intervention needed"
        fi
    else
        log WARN "anomaly $ANOMALY observed but skipping recovery (cooldown active)"
    fi
fi

# Always emit a heartbeat line so silence in the log = monitor not running
log INFO "heartbeat anomaly=${ANOMALY:-none}"
