"""FIFO rebuild from broker fills, the partial-exit gap it exposes, and the
DB-only reconciliation_status() the real-money gate will read."""

from datetime import datetime
from types import SimpleNamespace

import pytest

import backend.db as bdb
from backend import reconciliation as R


def _fill(i, sym, side, qty, px, t):
    return {"id": f"f{i}", "activity_type": "FILL", "symbol": sym, "side": side, "qty": str(qty),
            "price": str(px), "transaction_time": t, "order_id": f"o{i}", "type": "fill"}


FILLS = [
    _fill(1, "SBUX", "buy", 10, 100, "2026-04-15T13:30:00Z"),
    _fill(2, "SBUX", "buy", 10, 110, "2026-04-15T13:31:00Z"),
    _fill(3, "SBUX", "sell", 15, 120, "2026-04-20T14:00:00Z"),   # partial exit: 10@100 + 5@110
    _fill(4, "SBUX", "sell", 5, 90, "2026-04-21T14:00:00Z"),     # final exit
    _fill(5, "MA", "buy", 4, 500, "2026-10-09T13:40:00Z"),       # still open
]


def test_fifo_matches_lots_in_order():
    matches, open_lots = R.rebuild_fifo(FILLS)
    by_close = {}
    for m in matches:
        by_close[m["close_fill_id"]] = by_close.get(m["close_fill_id"], 0) + m["realized_pnl"]
    assert by_close == {"f3": pytest.approx(10 * 20 + 5 * 10), "f4": pytest.approx(5 * -20)}
    assert sum(m["realized_pnl"] for m in matches) == pytest.approx(150)
    assert open_lots == {"MA": [{"qty": 4.0, "price": 500.0, "fill_id": "f5",
                                 "time": "2026-10-09T13:40:00Z"}]}


def test_sell_without_lots_opens_short_and_buy_covers_it():
    matches, open_lots = R.rebuild_fifo([
        _fill(1, "X", "sell", 3, 50, "2026-05-01T14:00:00Z"),
        _fill(2, "X", "buy", 3, 40, "2026-05-02T14:00:00Z"),
    ])
    assert [(m["direction"], m["realized_pnl"]) for m in matches] == [("short", pytest.approx(30))]
    assert open_lots == {}


def _local(utc_iso):
    """Ledger timestamps are naive local time — derive them so the test is tz-independent."""
    return datetime.fromisoformat(utc_iso.replace("Z", "+00:00")).astimezone().replace(tzinfo=None).isoformat()


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(bdb, "DB_PATH", str(tmp_path / "wi.db"))
    bdb.init_db()
    with bdb.db() as c:
        # lifecycle booked only the FINAL fill: (90-105)*5 = -75, on a 20-share entry
        c.execute("INSERT INTO position_lifecycle(symbol, opened_at, entry_price, entry_qty, entry_cost_basis,"
                  " closed_at, exit_price, exit_qty, realized_pnl, status, close_reason)"
                  " VALUES('SBUX',?,105,20,2100,?,90,5,-75,'closed','ai_sell')",
                  (_local("2026-04-15T13:29:00Z"), _local("2026-04-21T14:00:30Z")))
        c.execute("INSERT INTO position_lifecycle(symbol, opened_at, entry_price, entry_qty, entry_cost_basis, status)"
                  " VALUES('MA',?,500,4,2000,'open')", (_local("2026-10-09T13:39:00Z"),))
    return bdb


def _fake_alpaca():
    other = [{"id": "d1", "activity_type": "JNLC", "net_amount": "100000"},
             {"id": "fee1", "activity_type": "FEE", "net_amount": "-0.50"}]
    ma = SimpleNamespace(symbol="MA", qty=4.0, current_price=510.0, unrealized_pl=40.0)
    # equity = deposits + realized(150) + unrealized(40) + fees(-0.5)
    return SimpleNamespace(
        iter_account_activities=lambda *a, **k: other[:1] + FILLS + other[1:],
        get_account_raw=lambda: {"equity": str(100_000 + 150 + 40 - 0.5)},
        get_positions=lambda: [ma],
    )


def test_run_writes_derived_tables_and_closes_the_equity_identity(ledger, monkeypatch):
    with ledger.db() as c:
        before = [dict(r) for r in c.execute("SELECT * FROM position_lifecycle")]
    rep = R.run(_fake_alpaca())

    assert rep["broker_realized"] == 150.0
    assert rep["lifecycle_realized"] == -75.0
    assert rep["lifecycle_vs_broker_gap"] == 225.0
    assert rep["equity_gap"] == 149.5            # equity - 100k - unrealized
    assert rep["residual"] == 0.0
    assert rep["qty_mismatch"] == {}
    [p] = rep["partial_rows"]
    assert (p["symbol"], p["fills_pnl"], p["missing_pnl"]) == ("SBUX", 150.0, 225.0)

    with ledger.db() as c:
        assert c.execute("SELECT COUNT(*) FROM broker_fills").fetchone()[0] == 5
        assert c.execute("SELECT COUNT(*) FROM fifo_realized").fetchone()[0] == 3
        after = [dict(r) for r in c.execute("SELECT * FROM position_lifecycle")]
    assert after == before                        # never overwrites position_lifecycle

    R.run(_fake_alpaca())                         # idempotent re-run
    with ledger.db() as c:
        assert c.execute("SELECT COUNT(*) FROM broker_fills").fetchone()[0] == 5
        assert c.execute("SELECT COUNT(*) FROM fifo_realized").fetchone()[0] == 3

    st = R.reconciliation_status()
    assert set(st) == {"broker_realized", "lifecycle_realized", "gap", "as_of"}
    assert (st["broker_realized"], st["lifecycle_realized"], st["gap"]) == (150.0, -75.0, 225.0)
    assert st["as_of"] == rep["as_of"]
    assert "SBUX" in R.format_report(rep)


def test_status_before_first_run_is_readable_and_empty(ledger):
    st = R.reconciliation_status()
    assert st["broker_realized"] == 0.0 and st["as_of"] is None and st["lifecycle_realized"] == -75.0
