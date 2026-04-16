"""
Performance Intelligence — self-improving feedback loop for the trading agent.

Every time a position closes, this module:
  1. Scores which entry signals were present at entry
  2. Calibrates per-signal win rates and avg P&L
  3. Evaluates exit quality (did we leave money on the table?)
  4. Checks confidence calibration (is Claude over/under-confident?)
  5. Builds a performance intelligence block injected into every Claude prompt

The agent gets measurably smarter with every closed trade — no model fine-tuning,
just richer context. Returns '' gracefully when < MIN_TRADES data exists.
"""

import re
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.db import DB_PATH

logger = logging.getLogger(__name__)

MIN_TRADES = 5       # minimum closed positions before any output
LOOKBACK   = 30      # default lookback window (trades, not days)


# ─── Signal extraction ────────────────────────────────────────────────────────

def _score_signals_from_position(pos: dict, trade: dict) -> Dict[str, bool]:
    """
    Determine which entry signals were present at the time of the buy.
    Uses numeric fields first, falls back to regex on reasoning text.
    Returns {signal_type: bool}
    """
    signals = {}
    reasoning = (trade.get("reasoning") or pos.get("notes") or "").lower()

    # ── Numeric fields (populated on newer trades) ───────────────────────────
    entry_price  = pos.get("entry_price") or 0
    entry_rsi    = pos.get("entry_rsi")
    entry_sma20  = pos.get("entry_sma20")
    entry_sma50  = pos.get("entry_sma50")
    entry_vol    = pos.get("entry_volume_ratio")  # ratio vs avg
    entry_macd   = pos.get("entry_macd_histogram")

    # RSI in momentum range 40-75
    if entry_rsi is not None:
        signals["rsi_in_range"] = 40 <= entry_rsi <= 75
    else:
        signals["rsi_in_range"] = bool(re.search(r"rsi\s+\d+", reasoning) and
                                       not re.search(r"rsi.*overbought|overbought.*rsi", reasoning))

    # Price above SMA50
    if entry_sma50 is not None and entry_price:
        signals["above_sma50"] = entry_price > entry_sma50
    else:
        signals["above_sma50"] = bool(re.search(r"above\s+sma50|above.*sma.*50|sma50.*above|price.*above.*sma", reasoning))

    # Price above SMA20
    if entry_sma20 is not None and entry_price:
        signals["above_sma20"] = entry_price > entry_sma20
    else:
        signals["above_sma20"] = bool(re.search(r"above\s+sma20|above.*sma.*20|sma20.*above", reasoning))

    # Volume above average
    if entry_vol is not None:
        signals["volume_above_avg"] = entry_vol >= 1.0
    else:
        signals["volume_above_avg"] = bool(re.search(r"volume.*above|above.*average.*volume|expanding.*volume|high.*volume", reasoning))

    # MACD positive
    if entry_macd is not None:
        signals["macd_positive"] = entry_macd > 0
    else:
        signals["macd_positive"] = bool(re.search(r"macd.*positive|macd.*bullish|macd.*histogram.*\+|macd.*building", reasoning))

    # Options flow bullish
    signals["options_flow_bullish"] = bool(re.search(r"options.*flow.*bullish|bullish.*options|call.*volume|put.call.*low|pc.*ratio.*low", reasoning))

    # Insider buy signal
    signals["insider_buy"] = bool(re.search(r"insider.*buy|form\s+4|cluster\s+buy|c-suite.*buy", reasoning))

    # Best combo: RSI in range + above SMA50 + volume above avg
    signals["combo_rsi_sma50_volume"] = (
        signals.get("rsi_in_range", False) and
        signals.get("above_sma50", False) and
        signals.get("volume_above_avg", False)
    )

    # Core combo: RSI in range + above SMA50
    signals["combo_rsi_sma50"] = (
        signals.get("rsi_in_range", False) and
        signals.get("above_sma50", False)
    )

    return signals


# ─── Signal calibration ───────────────────────────────────────────────────────

