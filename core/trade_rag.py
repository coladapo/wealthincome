"""
Trade History RAG — Retrieval-Augmented Generation over closed positions.

Inspired by Balyasny's domain-specific retrieval layer, which outperforms
OpenAI's general embeddings by 8-10 points on financial data.

Instead of a vector DB, we use structured similarity over trade metadata
stored in our own SQLite position_lifecycle table. No external dependencies.

When Claude is about to propose a trade on NVDA, this module answers:
  "What happened the last 5 times we entered a similar trade?"
  - Same symbol? Same regime? Similar RSI range? Similar confidence?

That context gets injected into the enrichment block before the LLM call
so Claude's decisions are grounded in our actual track record.

Similarity dimensions (all weighted):
  1. Symbol match (exact)                    — weight 0.40
  2. Market regime at entry (BULL/BEAR/etc.) — weight 0.25
  3. RSI range proximity                     — weight 0.15
  4. SMA50 alignment (above/below)           — weight 0.10
  5. Volume ratio similarity                 — weight 0.10
"""

import logging
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Min closed positions before RAG has anything useful to say
MIN_CLOSED_POSITIONS = 3
# Max similar trades to retrieve per lookup
TOP_K = 5


def _compute_similarity(
    candidate: Dict[str, Any],
    query: Dict[str, Any],
) -> float:
    """
    Compute a 0.0–1.0 similarity score between a closed trade and the query.
    query keys: symbol, regime, rsi, above_sma50, volume_ratio
    """
    score = 0.0

    # Symbol match (exact) — 0.40
    if candidate.get("symbol") == query.get("symbol"):
        score += 0.40
    # Same sector would also score here — skipping for now (no sector data in DB)

    # Regime match — 0.25
    c_regime = (candidate.get("regime_at_entry") or "").upper()
    q_regime = (query.get("regime") or "").upper()
    if c_regime and q_regime:
        if c_regime == q_regime:
            score += 0.25
        elif c_regime in q_regime or q_regime in c_regime:
            score += 0.10

    # RSI proximity — 0.15 (within 10 points = full score, degrades linearly)
    c_rsi = candidate.get("entry_rsi")
    q_rsi = query.get("rsi")
    if c_rsi is not None and q_rsi is not None:
        rsi_diff = abs(float(c_rsi) - float(q_rsi))
        rsi_sim = max(0.0, 1.0 - rsi_diff / 20.0)  # 20-point range = 0
        score += 0.15 * rsi_sim

    # SMA50 alignment — 0.10
    c_sma50 = candidate.get("entry_sma50")
    c_price = candidate.get("entry_price")
    q_above = query.get("above_sma50")
    if c_sma50 and c_price and q_above is not None:
        c_above = float(c_price) > float(c_sma50)
        if c_above == bool(q_above):
            score += 0.10

    # Volume ratio proximity — 0.10 (within 0.3 = full score)
    c_vol = candidate.get("entry_volume_ratio")
    q_vol = query.get("volume_ratio")
    if c_vol is not None and q_vol is not None:
        vol_diff = abs(float(c_vol) - float(q_vol))
        vol_sim = max(0.0, 1.0 - vol_diff / 0.5)
        score += 0.10 * vol_sim

    return round(score, 3)


def retrieve_similar_trades(
    symbol: str,
    regime: str = "",
    rsi: Optional[float] = None,
    above_sma50: Optional[bool] = None,
    volume_ratio: Optional[float] = None,
    top_k: int = TOP_K,
) -> List[Dict[str, Any]]:
    """
    Retrieve the most similar closed trades from history.
    Returns list of trade dicts sorted by similarity descending.
    """
    try:
        from backend.db import get_connection
        conn = get_connection()

        rows = conn.execute("""
            SELECT
                symbol, entry_price, entry_qty, entry_cost_basis,
                exit_price, realized_pnl, realized_pnl_pct,
                close_reason, hold_duration_seconds,
                entry_rsi, entry_sma50, entry_sma20, entry_volume_ratio,
                regime_at_entry, regime_score_at_entry,
                entry_confidence, momentum_score_at_entry,
                opened_at, closed_at
            FROM position_lifecycle
            WHERE status = 'closed'
              AND realized_pnl_pct IS NOT NULL
            ORDER BY closed_at DESC
            LIMIT 100
        """).fetchall()
        conn.close()

        if not rows:
            return []

        query = {
            "symbol": symbol.upper(),
            "regime": regime,
            "rsi": rsi,
            "above_sma50": above_sma50,
            "volume_ratio": volume_ratio,
        }

        candidates = []
        for row in rows:
            trade = dict(row)
            sim = _compute_similarity(trade, query)
            trade["_similarity"] = sim
            candidates.append(trade)

        # Sort by similarity desc, then by recency (already ordered by closed_at DESC)
        candidates.sort(key=lambda x: x["_similarity"], reverse=True)
        return candidates[:top_k]

    except Exception as e:
        logger.warning(f"RAG retrieval failed (non-fatal): {e}")
        return []


