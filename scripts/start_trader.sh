#!/bin/bash
# WealthIncome Trader — start script
# Called by launchd at 6:25am PT on weekdays

PROJECT="/Users/wivak/puo-jects/____active/wealthincome-unified"
LOG="$PROJECT/logs/trader.log"
PID_FILE="$PROJECT/trader.pid"

# Don't start if already running
if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
    echo "$(date): Trader already running (PID $(cat $PID_FILE))" >> "$LOG"
    exit 0
fi

mkdir -p "$PROJECT/logs"

cd "$PROJECT"

# Load environment
set -a
source "$PROJECT/.env"
set +a

# Activate virtualenv and start
source "$PROJECT/venv/bin/activate"

nohup python3 -m backend.trader >> "$LOG" 2>&1 &
echo $! > "$PID_FILE"

echo "$(date): Trader started — PID $(cat $PID_FILE)" >> "$LOG"
