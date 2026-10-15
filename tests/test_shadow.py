"""Shadow runner: start equity, next-open settlement, idempotency, catch-up, benchmarks. Offline."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from research import bars as rb
from research import shadow
from research import strategy as reg
from research.asof import ET, MarketData
from research.portfolio_sim import simulate
from research.strategies.spy_hold import SpyHoldCapped
from research.strategy import Strategy
from research_synth import frame


class HoldAAA(Strategy):
    """Test challenger: hold AAA at weight w."""
    id = "hold_aaa"
    default_params = {"w": 0.05}

    def target_weights(self, as_of, data):
        return {"AAA": float(self.params["w"])} if data.close("AAA") is not None else {}


DATES = pd.bdate_range("2026-03-02", periods=12)


def _market():
    rng = np.random.default_rng(3)
    frames = {}
    for i, s in enumerate(("SPY", "AAA")):
        c = 100 * np.cumprod(1 + rng.normal(0.001, 0.01, len(DATES)))
        o = np.r_[c[0], c[:-1] * (1 + rng.normal(0.0, 0.004, len(DATES) - 1))]   # gap opens
        frames[s] = frame(DATES, c, o)
    return MarketData(frames=frames, universe=["AAA"])


def _after_close(d):
    return datetime(d.year, d.month, d.day, 17, 0, tzinfo=ET)


@pytest.fixture()
def conn(tmp_path):
    c = rb.connect(str(tmp_path / "research.db"))
    shadow.ensure_schema(c)
    yield c
    c.close()


def _resolver(*strats):
    by = {(s.id, s.version): s for s in strats}
    return lambda row: by.get((row["id"], row["version"]))


def _setup(conn):
    cap, ch = SpyHoldCapped(), HoldAAA()
    reg.register(cap, conn=conn)                      # reference: stays a candidate
    reg.register(ch, conn=conn)
    reg.set_status(ch.id, ch.version, "shadow", conn=conn)
    return cap, ch


def _run(conn, market, d, *strats, **kw):
    return shadow.run(conn=conn, market=market, now=_after_close(d), champion=None,
                      resolve=_resolver(*strats), evaluate=False, **kw)


def test_start_day_is_100k_nothing_filled_targets_pending(conn):
    m = _market()
    cap, ch = _setup(conn)
    out = _run(conn, m, DATES[0], cap, ch)
    assert out["as_of"] == DATES[0].strftime("%Y-%m-%d")
    assert {s["id"]: s["status"] for s in out["strategies"]} == {"spy_hold_capped": "started",
                                                                  "hold_aaa": "started"}
    eq = conn.execute("SELECT strategy_id, equity, cash, invested_pct, daily_return FROM shadow_equity "
                      "ORDER BY strategy_id").fetchall()
    assert eq == [("hold_aaa", 100_000.0, 100_000.0, 0.0, 0.0), ("spy_hold_capped", 100_000.0, 100_000.0, 0.0, 0.0)]
    assert conn.execute("SELECT COUNT(*) FROM shadow_fills").fetchone()[0] == 0
    pend = conn.execute("SELECT strategy_id, symbol, weight, settled_on FROM shadow_pending_targets "
                        "WHERE strategy_id='hold_aaa'").fetchall()
    assert pend == [("hold_aaa", "AAA", 0.05, None)]
    assert conn.execute("SELECT COUNT(*) FROM shadow_pending_targets WHERE strategy_id='spy_hold_capped'"
                        ).fetchone()[0] == 8


def test_next_open_settlement(conn):
    m = _market()
    cap, ch = _setup(conn)
    _run(conn, m, DATES[0], cap, ch)
    out = _run(conn, m, DATES[1], cap, ch)
    assert {s["id"]: s["status"] for s in out["strategies"]}["hold_aaa"] == "settled"
    d1 = DATES[1].strftime("%Y-%m-%d")
    open1 = float(m.frame("AAA").at[DATES[1], "open"])
    close1 = float(m.frame("AAA").at[DATES[1], "close"])
    fills = conn.execute("SELECT date, symbol, side, qty, price, notional, cost, reason FROM shadow_fills "
                         "WHERE strategy_id='hold_aaa'").fetchall()
    assert len(fills) == 1
    date_, sym, side, qty, price, notional, cost, reason = fills[0]
    assert (date_, sym, side, reason) == (d1, "AAA", "buy", "target_enter")
    assert price == pytest.approx(open1)                         # filled at the NEXT session's open
    assert notional == pytest.approx(0.05 * 100_000)              # sized on yesterday's equity at today's open
    assert cost == pytest.approx(notional * 10 / 1e4)
    pos = conn.execute("SELECT date, symbol, qty, avg_price FROM shadow_positions WHERE strategy_id='hold_aaa'"
                       ).fetchall()
    assert pos == [(d1, "AAA", pytest.approx(qty), pytest.approx(open1))]
    eq, cash = conn.execute("SELECT equity, cash FROM shadow_equity WHERE strategy_id='hold_aaa' AND date=?",
                            (d1,)).fetchone()
    assert cash == pytest.approx(100_000 - notional - cost)
    assert eq == pytest.approx(cash + qty * close1)
    settled = conn.execute("SELECT settled_on FROM shadow_pending_targets WHERE strategy_id='hold_aaa' AND as_of=?",
                           (DATES[0].strftime("%Y-%m-%d"),)).fetchone()[0]
    assert settled == d1


def test_idempotent_per_strategy_and_date(conn):
    m = _market()
    cap, ch = _setup(conn)
    _run(conn, m, DATES[0], cap, ch)
    _run(conn, m, DATES[1], cap, ch)
    snap = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("shadow_equity", "shadow_fills", "shadow_positions", "shadow_pending_targets", "shadow_state")]
    out = _run(conn, m, DATES[1], cap, ch)
    assert {s["status"] for s in out["strategies"]} == {"up_to_date"}
    assert [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("shadow_equity", "shadow_fills", "shadow_positions", "shadow_pending_targets",
                      "shadow_state")] == snap
    assert conn.execute("SELECT COUNT(*) FROM shadow_runs").fetchone()[0] == 6   # every run is logged


def test_before_the_close_today_is_not_a_completed_session(conn):
    m = _market()
    d = DATES[3]
    assert shadow.latest_complete_session(m, datetime(d.year, d.month, d.day, 15, 59, tzinfo=ET)) == DATES[2]
    assert shadow.latest_complete_session(m, datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET)) == d


def test_catch_up_matches_one_continuous_simulation(conn):
    """Day-by-day shadow (with a missed run) == the simulator run straight through: the saved
    state round-trips exactly and missed days are caught up in order."""
    m = _market()
    cap, ch = _setup(conn)
    for d in (DATES[0], DATES[1], DATES[5], DATES[11]):      # runs missed in between
        _run(conn, m, d, cap, ch)
    for s in (cap, ch):
        got = pd.read_sql_query("SELECT date, equity FROM shadow_equity WHERE strategy_id=? ORDER BY date",
                                conn, params=(s.id,), parse_dates=["date"]).set_index("date")["equity"]
        ref = simulate(s, DATES[0], DATES[-1], m).daily["equity"]
        assert list(got.index) == list(ref.index)
        assert np.allclose(got.values, ref.values, rtol=1e-12)
    n_fills = conn.execute("SELECT COUNT(*) FROM shadow_fills WHERE strategy_id='spy_hold_capped'").fetchone()[0]
    assert n_fills == len(simulate(cap, DATES[0], DATES[-1], m).trades)


def test_benchmark_series_same_dates(conn):
    m = _market()
    cap, ch = _setup(conn)
    champ = pd.Series([100_000 + 10 * i for i in range(len(DATES))], index=DATES)
    shadow.run(conn=conn, market=m, now=_after_close(DATES[0]), champion=champ,
               resolve=_resolver(cap, ch), evaluate=False)
    out = shadow.run(conn=conn, market=m, now=_after_close(DATES[3]), champion=champ,
                     resolve=_resolver(cap, ch), evaluate=False)
    assert out["benchmarks"]["SPY"] == 3                      # day 0 already stored; first value kept
    spy = conn.execute("SELECT date, equity FROM benchmark_equity WHERE series='SPY' ORDER BY date").fetchall()
    ch_rows = conn.execute("SELECT date, equity FROM benchmark_equity WHERE series='champion' ORDER BY date"
                           ).fetchall()
    eq_dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM shadow_equity ORDER BY date")]
    assert [r[0] for r in spy] == eq_dates == [r[0] for r in ch_rows]
    assert spy[2][1] == pytest.approx(float(m.frame("SPY").at[DATES[2], "close"]))
    assert ch_rows[3][1] == 100_030.0


def test_frozen_version_mismatch_is_not_run(conn):
    m = _market()
    ch = HoldAAA({"w": 0.05})
    reg.register(ch, conn=conn)
    reg.set_status(ch.id, ch.version, "shadow", conn=conn)
    edited = HoldAAA({"w": 0.06})                         # a different version answers to the same id
    out = shadow.run(conn=conn, market=m, now=_after_close(DATES[0]), champion=None,
                     resolve=lambda row: edited, evaluate=False)
    assert out["strategies"][0]["status"] == "version_mismatch"
    assert conn.execute("SELECT COUNT(*) FROM shadow_equity").fetchone()[0] == 0


def test_tracks_shadow_champion_and_references_only(conn):
    cap, ch = _setup(conn)
    other = HoldAAA({"w": 0.03})
    reg.register(other, conn=conn)                        # plain candidate: not tracked
    ids = {(r["id"], r["version"]) for r in shadow.tracked_rows(conn)}
    assert ids == {(cap.id, cap.version), (ch.id, ch.version)}


def test_load_strategy_from_registry_row(conn):
    cap = SpyHoldCapped()
    row = reg.register(cap, conn=conn)
    s = shadow.load_strategy(row)
    assert type(s).__name__ == "SpyHoldCapped" and s.version == cap.version
    moved = dict(row, source_path="/elsewhere/checkout/research/strategies/spy_hold.py")
    assert shadow.load_strategy(moved).version == cap.version
