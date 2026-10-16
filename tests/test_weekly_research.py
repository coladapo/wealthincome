"""Weekly bounded research session: prompt, validation, module write, never beyond shadow. Offline."""

import ast
import copy
import json
import os
import sqlite3

import pytest

from research import bars as rb
from research import experiments
from research import pipeline
from research import strategy as reg
from research import weekly_research as wr
from research.strategies.spy_hold import SpyHoldCapped
from research_synth import random_market

GOOD_CLASS = '''class HoldAaaSlice(Strategy):
    """Hold AAA at 8%."""
    id = "hold_aaa_slice"
    horizon = "swing"
    default_params = {"w": 0.08}

    def target_weights(self, as_of, data):
        return {"AAA": float(self.params["w"])} if data.close("AAA") is not None else {}
'''


def proposal(**over):
    p = {"id": "hold_aaa_slice", "title": "Hold AAA", "horizon": "swing",
         "rationale": "Index funds under-own AAA because of a float adjustment lag.",
         "counterparty": "Passive funds that rebalance on a schedule, not on price.",
         "rule": "Hold AAA at 8% of equity, always.", "params": {"w": 0.08},
         "bar": {"plain_english": "Beat SPY with CI > 0.", "criteria": {"min_sharpe": 0.5}},
         "trial_budget": 2, "data_needed": ["daily_bars"], "related_rejected_edge": None,
         "new_reason": None, "class_name": "HoldAaaSlice", "class_source": GOOD_CLASS}
    p.update(over)
    return p


@pytest.fixture()
def env(tmp_path):
    db = str(tmp_path / "research.db")
    c = rb.connect(db)
    experiments.ensure_schema(c)
    c.close()
    experiments.register_hypothesis("baseline-spy-hold", "reference", "hold SPY", 10, db_path=db)
    reg.register(SpyHoldCapped(), hypothesis_id="baseline-spy-hold", notes="baseline", db_path=db)
    art = tmp_path / "artifacts"
    art.mkdir()
    (art / "edge-mean-reversion-report.md").write_text(
        "# Edge report: mean_reversion\n\nIt does not clear the bar: pure market drift.\n")
    (art / "hypothesis-treasury-sweep-report.md").write_text(
        "# Hypothesis treasury\n\n**Verdict: CLEARS its pre-registered bar.**\n")
    (art / "sleeve-design.md").write_text("| Sleeve | Horizon |\n|---|---|\n| Swing | 2-30 d |\n")
    (art / "phase-b-design.md").write_text("**New bar (tightened):** beat SPY with CI > 0.\n")
    return {"db": db, "art": str(art), "tmp": tmp_path}


def test_prompt_assembly_from_synthetic_state(env):
    st = wr.gather_state(env["db"], env["art"])
    assert any(s["id"] == "spy_hold_capped" for s in st["strategies"])
    assert st["hypotheses"][0]["id"] == "baseline-spy-hold"
    rej = {r["name"]: r for r in st["rejected_edges"]}
    assert rej["mean-reversion"]["rejected"] and not rej["treasury-sweep"]["rejected"]
    system, user = wr.build_prompt(st)
    assert "NO authority over orders" in system
    assert "At most 3 proposals" in system and "WHO is" in system
    for needle in ("spy_hold_capped", "baseline-spy-hold", "mean-reversion", "pure market drift",
                   "def target_weights", "class AsOfData", "class TrendFilteredCore", "class_source",
                   "Sleeve floors", "excess_vs_spy_ci", "New bar (tightened)", "Swing | 2-30 d"):
        assert needle in user, needle
    assert wr.estimate_tokens(system + user) > 1000


def test_json_validation_and_recycled_edge_rejection(env):
    st = wr.gather_state(env["db"], env["art"])
    recycled = proposal(id="oversold_bounce", class_name="OversoldBounce",
                        rule="Buy oversold names (RSI < 30) and hold 5 days.")
    with_reason = proposal(id="oversold_after_forced_selling", class_name="OversoldAfterForcedSelling",
                           rule="Buy oversold names after index deletions.",
                           new_reason="Not the generic dip: only dips caused by forced index-deletion selling, "
                                      "a different counterparty.")
    reply = json.dumps({"session_note": "x", "proposals": [
        proposal(), recycled, with_reason,
        proposal(id="fourth_one", class_name="FourthOne")]})
    obj = wr.parse_reply("```json\n" + reply + "\n```")
    acc, rej = wr.validate_reply(obj, st)
    assert [p["id"] for p in acc] == ["hold_aaa_slice", "oversold_after_forced_selling"]
    assert acc[1]["related_rejected_edge"] == "mean-reversion"
    reasons = {r["proposal"]["id"]: r["reason"] for r in rej}
    assert "already-rejected edge 'mean-reversion'" in reasons["oversold_bounce"]
    assert "over the cap" in reasons["fourth_one"]

    names = list(wr.REJECTED_EDGE_KEYWORDS)
    bad = [proposal(horizon="day"), proposal(trial_budget=6), proposal(id="spy_hold_capped"),
           proposal(bar={"plain_english": "x", "criteria": {"vibes": True}}),
           proposal(data_needed=["minute_bars"]), proposal(counterparty=""),
           proposal(related_rejected_edge="pead", new_reason="short")]
    for p in bad:
        assert wr.validate_proposal(p, st, names) is not None, p
    with pytest.raises(ValueError):
        wr.parse_reply('{"no_proposals": 1}')


