"""Reconciler failures that used to vanish behind `except: pass` are now logged
with a traceback and an errors-table row — behaviour otherwise unchanged."""

import logging
from types import SimpleNamespace

import pytest

import backend.db as bdb
import backend.reconciler as rec


@pytest.fixture()
def real_db(tmp_path, monkeypatch):
    monkeypatch.setattr(bdb, "DB_PATH", str(tmp_path / "wi.db"))
    bdb.init_db()
    from core import trade_lessons
    monkeypatch.setattr(trade_lessons, "record_lesson", lambda *a, **k: None)
    return bdb


def test_failing_calibration_recompute_is_logged_not_swallowed(real_db, monkeypatch, caplog):
    from core import performance_intelligence

    def broken(*a, **k):
        raise RuntimeError("calibration table locked")
    monkeypatch.setattr(performance_intelligence, "run_signal_calibration", broken)

    pid = real_db.open_position_lifecycle(symbol="NVDA", entry_price=100.0, entry_qty=10,
                                          cycle_id=None, trade_id=None, order_group_id=None)
    sold = SimpleNamespace(symbol="NVDA", side="sell", status="filled",
                           filled_avg_price=110.0, filled_at="2026-10-14T15:00:00Z")
    alpaca = SimpleNamespace(get_positions=lambda: [],
                             get_orders=lambda status="all", limit=200, **k: [sold])

    with caplog.at_level(logging.ERROR, logger="reconciler"):
        rec._reconcile_sell_fills(alpaca, real_db.DB_PATH)

    # the close itself still happened (no behaviour change)
    row = real_db.get_closed_positions(10)[0]
    assert row["id"] == pid and row["exit_price"] == 110.0
    # ...and the failure is visible: traceback in the log + an errors row
    hits = [r for r in caplog.records if "Signal calibration recompute failed" in r.getMessage()]
    assert hits and hits[0].exc_info is not None
    errs = [e for e in real_db.get_errors(20) if e["error_type"] == "signal_calibration_failed"]
    assert len(errs) == 1 and "calibration table locked" in errs[0]["message"]


def test_failed_order_lookup_is_logged_and_close_falls_back(real_db, monkeypatch, caplog):
    from core import performance_intelligence
    monkeypatch.setattr(performance_intelligence, "run_signal_calibration", lambda *a, **k: [])
    real_db.open_position_lifecycle(symbol="AMD", entry_price=50.0, entry_qty=4,
                                    cycle_id=None, trade_id=None, order_group_id=None)

    def orders_down(**k):
        raise ConnectionError("alpaca down")
    alpaca = SimpleNamespace(get_positions=lambda: [], get_orders=orders_down,
                             get_current_price=lambda s: 55.0)

    with caplog.at_level(logging.ERROR, logger="reconciler"):
        rec._reconcile_sell_fills(alpaca, real_db.DB_PATH)

    row = real_db.get_closed_positions(10)[0]
    assert row["exit_price"] == 55.0 and row["close_reason"] == "position_closed"
    assert [e for e in real_db.get_errors(20) if e["error_type"] == "reconciler_sell_lookup_failed"]
