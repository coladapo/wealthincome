#!/usr/bin/env python3
"""
Rebuild realized P&L from Alpaca's own fills and report how far the
position_lifecycle ledger is off.

Read-only against Alpaca (GET account / positions / account activities only —
never places, cancels or changes an order). Writes only the derived tables
broker_fills and fifo_realized; position_lifecycle is never touched.

Usage:
    venv/bin/python scripts/reconcile_ledger.py              # print report
    venv/bin/python scripts/reconcile_ledger.py --out FILE   # also write markdown
    venv/bin/python scripts/reconcile_ledger.py --json       # machine-readable

Keys come from the environment, falling back to .env (never printed).
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _load_env():
    if os.environ.get("ALPACA_API_KEY"):
        return
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", help="write the markdown report here too")
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    ap.add_argument("--db", help="SQLite path (default: backend.db.DB_PATH)")
    args = ap.parse_args()

    _load_env()
    from core.alpaca_client import AlpacaClient
    from backend import reconciliation

    alpaca = AlpacaClient(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
        paper=os.environ.get("ALPACA_PAPER", "true").lower() == "true",
    )
    report = reconciliation.run(alpaca, db_path=args.db)
    md = reconciliation.format_report(report)
    if args.out:
        with open(args.out, "w") as f:
            f.write(md)
    print(json.dumps(report, indent=2, default=str) if args.json else md)


if __name__ == "__main__":
    main()
