"""Every headless CLI call writes exactly one llm_calls row, whatever the outcome."""

import json
import sqlite3
import subprocess
import time

import pytest

import backend.db as bdb
import core.llm_router as lr
import core.plan_guard as pg
import core.validation_agent as va

ENV_OK = {"type": "result", "subtype": "success", "is_error": False, "result": '{"decisions": []}',
          "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000,
                    "cache_creation_input_tokens": 10},
          "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 100}}, "total_cost_usd": 0.01}


class _Proc:
    def __init__(self, envelope=None, rc=0, stdout=None, stderr=""):
        self.returncode = rc
        self.stdout = stdout if stdout is not None else json.dumps(envelope)
        self.stderr = stderr


@pytest.fixture()
def real_db(tmp_path, monkeypatch):
    monkeypatch.setattr(bdb, "DB_PATH", str(tmp_path / "wi.db"))
    bdb.init_db()
    return bdb


@pytest.fixture()
def cli(monkeypatch):
    """Fake subprocess.run; set cli['proc'] (a _Proc or an exception) per test."""
    state = {"proc": _Proc(ENV_OK), "argv": None}

    def fake_run(argv, **kw):
        state["argv"] = argv
        if isinstance(state["proc"], BaseException):
            raise state["proc"]
        return state["proc"]

    monkeypatch.setattr(subprocess, "run", fake_run)
    return state


def _rows():
    with bdb.db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM llm_calls ORDER BY id").fetchall()]


def test_ok_call_writes_full_row(real_db, cli):
    text, usage = lr._run_anthropic_cli("sys", "user", "sonnet", 10, effort="high", caller="brain", cycle_id=7)
    assert text == '{"decisions": []}'
    (r,) = _rows()
    assert (r["caller"], r["cycle_id"], r["outcome"], r["error"]) == ("brain", 7, "ok", None)
    assert (r["model_requested"], r["model_resolved"], r["effort"]) == ("sonnet", "claude-sonnet-5-5", "high")
    assert (r["input_tokens"], r["output_tokens"], r["cache_read_tokens"], r["cache_write_tokens"]) == (100, 50, 1000, 10)
    from core.llm_pricing import compute_cost
    assert r["nominal_usd"] == pytest.approx(compute_cost("claude-sonnet-5-5", 100, 50, 1000, 10))
    assert r["duration_ms"] is not None
    raw = json.loads(r["raw_json"])
    assert "result" not in raw and raw["total_cost_usd"] == 0.01
    assert r["plan_5h_pct"] is None and r["plan_week_pct"] is None   # no reading in tests


@pytest.mark.parametrize("proc,outcome,exc", [
    (subprocess.TimeoutExpired(cmd="claude", timeout=10), "timeout", subprocess.TimeoutExpired),
    (_Proc(rc=1, stdout="", stderr="boom"), "error", RuntimeError),
    (_Proc(stdout="not json"), "error", ValueError),
    (_Proc({**ENV_OK, "subtype": "error_max_budget_usd", "is_error": True, "result": None,
            "terminal_reason": "budget_exhausted"}, rc=1), "budget", RuntimeError),
])
def test_failures_still_write_a_row(real_db, cli, proc, outcome, exc):
    cli["proc"] = proc
    with pytest.raises(exc):
        lr._run_anthropic_cli("sys", "user", "sonnet", 10, caller="validator", max_budget_usd=0.25)
    (r,) = _rows()
    assert r["outcome"] == outcome and r["caller"] == "validator" and r["error"]
    if outcome == "budget":
        assert r["input_tokens"] == 100       # tokens of the stopped call are kept


def test_empty_reply_and_default_caller(real_db, cli):
    cli["proc"] = _Proc({**ENV_OK, "result": ""})
    text, _ = lr._run_anthropic_cli("sys", "user", "sonnet", 10)
    assert text == ""
    (r,) = _rows()
    assert (r["outcome"], r["caller"]) == ("empty", "other")


def test_budget_flag_in_argv():
    argv = lr.build_cli_argv("sonnet", "<sys>", max_budget_usd=0.25)
    assert argv[argv.index("--max-budget-usd") + 1] == "0.25"
    assert "--max-budget-usd" not in lr.build_cli_argv("sonnet", "<sys>")


def test_fresh_plan_reading_lands_on_row(real_db, cli, tmp_path, monkeypatch):
    p = tmp_path / "usage.json"
    p.write_text(json.dumps({"checked_at": time.time() - 60, "windows": {
        "five_hour": {"pct": 73, "resets_at": 1}, "seven_day": {"pct": 89, "resets_at": 2}}}))
    monkeypatch.setattr(pg, "USAGE_PATH", str(p))
    lr._run_anthropic_cli("sys", "user", "sonnet", 10)
    (r,) = _rows()
    assert (r["plan_5h_pct"], r["plan_week_pct"]) == (73, 89)


