#!/usr/bin/env python3
"""Daily self-heal run: diagnose → auto-fix the safe set → report to Slack.

Authority bounded by the 2026-06-16 cross-provider debate verdict (see
core/self_heal.py): auto-fix only reversible, unambiguous, risk-reducing
repairs with conservative fixed parameters; propose everything else.
"""

import os
import subprocess
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

SLACK_PUSH = os.path.expanduser("~/.claude/bin/slack-push.sh")


def main() -> int:
    from core.self_heal import diagnose

    findings = diagnose()
    auto = [f for f in findings if f.severity == "auto"]
    propose = [f for f in findings if f.severity == "propose"]

    # Execute the auto-fixable set. Each fix is individually guarded — one
    # failure never blocks the rest, and a failure becomes a propose-line.
    fixed, failed = [], []
    for f in auto:
        try:
            f.fixed_result = f.fix()
            fixed.append(f)
        except Exception as e:
            f.fixed_result = f"FAILED: {e}"
            failed.append(f)

    # Build the report.
    if not findings:
        msg = "🧰 Self-heal: nothing to do — positions protected, no orphans, DB matches broker."
    else:
        lines = ["🧰 Self-heal daily:"]
        if fixed:
            lines.append(f"✅ auto-fixed {len(fixed)}: " + "; ".join(f.fixed_result for f in fixed))
        if failed:
            lines.append(f"⚠️ fix failed {len(failed)}: " + "; ".join(f.title for f in failed))
        if propose:
            lines.append(f"🙋 needs Chris ({len(propose)}): " + "; ".join(f.title for f in propose))
        msg = "\n".join(lines)

    # Detail to stdout (session log); summary to Slack.
    print(msg)
    for f in propose:
        print(f"\n[PROPOSE] {f.title}\n  {f.detail}")

    if os.path.exists(SLACK_PUSH):
        subprocess.run([SLACK_PUSH, "wealthincome", msg[:1500]], timeout=20)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
