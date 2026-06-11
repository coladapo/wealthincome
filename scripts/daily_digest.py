#!/usr/bin/env python3
"""Daily market-close digest → Slack (#agent-alerts).

Run by com.wealthincome.digest.plist weekdays at 13:10 PT (after close).
Read-only; never touches trading state. Safe to run manually any time.
"""

import os
import sqlite3
import subprocess
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

SLACK_PUSH = os.path.expanduser("~/.claude/bin/slack-push.sh")
CHANNEL = "wealthincome"  # dedicated channel C0BA1KHAUUU (fallback path)
WEBHOOK_FILE = os.path.expanduser("~/.claude/secrets/wealthincome-slack-webhook")


def _post_via_webhook(text: str) -> bool:
    """Post to the #wealthincom-trader channel via its incoming webhook."""
    if not os.path.exists(WEBHOOK_FILE):
        return False
    with open(WEBHOOK_FILE) as f:
        url = f.read().strip()
    if not url:
        return False
    import json
    import urllib.request
    req = urllib.request.Request(
        url,
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"webhook post failed: {e}", file=sys.stderr)
        return False


def main() -> int:
    from core.scorecard import compute_scorecard, format_digest
    from backend.db import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    trades_today = conn.execute(
        "SELECT count(*) FROM trades WHERE date(executed_at) = date('now', 'localtime')"
    ).fetchone()[0]
    errors_today = conn.execute(
        "SELECT count(*) FROM errors WHERE date(occurred_at) = date('now', 'localtime')"
    ).fetchone()[0]
    conn.close()

    card = compute_scorecard()
    text = format_digest(card, trades_today=trades_today, errors_today=errors_today)

    if _post_via_webhook(text):
        return 0

    if os.path.exists(SLACK_PUSH):
        result = subprocess.run(
            [SLACK_PUSH, CHANNEL, text], capture_output=True, text=True, timeout=20
        )
        if result.returncode != 0:
            print(f"slack push failed: {result.stderr}", file=sys.stderr)
            print(text)
            return 1
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