def calibrate_signals(lookback_trades: int = LOOKBACK) -> List[Dict]:
    """
    Pull last N closed positions, score signals, compute per-signal stats.
    Returns list of calibration row dicts. Does not write to DB.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        positions = conn.execute("""
            SELECT pl.*, t.reasoning, t.confidence, t.signal_price
            FROM position_lifecycle pl
            LEFT JOIN trades t ON t.symbol = pl.symbol
                AND t.action = 'buy'
                AND ABS(JULIANDAY(t.executed_at) - JULIANDAY(pl.opened_at)) < 0.1
            WHERE pl.status = 'closed'
              AND pl.realized_pnl IS NOT NULL
            ORDER BY pl.closed_at DESC
            LIMIT ?
        """, (lookback_trades,)).fetchall()

        conn.close()

        if len(positions) < MIN_TRADES:
            return []

        # Accumulate per-signal stats
        stats: Dict[str, Dict] = {}

        for row in positions:
            pos = dict(row)
            trade = {"reasoning": pos.get("reasoning", ""), "confidence": pos.get("confidence")}
            signals = _score_signals_from_position(pos, trade)

            pnl_pct = float(pos.get("realized_pnl_pct") or 0)
            won = pnl_pct > 0
            regime = pos.get("regime_at_entry", "").upper() if pos.get("regime_at_entry") else "UNKNOWN"

            # Compute hold days
            try:
                opened = datetime.fromisoformat(pos["opened_at"])
                closed = datetime.fromisoformat(pos["closed_at"])
                hold_days = (closed - opened).total_seconds() / 86400
            except Exception:
                hold_days = 0

            for signal_type, present in signals.items():
                if signal_type not in stats:
                    stats[signal_type] = {
                        "trades": [], "wins": 0, "total": 0,
                        "bull": {"wins": 0, "total": 0},
                        "bear": {"wins": 0, "total": 0},
                        "caution": {"wins": 0, "total": 0},
                    }
                if present:
                    stats[signal_type]["trades"].append(pnl_pct)
                    stats[signal_type]["total"] += 1
                    if won:
                        stats[signal_type]["wins"] += 1
                    r = regime.lower()
                    if r in ("bull", "bear", "caution"):
                        stats[signal_type][r]["total"] += 1
                        if won:
                            stats[signal_type][r]["wins"] += 1

        now = datetime.now().isoformat()
        valid_from = min(dict(p)["opened_at"] for p in positions)

        rows = []
        for signal_type, s in stats.items():
            total = s["total"]
            if total == 0:
                continue
            wins = s["wins"]
            pnl_list = s["trades"]
            win_rate = wins / total
            avg_pnl = sum(pnl_list) / len(pnl_list) if pnl_list else 0

            def _wr(bucket):
                t = s[bucket]["total"]
                return s[bucket]["wins"] / t if t > 0 else None

            # recommended_weight: 1.0 = baseline, scale by win_rate vs 0.5 benchmark
            # clamp to [0.3, 2.0]
            weight = max(0.3, min(2.0, win_rate / 0.5))

            rows.append({
                "computed_at": now,
                "lookback_trades": len(positions),
                "signal_type": signal_type,
                "total_trades_with_signal": total,
                "winning_trades": wins,
                "win_rate": round(win_rate, 4),
                "avg_pnl_pct": round(avg_pnl, 4),
                "avg_hold_days": 0.0,
                "bull_win_rate": _wr("bull"),
                "bear_win_rate": _wr("bear"),
                "caution_win_rate": _wr("caution"),
                "recommended_weight": round(weight, 2),
                "valid_from": valid_from,
                "valid_through": now,
            })

        return rows

    except Exception as e:
        logger.warning(f"calibrate_signals failed: {e}")
        return []


def save_signal_calibration(rows: List[Dict]) -> None:
    """Persist calibration rows to DB."""
    if not rows:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        for row in rows:
            conn.execute("""
                INSERT OR IGNORE INTO signal_calibration
                (computed_at, lookback_trades, signal_type,
                 total_trades_with_signal, winning_trades, win_rate,
                 avg_pnl_pct, avg_hold_days,
                 bull_win_rate, bear_win_rate, caution_win_rate,
                 recommended_weight, valid_from, valid_through)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                row["computed_at"], row["lookback_trades"], row["signal_type"],
                row["total_trades_with_signal"], row["winning_trades"], row["win_rate"],
                row["avg_pnl_pct"], row["avg_hold_days"],
                row["bull_win_rate"], row["bear_win_rate"], row["caution_win_rate"],
                row["recommended_weight"], row["valid_from"], row["valid_through"],
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"save_signal_calibration failed: {e}")


