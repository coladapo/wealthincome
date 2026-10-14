"""Brain-health log parsing + the after-close alert, on a synthetic log."""

import json
import os
import sys
from datetime import date

import core.brain_health as bh

LOG = """\
2026-10-13 07:00:01,000 INFO trader — Asking anthropic_cli/claude-sonnet-4-6 to analyze 20 symbols...
2026-10-13 07:01:10,000 INFO core.llm_router — [anthropic_cli/claude-sonnet-4-6] tokens: in=1,000 out=200 cr=0 cw=0 total=1,200 duration=60000ms
2026-10-13 07:10:01,000 INFO trader — Asking anthropic_cli/claude-sonnet-4-6 to analyze 20 symbols...
2026-10-13 07:14:01,000 ERROR core.llm_router — LLM provider 'anthropic_cli' timed out after 240s
2026-10-13 07:14:01,001 WARNING trader — LLM returned no decision
2026-10-14 07:00:01,000 INFO trader — Asking anthropic_cli/claude-sonnet-4-6 to analyze 20 symbols...
2026-10-14 07:00:02,000 WARNING trader — Regime fetch failed: boom — defaulting to CAUTION
2026-10-14 07:01:10,000 INFO core.llm_router — [anthropic_cli/claude-sonnet-4-6] tokens: in=1,000 out=200 cr=0 cw=0 total=1,200 duration=60000ms
2026-10-14 07:10:01,000 INFO trader — Asking anthropic_cli/claude-sonnet-4-6 to analyze 20 symbols...
2026-10-14 07:10:05,000 ERROR core.llm_router — LLM provider 'anthropic_cli' failed: exit 1
2026-10-14 07:10:05,001 WARNING trader — LLM returned no decision
2026-10-14 07:20:01,000 INFO trader — Asking anthropic_cli/claude-sonnet-4-6 to analyze 20 symbols...
2026-10-14 07:21:01,000 INFO core.llm_router — [anthropic_cli/claude-sonnet-4-6] tokens: in=1,000 out=5 cr=0 cw=0 total=1,005 duration=60000ms
2026-10-14 07:21:01,001 WARNING trader — LLM returned no decision
2026-10-14 07:30:01,000 INFO trader — Asking anthropic_cli/claude-sonnet-4-6 to analyze 20 symbols...
2026-10-14 07:31:01,000 INFO core.llm_router — [anthropic_cli/claude-sonnet-4-6] tokens: in=1,000 out=200 cr=0 cw=0 total=1,200 duration=60000ms
2026-10-14 07:40:01,000 INFO trader — Asking anthropic_cli/claude-sonnet-4-6 to analyze 20 symbols...
"""


def _log(tmp_path, text=LOG):
    p = tmp_path / "trader.log"
    p.write_text(text)
    return str(p)


def test_brain_stats_counts_one_day(tmp_path):
    s = bh.brain_stats(date(2026, 10, 14), log_path=_log(tmp_path))
    assert s["asks"] == 4          # the 07:40 ask is still in flight
    assert s["pending"] == 1
    assert s["successes"] == 2
    assert s["failures"] == 2      # explicit failure + unparseable reply
    assert s["timeouts"] == 0
    assert s["success_rate_pct"] == 50.0


def test_brain_stats_timeout_and_missing_day(tmp_path):
    path = _log(tmp_path)
    s = bh.brain_stats(date(2026, 10, 13), log_path=path)
    assert (s["asks"], s["successes"], s["timeouts"], s["success_rate_pct"]) == (2, 1, 1, 50.0)
    empty = bh.brain_stats(date(2026, 10, 1), log_path=path)
    assert empty["asks"] == 0 and empty["success_rate_pct"] is None


def test_recent_sessions_newest_first(tmp_path):
    sessions = bh.recent_sessions(n=5, log_path=_log(tmp_path))
    assert [s["date"] for s in sessions] == ["2026-10-14", "2026-10-13"]
    assert bh.recent_sessions(n=1, log_path=_log(tmp_path))[0]["date"] == "2026-10-14"


def test_missing_log_is_empty(tmp_path):
    assert bh.recent_sessions(log_path=str(tmp_path / "nope.log")) == []


