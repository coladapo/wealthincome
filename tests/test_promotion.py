"""Promotion / demotion rules on crafted equity series; proposal, rollback and the promote CLI. Offline."""

import os

import numpy as np
import pandas as pd
import pytest

from research import bars as rb
from research import pipeline, promotion, shadow
from research import strategy as reg
from research.strategy import Strategy

N = 80
DATES = pd.bdate_range("2026-01-05", periods=N)


class Tmp(Strategy):
    """Test strategy."""
    id = "tmp_challenger"
    horizon = "swing"
    default_params = {"k": 1}

    def target_weights(self, as_of, data):
        return {}


def _eq(rets, start=100_000.0):
    return pd.Series(start * np.cumprod(1 + np.r_[0.0, np.asarray(rets)[1:]]), index=DATES[:len(rets)])


def _base(seed=11):
    """A challenger that clearly beats a weak champion and SPY, every day."""
    rng = np.random.default_rng(seed)
    spy_r = rng.normal(0.0004, 0.008, N)
    champ_r = spy_r - 0.0005 + rng.normal(0, 0.006, N)
    shad_r = spy_r + 0.003 + rng.normal(0, 0.0015, N)
    spy = _eq(spy_r, 600.0)
    champ = _eq(champ_r)
    eq = _eq(shad_r)
    sh = pd.DataFrame({"equity": eq, "costs": 5.0})
    return sh, champ, spy, (spy_r, champ_r, shad_r)


def test_all_rules_pass_on_a_clear_winner():
    sh, champ, spy, _ = _base()
    chk = promotion.promotion_checks(sh, champ, spy)
    assert chk["passed"], chk["failing"]
    assert chk["n_days"] == N - 1


def test_p1_too_few_days():
    sh, champ, spy, _ = _base()
    chk = promotion.promotion_checks(sh.iloc[:45], champ, spy)
    assert chk["failing"] == ["P1 >= 60 shadow days"]


def test_p2_does_not_beat_champion():
    sh, _, spy, (_, _, shad_r) = _base()
    champ = _eq(shad_r + 0.001)                        # champion did better
    chk = promotion.promotion_checks(sh, champ, spy)
    assert "P2 beats champion (CI > 0)" in chk["failing"]
    assert "P3 beats SPY (CI > 0)" not in chk["failing"]


def test_p3_does_not_beat_spy():
    sh, champ, _, (_, _, shad_r) = _base()
    spy = _eq(shad_r + 0.0005, 600.0)
    chk = promotion.promotion_checks(sh, champ, spy)
    assert "P3 beats SPY (CI > 0)" in chk["failing"]
    assert "P2 beats champion (CI > 0)" not in chk["failing"]


def test_p4_dies_at_double_cost():
    sh, champ, spy, _ = _base()
    sh = sh.assign(costs=sh["equity"] * 0.005)         # 1x costs already in equity; 2x subtracts 0.5%/day more
    chk = promotion.promotion_checks(sh, champ, spy)
    assert chk["failing"] == ["P4 positive at 2x cost vs champion and SPY"]


def test_p5_drawdown_deeper_than_champion():
    sh, _, spy, (_, _, shad_r) = _base()
    champ = pd.Series(100_000 * 1.0001 ** np.arange(N), index=DATES)   # never draws down
    chk = promotion.promotion_checks(sh, champ, spy)
    assert "P5 max drawdown <= champion's" in chk["failing"]
    assert "P2 beats champion (CI > 0)" not in chk["failing"]


def test_p6_one_bad_month_vs_spy():
    sh, champ, spy, (spy_r, champ_r, shad_r) = _base()
    r = shad_r.copy()
    i = int(np.where(DATES.month == 2)[0][5])
    r[i] -= 0.12                                       # one -12% day in February
    sh = pd.DataFrame({"equity": _eq(r), "costs": 5.0})
    chk = promotion.promotion_checks(sh, champ, spy)
    assert "P6 no month > 3 pts behind SPY" in chk["failing"]
    assert chk["worst_month"] == "2026-02"


def test_p7_deflated_by_many_trials():
    sh, champ, spy, _ = _base()
    ok = promotion.promotion_checks(sh, champ, spy, n_trials=1)
    assert ok["dsr"]["dsr"] > 0.5
    chk = promotion.promotion_checks(sh, champ, spy, n_trials=1000, trial_sharpes=[0.0, 1.0, -1.0])
    assert "P7 deflated Sharpe > 0.5" in chk["failing"]


def test_stressed_equity_doubles_costs():
    sh = pd.DataFrame({"equity": [100.0, 101.0, 102.0], "costs": [0.0, 1.0, 0.0]}, index=DATES[:3])
    e2 = promotion.stressed_equity(sh)
    assert e2.iloc[0] == 100.0
    assert e2.iloc[1] == pytest.approx(100.0)          # +1% return minus an extra 1.0 of cost
    assert e2.iloc[2] == pytest.approx(100.0 * 102.0 / 101.0)


