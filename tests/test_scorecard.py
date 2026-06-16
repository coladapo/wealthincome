"""Scorecard math on a seeded throwaway DB."""

import sqlite3

import core.scorecard as scorecard


def _seed(db_path):
    conn = sqlite3.connect(db_path)
    rows = [
        # symbol, status, closed_at, pnl, pnl_pct, hold_s, regime, rsi, reason
        ("AAA", "closed", "2026-06-01", 300.0, 3.0, 720000, "BULL", 50, "ai_sell"),
        ("BBB", "closed", "2026-06-02", -100.0, -1.0, 36000, "BULL", 60, "sma50_breach"),
        ("CCC", "closed", "2026-06-03", -100.0, -1.0, 36000, "BEAR", 35, "sma50_breach"),
        ("DDD", "closed", "2026-06-04", 100.0, 1.0, 360000, "BULL", 45, "ai_sell"),
    ]
    conn.executemany(
        "INSERT INTO position_lifecycle (symbol, status, closed_at, realized_pnl,"
        " realized_pnl_pct, hold_duration_seconds, regime_at_entry, entry_rsi, close_reason)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.executemany(
        "INSERT INTO orders (side, status, slippage_pct, created_at) VALUES (?,?,?,?)",
        [
            ("buy", "filled", 0.05, "2026-06-01"),
            ("buy", "canceled", None, "2026-06-01"),
            ("sell", "filled", -0.02, "2026-06-02"),
            ("buy", "filled", 0.10, "2026-06-03"),
        ],
    )
    conn.execute(
        "INSERT INTO equity_snapshots (portfolio_value, cash, unrealized_pnl,"
        " open_positions, drawdown_pct, snapshot_at)"
        " VALUES (104000, 90000, 500, 2, -0.5, '2026-06-04T12:00:00')"
    )
    conn.commit()
    conn.close()


def test_scorecard_math(tmp_db, monkeypatch):
    _seed(tmp_db)
    monkeypatch.setattr(scorecard, "DB_PATH", tmp_db)

    card = scorecard.compute_scorecard()
    o = card["overall"]
    assert o["closed_trades"] == 4
    assert o["wins"] == 2
    assert o["win_rate_pct"] == 50.0
    assert o["net_pnl"] == 200.0
    assert o["avg_win"] == 200.0
    assert o["avg_loss"] == -100.0
    assert o["expectancy_per_trade"] == 50.0
    assert o["profit_factor"] == 2.0

    reasons = {r["close_reason"]: r for r in card["by_close_reason"]}
    assert reasons["sma50_breach"]["net_pnl"] == -200.0
    assert reasons["sma50_breach"]["win_rate_pct"] == 0
    assert reasons["ai_sell"]["win_rate_pct"] == 100

    e = card["execution"]
    assert e["orders_total"] == 4
    assert e["fill_rate_pct"] == 75.0


def test_exit_mode_breakdown(tmp_db, monkeypatch):
    """The regime-exit experiment's headline cut must compute."""
    conn = sqlite3.connect(tmp_db)
    rows = [
        ("AAA", "closed", "2026-06-12", 300.0, 3.0, 720000, "BULL", 50, "ai_sell", "disarmed", "BULL"),
        ("BBB", "closed", "2026-06-13", -100.0, -1.0, 36000, "CAUTION", 60, "sma50_breach", "armed", "CAUTION"),
    ]
    conn.executemany(
        "INSERT INTO position_lifecycle (symbol, status, closed_at, realized_pnl,"
        " realized_pnl_pct, hold_duration_seconds, regime_at_entry, entry_rsi, close_reason,"
        " exit_preemptive_armed, close_regime) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit(); conn.close()
    monkeypatch.setattr(scorecard, "DB_PATH", tmp_db)
    modes = {r["exit_mode"]: r for r in scorecard.compute_scorecard()["by_exit_mode"]}
    assert modes["disarmed"]["net_pnl"] == 300.0
    assert modes["armed"]["net_pnl"] == -100.0


def test_digest_formats_without_error(tmp_db, monkeypatch):
    _seed(tmp_db)
    monkeypatch.setattr(scorecard, "DB_PATH", tmp_db)
    text = scorecard.format_digest(scorecard.compute_scorecard(), trades_today=2, errors_today=0)
    assert "daily digest" in text
    assert "fill rate" in text