def test_brain_stats_from_db(tmp_path, monkeypatch):
    import sqlite3
    import backend.db as db
    path = str(tmp_path / "brain.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    conn = sqlite3.connect(path)
    # 10-14: 4 market-open cycles reach the brain (3 decide, 1 no-decision),
    # plus one market-open cycle that errored before the brain and one closed-market cycle.
    for cid, at, mopen in [(1, "2026-10-14T07:00:00", 1), (2, "2026-10-14T07:10:00", 1),
                           (3, "2026-10-14T07:20:00", 1), (4, "2026-10-14T07:30:00", 1),
                           (5, "2026-10-14T07:40:00", 1), (6, "2026-10-14T17:00:00", 0),
                           (7, "2026-10-13T07:00:00", 1)]:
        conn.execute("INSERT INTO cycles (id, started_at, market_open) VALUES (?, ?, ?)", (cid, at, mopen))
    for cid in (1, 2, 3, 6):
        conn.execute("INSERT INTO ai_decisions (cycle_id, decided_at) VALUES (?, '2026-10-14T07:00:00')", (cid,))
    for cid, at in [(4, "2026-10-14T07:34:00"), (7, "2026-10-13T07:04:00")]:
        conn.execute("INSERT INTO errors (occurred_at, error_type, message, cycle_id)"
                     " VALUES (?, 'llm_no_decision', 'Empty response', ?)", (at, cid))
    conn.commit(); conn.close()

    s = bh.brain_stats(date(2026, 10, 14))
    assert s["source"] == "db"
    assert (s["asks"], s["successes"], s["failures"], s["success_rate_pct"]) == (4, 3, 1, 75.0)
    assert [r["date"] for r in bh.recent_sessions()] == ["2026-10-14", "2026-10-13"]
    assert bh.recent_sessions()[1]["success_rate_pct"] == 0.0


# ── after-close alert ────────────────────────────────────────────────────────

def _bad_day(d, ok, total):
    lines = []
    for i in range(total):
        lines.append(f"{d} 07:{i:02d}:00,000 INFO trader — Asking anthropic_cli/m to analyze 20 symbols...")
        if i < ok:
            lines.append(f"{d} 07:{i:02d}:30,000 INFO core.llm_router — [anthropic_cli/m] tokens: in=1")
        else:
            lines.append(f"{d} 07:{i:02d}:30,000 ERROR core.llm_router — LLM provider 'anthropic_cli' timed out after 240s")
    return "\n".join(lines) + "\n"


def _alert_module(monkeypatch, tmp_path, log_text):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
    import brain_health_alert as alert
    import backend.db as db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "missing.db"))  # DB down → log fallback
    monkeypatch.setattr(bh, "LOG_PATH", _log(tmp_path, log_text))
    monkeypatch.setattr(alert, "STATE_FILE", str(tmp_path / "state.json"))
    return alert


def test_alert_webhook_only_when_previous_session_healthy(monkeypatch, tmp_path):
    alert = _alert_module(monkeypatch, tmp_path,
                          _bad_day("2026-10-13", 10, 10) + _bad_day("2026-10-14", 3, 10))
    hooks, alerts = [], []
    alert.run(date(2026, 10, 14), post_webhook=lambda t: hooks.append(t) or True,
              post_alerts=lambda t: alerts.append(t) or True)
    assert len(hooks) == 1 and "3/10 calls OK today (30%)" in hooks[0]
    assert alerts == []


def test_alert_escalates_once_per_day(monkeypatch, tmp_path):
    alert = _alert_module(monkeypatch, tmp_path,
                          _bad_day("2026-10-13", 5, 10) + _bad_day("2026-10-14", 3, 10))
    hooks, alerts = [], []
    kw = dict(post_webhook=lambda t: hooks.append(t) or True,
              post_alerts=lambda t: alerts.append(t) or True)
    alert.run(date(2026, 10, 14), **kw)
    alert.run(date(2026, 10, 14), **kw)
    assert len(hooks) == 1 and len(alerts) == 1
    assert alerts[0] == hooks[0]
    assert json.loads((tmp_path / "state.json").read_text())["last_alert_date"] == "2026-10-14"


def test_alert_quiet_on_healthy_or_tiny_day(monkeypatch, tmp_path):
    alert = _alert_module(monkeypatch, tmp_path,
                          _bad_day("2026-10-13", 0, 4) + _bad_day("2026-10-14", 9, 10))
    sent = []
    alert.run(date(2026, 10, 14), post_webhook=sent.append, post_alerts=sent.append)
    alert.run(date(2026, 10, 13), post_webhook=sent.append, post_alerts=sent.append)  # only 4 asks
    assert sent == []
