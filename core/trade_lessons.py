"""Self-critique lessons memory — the recursive scaffold (debate verdict 2026-06-16).

The leaky-bucket fix: the trader's in-context learning forgets lessons once
history outgrows the RAG cap. This module gives lessons a DURABLE home and
distills many of them into a small, regime-tagged digest that is always read.

THE BRIGHT LINE (baked in, not optional):
  A lesson may make the trader MORE CAUTIOUS autonomously (lower conviction,
  smaller size, skip a setup). A lesson may NEVER autonomously make it bet
  MORE — increasing exposure/size or relaxing a rule requires backtest +
  forward-paper evidence + human promotion. Enforced two ways: (1) low-N
  lessons are tagged low-confidence and phrased as cautions only; (2) the
  digest header instructs the model that these notes may only reduce
  conviction, never raise it.

Failure mode guarded: eloquent overfitting — confident lessons from 3-trade
samples reading as wisdom. Every lesson carries its sample size; lessons under
MIN_CONFIDENT_N are explicitly "hypothesis, low-confidence."
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

from backend.db import DB_PATH

MIN_CONFIDENT_N = 8          # below this, a lesson cannot claim an edge
DIGEST_MAX_LESSONS = 12      # what the prompt actually carries


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout = 30000")
    return c


def record_lesson(position: Dict, analysis: Optional[Dict] = None) -> None:
    """Write a one-paragraph post-mortem when a position closes. Mechanical,
    grounded in the row — no model call here; the prose is templated from facts
    so it can never hallucinate an outcome."""
    pnl_pct = position.get("realized_pnl_pct")
    if pnl_pct is None and position.get("entry_price") and position.get("exit_price"):
        pnl_pct = (position["exit_price"] - position["entry_price"]) / position["entry_price"] * 100
    sym = position.get("symbol", "?")
    regime = position.get("regime_at_entry") or position.get("close_regime") or "unknown"
    reason = position.get("close_reason", "unknown")
    won = (pnl_pct or 0) > 0

    setup = (f"entry RSI {position.get('entry_rsi')}, regime {regime}, "
             f"conf {position.get('entry_confidence')}")
    expected = "uptrend continuation (long-only momentum entry)"
    outcome = f"{'WIN' if won else 'LOSS'} {round(pnl_pct or 0, 1)}% via {reason}"

    # Count prior closes of the same shape (symbol+regime) for sample size.
    conn = _conn()
    n = conn.execute(
        "SELECT count(*) FROM position_lifecycle WHERE status='closed' "
        "AND symbol=? AND regime_at_entry=?",
        (sym, regime),
    ).fetchone()[0] or 1

    if won:
        lesson = (f"{sym} in {regime}: this setup worked ({outcome}). "
                  f"Sample {n} — { 'note as supportive' if n >= MIN_CONFIDENT_N else 'too thin to raise conviction' }.")
    else:
        lesson = (f"{sym} in {regime}: this setup failed ({outcome}) — exit via {reason}. "
                  f"Treat similar setups with extra caution.")

    confidence = "medium" if n >= MIN_CONFIDENT_N else "low"
    conn.execute(
        "INSERT INTO trade_lessons (created_at, position_id, symbol, regime, exit_reason, "
        "realized_pnl_pct, setup, expected, outcome, lesson, sample_n, confidence) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (datetime.now().isoformat(), position.get("id"), sym, regime, reason,
         round(pnl_pct or 0, 3), setup, expected, outcome, lesson, n, confidence),
    )
    conn.commit()
    conn.close()


def build_lessons_digest() -> str:
    """The distilled, deduped, regime-tagged digest injected into the prompt.

    Loss-lessons (cautions) lead — they're the autonomous-safe half. Win-lessons
    appear only as low-pressure context. Header hard-codes the bright line."""
    conn = _conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM trade_lessons ORDER BY id DESC LIMIT 200"
    ).fetchall()]
    conn.close()
    if not rows:
        return ""

    # Dedupe to the most recent lesson per (symbol, regime, win/loss) cell.
    seen = set()
    cautions: List[str] = []
    supports: List[str] = []
    for r in rows:
        won = (r["realized_pnl_pct"] or 0) > 0
        key = (r["symbol"], r["regime"], won)
        if key in seen:
            continue
        seen.add(key)
        line = f"- {r['lesson']}"
        (supports if won else cautions).append(line)

    out = [
        "=== SELF-CRITIQUE LESSONS (your own closed-trade post-mortems) ===",
        "RULE: these notes may only make you MORE cautious — lower conviction, "
        "smaller size, or skip. They may NOT justify a larger position or "
        "overriding a rule; low-confidence lessons are hypotheses, not edges.",
        "",
        "CAUTIONS (setups that have failed):",
    ]
    out += (cautions[:DIGEST_MAX_LESSONS] or ["- (none yet)"])
    if supports:
        out += ["", "SUPPORTIVE CONTEXT (worked before — do NOT upsize on this alone):"]
        out += supports[: max(0, DIGEST_MAX_LESSONS - len(cautions))]
    return "\n".join(out)


if __name__ == "__main__":
    print(build_lessons_digest() or "(no lessons yet)")
