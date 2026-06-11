#!/bin/bash
# WealthIncome Trader — stop script
# Called by the evening routine, the API /stop endpoint, and manual use.
# The trader is launchd-managed with KeepAlive=true, so a bare kill would be
# undone by launchd within seconds. A real stop must unload the launchd job.

PROJECT="/Users/wivak/puo-jects/____active/wealthincome-unified"
LOG="$PROJECT/logs/trader.log"
PID_FILE="$PROJECT/trader.pid"
PLIST="$HOME/Library/LaunchAgents/com.wealthincome.trader.plist"

# Unload the launchd job first — this sends SIGTERM and prevents auto-restart.
launchctl unload "$PLIST" 2>/dev/null

# Clean up any straggler started outside launchd.
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        sleep 3
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID"
        fi
        echo "$(date): Trader stopped — PID $PID" >> "$LOG"
    else
        echo "$(date): Trader was not running (stale PID $PID)" >> "$LOG"
    fi
    rm -f "$PID_FILE"
else
    echo "$(date): Trader job unloaded — no PID file found" >> "$LOG"
fi
