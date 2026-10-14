"""Validator fails CLOSED by default; old fail-open reachable via config."""

import logging

import pytest

import backend.db as bdb
import core.llm_router as llm_router
import core.validation_agent as va


@pytest.fixture()
def real_db(tmp_path, monkeypatch):
    monkeypatch.setattr(bdb, "DB_PATH", str(tmp_path / "wi.db"))
    bdb.init_db()
    return bdb


@pytest.fixture()
def calls(monkeypatch):
    seen = {}

    def fake_timeout(system_prompt, user_prompt, model, timeout):
        seen["timeout"] = timeout
        raise TimeoutError("model call timed out")

    monkeypatch.setitem(llm_router._PROVIDERS, "anthropic_cli", fake_timeout)
    return seen


BUY = {"action": "buy", "symbol": "NVDA", "confidence": 0.8, "position_size_pct": 0.05,
       "reasoning": "trend"}


def test_default_config_has_fail_closed_true(real_db):
    assert real_db.get_config()["validator_fail_closed"] == "true"


def test_timeout_blocks_when_fail_closed(real_db, calls):
    v = va.validate_decision(dict(BUY), "ctx", {}, {"portfolio_value": 1e5, "cash": 1e4})
    assert v["verdict"] == va.BLOCK
    assert v["block_reason"] == "validator_unavailable"
    assert v["_source"] == "fail_closed"
    assert calls["timeout"] == 90


def test_fail_open_reachable_with_false(real_db, calls):
    real_db.set_config("validator_fail_closed", "false")
    v = va.validate_decision(dict(BUY), "ctx", {}, {"portfolio_value": 1e5, "cash": 1e4})
    assert v["verdict"] == va.PASS
    assert v["_source"] == "fallback"


def test_validate_decisions_skips_buy_keeps_sell_and_logs_once(real_db, calls, caplog):
    sell = {"action": "sell", "symbol": "AAPL", "confidence": 0.9}
    with caplog.at_level(logging.WARNING, logger="core.validation_agent"):
        approved = va.validate_decisions([dict(BUY), sell], "ctx", {"AAPL": {}},
                                         {"portfolio_value": 1e5, "cash": 1e4})
    assert [d["symbol"] for d in approved] == ["AAPL"]  # exits untouched
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].startswith("validator unavailable — buy skipped (fail-closed)")


def test_unreadable_config_fails_closed(monkeypatch, calls):
    def boom():
        raise RuntimeError("db gone")
    monkeypatch.setattr(bdb, "get_config", boom)
    assert va._fail_closed_enabled() is True