def test_module_write_and_ast_check(env):
    d = str(env["tmp"] / "proposed")
    path, err = wr.write_module(proposal(), "2026-42", "research-2026-42-hold_aaa_slice", "claude-x", proposed_dir=d)
    assert err is None and path.endswith(os.path.join("2026-42", "hold_aaa_slice.py"))
    src = open(path).read()
    ast.parse(src)
    assert "Pre-registered bar: Beat SPY with CI > 0." in src and "Who is on the other side" in src
    cls = wr.load_class(path, "HoldAaaSlice")
    s = cls(None)
    assert s.id == "hold_aaa_slice" and s.horizon == "swing" and s.source_path == path
    # never overwritten (frozen)
    _, err2 = wr.write_module(proposal(), "2026-42", "h", "m", proposed_dir=d)
    assert "already exists" in err2

    cases = {
        "syntax": GOOD_CLASS.replace("def target_weights(self, as_of, data):", "def target_weights(self, as_of, data)"),
        "import": "import os\n" + GOOD_CLASS,
        "open": GOOD_CLASS.replace('return {"AAA"', 'open("/tmp/x")\n        return {"AAA"'),
        "dunder": GOOD_CLASS.replace('return {"AAA"', 'x = self.__class__.__subclasses__()\n        return {"AAA"'),
        "id": GOOD_CLASS.replace('id = "hold_aaa_slice"', 'id = "other"'),
        "horizon": GOOD_CLASS.replace('horizon = "swing"', 'horizon = "long"'),
        "base": GOOD_CLASS.replace("(Strategy)", "(object)"),
        "two_classes": GOOD_CLASS + "\n\nclass Extra(Strategy):\n    id = 'x'\n",
    }
    for name, src in cases.items():
        p = proposal(id=f"case_{name}", class_source=src.replace("hold_aaa_slice", f"case_{name}")
                     if name not in ("id",) else src)
        path, err = wr.write_module(p, "2026-42", "h", "m", proposed_dir=d)
        assert err is not None and path is None, name


def test_no_code_path_moves_a_strategy_beyond_shadow():
    src = open(wr.__file__).read()
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    set_status = [c for c in calls if getattr(c.func, "attr", getattr(c.func, "id", None)) == "set_status"]
    assert len(set_status) == 1
    st_arg = set_status[0].args[2]
    assert isinstance(st_arg, ast.Constant) and st_arg.value == "shadow"
    assert not any(isinstance(k.value, ast.Constant) and k.arg == "approved_by" for c in set_status for k in c.keywords)
    banned = {"promote", "set_allocation"}
    assert not [c for c in calls if getattr(c.func, "attr", getattr(c.func, "id", None)) in banned]
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == "champion"]


def _fake_eval(m):
    """pipeline.evaluate on the synthetic market, without the live champion series."""
    def ev(strategy, hid, db_path=None, market=None, notes=None):
        return pipeline.evaluate(strategy, hid, db_path=db_path, market=m, champion=None, notes=notes)
    return ev


def test_run_proposal_clears_bar_to_shadow_and_fails_otherwise(env):
    m = random_market("2019-01-01", "2022-12-30", symbols=("AAA", "BBB"), seed=2)
    d = str(env["tmp"] / "proposed")
    easy = proposal(bar={"plain_english": "any", "criteria": {"min_sharpe": -100}})
    # the swing floor still applies: drop it for the "mechanical pass" check
    orig = copy.deepcopy(wr.SLEEVE_FLOORS)
    try:
        wr.SLEEVE_FLOORS["swing"] = {}
        out = wr.run_proposal(easy, "2026-42", "m", db_path=env["db"], market=m, proposed_dir=d,
                              evaluate_fn=_fake_eval(m))
    finally:
        wr.SLEEVE_FLOORS.clear()
        wr.SLEEVE_FLOORS.update(orig)
    assert out["status"] == "shadow", out
    assert reg.get("hold_aaa_slice", out["version"], db_path=env["db"])["status"] == "shadow"
    h = experiments.get_hypothesis("research-2026-42-hold_aaa_slice", db_path=env["db"])
    assert h["max_trials"] == 2 and h["trials_used"] == 1 and "PRE-REGISTERED BAR" in h["rule_text"]

    hard = proposal(id="hold_bbb_slice", class_name="HoldBbbSlice",
                    class_source=GOOD_CLASS.replace("hold_aaa_slice", "hold_bbb_slice")
                    .replace("HoldAaaSlice", "HoldBbbSlice").replace('"AAA"', '"BBB"'),
                    bar={"plain_english": "impossible", "criteria": {"min_sharpe": 100}})
    out2 = wr.run_proposal(hard, "2026-42", "m", db_path=env["db"], market=m, proposed_dir=d,
                           evaluate_fn=_fake_eval(m))
    assert out2["status"] == "failed_bar"
    assert reg.get("hold_bbb_slice", out2["version"], db_path=env["db"])["status"] == "candidate"
    assert any(c["criterion"] == "min_trades" and c["threshold"] == 200 for c in out2["checks"])  # floor merged