def _format_trade_for_context(trade: Dict[str, Any], rank: int) -> str:
    """Format a single historical trade as a compact context line."""
    sym        = trade.get("symbol", "?")
    entry      = trade.get("entry_price", 0)
    exit_p     = trade.get("exit_price")
    pnl_pct    = trade.get("realized_pnl_pct")
    reason     = trade.get("close_reason", "?")
    regime     = trade.get("regime_at_entry", "?")
    rsi        = trade.get("entry_rsi")
    conf       = trade.get("entry_confidence")
    hold_secs  = trade.get("hold_duration_seconds") or 0
    hold_hrs   = hold_secs / 3600
    similarity = trade.get("_similarity", 0)
    closed_at  = (trade.get("closed_at") or "")[:10]

    pnl_str  = f"{pnl_pct:+.1f}%" if pnl_pct is not None else "open"
    exit_str = f"${exit_p:.2f}" if exit_p else "?"
    rsi_str  = f"RSI={rsi:.0f}" if rsi else ""
    conf_str = f"conf={conf:.0%}" if conf else ""
    sim_str  = f"sim={similarity:.2f}"

    return (
        f"  #{rank} {sym} [{closed_at}] entry=${entry:.2f} exit={exit_str} "
        f"P&L={pnl_str} | held={hold_hrs:.1f}h | "
        f"regime={regime} {rsi_str} {conf_str} | "
        f"closed_by={reason} | {sim_str}"
    )


def build_rag_block(
    symbol: str,
    regime: str = "",
    rsi: Optional[float] = None,
    above_sma50: Optional[bool] = None,
    volume_ratio: Optional[float] = None,
) -> str:
    """
    Build the RAG context block to inject into Claude's prompt.
    Returns empty string if insufficient history.
    """
    trades = retrieve_similar_trades(
        symbol=symbol,
        regime=regime,
        rsi=rsi,
        above_sma50=above_sma50,
        volume_ratio=volume_ratio,
    )

    if not trades:
        return ""

    # Only include trades with meaningful similarity
    # 0.10 = at least RSI proximity or SMA alignment match (even with no regime stored yet)
    relevant = [t for t in trades if t["_similarity"] >= 0.10]
    if not relevant:
        return ""

    wins   = [t for t in relevant if (t.get("realized_pnl_pct") or 0) > 0]
    losses = [t for t in relevant if (t.get("realized_pnl_pct") or 0) <= 0]
    win_rate = len(wins) / len(relevant) if relevant else 0
    avg_pnl  = sum(t.get("realized_pnl_pct") or 0 for t in relevant) / len(relevant)

    lines = [
        f"=== TRADE HISTORY RAG — Similar past trades for {symbol} ===",
        f"Found {len(relevant)} similar closed trades | "
        f"Win rate: {win_rate:.0%} | Avg P&L: {avg_pnl:+.1f}%",
    ]

    for i, trade in enumerate(relevant[:TOP_K], 1):
        lines.append(_format_trade_for_context(trade, i))

    # Guidance hint based on history
    if len(relevant) >= 3:
        if win_rate >= 0.6 and avg_pnl > 1.0:
            lines.append(f"HISTORY SIGNAL: Strong positive track record in similar conditions.")
        elif win_rate <= 0.3 or avg_pnl < -1.5:
            lines.append(
                f"HISTORY SIGNAL: Poor track record in similar conditions "
                f"({win_rate:.0%} win rate, {avg_pnl:+.1f}% avg). "
                f"Require stronger signal alignment or reduce position size."
            )
        else:
            lines.append(f"HISTORY SIGNAL: Mixed results — no strong directional bias from history.")

    return "\n".join(lines)


def build_portfolio_rag_block(
    watchlist_data: List[Dict[str, Any]],
    regime: str = "",
) -> str:
    """
    Build RAG context for the full watchlist — one block covering all symbols
    with relevant history. Called once per cycle, injected into the enrichment block.

    watchlist_data: list of dicts with keys: symbol, rsi, above_sma50, volume_ratio
    """
    blocks = []
    for item in watchlist_data:
        sym = item.get("symbol") or item.get("ticker")
        if not sym:
            continue
        block = build_rag_block(
            symbol=sym,
            regime=regime,
            rsi=item.get("rsi"),
            above_sma50=item.get("above_sma50"),
            volume_ratio=item.get("vol_ratio") or item.get("volume_ratio"),
        )
        if block:
            blocks.append(block)

    if not blocks:
        return ""

    return "\n\n".join(blocks)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")

    print("=== Trade History RAG Test ===\n")
    block = build_rag_block(
        symbol="SBUX",
        regime="BEAR",
        rsi=52.0,
        above_sma50=True,
        volume_ratio=1.1,
    )
    print(block if block else "(no similar trades found — need more closed positions)")
