"""Pre-registration, trial budget refusal, safe variant_runs migration, budgeted evaluate. Offline."""

import sqlite3

import pytest

from research import bars as rb
from research import experiments as ex
from research import pipeline
from research.strategies.spy_hold import SpyHoldCapped
from research_synth import random_market


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "research.db")


def test_register_is_immutable(db):
    h = ex.register_hypothesis("h1", "why", "rule", max_trials=2, db_path=db)
    assert h["trials_used"] == 0 and h["max_trials"] == 2
    ex.register_hypothesis("h1", "why", "rule", max_trials=2, db_path=db)   # same content: no-op
    with pytest.raises(ValueError):
        ex.register_hypothesis("h1", "why", "a different rule", max_trials=2, db_path=db)


def test_budget_refusal(db):
    ex.register_hypothesis("h2", "why", "rule", max_trials=2, db_path=db)
    assert ex.check_budget("h2", db_path=db) == 2
    ex.log_trial("h2", "edge", {"a": 1}, {"r": 1}, db_path=db)
    ex.log_trial("h2", "edge", {"a": 2}, {"r": 2}, db_path=db)
    with pytest.raises(ex.TrialBudgetExhausted):
        ex.check_budget("h2", db_path=db)
    with pytest.raises(ex.TrialBudgetExhausted):
        ex.log_trial("h2", "edge", {"a": 3}, {"r": 3}, db_path=db)
    assert ex.trials_used("h2", db_path=db) == 2
    assert [t["params"]["a"] for t in ex.trial_results("h2", db_path=db)] == [1, 2]
    with pytest.raises(ex.UnknownHypothesis):
        ex.check_budget("never-registered", db_path=db)


def test_migration_adds_hypothesis_column_and_keeps_rows(tmp_path):
    path = str(tmp_path / "old.db")
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE variant_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,
        edge_name TEXT NOT NULL, run_at TEXT NOT NULL, params_json TEXT, n_events INTEGER,
        result_json TEXT);
        INSERT INTO variant_runs(edge_name, run_at) VALUES ('pead', '2026-10-14');""")
    c.commit()
    c.close()
    c = rb.connect(path)
    ex.ensure_schema(c)
    ex.ensure_schema(c)                                   # idempotent
    cols = [r[1] for r in c.execute("PRAGMA table_info(variant_runs)")]
    assert "hypothesis_id" in cols
    assert c.execute("SELECT edge_name, hypothesis_id FROM variant_runs").fetchall() == [("pead", None)]
    c.close()


def test_evaluate_runs_one_trial_then_refuses(db):
    market = random_market("2019-01-01", "2021-12-31")
    ex.register_hypothesis("h3", "why", "rule", max_trials=1, db_path=db)
    out = pipeline.evaluate(SpyHoldCapped(), "h3", db_path=db, market=market, champion=None)
    assert out["run_id"] > 0
    assert out["registry"]["status"] == "candidate"
    assert out["selection"]["dsr"]["n_trials"] == 1
    logged = ex.trial_results("h3", db_path=db)[0]["result"]
    assert logged["daily_returns"] and logged["strategy_id"] == "spy_hold_capped"
    with pytest.raises(ex.TrialBudgetExhausted):
        pipeline.evaluate(SpyHoldCapped({"slots": 4}), "h3", db_path=db, market=market, champion=None)


def test_cli_refuses_unregistered_hypothesis(db, capsys):
    rc = pipeline.main(["--db", db, "evaluate", "research.strategies.spy_hold:SpyHoldCapped",
                        "--hypothesis", "nope"])
    assert rc == 2
    assert "not pre-registered" in capsys.readouterr().err
