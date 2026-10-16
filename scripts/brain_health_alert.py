#!/usr/bin/env python3
"""After-close brain-health alert → Slack.

Run by com.wealthincome.brainhealth.plist weekdays at 13:20 PT. If fewer
than 80% of today's LLM calls came back with a decision (and there were at
least 5), post one line to the #wealthincome webhook. If the previous session
was ALSO under 80%, escalate the same line via slack-push.sh — to the PROJECT channel (#wealthincome); Chris reads the project channel, not a general alerts channel (his call, 2026-10-16).
At most one alert per day (state in data/persistent/brain_alert_state.json).
Read-only on trading state; safe to run manually.
"""

import json
import os
import subprocess
import sys
from datetime import date, datetime
from typing import Optional

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "scripts"))

from core.brain_health import brain_stats, recent_sessions  # noqa: E402
from daily_digest import _post_via_webhook  # noqa: E402

SLACK_PUSH = os.path.expanduser("~/.claude/bin/slack-push.sh")
STATE_FILE = os.path.join(PROJECT, "data", "persistent", "brain_alert_state.json")
THRESHOLD_PCT = 80.0
MIN_ASKS = 5


def _low(s: Optional[dict]) -> bool:
    return bool(s) and s["asks"] >= MIN_ASKS and s["success_rate_pct"] < THRESHOLD_PCT


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _post_alerts(text: str) -> bool:
    if not os.path.exists(SLACK_PUSH):
        return False
    r = subprocess.run([SLACK_PUSH, "wealthincome", text], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        print(f"slack-push alerts failed: {r.stderr.strip()}", file=sys.stderr)
    return r.returncode == 0


def _alert_line(s: dict, prev: Optional[dict]) -> str:
    line = (f"⚠️ Trader brain unreliable: {s['successes']}/{s['asks']} calls OK today "
            f"({s['success_rate_pct']:.0f}%) — {s['timeouts']} timeouts, {s['failures']} failures")
    if prev:
        line += f"; previous session {prev['date']} {prev['success_rate_pct']:.0f}%"
    return line


def run(today: Optional[date] = None, post_webhook=_post_via_webhook, post_alerts=_post_alerts) -> str:
    """Returns the outcome string (also the log heartbeat)."""
    today = today or date.today()
    s = brain_stats(today)
    if not _low(s):
        return f"ok ({s['successes']}/{s['asks']} calls)"

    state = _load_state()
    if state.get("last_alert_date") == today.isoformat():
        return "low, already alerted today"

    earlier = [r for r in recent_sessions(n=10) if r["date"] < today.isoformat()]
    prev = earlier[0] if earlier else None
    prev_low = _low(prev)
    text = _alert_line(s, prev if prev_low else None)

    sent_webhook = post_webhook(text)
    sent_alerts = post_alerts(text) if prev_low else False
    if sent_webhook or sent_alerts:  # nothing delivered → let a manual re-run retry
        state.update({"last_alert_date": today.isoformat(), "last_alert_text": text,
                      "escalated": prev_low})
        _save_state(state)
    return (f"low; webhook={'sent' if sent_webhook else 'FAILED'}"
            + (f", alerts={'sent' if sent_alerts else 'FAILED'}" if prev_low else ""))


def main() -> int:
    outcome = run()
    print(f"{datetime.now().isoformat(timespec='seconds')} brain-health run: {outcome}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
