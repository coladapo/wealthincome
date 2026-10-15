"""Real-money gate on a throwaway DB built with the production schema."""

import sqlite3
from datetime import datetime, timedelta

import pytest

import backend.db as db
import core.real_money_gate as gate_mod

NOW = datetime(2026, 10, 14, 13, 0, 0)


@pytest.fixture(autouse=True)
def fresh_reconciliation(monkeypatch):
    monkeypatch.setattr(gate_mod, "_reconciliation_status",
                        lambda: {"broker_realized": 250.0, "lifecycle_realized": 250.0,
                                 "gap": 0.0, "as_of": NOW.isoformat()})


@pytest.fixture()
def gate_db(tmp_path, monkeypatch):
    path = str(tmp_path / "gate.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    return path


def _pos(conn, id_, pnl, opened, regime="BULL", status="closed"):
    conn.execute(
        "INSERT INTO position_lifecycle (id, symbol, opened_at, entry_price, entry_qty,"
        " entry_cost_basis, closed_at, realized_pnl, status, regime_at_entry)"
        " VALUES (?, 'AAA', ?, 10, 10, 100, ?, ?, ?, ?)",
        (id_, opened, opened if status == "closed" else None, pnl, status, regime),
    )


def _snap(conn, at, value, cash, unrealized=0.0):
    conn.execute(
        "INSERT INTO equity_snapshots (snapshot_at, portfolio_value, cash, unrealized_pnl)"
        " VALUES (?, ?, ?, ?)", (at, value, cash, unrealized),
    )


def _by_id(card):
    return {i["id"]: i for i in card["items"]}


def test_gate_mostly_failing(gate_db):
    conn = sqlite3.connect(gate_db)
    _pos(conn, 10, -9999.0, "2026-06-01T09:00:00")         # pre-clean row: ignored
    start = datetime(2026, 9, 1, 9, 0)
    for k in range(10):
        _pos(conn, 58 + k, 100.0 if k % 2 else -50.0, (start + timedelta(days=k)).isoformat())
    _snap(conn, "2026-09-01T09:30:00", 100000, 100000)
    _snap(conn, "2026-09-20T10:00:00", 95000, 60000)          # 5% drawdown
    _snap(conn, (NOW - timedelta(days=1)).isoformat(), 100300, 80000, unrealized=50.0)
    conn.commit(); conn.close()

    card = gate_mod.compute_gate(now=NOW)
    items = _by_id(card)
    assert card["total"] == 9 and len(items) == 9
    assert items["a"]["value"] == 10 and not items["a"]["pass"]
    assert items["b"]["value"]["profit_factor"] == 2.0
    assert items["b"]["value"]["expectancy"] == 25.0
    assert not items["b"]["pass"]                         # CI over 10 trades spans 0
    assert items["c"]["value"] == 0 and not items["c"]["pass"]
    assert items["e"]["value"] == 5.0 and items["e"]["pass"]
    # f/g: no config rows → the code defaults ('true') apply, as in the live trader
    assert items["f"]["value"] == "true" and items["f"]["pass"]
    assert items["g"]["pass"] and not items["i"]["pass"]
    # books: broker realized 250 + 50 unrealized - (100300 - 100000) = 0 (fresh rebuild)
    assert items["h"]["value"]["residual"] == 0 and items["h"]["pass"]
    # deployment over last 30d (from 09-14): 09-20 (36.8%) and NOW-1d (20.2%); 09-01 excluded
    assert items["d"]["value"] == pytest.approx(28.5, abs=0.1) and not items["d"]["pass"]
    assert card["passed_count"] == 4  # e, h, and the two default-on switches f, g
    assert card["summary"] == "gate: 4/9"
    assert card["next_blocker"] == items["a"]["label"]


def test_gate_bootstrap_is_seeded(gate_db):
    conn = sqlite3.connect(gate_db)
    for k in range(30):
        _pos(conn, 58 + k, 40.0 + k, f"2026-09-{1 + k % 28:02d}T09:00:00")
    conn.commit(); conn.close()
    a = _by_id(gate_mod.compute_gate(now=NOW))["b"]["value"]["ci95_mean_pnl"]
    b = _by_id(gate_mod.compute_gate(now=NOW))["b"]["value"]["ci95_mean_pnl"]
    assert a == b and a[0] > 0


def test_gate_regime_and_switches(gate_db):
    conn = sqlite3.connect(gate_db)
    _pos(conn, 58, 10.0, "2026-07-01T09:00:00")
    for k in range(21):
        conn.execute(
            "INSERT INTO ai_decisions (cycle_id, decided_at, prompt_user) VALUES (1, ?, ?)",
            (f"2026-08-{1 + k:02d}T10:00:00",
             "=== MACRO REGIME ===\nMARKET REGIME: CAUTION (confidence score: 55/100)\n"),
        )
    # one day with a BEAR position and no prompt → position fallback counts it
    _pos(conn, 59, 10.0, "2026-09-05T09:00:00", regime="BEAR")
    conn.executemany("INSERT INTO config (key, value) VALUES (?, ?)", [
        ("pdt_guard_in_paper", "true"), ("validator_fail_closed", "TRUE"), ("live_shadow_days", "31"),
    ])
    conn.commit(); conn.close()

    items = _by_id(gate_mod.compute_gate(now=NOW))
    assert items["c"]["value"] == 22 and items["c"]["pass"]
    assert items["f"]["pass"] and items["g"]["pass"] and items["i"]["pass"]


def test_gate_line_and_brain_line_in_digest():
    from core.scorecard import format_brain_line, format_gate_line
    assert format_brain_line({"asks": 33, "successes": 31, "success_rate_pct": 93.9}) == \
        "Brain: 31/33 calls OK today (94%)"
    assert format_brain_line({"asks": 0}) == "Brain: no calls today"
    assert format_gate_line({"passed_count": 2, "total": 9, "next_blocker": "100+ clean closed trades"}) == \
        "Real-money gate: 2/9 — next blocker: 100+ clean closed trades"
    assert format_gate_line({"passed_count": 9, "total": 9, "next_blocker": None}) == \
        "Real-money gate: 9/9 — all clear"


def test_gate_switches_use_code_defaults_when_rows_missing(gate_db):
    # No config rows at all: the gate must see the same defaults the trader
    # and validator use (both 'true'), not report the switches as off.
    conn = sqlite3.connect(gate_db)
    conn.execute("DELETE FROM config WHERE key IN ('pdt_guard_in_paper', 'validator_fail_closed')")
    conn.commit(); conn.close()
    assert db.DEFAULT_CONFIG["pdt_guard_in_paper"] == "true"
    assert db.DEFAULT_CONFIG["validator_fail_closed"] == "true"
    items = _by_id(gate_mod.compute_gate(now=NOW))
    assert items["f"]["pass"] and items["f"]["value"] == "true"
    assert items["g"]["pass"] and items["g"]["value"] == "true"
