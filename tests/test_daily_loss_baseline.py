"""Daily-loss breaker: the day's opening equity is persisted (survives restarts),
and a trip blocks NEW BUYS only — exits still run."""

from datetime import datetime
from types import SimpleNamespace

import pytest

import backend.db as bdb
import backend.trader as trader


@pytest.fixture()
def real_db(tmp_path, monkeypatch):
    monkeypatch.setattr(bdb, "DB_PATH", str(tmp_path / "wi.db"))
    bdb.init_db()
    monkeypatch.setattr(trader, "_today_baseline", None)
    monkeypatch.setattr(trader, "_baseline_date", None)
    return bdb


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def test_first_cycle_persists_baseline_and_later_cycles_reuse_it(real_db):
    assert trader.daily_loss_baseline(100_000.0, "2026-10-14") == 100_000.0
    assert trader.daily_loss_baseline(97_000.0, "2026-10-14") == 100_000.0
    assert real_db.get_config()["daily_loss_baseline_2026-10-14"] == "100000.00"
    assert trader.daily_loss_baseline(97_000.0, "2026-10-15") == 97_000.0   # new day, new key


def test_restart_does_not_reset_baseline(real_db, monkeypatch):
    assert trader.is_daily_loss_limit_hit(100_000.0, 0.05) is False
    # Process restarts: in-memory state is gone.
    monkeypatch.setattr(trader, "_today_baseline", None)
    monkeypatch.setattr(trader, "_baseline_date", None)
    # 6% down vs the persisted open — the old in-memory version re-based to 94k and saw 0%.
    assert trader.is_daily_loss_limit_hit(94_000.0, 0.05) is True


def test_trip_blocks_buys_but_exits_still_run(real_db, monkeypatch):
    real_db.set_config(f"daily_loss_baseline_{_today()}", "100000.00")
    account = SimpleNamespace(portfolio_value=90_000.0, cash=50_000.0, long_market_value=40_000.0,
                              buying_power=50_000.0, trading_blocked=False)
    held = SimpleNamespace(symbol="MSFT", qty=10, avg_entry_price=500, current_price=450,
                           market_value=4500, unrealized_pl=-500, unrealized_plpc=-0.1)
    alpaca = SimpleNamespace(paper=True, is_market_open=lambda: True,
                             get_account=lambda: account, get_positions=lambda: [held])

    def boom(*a, **k):
        raise RuntimeError("not under test")

    for name in ("get_performance_summary", "get_news_summary", "compute_correlation_matrix",
                 "tick_snapshot_symbols", "tick_get_latest", "get_options_flow", "get_insider_signals",
                 "get_enriched_context", "build_session_feedback_block", "get_calendar_summary_for_claude"):
        monkeypatch.setattr(trader, name, boom)
    from core import trade_lessons
    monkeypatch.setattr(trade_lessons, "build_lessons_digest", lambda *a, **k: "")
    monkeypatch.setattr(trader, "get_market_regime", lambda **k: {"regime": "BULL", "score": 80})
    monkeypatch.setattr(trader, "regime_summary_for_claude", lambda d: "BULL")
    monkeypatch.setattr(trader, "build_watchlist", lambda **k: {"symbols": ["NVDA"]})
    monkeypatch.setattr(trader, "fetch_market_data", lambda wl, a: {})
    monkeypatch.setattr(trader, "run_decision", lambda **k: {"decisions": [
        {"symbol": "NVDA", "action": "buy", "confidence": 0.9},
        {"symbol": "MSFT", "action": "sell", "confidence": 0.9},
    ], "market_summary": "test"})
    monkeypatch.setattr(trader, "record_ai_decision", lambda **k: 1)
    monkeypatch.setattr(trader, "finish_cycle", lambda *a, **k: None)
    monkeypatch.setattr(trader, "validate_decisions", lambda decisions, **k: decisions)

    executed, stop_sweeps, failed = [], [], []
    monkeypatch.setattr(trader, "execute_decision", lambda d, *a, **k: executed.append(d["action"]))
    monkeypatch.setattr(trader, "check_stop_losses", lambda *a, **k: stop_sweeps.append(1))
    monkeypatch.setattr(trader, "fail_cycle", lambda cid, reason, **k: failed.append(reason))

    trader.run_cycle(alpaca)

    assert executed == ["sell"]          # buy suppressed, exit went through
    assert stop_sweeps == [1]            # stop-loss sweep still ran
    assert failed == []                  # cycle not aborted
    errs = [e for e in real_db.get_errors(50) if e["error_type"] == "daily_loss_limit"]
    assert len(errs) == 1 and "buys blocked" in errs[0]["message"]


def test_cycle_lock_is_exclusive(tmp_path):
    path = str(tmp_path / "cycle.lock")
    first = trader.acquire_cycle_lock(path)
    assert first is not None
    assert trader.acquire_cycle_lock(path) is None   # flock is per open file description
    first.close()
    again = trader.acquire_cycle_lock(path)
    assert again is not None
    again.close()