def run_signal_calibration(lookback_trades: int = LOOKBACK) -> List[Dict]:
    """Calibrate + save. Called after every position close. Fault-tolerant."""
    try:
        rows = calibrate_signals(lookback_trades)
        save_signal_calibration(rows)
        logger.info(f"Signal calibration updated: {len(rows)} signal types")
        return rows
    except Exception as e:
        logger.warning(f"run_signal_calibration failed (non-fatal): {e}")
        return []


def get_latest_signal_calibration() -> Dict[str, Dict]:
    """Fetch most recent calibration row per signal type."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM signal_calibration
            WHERE computed_at = (SELECT MAX(computed_at) FROM signal_calibration)
        """).fetchall()
        conn.close()
        return {r["signal_type"]: dict(r) for r in rows}
    except Exception:
        return {}


# ─── Confidence calibration ───────────────────────────────────────────────────

def compute_confidence_calibration() -> Dict:
    """
    Build a calibration curve: for each confidence bucket,
    what was the actual win rate vs the stated confidence?
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT t.confidence, pl.realized_pnl_pct
            FROM trades t
            JOIN position_lifecycle pl
                ON pl.symbol = t.symbol
                AND ABS(JULIANDAY(t.executed_at) - JULIANDAY(pl.opened_at)) < 0.1
            WHERE t.action = 'buy'
              AND t.confidence IS NOT NULL
              AND pl.status = 'closed'
              AND pl.realized_pnl_pct IS NOT NULL
        """).fetchall()
        conn.close()

        if len(rows) < MIN_TRADES:
            return {"insufficient_data": True, "n_trades": len(rows)}

        buckets = [
            (0.70, 0.75), (0.75, 0.80), (0.80, 0.85), (0.85, 0.90), (0.90, 1.01)
        ]

        bucket_stats = []
        all_bias = []

        for low, high in buckets:
            in_bucket = [(dict(r)["confidence"], dict(r)["realized_pnl_pct"])
                         for r in rows if low <= dict(r)["confidence"] < high]
            if not in_bucket:
                continue
            n = len(in_bucket)
            actual_wr = sum(1 for _, pnl in in_bucket if pnl > 0) / n
            mid_conf = (low + high) / 2
            bias = mid_conf - actual_wr
            all_bias.append(bias)
            bucket_stats.append({
                "range": f"{low:.2f}-{high:.2f}",
                "n_trades": n,
                "stated_confidence": round(mid_conf, 2),
                "actual_win_rate": round(actual_wr, 3),
                "overconfident_by": round(bias, 3),
            })

        if not bucket_stats:
            return {"insufficient_data": True, "n_trades": len(rows)}

        overall_bias = sum(all_bias) / len(all_bias)

        if overall_bias > 0.20:
            grade = "overconfident"
        elif overall_bias < -0.15:
            grade = "underconfident"
        else:
            grade = "well_calibrated"

        worst = max(bucket_stats, key=lambda b: b["overconfident_by"])

        adjustment = ""
        if grade == "overconfident" and overall_bias > 0.20:
            adjustment = (
                f"CONFIDENCE CALIBRATION WARNING: Your stated confidence overestimates "
                f"actual win rate by {overall_bias:.0%} on average. "
                f"Worst bucket: {worst['range']} confidence → only {worst['actual_win_rate']:.0%} actual wins. "
                f"Reduce position_size_pct by 20% on calls with confidence > 0.82 until recalibrated."
            )
        elif grade == "underconfident":
            adjustment = (
                f"Confidence calibration: you are slightly underconfident (actual win rate "
                f"exceeds stated confidence by {abs(overall_bias):.0%}). Trust your signals more."
            )

        return {
            "insufficient_data": False,
            "n_trades": len(rows),
            "buckets": bucket_stats,
            "overall_bias": round(overall_bias, 3),
            "grade": grade,
            "adjustment_instruction": adjustment,
        }

    except Exception as e:
        logger.warning(f"compute_confidence_calibration failed: {e}")
        return {"insufficient_data": True, "n_trades": 0}


# ─── Exit quality ─────────────────────────────────────────────────────────────