# --------------------------------------------------------------------------- demotion rules

def test_d1_rolling_excess_and_drawdown():
    rng = np.random.default_rng(5)
    spy_r = rng.normal(0.0005, 0.006, N)
    spy = _eq(spy_r, 600.0)
    bad = _eq(spy_r - 0.004 + rng.normal(0, 0.001, N))
    chk = promotion.demotion_checks(bad, spy, promotion_equity=float(bad.iloc[-1]))   # D2 not hit
    assert chk["checks"]["D1 60d excess vs SPY CI < 0 and drawdown > 8%"] and chk["demote"]
    assert chk["window_drawdown"] < -0.08
    # same lag but too short a record -> D1 not evaluable
    assert not promotion.demotion_checks(bad.iloc[:40], spy, None)["demote"]
    # lagging SPY but a shallow drawdown -> no D1
    calm_r = rng.normal(0.0005, 0.002, N)
    mild = _eq(calm_r - 0.0008 + rng.normal(0, 0.0003, N))
    m = promotion.demotion_checks(mild, _eq(calm_r, 600.0), None)
    assert m["window_excess"]["ci_high"] < 0 and m["window_drawdown"] > -0.08 and not m["demote"]


def test_d2_fall_from_promotion_day():
    eq = pd.Series(np.linspace(100_000, 87_000, 20), index=DATES[:20])
    spy = pd.Series(600.0, index=DATES[:20])
    assert promotion.demotion_checks(eq, spy, promotion_equity=100_000)["demote"]        # -13%
    assert not promotion.demotion_checks(eq, spy, promotion_equity=98_000)["demote"]     # -11.2%


def test_healthy_champion_not_demoted():
    sh, _, spy, _ = _base()
    chk = promotion.demotion_checks(sh["equity"], spy, promotion_equity=100_000)
    assert not chk["demote"]


# --------------------------------------------------------------------------- end to end on a store

@pytest.fixture()
def conn(tmp_path):
    c = rb.connect(str(tmp_path / "research.db"))
    shadow.ensure_schema(c)
    yield c
    c.close()


