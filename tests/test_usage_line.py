"""Digest Usage line + /usage summary from the llm_calls ledger."""

from datetime import date, datetime

import pytest

import backend.db as bdb
from core.scorecard import format_digest
from core.usage_report import format_usage_line, usage_summary

TODAY = date(2026, 10, 15)


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(bdb, "DB_PATH", str(tmp_path / "wi.db"))
    bdb.init_db()
    for caller, day, inp, cr, usd in [("brain", "2026-10-15", 30_000, 1_000_000, 4.0),
                                      ("brain", "2026-10-15", 30_000, 100_000, 0.4),
                                      ("validator", "2026-10-15", 1_000, 0, 0.1),
                                      ("research", "2026-09-30", 0, 0, 100.0),
                                      ("brain", "2026-08-01", 0, 0, 999.0)]:   # outside 30d
        bdb.record_llm_call({"called_at": f"{day}T10:00:00", "caller": caller, "input_tokens": inp,
                             "output_tokens": 0, "cache_read_tokens": cr, "cache_write_tokens": 0,
                             "nominal_usd": usd, "outcome": "ok"})
    return bdb


def _plan():
    resets = datetime(2026, 10, 21, 5, 0).timestamp()   # a Wednesday, 5am local
    return {"fresh": True, "five_hour_pct": 73.0, "week_pct": 89.0, "week_resets_at": resets}


def test_usage_line_renders(ledger):
    line = format_usage_line(usage_summary(today=TODAY, plan=_plan()))
    assert line == ("Usage: 2 brain + 1 validator calls · 1.2M tokens (1.1M cache-read) · $4.5 nominal · "
                    "plan 5h 73% / week 89% (resets Wed 5am) · 30d $104")


def test_usage_line_without_plan_or_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(bdb, "DB_PATH", str(tmp_path / "empty.db"))
    bdb.init_db()
    line = format_usage_line(usage_summary(today=TODAY, plan={"fresh": False}))
    assert line == "Usage: no model calls today · plan n/a · 30d $0.0"


def test_summary_by_caller(ledger):
    s = usage_summary(today=TODAY, plan=_plan())
    assert s["today"]["by_caller"]["brain"]["calls"] == 2
    assert s["last_30d"]["by_caller"]["research"]["nominal_usd"] == 100.0
    assert "brain" in s["last_30d"]["by_caller"] and s["last_30d"]["total"]["calls"] == 4


def test_format_digest_appends_usage_line():
    card = {"overall": {"closed_trades": 0, "win_rate_pct": 0, "net_pnl": 0, "expectancy_per_trade": 0,
                        "profit_factor": None},
            "execution": {"fill_rate_pct": 0, "filled": 0, "orders_total": 0,
                          "avg_slippage_buy_pct": 0, "avg_slippage_sell_pct": 0},
            "equity": {}, "by_close_reason": []}
    assert format_digest(card, usage_line="Usage: x").splitlines()[-1] == "Usage: x"
    assert "Usage" not in format_digest(card)
