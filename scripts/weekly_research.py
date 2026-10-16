#!/usr/bin/env python3
"""Weekly bounded research session (research/weekly_research.py).

Run by com.wealthincome.research.plist on Sundays 09:00 PT. One model call proposes
<= 3 hypotheses; the pipeline runs one trial each and decides. Never places orders.

  venv/bin/python scripts/weekly_research.py --dry-run     # print the prompt, no model call, no writes
  venv/bin/python scripts/weekly_research.py               # the real session
"""

import argparse
import os
import sys
from datetime import datetime

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from research import weekly_research as wr  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scripts/weekly_research.py")
    ap.add_argument("--dry-run", action="store_true", help="build and print the prompt; no model call, no writes")
    ap.add_argument("--artifacts-dir", default=wr.DEFAULT_ARTIFACTS_DIR,
                    help="folder with the edge/hypothesis reports (and where the report copy goes)")
    ap.add_argument("--no-post", action="store_true", help="do not post the Slack line")
    args = ap.parse_args(argv)

    if args.dry_run:
        out = wr.run_weekly_session(dry_run=True, artifacts_dir=args.artifacts_dir)
        print("=== SYSTEM PROMPT ===")
        print(out["system_prompt"])
        print("=== USER PROMPT ===")
        print(out["user_prompt"])
        print(f"=== week {out['week']}: {out['chars']:,} chars, ~{out['tokens_estimate']:,} tokens (chars/4) ===")
        return 0

    print(f"{datetime.now().isoformat(timespec='seconds')} weekly research session starting", flush=True)
    post = (lambda text: False) if args.no_post else None
    rec = wr.run_weekly_session(artifacts_dir=args.artifacts_dir, post=post)
    print(rec["summary_line"])
    for o in rec["outcomes"]:
        print(f"  {o['id']:32s} {o['status']:18s} {o.get('error') or ''}")
    for r in rec["rejected"]:
        p = r["proposal"] if isinstance(r["proposal"], dict) else {}
        print(f"  {p.get('id', '?'):32s} {'rejected':18s} {r['reason']}")
    print(f"  model {rec.get('model_resolved')} in {rec.get('input_tokens')} / out {rec.get('output_tokens')} "
          f"tokens, {rec.get('model_seconds')}s; est ${rec.get('cost_usd_estimate') or 0:.2f}")
    print(f"  report {rec['report_path']}; posted={rec.get('posted')}; session_id={rec['session_id']}")
    print(f"{datetime.now().isoformat(timespec='seconds')} weekly research session done ({rec['status']})", flush=True)
    return 0 if rec["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