def _write_shadow(conn, s, eq, costs=5.0):
    rows = []
    prev = None
    for d, v in eq.items():
        rows.append((s.id, s.version, d.strftime("%Y-%m-%d"), float(v), float(v) * 0.4, 0.6,
                     0.0 if prev is None else v / prev - 1, costs))
        prev = v
    conn.executemany("INSERT INTO shadow_equity VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()


def _write_bench(conn, series, eq):
    conn.executemany("INSERT INTO benchmark_equity VALUES (?,?,?)",
                     [(series, d.strftime("%Y-%m-%d"), float(v)) for d, v in eq.items()])
    conn.commit()


def test_proposal_written_and_posted_once_status_unchanged(conn, tmp_path):
    sh, champ, spy, _ = _base()
    s = Tmp()
    reg.register(s, conn=conn)
    reg.set_status(s.id, s.version, "shadow", conn=conn)
    _write_shadow(conn, s, sh["equity"])
    _write_bench(conn, "champion", champ)
    _write_bench(conn, "SPY", spy)
    posts = []
    out = promotion.evaluate_all(conn=conn, poster=lambda t: posts.append(t) or True,
                                 out_dir=str(tmp_path / "props"), today="2026-04-27")
    path = str(tmp_path / "props" / "tmp_challenger-2026-04-27.md")
    assert out["proposals"] == [path] and os.path.exists(path)
    text = open(path).read()
    assert f"research.pipeline promote tmp_challenger {s.version[:12]} --approved-by chris" in text
    assert len(posts) == 1 and "Promotion proposal: tmp_challenger" in posts[0] and "http" not in posts[0]
    assert reg.get(s.id, s.version, conn=conn)["status"] == "shadow"     # never promotes
    promotion.evaluate_all(conn=conn, poster=lambda t: posts.append(t) or True,
                           out_dir=str(tmp_path / "props"), today="2026-04-27")
    assert len(posts) == 1                                               # idempotent per day


def test_failing_challenger_writes_nothing(conn, tmp_path):
    sh, champ, spy, _ = _base()
    s = Tmp()
    reg.register(s, conn=conn)
    reg.set_status(s.id, s.version, "shadow", conn=conn)
    _write_shadow(conn, s, sh["equity"].iloc[:30])
    _write_bench(conn, "champion", champ)
    _write_bench(conn, "SPY", spy)
    posts = []
    out = promotion.evaluate_all(conn=conn, poster=posts.append, out_dir=str(tmp_path / "props"))
    assert out["proposals"] == [] and posts == [] and not os.path.exists(tmp_path / "props")
    assert out["evaluated"][0]["failing"][0] == "P1 >= 60 shadow days"


def test_reference_series_never_proposed(conn):
    from research.strategies.spy_hold import SpyHoldCapped
    cap = SpyHoldCapped()
    row = reg.register(cap, conn=conn)
    row = reg.set_status(cap.id, cap.version, "shadow", conn=conn)
    assert promotion.evaluate_promotion(row, conn, post=False)["skipped"] == "reference only"


def _champion_pair(conn):
    a, b = Tmp({"k": 1}), Tmp({"k": 2})
    for s in (a, b):
        reg.register(s, conn=conn)
        reg.set_status(s.id, s.version, "shadow", conn=conn)
    reg.set_status(a.id, a.version, "champion", approved_by="chris", conn=conn)
    reg.set_status(b.id, b.version, "champion", approved_by="chris", conn=conn)   # retires a
    conn.execute("UPDATE strategies SET promoted_at='2026-02-02T15:00:00+00:00' WHERE id=? AND version=?",
                 (b.id, b.version))
    conn.commit()
    return a, b


def test_demotion_rolls_back_to_previous_champion(conn, tmp_path):
    a, b = _champion_pair(conn)
    eq = pd.Series(100_000.0, index=DATES)
    eq[DATES > pd.Timestamp("2026-02-02")] = np.linspace(99_000, 85_000, int((DATES > "2026-02-02").sum()))
    _write_shadow(conn, b, eq)
    _write_bench(conn, "SPY", pd.Series(600.0, index=DATES))
    posts = []
    out = promotion.evaluate_all(conn=conn, poster=lambda t: posts.append(t) or True,
                                 out_dir=str(tmp_path / "x"), today="2026-04-27")
    assert len(out["demotions"]) == 1
    d = out["demotions"][0]
    assert d["rollback_to"].startswith(f"tmp_challenger v{a.version[:12]}")
    assert reg.get(b.id, b.version, conn=conn)["status"] == "retired"
    ra = reg.get(a.id, a.version, conn=conn)
    assert ra["status"] == "champion" and "automatic rollback" in ra["notes"]
    assert os.path.exists(d["report"]) and len(posts) == 1 and "auto-demoted" in posts[0]
    # the restored champion has no shadow record yet -> nothing more happens on the next run
    again = promotion.evaluate_all(conn=conn, poster=posts.append, out_dir=str(tmp_path / "x"))
    assert again["demotions"] == [] and len(posts) == 1


def test_demotion_without_previous_champion_falls_back_to_live_trader(conn, tmp_path):
    s = Tmp()
    reg.register(s, conn=conn)
    reg.set_status(s.id, s.version, "shadow", conn=conn)
    reg.set_status(s.id, s.version, "champion", approved_by="chris", conn=conn)
    conn.execute("UPDATE strategies SET promoted_at='2026-01-05T15:00:00+00:00'")
    conn.commit()
    _write_shadow(conn, s, pd.Series(np.linspace(100_000, 86_000, N), index=DATES))
    _write_bench(conn, "SPY", pd.Series(600.0, index=DATES))
    out = promotion.evaluate_all(conn=conn, post=False, out_dir=str(tmp_path / "x"))
    assert out["demotions"][0]["rollback_to"].startswith("the live trader")
    assert reg.list_strategies(status="champion", conn=conn) == []


def test_healthy_champion_untouched(conn, tmp_path):
    a, b = _champion_pair(conn)
    sh, _, spy, _ = _base()
    _write_shadow(conn, b, sh["equity"])
    _write_bench(conn, "SPY", spy)
    out = promotion.evaluate_all(conn=conn, post=False, out_dir=str(tmp_path / "x"))
    assert out["demotions"] == []
    assert reg.get(b.id, b.version, conn=conn)["status"] == "champion"


# --------------------------------------------------------------------------- the promote CLI

def test_promote_cli_needs_chris_and_refuses_references(tmp_path, capsys):
    db = str(tmp_path / "research.db")
    s = Tmp()
    reg.register(s, db_path=db)
    reg.set_status(s.id, s.version, "shadow", db_path=db)
    from research.strategies.spy_hold import SpyHoldCapped
    cap = SpyHoldCapped()
    reg.register(cap, db_path=db)
    reg.set_status(cap.id, cap.version, "shadow", db_path=db)

    assert pipeline.main(["--db", db, "promote", s.id, s.version[:12], "--approved-by", "bot"]) == 2
    assert reg.get(s.id, s.version, db_path=db)["status"] == "shadow"
    assert pipeline.main(["--db", db, "promote", cap.id, cap.version[:12], "--approved-by", "chris"]) == 2
    assert pipeline.main(["--db", db, "promote", s.id, "zzzz", "--approved-by", "chris"]) == 2
    assert pipeline.main(["--db", db, "promote", s.id, s.version[:12], "--approved-by", "chris"]) == 0
    row = reg.get(s.id, s.version, db_path=db)
    assert row["status"] == "champion" and "approved_by=chris" in row["notes"]
    assert "Phase C" in capsys.readouterr().out
