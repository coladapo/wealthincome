"""Duplicate-order prevention: a replayed cycle (restart mid-cycle, retried POST)
submits nothing new; the same symbol twice in one cycle buys once."""

from types import SimpleNamespace

import pytest
import requests

import backend.db as bdb
import backend.trader as trader
from backend.order_keys import order_idempotency_key, trailing_stop_key
from core.alpaca_client import AlpacaClient
from tests.fake_broker import FakeBroker

CFG = {"max_position_pct": "0.08", "max_open_positions": "8",
       "confidence_threshold": "0.72", "pdt_guard_in_paper": "true"}
ACCOUNT = SimpleNamespace(cash=100_000.0, portfolio_value=100_000.0, long_market_value=0.0)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(bdb, "DB_PATH", str(tmp_path / "wi.db"))
    bdb.init_db()
    from core import catalyst_risk
    monkeypatch.setattr(catalyst_risk, "get_catalyst_risk",
                        lambda *a, **k: catalyst_risk._clear())
    monkeypatch.setattr(trader.time, "sleep", lambda *_: None)
    return SimpleNamespace(broker=FakeBroker(price=100.0), cycle_id=bdb.start_cycle(True))


def _buy(env, symbol="NVDA", size=0.05, positions=None):
    d = {"symbol": symbol, "action": "buy", "confidence": 0.9, "reasoning": "test",
         "position_size_pct": size}
    return trader.execute_decision(d, ACCOUNT, positions or {}, CFG, env.broker,
                                   env.cycle_id, 0, {symbol: {"atr_pct": 2.0}})


def _count(sql, *args):
    with bdb.db() as c:
        return c.execute(sql, args).fetchone()[0]


def test_keys_are_deterministic_and_distinct():
    k = order_idempotency_key(7, "nvda", "BUY", 50)
    assert k == order_idempotency_key(7, "NVDA", "buy", 50.0)
    assert k.startswith("wi-") and len(k) <= 128
    assert k != order_idempotency_key(8, "NVDA", "buy", 50)
    assert k != order_idempotency_key(7, "NVDA", "sell", 50)
    assert order_idempotency_key(7, "NVDA", "buy", 50, attempt=1) != k
    assert trailing_stop_key(3, 10) != trailing_stop_key(3, 10, "old-stop")


def test_replayed_cycle_submits_nothing_new(env):
    assert _buy(env) is not None
    first = list(env.broker.submissions)
    assert [o.order_type for o in first] == ["limit", "trailing_stop"]
    assert first[0].raw["client_order_id"] == order_idempotency_key(env.cycle_id, "NVDA", "buy", 50)

    # Restart mid-cycle: same cycle, same decision, stale (pre-buy) positions snapshot.
    assert _buy(env, positions={}) is None
    assert env.broker.submissions == first
    assert _count("SELECT COUNT(*) FROM orders WHERE side='buy'") == 1
    assert _count("SELECT COUNT(*) FROM position_lifecycle") == 1


def test_killed_between_submit_and_record_is_caught_at_broker(env):
    # The order reached Alpaca under its key, but the process died before any DB write.
    key = order_idempotency_key(env.cycle_id, "NVDA", "buy", 50)
    env.broker.place_limit_order("NVDA", 50, "buy", 100.1, client_order_id=key)
    n = len(env.broker.submissions)

    assert _buy(env) is None
    assert len(env.broker.submissions) == n
    assert _count("SELECT COUNT(*) FROM errors WHERE error_type='duplicate_order_suppressed'") == 1


def test_same_symbol_twice_in_one_cycle_buys_once(env):
    assert _buy(env, size=0.05) is not None
    assert _buy(env, size=0.03) is None          # different size → different key; cycle guard catches it
    assert sum(o.side == "buy" for o in env.broker.submissions) == 1


def test_broker_lookup_failure_blocks_buy(env, monkeypatch):
    def boom(_):
        raise requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(env.broker, "get_order_by_client_id", boom)
    assert _buy(env) is None
    assert env.broker.submissions == []


def test_replayed_sell_submits_nothing_new(env):
    env.broker.hold("NVDA", 10)
    pos = {"NVDA": env.broker.positions["NVDA"]}
    d = {"symbol": "NVDA", "action": "sell", "confidence": 0.9, "reasoning": "exit", "reduce_pct": 1.0}
    assert trader.execute_decision(d, ACCOUNT, pos, CFG, env.broker, env.cycle_id, 0, {}) is not None
    n = len(env.broker.submissions)
    assert trader.execute_decision(d, ACCOUNT, pos, CFG, env.broker, env.cycle_id, 0, {}) is None
    assert len(env.broker.submissions) == n


def test_missed_ioc_entry_opens_no_lifecycle_row(env, monkeypatch):
    def unfilled(symbol, qty, side, limit_price, time_in_force=None, enforce_cap=True, client_order_id=None):
        return env.broker._submit(symbol, qty, "buy", "limit", client_order_id, "canceled", filled=False)
    monkeypatch.setattr(env.broker, "place_limit_order", unfilled)
    assert _buy(env) is None
    assert _count("SELECT COUNT(*) FROM position_lifecycle") == 0
    assert _count("SELECT COUNT(*) FROM orders WHERE status='canceled'") == 1
    assert not env.broker.trailing_stops()


def test_client_retry_after_accepted_post_returns_existing_order(monkeypatch):
    """_request_with_retry re-POSTs after a connection error; if the first POST
    was accepted, Alpaca answers 422 on the repeat and we must not raise or re-buy."""
    c = AlpacaClient("k", "s", paper=True)
    existing = {"id": "ord-1", "symbol": "NVDA", "side": "buy", "type": "limit", "qty": "5",
                "filled_qty": "5", "status": "filled", "client_order_id": "wi-abc"}

    def post(path, body):
        assert body["client_order_id"] == "wi-abc"
        resp = requests.Response()
        resp.status_code = 422
        resp._content = b'{"code":40010001,"message":"client_order_id must be unique"}'
        raise requests.exceptions.HTTPError(response=resp)

    monkeypatch.setattr(c, "_post", post)
    monkeypatch.setattr(c, "_get", lambda path, params=None: existing)
    o = c.place_limit_order("NVDA", 5, trader.AlpacaOrderSide.BUY, 100.0,
                            enforce_cap=False, client_order_id="wi-abc")
    assert o.id == "ord-1"
