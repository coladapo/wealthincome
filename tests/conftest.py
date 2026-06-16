"""Shared fixtures. All tests are network-free and DB-isolated."""

import os
import sys
import sqlite3

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def tmp_db(tmp_path):
    """Minimal schema covering what core/scorecard.py reads."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE position_lifecycle (
            id INTEGER PRIMARY KEY,
            symbol TEXT, status TEXT, closed_at TEXT,
            realized_pnl REAL, realized_pnl_pct REAL,
            hold_duration_seconds INTEGER,
            regime_at_entry TEXT, entry_rsi REAL, close_reason TEXT,
            exit_preemptive_armed TEXT, close_regime TEXT
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            side TEXT, status TEXT, slippage_pct REAL, created_at TEXT
        );
        CREATE TABLE equity_snapshots (
            id INTEGER PRIMARY KEY,
            portfolio_value REAL, cash REAL, unrealized_pnl REAL,
            open_positions INTEGER, drawdown_pct REAL, snapshot_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    return db_path
