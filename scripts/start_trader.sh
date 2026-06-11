#!/bin/bash
# WealthIncome Trader — start script
# Called by the morning routine, the API /start endpoint, and manual use.
# The trader is launchd-managed (com.wealthincome.trader.plist, KeepAlive=true).
# Always start it through launchctl — a nohup'd trader started from a launchd
# context (e.g. the health monitor) gets killed when the parent job exits.

PROJECT="/Users/wivak/puo-jects/____active/wealthincome-unified"
LOG="$PROJECT/logs/trader.log"
PID_FILE="$PROJECT/trader.pid"
PLIST="$HOME/Library/LaunchAgents/com.wealthincome.trader.plist"

# Don't start if already running
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "$(date): Trader already running (PID $(cat "$PID_FILE"))" >> "$LOG"
    exit 0
fi

mkdir -p "$PROJECT/logs"

if [ ! -f "$PLIST" ]; then
    echo "$(date): ERROR — trader plist missing at $PLIST" >> "$LOG"
    exit 1
fi

launchctl unload "$PLIST" 2>/dev/null
sleep 1
launchctl load "$PLIST"

echo "$(date): Trader start requested via launchctl" >> "$LOG"