def score_exit_quality(lookback_positions: int = 20) -> Dict:
    """
    Use post_exit_tracking to evaluate exit timing.
    Premature = left_on_table_pct > +2.0 (stock kept rising after sell)
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT pet.left_on_table_pct, pet.return_5d_pct,
                   pl.close_reason, pl.realized_pnl_pct
            FROM post_exit_tracking pet
            JOIN position_lifecycle pl ON pl.id = pet.position_id
            WHERE pet.left_on_table_pct IS NOT NULL
            ORDER BY pet.tracked_at DESC
            LIMIT ?
        """, (lookback_positions,)).fetchall()
        conn.close()

        if len(rows) < 3:
            return {"insufficient_data": True}

        by_reason: Dict[str, Dict] = {}
        for r in rows:
            reason = r["close_reason"] or "unknown"
            if reason not in by_reason:
                by_reason[reason] = {"left_on_table": [], "realized": []}
            if r["left_on_table_pct"] is not None:
                by_reason[reason]["left_on_table"].append(float(r["left_on_table_pct"]))
            if r["realized_pnl_pct"] is not None:
                by_reason[reason]["realized"].append(float(r["realized_pnl_pct"]))

        exit_quality = {}
        feedback_lines = ["EXIT QUALITY FEEDBACK:"]

        for reason, data in by_reason.items():
            lot = data["left_on_table"]
            n = len(lot)
            if n < 2:
                continue
            avg_lot = sum(lot) / n
            pct_premature = sum(1 for x in lot if x > 2.0) / n

            verdict = "good" if avg_lot < 1.0 else "leaving_money" if avg_lot > 2.5 else "acceptable"

            note = ""
            if reason == "sma50_breach" and avg_lot > 2.0:
                note = "→ Consider requiring 2 consecutive closes below SMA50 before exiting"
            elif reason == "take_profit" and avg_lot > 3.0:
                note = "→ Fixed take-profit targets are cutting winners too early"
            elif reason == "ai_sell" and avg_lot > 2.0:
                note = "→ AI sell exits are firing too early on some positions"

            exit_quality[reason] = {
                "n": n, "avg_left_on_table": round(avg_lot, 2),
                "pct_premature": round(pct_premature, 2),
                "verdict": verdict, "note": note,
            }

            line = f"  {reason}: avg +{avg_lot:.1f}% left on table ({n} exits)"
            if note:
                line += f"\n  {note}"
            feedback_lines.append(line)

        overall = sum(d["avg_left_on_table"] for d in exit_quality.values()) / max(len(exit_quality), 1)

        return {
            "insufficient_data": False,
            "by_reason": exit_quality,
            "overall_left_on_table": round(overall, 2),
            "exit_feedback_block": "\n".join(feedback_lines) if len(exit_quality) > 0 else "",
        }

    except Exception as e:
        logger.warning(f"score_exit_quality failed: {e}")
        return {"insufficient_data": True}


# ─── Regime-conditional stats ─────────────────────────────────────────────────

def _build_regime_stats(lookback_trades: int = LOOKBACK) -> str:
    """Return regime-conditional win rate string, or '' if insufficient."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT regime_at_entry,
                   COUNT(*) as n,
                   SUM(CASE WHEN realized_pnl_pct > 0 THEN 1 ELSE 0 END) as wins
            FROM position_lifecycle
            WHERE status='closed' AND realized_pnl_pct IS NOT NULL
              AND regime_at_entry IS NOT NULL
            GROUP BY regime_at_entry
            ORDER BY n DESC
        """).fetchall()
        conn.close()

        if not rows:
            return ""

        parts = []
        for r in rows:
            wr = r["wins"] / r["n"] if r["n"] > 0 else 0
            parts.append(f"{r['regime_at_entry'].upper()}={wr:.0%} ({r['n']} trades)")

        return "Regime performance: " + " | ".join(parts)

    except Exception:
        return ""


# ─── Master block builder ─────────────────────────────────────────────────────

