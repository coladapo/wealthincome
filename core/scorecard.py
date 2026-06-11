"""Performance scorecard — the measurement layer (Phase A of VISION-RETHINK.md).

Read-only analytics over position_lifecycle, trades, orders, and
equity_snapshots. Answers: where do wins/losses actually come from,
and is execution quality eating the edge?

Used by GET /scorecard (backend/api.py) and scripts/daily_digest.py.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from backend.db import DB_PATH


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _since_clause(days: Optional[int], column: str) -> str:
    if not days:
        return ""
    return f" AND {column} >= datetime('now', '-{int(days)} days')"


def _rows(conn, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def compute_scorecard(days: Optional[int] = None) -> Dict[str, Any]:
    """Full scorecard. days=None → all time; days=N → closed in last N days."""
    conn = _conn()
    try:
        since = _since_clause(days, "closed_at")

        overall = _rows(conn, f"""
            SELECT count(*)                                          AS closed_trades,
                   sum(realized_pnl > 0)                             AS wins,
                   round(avg(realized_pnl > 0) * 100, 1)             AS win_rate_pct,
                   round(sum(realized_pnl), 2)                       AS net_pnl,
                   round(avg(CASE WHEN realized_pnl > 0 THEN realized_pnl END), 2)  AS avg_win,
                   round(avg(CASE WHEN realized_pnl <= 0 THEN realized_pnl END), 2) AS avg_loss,
                   round(avg(realized_pnl), 2)                       AS expectancy_per_trade,
                   round(abs(sum(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END) /
                         NULLIF(sum(CASE WHEN realized_pnl <= 0 THEN realized_pnl ELSE 0 END), 0)), 2)
                                                                     AS profit_factor,
                   round(avg(CASE WHEN realized_pnl > 0 THEN hold_duration_seconds END) / 3600, 1)
                                                                     AS avg_hold_hours_win,
                   round(avg(CASE WHEN realized_pnl <= 0 THEN hold_duration_seconds END) / 3600, 1)
                                                                     AS avg_hold_hours_loss
            FROM position_lifecycle
            WHERE status = 'closed'{since}
        """)[0]

        by_close_reason = _rows(conn, f"""
            SELECT close_reason,
                   count(*)                              AS n,
                   round(avg(realized_pnl > 0) * 100, 0) AS win_rate_pct,
                   round(sum(realized_pnl), 2)           AS net_pnl,
                   round(avg(realized_pnl_pct), 2)       AS avg_pnl_pct
            FROM position_lifecycle
            WHERE status = 'closed'{since}
            GROUP BY close_reason ORDER BY net_pnl
        """)

        by_regime = _rows(conn, f"""
            SELECT COALESCE(regime_at_entry, 'unknown')  AS regime,
                   count(*)                              AS n,
                   round(avg(realized_pnl > 0) * 100, 0) AS win_rate_pct,
                   round(sum(realized_pnl), 2)           AS net_pnl
            FROM position_lifecycle
            WHERE status = 'closed'{since}
            GROUP BY regime ORDER BY net_pnl DESC
        """)

        by_rsi_band = _rows(conn, f"""
            SELECT CASE
                     WHEN entry_rsi IS NULL THEN 'unknown'
                     WHEN entry_rsi < 40 THEN '<40'
                     WHEN entry_rsi < 55 THEN '40-55'
                     WHEN entry_rsi < 70 THEN '55-70'
                     ELSE '70+'
                   END                                   AS rsi_band,
                   count(*)                              AS n,
                   round(avg(realized_pnl > 0) * 100, 0) AS win_rate_pct,
                   round(sum(realized_pnl), 2)           AS net_pnl
            FROM position_lifecycle
            WHERE status = 'closed'{since}
            GROUP BY rsi_band ORDER BY net_pnl DESC
        """)

        by_symbol = _rows(conn, f"""
            SELECT symbol,
                   count(*)                              AS n,
                   round(avg(realized_pnl > 0) * 100, 0) AS win_rate_pct,
                   round(sum(realized_pnl), 2)           AS net_pnl
            FROM position_lifecycle
            WHERE status = 'closed'{since}
            GROUP BY symbol ORDER BY net_pnl DESC
        """)

        exec_since = _since_clause(days, "created_at")
        execution = _rows(conn, f"""
            SELECT count(*)                                            AS orders_total,
                   sum(status = 'filled')                              AS filled,
                   sum(status = 'canceled')                            AS canceled,
                   round(avg(status = 'filled') * 100, 1)              AS fill_rate_pct,
                   round(avg(CASE WHEN side = 'buy'  THEN slippage_pct END), 4) AS avg_slippage_buy_pct,
                   round(avg(CASE WHEN side = 'sell' THEN slippage_pct END), 4) AS avg_slippage_sell_pct
            FROM orders WHERE 1=1{exec_since}
        """)[0]

        equity = _rows(conn, """
            SELECT round(portfolio_value, 2) AS portfolio_value,
                   round(cash, 2)            AS cash,
                   round(unrealized_pnl, 2)  AS unrealized_pnl,
                   open_positions,
                   round(drawdown_pct, 2)    AS drawdown_pct,
                   snapshot_at
            FROM equity_snapshots ORDER BY id DESC LIMIT 1
        """)
        equity_now = equity[0] if equity else {}

        day_change = _rows(conn, """
            SELECT round(max(portfolio_value) - min(portfolio_value), 2) AS range_today,
                   round((SELECT portfolio_value FROM equity_snapshots
                          WHERE date(snapshot_at) = date('now', 'localtime')
                          ORDER BY id DESC LIMIT 1)
                       - (SELECT portfolio_value FROM equity_snapshots
                          WHERE date(snapshot_at) = date('now', 'localtime')
                          ORDER BY id ASC LIMIT 1), 2) AS change_today
            FROM equity_snapshots
            WHERE date(snapshot_at) = date('now', 'localtime')
        """)

        return {
            "window_days": days,
            "overall": overall,
            "by_close_reason": by_close_reason,
            "by_regime": by_regime,
            "by_rsi_band": by_rsi_band,
            "by_symbol": by_symbol,
            "execution": execution,
            "equity": {**equity_now, **(day_change[0] if day_change else {})},
        }
    finally:
        conn.close()


def format_digest(card: Dict[str, Any], trades_today: int = 0, errors_today: int = 0) -> str:
    """One-screen plain-text digest for Slack."""
    o = card["overall"]
    e = card["execution"]
    q = card["equity"]
    lines = [
        f"📊 WealthIncome daily digest",
        f"Equity ${q.get('portfolio_value', 0):,.0f} "
        f"({'+' if (q.get('change_today') or 0) >= 0 else ''}{q.get('change_today') or 0:,.2f} today) | "
        f"cash ${q.get('cash', 0):,.0f} | {q.get('open_positions', 0)} open positions",
        f"All-time: {o['closed_trades']} closed, {o['win_rate_pct'] or 0}% wins, "
        f"net ${o['net_pnl'] or 0:,.0f}, expectancy ${o['expectancy_per_trade'] or 0}/trade, "
        f"PF {o['profit_factor'] or 'n/a'}",
        f"Execution: {e['fill_rate_pct'] or 0}% fill rate "
        f"({e['filled']}/{e['orders_total']}), slippage buy {e['avg_slippage_buy_pct'] or 0}% / "
        f"sell {e['avg_slippage_sell_pct'] or 0}%",
        f"Today: {trades_today} trades, {errors_today} errors",
    ]
    worst = card["by_close_reason"][:1]
    if worst and (worst[0]["net_pnl"] or 0) < 0:
        w = worst[0]
        lines.append(f"Biggest leak: {w['close_reason']} exits — {w['n']} trades, ${w['net_pnl']:,.0f}")
    return "\n".join(lines)


if __name__ == "__main__":
    import json
    print(json.dumps(compute_scorecard(), indent=2, default=str))
