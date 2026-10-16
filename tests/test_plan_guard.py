"""Plan guard: near the shared plan's limit the brain is skipped; exits/stops still run."""

import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import backend.db as bdb
import backend.trader as trader
import core.plan_guard as pg


def _reading(path, five=50, week=50, age_s=60):
    path.write_text(json.dumps({"checked_at": time.time() - age_s, "windows": {
        "five_hour": {"pct": five, "resets_at": 1792130400},
        "seven_day": {"pct": week, "resets_at": 1792584000}}}))
    return str(path)


@pytest.fixture()
def real_db(tmp_path, monkeypatch):
    monkeypatch.setattr(bdb, "DB_PATH", str(tmp_path / "wi.db"))
    bdb.init_db()
    return bdb


CFG = dict(bdb.DEFAULT_CONFIG)


@pytest.mark.parametrize("five,week,hold", [
    (50, 94, False), (50, 95, True), (89, 50, False), (90, 50, True), (99, 99, True),
])
def test_thresholds(tmp_path, five, week, hold):
    s = pg.plan_state(_reading(tmp_path / "u.json", five, week))
    assert s["fresh"]
    assert pg.should_hold(CFG, s)[0] is hold


def test_thresholds_follow_config(tmp_path):
    s = pg.plan_state(_reading(tmp_path / "u.json", five=10, week=80))
    assert pg.should_hold({**CFG, "plan_guard_week_pct": "80"}, s)[0] is True


@pytest.mark.parametrize("make", [
    lambda p: str(p / "missing.json"),
    lambda p: _reading(p / "u.json", 99, 99, age_s=2 * 3600 + 60),
    lambda p: (p / "bad.json").write_text("{nope") and str(p / "bad.json"),
])
def test_stale_missing_or_bad_reading_means_guard_off(tmp_path, make):
    s = pg.plan_state(make(tmp_path))
    assert s["fresh"] is False and s["reason"]
    assert pg.should_hold(CFG, s)[0] is False


def test_note_hold_throttles_errors_and_posts_once_per_day(real_db):
    def holds():
        return [e for e in real_db.get_errors(50) if e["error_type"] == "plan_guard_hold"]

    posts = []
    for _ in range(3):
        pg.note_hold("week at 96%", post=posts.append)
    assert len(holds()) == 1                    # once per hour, not per cycle
    with bdb.db() as conn:                      # an hour passes
        conn.execute("UPDATE errors SET occurred_at = ?",
                     ((datetime.now() - timedelta(minutes=61)).isoformat(),))
    pg.note_hold("week at 96%", post=posts.append)
    assert len(holds()) == 2
    assert len(posts) == 1 and "week at 96%" in posts[0]   # one post per day
    pg.note_hold("week at 96%", post=posts.append, now=datetime.now() + timedelta(days=1))
    assert len(posts) == 2                      # next day posts again


def test_held_cycle_skips_brain_but_runs_stops(real_db, tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "USAGE_PATH", _reading(tmp_path / "u.json", five=40, week=97))
    account = SimpleNamespace(portfolio_value=100_000.0, cash=50_000.0, long_market_value=50_000.0,
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
    brain_calls, stop_sweeps, failed, posts = [], [], [], []
    monkeypatch.setattr(trader, "run_decision", lambda **k: brain_calls.append(1))
    monkeypatch.setattr(trader, "check_stop_losses", lambda *a, **k: stop_sweeps.append(1))
    monkeypatch.setattr(trader, "fail_cycle", lambda cid, reason, **k: failed.append(reason))
    monkeypatch.setattr(pg, "_default_post", lambda text: posts.append(text) or True)

    trader.run_cycle(alpaca)

    assert brain_calls == []                 # no brain → no new entries
    assert stop_sweeps == [1]                # stop-loss sweep still ran
    assert failed == ["plan_guard_hold"]
    assert [e["error_type"] for e in real_db.get_errors(50)].count("plan_guard_hold") == 1
    assert len(posts) == 1
