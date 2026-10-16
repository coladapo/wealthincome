"""Promotion / demotion per sleeve: each challenger vs its own sleeve's champion (or the sleeve's
benchmark when it has none) and the sleeve's market; rollback within the sleeve. Offline."""

import numpy as np
import pandas as pd
import pytest

from research import bars as rb
from research import promotion, shadow
from research import strategy as reg
from research.strategy import Strategy

N = 80
DATES = pd.bdate_range("2026-01-05", periods=N)


def _cls(horizon):
    class S(Strategy):
        """Test strategy."""
        id = f"sleeve_{horizon}"
        default_params = {"k": 1}

        def target_weights(self, as_of, data):
            return {}
    S.horizon = horizon
    return S


Day, Swing, Long, Treasury = (_cls(h) for h in ("day", "swing", "long", "treasury"))


@pytest.fixture()
def conn(tmp_path):
    c = rb.connect(str(tmp_path / "research.db"))
    shadow.ensure_schema(c)
    yield c
    c.close()


def _eq(rets, start=100_000.0):
    return pd.Series(start * np.cumprod(1 + np.r_[0.0, np.asarray(rets)[1:]]), index=DATES[:len(rets)])


def _write_shadow(conn, s, eq, costs=1.0):
    prev, rows = None, []
    for d, v in eq.items():
        rows.append((s.id, s.version, d.strftime("%Y-%m-%d"), float(v), float(v), 0.0,
                     0.0 if prev is None else v / prev - 1, costs))
        prev = v
    conn.executemany("INSERT INTO shadow_equity VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()


def _write_bench(conn, series, eq):
    conn.executemany("INSERT INTO benchmark_equity VALUES (?,?,?)",
                     [(series, d.strftime("%Y-%m-%d"), float(v)) for d, v in eq.items()])
    conn.commit()


def _shadow_row(conn, s):
    reg.register(s, conn=conn)
    return reg.set_status(s.id, s.version, "shadow", conn=conn)


def _champion(conn, s, promoted_at="2026-01-05T15:00:00+00:00"):
    _shadow_row(conn, s)
    reg.set_status(s.id, s.version, "champion", approved_by="chris", conn=conn)
    conn.execute("UPDATE strategies SET promoted_at=? WHERE id=? AND version=?", (promoted_at, s.id, s.version))
    conn.commit()
    return reg.get(s.id, s.version, conn=conn)


RNG = np.random.default_rng(21)
SPY_R = RNG.normal(0.0006, 0.008, N)
LIVE_R = SPY_R - 0.0005 + RNG.normal(0, 0.006, N)
TBILL_R = 0.00018 + np.abs(RNG.normal(0, 0.00002, N))          # never a down day


def _benchmarks(conn):
    _write_bench(conn, "SPY", _eq(SPY_R, 600.0))
    _write_bench(conn, "champion", _eq(LIVE_R))


# --------------------------------------------------------------------------- comparators

@pytest.mark.parametrize("cls,champ_label,market_label", [
    (Treasury, "cash (no treasury champion)", "cash"),
    (Long, "SPY (no long champion)", "SPY"),
    (Swing, "the live picker's real equity (no swing champion)", "SPY"),
    (Day, "SPY (no day champion)", "SPY"),
])
def test_fallback_benchmark_per_sleeve(conn, cls, champ_label, market_label):
    _benchmarks(conn)
    row = _shadow_row(conn, cls())
    cmp_ = promotion.sleeve_comparators(conn, row, DATES)
    (cl, ce), (ml, me) = cmp_["champion"], cmp_["market"]
    assert (cl, ml) == (champ_label, market_label)
    expect = {"cash": pd.Series(1.0, index=DATES), "SPY": _eq(SPY_R, 600.0),
              "the live picker's real equity": _eq(LIVE_R)}
    assert np.allclose(ce.values, expect[champ_label.split(" (")[0]].values)
    assert np.allclose(me.values, expect[market_label].values)


def test_sleeve_champion_is_used_and_other_sleeves_are_not(conn):
    _benchmarks(conn)
    lc = Long({"k": 9})
    _champion(conn, lc)
    _write_shadow(conn, lc, _eq(SPY_R + 0.002))
    long_row = _shadow_row(conn, Long())
    swing_row = _shadow_row(conn, Swing())
    (label, eq), _ = promotion.sleeve_comparators(conn, long_row, DATES).values()
    assert label == f"long champion sleeve_long v{lc.version[:12]}"
    assert np.allclose(eq.values, _eq(SPY_R + 0.002).values)
    (label, eq), _ = promotion.sleeve_comparators(conn, swing_row, DATES).values()   # the long champion is not swing's
    assert label.startswith("the live picker's real equity")


# --------------------------------------------------------------------------- promotion end to end

def test_treasury_challenger_judged_vs_cash_not_spy(conn, tmp_path):
    _benchmarks(conn)
    t = Treasury()
    _shadow_row(conn, t)
    _write_shadow(conn, t, _eq(TBILL_R))
    out = promotion.evaluate_all(conn=conn, post=False, out_dir=str(tmp_path / "p"), today="2026-04-27")
    ev = out["evaluated"][0]
    assert ev["sleeve"] == "treasury" and ev["vs"] == "cash (no treasury champion)"
    assert ev["passed"], ev["failing"]                          # SPY beat it handily: irrelevant here
    text = open(out["proposals"][0]).read()
    assert "Sleeve: treasury" in text and "| P3 beats cash (CI > 0) | yes |" in text


def test_same_series_in_long_sleeve_fails_vs_spy(conn, tmp_path):
    _benchmarks(conn)
    s = Long()
    _shadow_row(conn, s)
    _write_shadow(conn, s, _eq(TBILL_R))
    ev = promotion.evaluate_all(conn=conn, post=False, out_dir=str(tmp_path / "p"))["evaluated"][0]
    assert "P2 beats champion (CI > 0)" in ev["failing"] and "P3 beats SPY (CI > 0)" in ev["failing"]


def test_long_challenger_must_beat_the_long_champion(conn, tmp_path):
    _benchmarks(conn)
    noise = RNG.normal(0, 0.0015, N)
    ch_r = SPY_R + 0.003 + noise                                # beats SPY every day
    s = Long()
    _shadow_row(conn, s)
    _write_shadow(conn, s, _eq(ch_r))
    ok = promotion.evaluate_all(conn=conn, post=False, out_dir=str(tmp_path / "a"))["evaluated"][0]
    assert ok["vs"] == "SPY (no long champion)" and ok["passed"], ok["failing"]
    lc = Long({"k": 2})
    _champion(conn, lc)
    _write_shadow(conn, lc, _eq(ch_r + 0.001))                  # the sleeve champion does better
    ev = promotion.evaluate_all(conn=conn, post=False, out_dir=str(tmp_path / "b"))["evaluated"]
    ev = [e for e in ev if e["version"] == s.version][0]
    assert ev["vs"].startswith("long champion") and "P2 beats champion (CI > 0)" in ev["failing"]
    assert "P3 beats SPY (CI > 0)" not in ev["failing"]


def test_day_challenger_vs_spy(conn, tmp_path):
    _benchmarks(conn)
    s = Day()
    _shadow_row(conn, s)
    _write_shadow(conn, s, _eq(SPY_R - 0.001))
    ev = promotion.evaluate_all(conn=conn, post=False, out_dir=str(tmp_path / "p"))["evaluated"][0]
    assert ev["vs"] == "SPY (no day champion)" and "P3 beats SPY (CI > 0)" in ev["failing"]


# --------------------------------------------------------------------------- demotion / rollback per sleeve

def _falling():
    return pd.Series(np.linspace(100_000, 85_000, N), index=DATES)


def test_rollback_stays_inside_the_sleeve(conn, tmp_path):
    _benchmarks(conn)
    la, lb = Long({"k": 1}), Long({"k": 2})
    _champion(conn, la, "2026-01-05T15:00:00+00:00")
    _champion(conn, lb, "2026-01-06T15:00:00+00:00")             # retires la (same sleeve)
    conn.execute("UPDATE strategies SET demoted_at='2026-01-06T15:00:00+00:00' WHERE version=?", (la.version,))
    sa, sb = Swing({"k": 1}), Swing({"k": 2})
    _champion(conn, sa, "2026-01-07T15:00:00+00:00")
    _champion(conn, sb, "2026-01-08T15:00:00+00:00")             # retires sa -- demoted MORE recently than la
    conn.commit()
    assert reg.get(la.id, la.version, conn=conn)["status"] == "retired"
    assert reg.get(lb.id, lb.version, conn=conn)["status"] == "champion"        # swing promotion left long alone
    _write_shadow(conn, lb, _falling())
    out = promotion.evaluate_all(conn=conn, post=False, out_dir=str(tmp_path / "x"), today="2026-04-27")
    assert [d["sleeve"] for d in out["demotions"]] == ["long"]
    assert out["demotions"][0]["rollback_to"].startswith(f"sleeve_long v{la.version[:12]}")
    assert reg.get(la.id, la.version, conn=conn)["status"] == "champion"
    assert reg.get(sa.id, sa.version, conn=conn)["status"] == "retired"         # not pulled across sleeves
    assert reg.get(sb.id, sb.version, conn=conn)["status"] == "champion"


def test_demotion_without_previous_sleeve_champion(conn, tmp_path):
    _benchmarks(conn)
    sa, sb = Swing({"k": 1}), Swing({"k": 2})
    _champion(conn, sa)
    _champion(conn, sb)                                          # a former SWING champion exists
    t = Treasury()
    _champion(conn, t)
    _write_shadow(conn, t, _falling())
    out = promotion.evaluate_all(conn=conn, post=False, out_dir=str(tmp_path / "x"))
    d = out["demotions"][0]
    assert d["sleeve"] == "treasury" and d["rollback_to"].startswith("no treasury champion")
    assert reg.list_strategies(status="champion", horizon="treasury", conn=conn) == []
    assert reg.get(sa.id, sa.version, conn=conn)["status"] == "retired"


def test_treasury_demotion_measured_vs_cash(conn, tmp_path):
    _benchmarks(conn)
    t = Treasury()
    _champion(conn, t)
    _write_shadow(conn, t, _eq(TBILL_R))
    out = promotion.evaluate_demotion(reg.get(t.id, t.version, conn=conn), conn, post=False,
                                      out_dir=str(tmp_path / "x"))
    assert not out["demoted"] and list(out["checks"])[0] == "D1 60d excess vs cash CI < 0 and drawdown > 8%"
