#!/usr/bin/env python3
"""Daily market-close digest → Slack (#agent-alerts).

Run by com.wealthincome.digest.plist weekdays at 13:10 PT (after close).
Read-only; never touches trading state. Safe to run manually any time.
"""

import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

SLACK_PUSH = os.path.expanduser("~/.claude/bin/slack-push.sh")
CHANNEL = "wealthincome"  # dedicated channel C0BA1KHAUUU (fallback path)
WEBHOOK_FILE = os.path.expanduser("~/.claude/secrets/wealthincome-slack-webhook")

# launchd runs with a bare PATH (/usr/bin:/bin:/usr/sbin:/sbin); slack-push.sh
# needs node (found in /usr/local/bin or /opt/homebrew/bin). Broken fallback
# caused the Jun 23-24 digest losses to go fully silent.
_ENV = dict(os.environ)
_ENV["PATH"] = "/usr/local/bin:/opt/homebrew/bin:" + _ENV.get("PATH", "/usr/bin:/bin")


def _heartbeat(outcome: str) -> None:
    """One line per run, success or failure, so digest.log is a real
    liveness signal (previously the script wrote nothing on success and
    a silent log was indistinguishable from a dead job)."""
    print(f"{datetime.now().isoformat(timespec='seconds')} digest run: {outcome}", flush=True)


def _post_via_webhook(text: str, attempts: int = 3, delay: int = 30) -> bool:
    """Post to the #wealthincom-trader channel via its incoming webhook.

    Retries a few times: the 13:10 fire often coincides with wake-from-sleep
    when DNS is not up yet (this is what silently ate the Jun 23-24 digests).
    """
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
    for i in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    return True
                print(f"webhook attempt {i}/{attempts}: status {resp.status}", file=sys.stderr)
        except Exception as e:
            print(f"webhook attempt {i}/{attempts} failed: {e}", file=sys.stderr)
        if i < attempts:
            time.sleep(delay)
    return False


def _brain_and_gate():
    """Brain-health and real-money-gate inputs; a failure here must never
    cost the digest itself, so each one degrades to None (line omitted)."""
    brain = gate = None
    try:
        from core.brain_health import brain_stats
        brain = brain_stats()
    except Exception as e:
        print(f"brain stats failed: {e}", file=sys.stderr)
    try:
        from core.real_money_gate import compute_gate
        gate = compute_gate()
    except Exception as e:
        print(f"gate failed: {e}", file=sys.stderr)
    return brain, gate


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
    brain, gate = _brain_and_gate()
    text = format_digest(card, trades_today=trades_today, errors_today=errors_today,
                         brain=brain, gate=gate)

    if _post_via_webhook(text):
        _heartbeat("posted via webhook")
        return 0

    if os.path.exists(SLACK_PUSH):
        result = subprocess.run(
            [SLACK_PUSH, CHANNEL, text],
            capture_output=True, text=True, timeout=20, env=_ENV,
        )
        if result.returncode != 0:
            print(f"slack push failed: {result.stderr}", file=sys.stderr)
            print(text)
            _heartbeat("FAILED (webhook and slack-push both down); digest printed above")
            return 1
        _heartbeat("posted via slack-push fallback")
    else:
        print(text)
        _heartbeat("no delivery path; digest printed above")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
