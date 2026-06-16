"""Daily self-heal diagnostic (cross-provider debate verdict, 2026-06-16).

Bright line — the ONLY rule that matters here:
  AUTO-FIX  iff  reversible AND unambiguous AND risk-reducing, with conservative
            FIXED parameters (never inferred).
  PROPOSE   for anything touching trading policy/logic, market exposure
            (forcing entries/exits), sizing, code merges, OR ambiguous state
            (DB and broker disagree on which is right).
  When the classification itself is uncertain → downgrade to PROPOSE.

The worst failure mode the panel named: a confident wrong "fix" that leaves
git/tests/logs looking clean while real risk is worse. So every auto-fix here
is provably risk-reducing on broker-confirmed state, or it doesn't run.

This module DETECTS and classifies. The routine (scripts/daily_self_heal.py)
decides whether to execute the auto-fixable ones and always reports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class Finding:
    severity: str                 # "auto" (safe to fix) | "propose" (needs Chris) | "info"
    title: str
    detail: str
    fix: Optional[Callable[[], str]] = None   # only set for severity=="auto"
    fixed_result: Optional[str] = None


def _alpaca():
    from core.alpaca_client import AlpacaClient
    if not os.environ.get("ALPACA_API_KEY"):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for line in open(os.path.join(root, ".env")):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"'))
    return AlpacaClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True)


# Conservative FIXED parameters — never inferred per-position.
DEFAULT_TRAIL_PCT = 12.0


def diagnose() -> List[Finding]:
    """Read-only scan. Returns findings; fixes are deferred to the caller."""
    findings: List[Finding] = []
    import sqlite3
    from backend.db import DB_PATH

    a = _alpaca()
    market_open = False
    try:
        market_open = a.is_market_open()
    except Exception:
        pass

    broker_positions = {p.symbol: p for p in a.get_positions()}
    open_orders = [o for o in a.get_orders(status="open", limit=200)]
    open_sells_by_sym: dict = {}
    for o in open_orders:
        if o.side == "sell":
            open_sells_by_sym.setdefault(o.symbol, []).append(o)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    lifecycles = conn.execute(
        "SELECT id, symbol, entry_qty, entry_price, trailing_stop_order_id "
        "FROM position_lifecycle WHERE status='open'"
    ).fetchall()
    conn.close()
    lifecycle_syms = {r["symbol"] for r in lifecycles}

    # ── AUTO #1: broker-confirmed position with NO protective stop ────────────
    # Reversible (we can cancel a stop), unambiguous (broker says we hold it,
    # no open sell exists), risk-reducing (adds protection). Conservative fixed
    # trail. Only when market is open (stops rest otherwise).
    for sym, pos in broker_positions.items():
        has_open_sell = sym in open_sells_by_sym
        # Only re-arm a stop on a position the DB also tracks. A broker-held but
        # DB-untracked symbol is AMBIGUOUS (handled as propose below) — never
        # auto-act on it, even for something as benign-seeming as a stop.
        tracked = sym in lifecycle_syms
        if not has_open_sell and market_open and tracked:
            qty = abs(int(float(pos.qty)))
            def _mk(sym=sym, qty=qty):
                def _do():
                    ts = a.place_trailing_stop_order(symbol=sym, qty=qty, trail_percent=DEFAULT_TRAIL_PCT)
                    return f"placed {DEFAULT_TRAIL_PCT}% trailing stop on {qty} {sym} ({ts.id[:8]})"
                return _do
            findings.append(Finding(
                "auto", f"{sym}: held position with no protective stop",
                f"Broker shows {pos.qty} {sym}, no open sell order. Re-arm a "
                f"conservative {DEFAULT_TRAIL_PCT}% trailing stop.",
                fix=_mk(),
            ))

    # ── AUTO #2: orphaned sell order — open sell for a symbol not held ────────
    # Reversible (cancel), unambiguous (broker holds 0 shares), risk-reducing
    # (clears a jam-causing ghost). This is the MPC/ABBV exit-jam class.
    for sym, sells in open_sells_by_sym.items():
        held = abs(int(float(broker_positions[sym].qty))) if sym in broker_positions else 0
        if held == 0:
            for o in sells:
                def _mk(oid=o.id, sym=sym):
                    def _do():
                        a.cancel_order(oid)
                        return f"cancelled orphaned sell {oid[:8]} on {sym} (0 shares held)"
                    return _do
                findings.append(Finding(
                    "auto", f"{sym}: orphaned sell order on a position no longer held",
                    f"Open sell {o.id[:8]} but broker holds 0 {sym}. Cancel it.",
                    fix=_mk(),
                ))

    # ── PROPOSE: DB and broker DISAGREE on a position (ambiguous state) ───────
    for sym in lifecycle_syms - set(broker_positions):
        findings.append(Finding(
            "propose", f"{sym}: open in our records but NOT held at broker",
            "Ambiguous — could be an unrecorded exit or a fill we missed. "
            "Do NOT auto-resolve; Chris should confirm which is truth.",
        ))
    for sym in set(broker_positions) - lifecycle_syms:
        findings.append(Finding(
            "propose", f"{sym}: held at broker but NO open record in our DB",
            "Ambiguous — untracked position. Needs a human to reconstruct entry "
            "context before the system manages it.",
        ))

    return findings
