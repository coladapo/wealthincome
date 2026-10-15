"""Restart recovery: a cycle killed after the entry order was submitted but before
the lifecycle row / trailing stop were written is repaired by the reconciler —
lifecycle row created, missing stop placed exactly once."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import backend.db as bdb
import backend.reconciler as rec
from backend.order_keys import order_idempotency_key
from tests.fake_broker import FakeBroker

OLD_UTC = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
OLD_LOCAL = (datetime.now() - timedelta(hours=1)).isoformat()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(bdb, "DB_PATH", str(tmp_path / "wi.db"))
    bdb.init_db()
    # keep the reconcile pass network-free and away from the live DB
    monkeypatch.setattr(rec, "_check_sma50_exits", lambda *a, **k: None)
    monkeypatch.setattr(bdb, "backfill_post_exit_prices", lambda: None)
    from core import performance_intelligence, trade_lessons
    monkeypatch.setattr(performance_intelligence, "run_signal_calibration", lambda *a, **k: [])
    monkeypatch.setattr(trade_lessons, "record_lesson", lambda *a, **k: None)
    return SimpleNamespace(broker=FakeBroker(price=100.0), cycle_id=bdb.start_cycle(True))


def _rows(sql, *args):
    with bdb.db() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def _errors(kind):
    return [e for e in bdb.get_errors(100) if e["error_type"] == kind]


def _killed_after_record_order(env, symbol="NVDA", qty=50, filled_at=OLD_UTC):
    """What the trader leaves behind if it dies right after record_order()."""
    key = order_idempotency_key(env.cycle_id, symbol, "buy", qty)
    o = env.broker.place_limit_order(symbol, qty, "buy", 100.1, client_order_id=key)
    o.filled_at = o.raw["filled_at"] = filled_at
    trade_id = bdb.record_trade(env.cycle_id, {"symbol": symbol, "action": "buy", "qty": qty,
                                               "order_id": o.id, "order_status": "filled"})
    group_id = bdb.record_order_group(trade_id=trade_id, cycle_id=env.cycle_id, symbol=symbol,
                                      parent_order_id=o.id, parent_side="buy", parent_qty=qty)
    bdb.record_order(alpaca_order_id=o.id, symbol=symbol, side="buy", order_type="limit_ioc",
                     qty=qty, status="filled", cycle_id=env.cycle_id, trade_id=trade_id,
                     order_group_id=group_id, client_order_id=key)
    with bdb.db() as c:
        c.execute("UPDATE orders SET filled_at=?, filled_qty=?, status_updated_at=? WHERE alpaca_order_id=?",
                  (filled_at, qty, OLD_LOCAL, o.id))
    return o, trade_id, group_id


def test_orphan_fill_gets_lifecycle_row_and_one_stop(env):
    o, trade_id, group_id = _killed_after_record_order(env)
    assert _rows("SELECT * FROM position_lifecycle") == []

    rec._reconcile_once(env.broker)   # "next start"
    rec._reconcile_once(env.broker)   # and a later pass — must not duplicate anything

    rows = _rows("SELECT * FROM position_lifecycle")
    assert len(rows) == 1
    row = rows[0]
    assert (row["symbol"], row["entry_qty"], row["status"]) == ("NVDA", 50, "open")
    assert row["entry_order_group_id"] == group_id and row["entry_trade_id"] == trade_id
    stops = env.broker.trailing_stops("NVDA")
    assert len(stops) == 1 and stops[0].qty == 50
    assert row["trailing_stop_order_id"] == stops[0].id
    assert len(_errors("orphan_position_recovered")) == 1
    assert len(_errors("trailing_stop_replaced")) == 1
    assert _rows("SELECT position_lifecycle_id FROM trades WHERE id=?", trade_id)[0]["position_lifecycle_id"] == row["id"]


def test_orphan_known_only_to_broker_is_recovered(env):
    # Died before record_order: only Alpaca knows, via our keyed client_order_id.
    key = order_idempotency_key(env.cycle_id, "AMD", "buy", 20)
    o = env.broker.place_limit_order("AMD", 20, "buy", 100.1, client_order_id=key)
    o.filled_at = OLD_UTC

    rec._reconcile_once(env.broker)
    rec._reconcile_once(env.broker)

    assert [r["symbol"] for r in _rows("SELECT * FROM position_lifecycle")] == ["AMD"]
    assert len(env.broker.trailing_stops("AMD")) == 1
    assert _rows("SELECT client_order_id FROM orders WHERE side='buy'")[0]["client_order_id"] == key


def test_fresh_entry_inside_grace_window_is_left_to_the_trader(env):
    _killed_after_record_order(env, filled_at=datetime.now(timezone.utc).isoformat())
    rec._reconcile_once(env.broker)
    assert _rows("SELECT * FROM position_lifecycle") == []
    assert env.broker.trailing_stops() == []


def test_untracked_manual_position_is_flagged_not_adopted(env):
    env.broker.hold("TSLA", 5)    # no order of ours behind it
    rec._reconcile_once(env.broker)
    rec._reconcile_once(env.broker)
    assert _rows("SELECT * FROM position_lifecycle") == []
    assert env.broker.trailing_stops() == []
    assert len(_errors("untracked_position")) == 1


def _open_row(symbol, qty, stop_id=None, trail=15.0, suspended=None):
    pid = bdb.open_position_lifecycle(symbol=symbol, entry_price=100.0, entry_qty=qty,
                                      cycle_id=None, trade_id=None, order_group_id=None)
    with bdb.db() as c:
        c.execute("UPDATE position_lifecycle SET opened_at=?, trailing_stop_order_id=?, trail_percent=?,"
                  " stop_suspended_reason=? WHERE id=?", (OLD_LOCAL, stop_id, trail, suspended, pid))
    return pid


def test_cancelled_stop_is_replaced_once_at_current_qty(env):
    # Stop was cancelled for a partial sell and never re-armed; 30 of 50 shares remain.
    env.broker.hold("MSFT", 30)
    old = env.broker.place_trailing_stop_order("MSFT", 50, 15.0)
    env.broker.cancel_order(old.id)
    pid = _open_row("MSFT", 50, stop_id=old.id)

    rec._reconcile_once(env.broker)
    rec._reconcile_once(env.broker)

    live = env.broker.trailing_stops("MSFT")
    assert len(live) == 1 and live[0].qty == 30
    assert _rows("SELECT trailing_stop_order_id FROM position_lifecycle WHERE id=?", pid)[0][
        "trailing_stop_order_id"] == live[0].id
    assert len(_errors("trailing_stop_replaced")) == 1


def test_existing_stop_and_deliberate_suspension_are_left_alone(env):
    env.broker.hold("MA", 13)
    keep = env.broker.place_trailing_stop_order("MA", 13, 12.0)
    _open_row("MA", 13, stop_id=keep.id)
    env.broker.hold("LLY", 4)
    _open_row("LLY", 4, suspended="tier 2: earnings in 1 day")

    rec._reconcile_once(env.broker)

    assert [o.id for o in env.broker.trailing_stops("MA")] == [keep.id]
    assert env.broker.trailing_stops("LLY") == []
    assert _errors("trailing_stop_replaced") == []


def test_no_stop_placed_while_market_closed(env):
    env.broker.market_open = False
    env.broker.hold("MSFT", 10)
    _open_row("MSFT", 10)
    rec._reconcile_once(env.broker)
    assert env.broker.trailing_stops() == []