def test_validator_call_is_ledgered_with_its_budget(real_db, cli):
    cli["proc"] = _Proc({**ENV_OK, "result": '{"verdict": "pass", "risk_score": 2, "top_risks": []}'})
    buy = {"action": "buy", "symbol": "NVDA", "confidence": 0.8, "position_size_pct": 0.05, "reasoning": "x"}
    v = va.validate_decision(buy, "ctx", {}, {"portfolio_value": 1e5, "cash": 1e4}, cycle_id=3)
    assert v["verdict"] == "pass"
    argv = cli["argv"]
    assert argv[argv.index("--max-budget-usd") + 1] == "0.25"
    assert argv[argv.index("--effort") + 1] == "medium"
    (r,) = _rows()
    assert (r["caller"], r["cycle_id"], r["outcome"]) == ("validator", 3, "ok")


def test_brain_call_is_ledgered_with_its_budget(real_db, cli, monkeypatch):
    import core.claude_trader as ct
    monkeypatch.setattr(ct, "_build_prompt", lambda *a, **k: "prompt")
    out = lr.run_decision([], {}, {}, {}, cycle_id=11)
    assert out["_model"] == "claude-sonnet-5-5"
    argv = cli["argv"]
    assert argv[argv.index("--max-budget-usd") + 1] == "1"
    (r,) = _rows()
    assert (r["caller"], r["cycle_id"], r["outcome"], r["effort"]) == ("brain", 11, "ok", "high")


def test_brain_timeout_is_ledgered(real_db, cli, monkeypatch):
    import core.claude_trader as ct
    monkeypatch.setattr(ct, "_build_prompt", lambda *a, **k: "prompt")
    cli["proc"] = subprocess.TimeoutExpired(cmd="claude", timeout=240)
    assert lr.run_decision([], {}, {}, {}, cycle_id=12) is None
    (r,) = _rows()
    assert (r["caller"], r["cycle_id"], r["outcome"]) == ("brain", 12, "timeout")


def test_research_call_is_ledgered(real_db, cli):
    from research.weekly_research import call_model
    call_model("sys", "user")
    argv = cli["argv"]
    assert argv[argv.index("--max-budget-usd") + 1] == "2"
    (r,) = _rows()
    assert (r["caller"], r["model_requested"]) == ("research", "opus")


def test_budget_config_none_disables_cap(real_db):
    real_db.set_config("llm_max_budget_usd", "none")
    assert lr.budget_from_config("llm_max_budget_usd", 1.0) is None
    real_db.set_config("llm_max_budget_usd", "0.5")
    assert lr.budget_from_config("llm_max_budget_usd", 1.0) == 0.5


def test_ledger_failure_never_breaks_the_call(cli, tmp_path, monkeypatch):
    monkeypatch.setattr(bdb, "DB_PATH", str(tmp_path / "no_tables.db"))   # llm_calls missing
    text, _ = lr._run_anthropic_cli("sys", "user", "sonnet", 10)
    assert text == '{"decisions": []}'


def _research_db(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE research_sessions (id INTEGER PRIMARY KEY, started_at TEXT, ended_at TEXT,
        week TEXT, model_requested TEXT, model_resolved TEXT, effort TEXT, input_tokens INTEGER,
        output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, model_seconds REAL,
        cost_usd_estimate REAL, status TEXT, error TEXT)""")
    conn.execute("""INSERT INTO research_sessions VALUES (1, '2026-10-16T04:22:34+00:00',
        '2026-10-16T04:24:40+00:00', '2026-W42', 'opus', 'claude-opus-5-5', 'high', 2, 10768, 0, 31293,
        105.8, 0.37, 'ok', NULL)""")
    conn.commit()
    conn.close()


def test_backfill_is_idempotent(real_db, tmp_path):
    with bdb.db() as conn:
        conn.execute("""INSERT INTO cycles(id, started_at, market_open, status, llm_model, input_tokens,
                        output_tokens, cache_read_tokens, cache_write_tokens, duration_ms)
                        VALUES (1, '2026-10-14T10:00:00', 1, 'done', 'claude-sonnet-5-5', 10, 20, 30, 40, 5000)""")
        conn.execute("INSERT INTO cycles(id, started_at, market_open, status) VALUES (2, '2026-10-14T10:05:00', 1, 'error')")
        conn.execute("INSERT INTO cycles(id, started_at, market_open, status) VALUES (3, '2026-10-14T10:10:00', 0, 'error')")
        conn.execute("INSERT INTO errors(occurred_at, error_type, message, cycle_id) VALUES ('x', 'llm_no_decision', 'Empty', 2)")
    rdb = str(tmp_path / "research.db")
    _research_db(rdb)

    assert bdb.backfill_llm_calls(rdb) == {"brain": 2, "research": 1}
    assert bdb.backfill_llm_calls(rdb) == {"brain": 0, "research": 0}
    rows = _rows()
    assert len(rows) == 3
    brain = {r["cycle_id"]: r for r in rows if r["caller"] == "brain"}
    assert brain[1]["outcome"] == "ok" and brain[1]["nominal_usd"] > 0
    assert brain[2]["outcome"] == "error"
    (res,) = [r for r in rows if r["caller"] == "research"]
    assert res["model_resolved"] == "claude-opus-5-5" and res["duration_ms"] == 105800
    assert res["called_at"][:4] == "2026" and "+" not in res["called_at"]   # local naive clock
