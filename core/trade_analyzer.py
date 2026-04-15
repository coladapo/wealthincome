"""
Feature 3: Performance Feedback Loop

Analyzes closed positions to classify entry signals, compute P&L metrics,
and build feedback blocks injected into Claude's trading prompts.

This creates a self-improving loop: Claude learns from its own past decisions.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# ─── Signal classifier ────────────────────────────────────────────────────────

def classify_entry_signal(rsi: float, macd_hist: float, atr_pct: float) -> str:
    """
    Classify what type of entry signal triggered based on indicator values at entry.

    Rules:
      - 'rsi_momentum':  RSI < 35 (oversold bounce) OR RSI in 40-55 with positive MACD minor
      - 'macd_cross':    MACD histogram > 0.5 AND RSI 45-70 (momentum confirmation)
      - 'sma_breakout':  RSI 55-70, MACD histogram positive but < 0.5 (quiet trend continuation)
      - 'mixed':         any other combination

    Returns one of: 'macd_cross' | 'rsi_momentum' | 'sma_breakout' | 'mixed'
    """
    # RSI momentum: oversold or low-RSI entry
    if rsi < 40:
        return 'rsi_momentum'

    # Strong MACD cross with RSI in momentum range
    if macd_hist > 0.5 and 45 <= rsi <= 75:
        return 'macd_cross'

    # Quiet trend continuation: RSI mid-range, mild MACD
    if 55 <= rsi <= 70 and 0 < macd_hist <= 0.5:
        return 'sma_breakout'

    # MACD cross even if RSI outside ideal range
    if macd_hist > 0.5:
        return 'macd_cross'

    return 'mixed'


# ─── Position analysis ────────────────────────────────────────────────────────

def analyze_closed_position(position_dict: Dict) -> Dict:
    """
    Compute performance metrics for a closed position.

    Expected keys in position_dict (from position_lifecycle row):
        entry_price, exit_price, entry_qty,
        opened_at, closed_at,
        entry_rsi, entry_macd_histogram, entry_atr_pct,
        realized_pnl (optional)

    Returns dict with:
        pnl_pct, hold_days, was_profitable, entry_signal
    """
    entry_price = float(position_dict.get("entry_price") or 0)
    exit_price  = float(position_dict.get("exit_price")  or 0)
    entry_qty   = float(position_dict.get("entry_qty")   or 0)

    # P&L %
    if entry_price > 0 and exit_price > 0:
        pnl_pct = (exit_price - entry_price) / entry_price * 100
    else:
        pnl_pct = 0.0

    # Hold duration in days
    opened_at = position_dict.get("opened_at", "")
    closed_at = position_dict.get("closed_at", "")
    hold_days = 0
    try:
        if opened_at and closed_at:
            t0 = datetime.fromisoformat(str(opened_at)[:19])
            t1 = datetime.fromisoformat(str(closed_at)[:19])
            hold_days = max(0, (t1 - t0).days)
    except Exception:
        hold_days = 0

    # Entry signal classification
    rsi       = float(position_dict.get("entry_rsi") or 50)
    macd_hist = float(position_dict.get("entry_macd_histogram") or 0)
    atr_pct   = float(position_dict.get("entry_atr_pct") or 2.0)
    entry_signal = classify_entry_signal(rsi, macd_hist, atr_pct)

    was_profitable = pnl_pct > 0

    return {
        "pnl_pct":       round(pnl_pct, 4),
        "hold_days":     hold_days,
        "was_profitable": was_profitable,
        "entry_signal":  entry_signal,
        "entry_price":   entry_price,
        "exit_price":    exit_price,
    }


# ─── Feedback block for Claude ────────────────────────────────────────────────

def build_feedback_block_for_claude(summary_dict: Dict) -> str:
    """
    Build a performance feedback block to inject into Claude's prompt.
    Returns empty string if fewer than 5 closed trades in the summary window.

    summary_dict keys (from get_performance_summary):
        total_closed, win_rate, avg_pnl_pct, avg_hold_days,
        best_signal, worst_signal, signal_breakdown
    """
    total = summary_dict.get("total_closed", 0)
    if total < 5:
        return ""

    win_rate     = summary_dict.get("win_rate", 0) * 100
    avg_pnl      = summary_dict.get("avg_pnl_pct", 0)
    avg_hold     = summary_dict.get("avg_hold_days", 0)
    best_signal  = summary_dict.get("best_signal", "N/A")
    worst_signal = summary_dict.get("worst_signal", "N/A")

    signal_bd = summary_dict.get("signal_breakdown", {})
    signal_lines = []
    for sig, stats in signal_bd.items():
        sig_count    = stats.get("count", 0)
        sig_win_rate = stats.get("win_rate", 0) * 100
        sig_avg_pnl  = stats.get("avg_pnl_pct", 0)
        signal_lines.append(
            f"  {sig}: {sig_count} trades | win_rate={sig_win_rate:.0f}% | avg_pnl={sig_avg_pnl:+.1f}%"
        )

    lines = [
        "=== YOUR RECENT PERFORMANCE ===",
        f"Last {total} closed trades:",
        f"  Win rate:     {win_rate:.0f}%",
        f"  Avg P&L:      {avg_pnl:+.2f}%",
        f"  Avg hold:     {avg_hold:.1f} days",
        f"  Best signal:  {best_signal}",
        f"  Worst signal: {worst_signal}",
        "",
        "By signal type:",
    ] + signal_lines + [
        "",
        "GUIDANCE: Favor entry signals with higher win rates. Be skeptical of signals "
        "showing consistent losses. Adjust position sizing accordingly.",
    ]

    return "\n".join(lines)
