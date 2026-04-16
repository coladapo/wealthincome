#!/bin/bash
# WealthIncome launcher — starts backend API + Streamlit dashboard

PROJECT="/Users/wivak/puo-jects/____active/wealthincome-unified"
VENV="$PROJECT/venv/bin/activate"
LOGDIR="$PROJECT/logs"
mkdir -p "$LOGDIR"

# ── 1. Start backend API via launchd (idempotent) ────────────────────────────
PLIST="$HOME/Library/LaunchAgents/com.wealthincome.api.plist"
if launchctl list com.wealthincome.api &>/dev/null; then
    echo "API already running"
else
    echo "Starting backend API..."
    launchctl load "$PLIST"
    # Wait up to 10s for it to be reachable
    for i in $(seq 1 10); do
        curl -s http://localhost:8000/health &>/dev/null && break
        sleep 1
    done
    echo "Backend API ready"
fi

# ── 2. Start Streamlit if not already running ────────────────────────────────
if curl -s http://localhost:8501 &>/dev/null; then
    echo "Dashboard already running"
else
    echo "Starting dashboard..."
    source "$VENV"
    cd "$PROJECT"
    nohup streamlit run app.py \
        --server.port 8501 \
        --server.headless true \
        >> "$LOGDIR/dashboard.log" 2>&1 &
    echo $! > "$PROJECT/dashboard.pid"
    # Wait up to 10s for Streamlit to be ready
    for i in $(seq 1 10); do
        curl -s http://localhost:8501 &>/dev/null && break
        sleep 1
    done
    echo "Dashboard ready"
fi

# ── 3. Open in browser ───────────────────────────────────────────────────────
open http://localhost:8501