def build_performance_intelligence_block(lookback_trades: int = LOOKBACK) -> str:
    """
    Assemble the full performance intelligence block for Claude's prompt.
    Returns '' if fewer than MIN_TRADES closed positions exist.
    This is the only function called from signal_enricher.py.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        n_closed = conn.execute(
            "SELECT COUNT(*) FROM position_lifecycle WHERE status='closed'"
        ).fetchone()[0]
        conn.close()

        if n_closed < MIN_TRADES:
            return ""

        lines = [f"=== PERFORMANCE INTELLIGENCE ({n_closed} closed trades) ==="]

        # ── Signal calibration ───────────────────────────────────────────────
        cal = get_latest_signal_calibration()
        if cal:
            lines.append("\nSIGNAL CALIBRATION — adjust your weighting:")
            # Sort by win_rate descending, show meaningful signals only
            ordered = sorted(
                [(k, v) for k, v in cal.items() if v["total_trades_with_signal"] >= 3],
                key=lambda x: x[1]["win_rate"], reverse=True
            )
            for sig, data in ordered:
                n = data["total_trades_with_signal"]
                wr = data["win_rate"]
                avg_pnl = data["avg_pnl_pct"]
                weight = data["recommended_weight"]
                if weight >= 1.3:
                    tag = "STRONG — lean on this"
                elif weight <= 0.7:
                    tag = "WEAK — tighten filter or reduce weight"
                else:
                    tag = "OK"
                lines.append(
                    f"  {sig:<28} {wr:.0%} win | avg P&L: {avg_pnl:+.1f}% | "
                    f"weight: {weight:.1f}x  [{tag}]"
                )

        # ── Confidence calibration ───────────────────────────────────────────
        conf_cal = compute_confidence_calibration()
        if not conf_cal.get("insufficient_data") and conf_cal.get("adjustment_instruction"):
            lines.append("")
            lines.append(conf_cal["adjustment_instruction"])

        # ── Exit quality ─────────────────────────────────────────────────────
        exit_q = score_exit_quality()
        if not exit_q.get("insufficient_data") and exit_q.get("exit_feedback_block"):
            lines.append("")
            lines.append(exit_q["exit_feedback_block"])

        # ── Regime stats ─────────────────────────────────────────────────────
        regime_line = _build_regime_stats(lookback_trades)
        if regime_line:
            lines.append("")
            lines.append(regime_line)

        # ── Actionable guidance summary ──────────────────────────────────────
        guidance = []

        # Best combo signal
        if cal:
            combos = [(k, v) for k, v in cal.items()
                      if k.startswith("combo_") and v["total_trades_with_signal"] >= 3]
            if combos:
                best_combo = max(combos, key=lambda x: x[1]["win_rate"])
                if best_combo[1]["win_rate"] > 0.6:
                    guidance.append(
                        f"Prioritize entries where {best_combo[0].replace('combo_', '').replace('_', ' + ')} "
                        f"all align ({best_combo[1]['win_rate']:.0%} historical win rate)"
                    )

        # Weak signal warning
        if cal:
            weak = [(k, v) for k, v in cal.items()
                    if v["recommended_weight"] <= 0.6 and v["total_trades_with_signal"] >= 3]
            if weak:
                weak_names = ", ".join(k for k, _ in weak[:2])
                guidance.append(f"Be skeptical of entries relying mainly on: {weak_names}")

        if guidance:
            lines.append("\nGUIDANCE FOR THIS CYCLE:")
            for i, g in enumerate(guidance, 1):
                lines.append(f"  {i}. {g}")

        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"build_performance_intelligence_block failed (non-fatal): {e}")
        return ""


# ─── Weekly strategy memo ─────────────────────────────────────────────────────

def generate_weekly_strategy_memo(lookback_days: int = 14) -> str:
    """
    Offline analysis — run manually or weekly. Produces a strategy memo
    for human review. Never auto-modifies the system prompt.
    Stores result in strategy_memos table.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        cutoff = (datetime.now() - timedelta(days=lookback_days)).isoformat()

        # Closed positions in window
        positions = conn.execute("""
            SELECT * FROM position_lifecycle
            WHERE status='closed' AND closed_at > ?
        """, (cutoff,)).fetchall()

        n = len(positions)
        if n == 0:
            return "No closed positions in the last {} days.".format(lookback_days)

        wins = sum(1 for p in positions if (p["realized_pnl_pct"] or 0) > 0)
        avg_pnl = sum((p["realized_pnl_pct"] or 0) for p in positions) / n
        win_rate = wins / n

        # Signal calibration
        cal = calibrate_signals(lookback_trades=n)
        cal_by_type = {r["signal_type"]: r for r in cal}

        # Exit quality
        exit_q = score_exit_quality(lookback_positions=n)

        # Confidence calibration
        conf_cal = compute_confidence_calibration()

        lines = [
            f"=== WEEKLY STRATEGY MEMO — {datetime.now().strftime('%Y-%m-%d')} ===",
            f"Period: last {lookback_days} days | Closed positions: {n}",
            f"Win rate: {win_rate:.0%} | Avg P&L: {avg_pnl:+.2f}%",
            "",
            "SIGNAL FINDINGS:",
        ]

        for sig, data in sorted(cal_by_type.items(), key=lambda x: x[1]["win_rate"], reverse=True):
            if data["total_trades_with_signal"] < 2:
                continue
            wr = data["win_rate"]
            avg = data["avg_pnl_pct"]
            n_sig = data["total_trades_with_signal"]
            weight = data["recommended_weight"]
            if weight >= 1.3:
                rec = "KEEP/INCREASE WEIGHT"
            elif weight <= 0.7:
                rec = "REDUCE WEIGHT or tighten filter"
            else:
                rec = "KEEP"
            lines.append(f"  {sig}: {wr:.0%} win rate | avg {avg:+.1f}% | {n_sig} trades → {rec}")

        lines.append("")
        lines.append("CONFIDENCE FINDINGS:")
        if conf_cal.get("insufficient_data"):
            lines.append("  Insufficient data for confidence calibration.")
        else:
            bias = conf_cal.get("overall_bias", 0)
            grade = conf_cal.get("grade", "unknown")
            lines.append(f"  Calibration grade: {grade} | Overall bias: {bias:+.2f}")
            for b in conf_cal.get("buckets", []):
                lines.append(
                    f"  Confidence {b['range']}: stated {b['stated_confidence']:.0%} → "
                    f"actual {b['actual_win_rate']:.0%} (n={b['n_trades']})"
                )

        lines.append("")
        lines.append("EXIT FINDINGS:")
        if exit_q.get("insufficient_data"):
            lines.append("  Insufficient exit data.")
        else:
            for reason, data in exit_q.get("by_reason", {}).items():
                lines.append(
                    f"  {reason}: avg +{data['avg_left_on_table']:.1f}% left on table "
                    f"({data['pct_premature']:.0%} premature) — {data['verdict']}"
                )
                if data.get("note"):
                    lines.append(f"    {data['note']}")

        lines.append("")
        lines.append("RULES TO CONSIDER (requires your review before applying):")
        # Generate diff-style suggestions
        weak_signals = [k for k, v in cal_by_type.items()
                        if v["recommended_weight"] <= 0.6 and v["total_trades_with_signal"] >= 3]
        if weak_signals:
            lines.append(f"  - Consider requiring stronger confirmation when only {', '.join(weak_signals[:2])} is present")
        if not conf_cal.get("insufficient_data") and conf_cal.get("overall_bias", 0) > 0.20:
            lines.append("  - Raise confidence threshold from 0.72 to 0.78 (overconfidence detected)")
        if not exit_q.get("insufficient_data"):
            for reason, data in exit_q.get("by_reason", {}).items():
                if data["avg_left_on_table"] > 3.0:
                    lines.append(f"  - Review {reason} exit rule — consistently leaving >3% on table")

        memo = "\n".join(lines)

        # Save to DB
        week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO strategy_memos (created_at, week_start, memo) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), week_start, memo)
        )
        conn.commit()
        conn.close()

        return memo

    except Exception as e:
        logger.warning(f"generate_weekly_strategy_memo failed: {e}")
        return f"Error generating memo: {e}"


# ─── Entry point for testing ──────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    from backend.db import init_db
    init_db()

    print("=== Running signal calibration ===")
    rows = run_signal_calibration()
    print(f"Calibrated {len(rows)} signal types")

    print("\n=== Performance Intelligence Block ===")
    block = build_performance_intelligence_block()
    print(block or "(insufficient data — need {} closed trades)".format(MIN_TRADES))

    if "--memo" in sys.argv:
        print("\n=== Weekly Strategy Memo ===")
        print(generate_weekly_strategy_memo())