def test_effective_bar_only_tightens():
    p = proposal(bar={"plain_english": "x", "criteria": {"excess_vs_spy_ci": "date", "min_trades": 50,
                                                         "min_sharpe": 0.8}})
    b = wr.effective_bar(p)
    assert b["excess_vs_spy_ci"] == "both" and b["min_trades"] == 200 and b["min_sharpe"] == 0.8
    t = proposal(horizon="treasury", bar={"plain_english": "x", "criteria": {"max_drawdown_floor": -0.5}})
    assert wr.effective_bar(t)["max_drawdown_floor"] == -0.01


def test_dry_run_builds_prompt_without_model_or_writes(env):
    def boom(*a, **k):
        raise AssertionError("model must not be called in a dry run")
    def files():   # a read-only open of a WAL database may leave -shm/-wal sidecars; not a write
        return sorted(f for f in os.listdir(env["tmp"]) if not f.endswith(("-shm", "-wal")))
    before = files()
    out = wr.run_weekly_session(dry_run=True, db_path=env["db"], artifacts_dir=env["art"], model_call=boom,
                                post=boom)
    assert out["dry_run"] and out["tokens_estimate"] > 1000 and "Reply: strict JSON" in out["user_prompt"]
    assert files() == before
    conn = sqlite3.connect(env["db"])
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='research_sessions'").fetchone() is None
    conn.close()


def test_full_session_offline(env):
    m = random_market("2019-01-01", "2022-12-30", symbols=("AAA", "BBB"), seed=2)
    reply = json.dumps({"session_note": "test", "proposals": [
        proposal(), proposal(id="oversold_bounce", class_name="OversoldBounce", rule="buy oversold dips")]})
    posted = []

    def model(system, user):
        assert "Weekly research session" in user
        return reply, {"input_tokens": 9000, "output_tokens": 2000, "resolved_model": "claude-opus-5-5"}
    rec = wr.run_weekly_session(db_path=env["db"], artifacts_dir=env["art"], proposed_dir=str(env["tmp"] / "p"),
                                post=lambda t: posted.append(t) or True, model_call=model,
                                evaluate_fn=_fake_eval(m), report_dir=str(env["tmp"] / "reports"), market=m)
    assert rec["status"] == "ok" and rec["n_proposed"] == 2 and rec["n_accepted"] == 1
    assert rec["outcomes"][0]["status"] in ("shadow", "failed_bar")
    assert len(posted) == 1 and posted[0].startswith("🧪 Weekly research: 2 proposed")
    rep = open(rec["report_path"]).read()
    assert "hold_aaa_slice" in rep and "Rejected before any run" in rep and "claude-opus-5-5" in rep
    assert os.path.exists(rec["artifact_path"])
    conn = sqlite3.connect(env["db"])
    row = conn.execute("SELECT model_resolved, input_tokens, output_tokens, n_proposed, cost_usd_estimate "
                       "FROM research_sessions").fetchone()
    conn.close()
    assert row[:4] == ("claude-opus-5-5", 9000, 2000, 2) and row[4] == pytest.approx(9000 * 4e-6 + 2000 * 20e-6)


def test_model_failure_is_recorded_not_retried(env):
    import subprocess
    calls = []

    def model(system, user):
        calls.append(1)
        raise subprocess.TimeoutExpired("claude", 1800)
    posted = []
    rec = wr.run_weekly_session(db_path=env["db"], artifacts_dir=env["art"], proposed_dir=str(env["tmp"] / "p"),
                                post=lambda t: posted.append(t) or True, model_call=model,
                                report_dir=str(env["tmp"] / "reports"), copy_to_artifacts=False)
    assert calls == [1] and rec["status"] == "model_failed" and "1800" in rec["error"]
    assert "no proposals this week" in posted[0]
