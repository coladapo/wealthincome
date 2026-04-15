#!/bin/bash
# WealthIncome Trader — stop script
# Called by launchd at 1:05pm PT on weekdays

PROJECT="/Users/wivak/puo-jects/____active/wealthincome-unified"
LOG="$PROJECT/logs/trader.log"
PID_FILE="$PROJECT/trader.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        # Graceful shutdown — send SIGTERM first
        kill "$PID"
        sleep 3
        # Force kill if still running
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID"
        fi
        echo "$(date): Trader stopped — PID $PID" >> "$LOG"
    else
        echo "$(date): Trader was not running (stale PID $PID)" >> "$LOG"
    fi
    rm -f "$PID_FILE"
else
    echo "$(date): No PID file found — trader was not running" >> "$LOG"
fi
