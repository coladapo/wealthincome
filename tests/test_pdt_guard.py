"""PDT guard: rolling 5-business-day day-trade count, runs in paper when enabled (default off
since the FINRA Rule 4210 amendment, 2026-06-04), never blocks sells."""

from datetime import date
from types import SimpleNamespace

import pytest

import backend.db as bdb
import backend.trader as trader

# Wednesday. Window = Wed 10-14, Tue 10-13, Mon 10-12, Fri 10-09, Thu 10-08.
TODAY = date(2026, 10, 14)


@pytest.fixture()
def real_db(tmp_path, monkeypatch):
    monkeypatch.setattr(bdb, "DB_PATH", str(tmp_path / "wi.db"))
    bdb.init_db()
    return bdb


def _trade(conn, sym, action, ts):
    conn.execute("INSERT INTO trades(executed_at, symbol, action, qty) VALUES(?,?,?,1)",
                 (ts, sym, action))


def _lifecycle(conn, sym, opened, closed):
    conn.execute(
        "INSERT INTO position_lifecycle(symbol, opened_at, entry_price, entry_qty,"
        " entry_cost_basis, closed_at, status) VALUES(?,?,1,1,1,?,?)",
        (sym, opened, closed, "closed" if closed else "open"),
    )


def test_window_is_five_weekdays():
    assert trader._pdt_window_days(TODAY) == [
        "2026-10-14", "2026-10-13", "2026-10-12", "2026-10-09", "2026-10-08"]


def test_counts_same_day_round_trips_only_inside_window(real_db):
    with bdb.db() as c:
        _trade(c, "AAA", "buy", "2026-10-14T09:31:00")   # day trade 1 (trades table)
        _trade(c, "AAA", "sell", "2026-10-14T14:00:00")
        _trade(c, "BBB", "buy", "2026-10-13T09:31:00")   # overnight hold — not a day trade
        _trade(c, "BBB", "sell", "2026-10-14T10:00:00")
        _lifecycle(c, "AAA", "2026-10-14T09:31:00", "2026-10-14T14:00:00")  # same trip, no double count
        _lifecycle(c, "CCC", "2026-10-08T10:00:00", "2026-10-08T15:00:00")  # day trade 2 (stop-out, lifecycle only)
        _lifecycle(c, "DDD", "2026-10-07T10:00:00", "2026-10-07T15:00:00")  # outside window
        _lifecycle(c, "EEE", "2026-10-12T10:00:00", None)                     # still open
    assert trader.count_day_trades_rolling(TODAY) == 2


def test_fourth_day_trade_buy_blocked_with_error_row(real_db):
    with bdb.db() as c:
        for i, d in enumerate(["2026-10-14", "2026-10-12", "2026-10-09"]):
            _lifecycle(c, f"S{i}", f"{d}T10:00:00", f"{d}T11:00:00")
    assert trader.pdt_blocks_buy("NVDA", cycle_id=None, today=TODAY) is True
    errs = [e for e in bdb.get_errors(10) if e["error_type"] == "pdt_blocked"]
    assert len(errs) == 1 and "NVDA" in errs[0]["message"]


def test_three_or_fewer_allows_buy(real_db):
    with bdb.db() as c:
        _lifecycle(c, "S1", "2026-10-14T10:00:00", "2026-10-14T11:00:00")
        _lifecycle(c, "S2", "2026-10-13T10:00:00", "2026-10-13T11:00:00")
    assert trader.pdt_blocks_buy("NVDA", today=TODAY) is False


def test_guard_applies_in_paper_by_default_and_is_configurable():
    paper, live = SimpleNamespace(paper=True), SimpleNamespace(paper=False)
    assert trader.pdt_guard_applies(paper, {}) is True
    assert trader.pdt_guard_applies(paper, {"pdt_guard_in_paper": "true"}) is True
    assert trader.pdt_guard_applies(paper, {"pdt_guard_in_paper": "false"}) is False
    assert trader.pdt_guard_applies(live, {"pdt_guard_in_paper": "false"}) is True


def test_default_config_has_pdt_guard_in_paper_false(real_db):
    # FINRA Rule 4210 amendment (effective 2026-06-04) retired PDT; paper default is off
    assert real_db.get_config()["pdt_guard_in_paper"] == "false"


class _ReachedOrder(Exception):
    pass


class _FakeAlpaca:
    paper = True

    def get_current_price(self, symbol):
        return 100.0

    def get_orders(self, status="open", limit=100):
        return []

    def place_market_order(self, **kw):
        raise _ReachedOrder("sell")


def _cfg():
    cfg = dict(bdb.DEFAULT_CONFIG)
    cfg["confidence_threshold"] = "0.5"
    return cfg


def _saturate(real_db):
    with bdb.db() as c:
        for i, d in enumerate(["2026-10-14", "2026-10-13", "2026-10-12", "2026-10-09"]):
            _lifecycle(c, f"S{i}", f"{d}T10:00:00", f"{d}T11:00:00")


def test_execute_decision_blocks_paper_buy(real_db, monkeypatch):
    _saturate(real_db)
    monkeypatch.setattr(trader, "_pdt_window_days",
                        lambda today=None, n=5: ["2026-10-14", "2026-10-13", "2026-10-12",
                                                 "2026-10-09", "2026-10-08"])
    from core import catalyst_risk
    monkeypatch.setattr(catalyst_risk, "get_catalyst_risk", lambda *a, **k: catalyst_risk._clear())
    acct = SimpleNamespace(cash=50_000, portfolio_value=100_000, long_market_value=0)
    cfg = {**_cfg(), "pdt_guard_in_paper": "true"}      # guard code kept; switched on explicitly
    out = trader.execute_decision(
        {"action": "buy", "symbol": "NVDA", "confidence": 0.9, "position_size_pct": 0.05},
        acct, {}, cfg, _FakeAlpaca(), cycle_id=None, ai_decision_id=None, market_data={})
    assert out is None
    assert any(e["error_type"] == "pdt_blocked" for e in bdb.get_errors(10))


def test_execute_decision_never_blocks_sell(real_db, monkeypatch):
    _saturate(real_db)

    def must_not_run(*a, **k):
        raise AssertionError("PDT guard consulted for a sell")
    monkeypatch.setattr(trader, "pdt_blocks_buy", must_not_run)
    monkeypatch.setattr(trader.time, "sleep", lambda s: None)
    from core import catalyst_risk
    monkeypatch.setattr(catalyst_risk, "get_catalyst_risk", lambda *a, **k: catalyst_risk._clear())
    acct = SimpleNamespace(cash=50_000, portfolio_value=100_000, long_market_value=0)
    with pytest.raises(_ReachedOrder):
        trader.execute_decision(
            {"action": "sell", "symbol": "AAPL", "confidence": 0.9},
            acct, {"AAPL": SimpleNamespace(qty=10)}, _cfg(), _FakeAlpaca(),
            cycle_id=None, ai_decision_id=None, market_data={})
