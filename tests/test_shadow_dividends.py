"""Shadow total return: dividend cache, ex-date credit, no basis mixing when history is re-adjusted. Offline.

The synthetic store mimics yfinance auto_adjust: the store as read on run date R holds every
bar up to R, with each bar before an ex-date e <= R multiplied by 1 - amount / raw close(e-1).
"""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from research import bars as rb
from research import shadow
from research import strategy as reg
from research.asof import ET, MarketData
from research.portfolio_sim import simulate
from research.strategies.treasury_sweep import TreasurySweep
from research_synth import frame

DATES = pd.bdate_range("2026-03-02", periods=12)
EX = DATES[5]
AMOUNT = 0.40


def _raw():
    t = np.arange(len(DATES))
    bil = 100.0 + 0.01 * t - np.where(DATES >= EX, AMOUNT, 0.0)     # drops by the dividend on EX
    spy = 500.0 * (1.001 ** t)
    return {"BIL": bil, "SPY": spy}


def _store_as_of(r_date, divs=None):
    """MarketData exactly as an auto-adjusted store would read on r_date."""
    divs = {"BIL": [(EX, AMOUNT)]} if divs is None else divs
    keep = DATES <= r_date
    frames = {}
    for sym, raw in _raw().items():
        c = raw[keep].copy()
        o = c - 0.003
        f = np.ones(keep.sum())
        for e, a in divs.get(sym, []):
            if e <= r_date:
                i = int(np.where(DATES[keep] == e)[0][0])
                f[:i] *= 1.0 - a / c[i - 1]
        frames[sym] = frame(DATES[keep], c * f, o * f)
    return MarketData(frames=frames, universe=[])


def _fetcher(visible_from=None):
    """yfinance-like: tz-aware 09:30 ex-date index; the dividend appears from `visible_from` on."""
    calls = []

    def fetch(sym):
        calls.append(sym)
        if sym != "BIL" or (visible_from is not None and fetch.today < visible_from):
            return pd.Series(dtype=float)
        idx = pd.DatetimeIndex([EX + pd.Timedelta(hours=9, minutes=30)]).tz_localize("America/New_York")
        return pd.Series([AMOUNT], index=idx, name="Dividends")
    fetch.calls, fetch.today = calls, DATES[0]
    return fetch


@pytest.fixture()
def conn(tmp_path):
    c = rb.connect(str(tmp_path / "research.db"))
    shadow.ensure_schema(c)
    yield c
    c.close()


def _setup(conn):
    s = TreasurySweep()
    reg.register(s, conn=conn)
    reg.set_status(s.id, s.version, "shadow", conn=conn)
    return s


def _run(conn, d, s, fetch, divs=None):
    fetch.today = d
    return shadow.run(conn=conn, market=_store_as_of(d, divs), now=datetime(d.year, d.month, d.day, 17, 0, tzinfo=ET),
                      champion=None, resolve=lambda row: s if row["version"] == s.version else None,
                      evaluate=False, dividend_fetcher=fetch)


def _equity(conn, sid):
    return pd.read_sql_query("SELECT date, equity, cash, daily_return FROM shadow_equity WHERE strategy_id=? "
                             "ORDER BY date", conn, params=(sid,), parse_dates=["date"]).set_index("date")


def test_dividend_cache_upsert_and_max_age(conn):
    f = _fetcher()
    now = datetime(2026, 3, 10, 21, 0, tzinfo=ET)
    out = rb.refresh_dividends(["BIL", "SPY#3", "SPY#4"], conn=conn, fetcher=f, now=now, pause=0)
    assert out == {"symbols": 2, "fetched": 2, "cached": 0, "rows": 1, "failed": 0}
    assert conn.execute("SELECT symbol, ex_date, amount, source FROM dividends").fetchall() == [
        ("BIL", EX.strftime("%Y-%m-%d"), AMOUNT, "yfinance")]
    again = rb.refresh_dividends(["BIL", "SPY"], conn=conn, fetcher=f, now=now + pd.Timedelta(hours=5), pause=0)
    assert again["cached"] == 2 and len(f.calls) == 2                  # within max age: not re-fetched
    rb.refresh_dividends(["BIL"], conn=conn, fetcher=f, now=now + pd.Timedelta(hours=30), pause=0)
    assert len(f.calls) == 3 and conn.execute("SELECT COUNT(*) FROM dividends").fetchone()[0] == 1   # upsert

    def boom(sym):
        raise OSError("offline")
    assert rb.refresh_dividends(["QQQ"], conn=conn, fetcher=boom, now=now, pause=0)["failed"] == 1


def test_dividend_scale_undoes_later_adjustments():
    m = _store_as_of(DATES[-1])
    close = m.frame("BIL")["close"]
    divs = pd.Series([AMOUNT], index=pd.DatetimeIndex([EX]))
    raw = _raw()["BIL"]
    k = rb.dividend_scale(close, divs, DATES[3])                       # before EX: undo the EX factor
    assert close[DATES[3]] * k == pytest.approx(raw[3], rel=1e-12)
    assert rb.dividend_scale(close, divs, EX) == 1.0                    # on/after EX: already raw


def test_ex_date_credit_makes_total_return(conn):
    s = _setup(conn)
    f = _fetcher()
    for d in DATES:
        _run(conn, d, s, f)
    eq = _equity(conn, s.id)
    raw = _raw()["BIL"]
    qty = conn.execute("SELECT qty FROM shadow_positions WHERE strategy_id=? AND date=?",
                       (s.id, DATES[4].strftime("%Y-%m-%d"))).fetchone()[0]
    rows = conn.execute("SELECT symbol, ex_date, credited_on, shares, amount, credit FROM shadow_dividends"
                        ).fetchall()
    exs = EX.strftime("%Y-%m-%d")
    assert rows == [("BIL", exs, exs, pytest.approx(qty), AMOUNT, pytest.approx(qty * AMOUNT))]
    # the ex-date return is the T-bill accrual (+0.01 on ~100), not the -0.4% price drop
    r_ex = eq.loc[EX, "daily_return"]
    assert r_ex == pytest.approx(qty * (raw[5] + AMOUNT - raw[4]) / eq.loc[DATES[4], "equity"], rel=1e-9)
    assert r_ex > 0
    assert eq.loc[EX, "cash"] == pytest.approx(eq.loc[DATES[4], "cash"] + qty * AMOUNT)
    # marked at the raw close every day: equity = cash + shares x raw close
    assert eq.loc[DATES[-1], "equity"] == pytest.approx(eq.loc[DATES[-1], "cash"] + qty * raw[-1], rel=1e-12)
    # same as one straight simulation on the final (dividend-adjusted, i.e. total-return) store
    ref = simulate(s, DATES[0], DATES[-1], _store_as_of(DATES[-1])).daily["equity"]
    assert np.allclose(eq["equity"].values, ref.values, rtol=2e-5)
    assert "BIL" in f.calls


def test_catch_up_across_ex_date_does_not_mix_bases(conn):
    s = _setup(conn)
    f = _fetcher()
    for d in DATES:
        _run(conn, d, s, f)
    daily = _equity(conn, s.id)
    fills = conn.execute("SELECT date, price FROM shadow_fills").fetchall()

    c2 = rb.connect(":memory:")
    shadow.ensure_schema(c2)
    s2 = _setup(c2)
    f2 = _fetcher()
    for d in (DATES[0], DATES[1], DATES[2], DATES[-1]):              # one run catches up across EX
        out = _run(c2, d, s2, f2)
    assert out["strategies"][0]["status"] == "settled" and "basis" not in (out["strategies"][0]["detail"] or "")
    caught = _equity(c2, s2.id)
    assert np.allclose(caught["equity"].values, daily["equity"].values, rtol=1e-12)
    assert np.allclose(caught["daily_return"].values, daily["daily_return"].values, atol=1e-12)
    assert c2.execute("SELECT date, price FROM shadow_fills").fetchall() == fills   # raw open, not adjusted
    assert c2.execute("SELECT COUNT(*) FROM shadow_dividends").fetchone()[0] == 1
    c2.close()


def test_bought_on_the_ex_date_open_is_not_entitled(conn):
    s = _setup(conn)
    f = _fetcher()
    for d in DATES[4:8]:                 # enters shadow the session before EX: first fill at EX's open
        _run(conn, d, s, f)
    assert conn.execute("SELECT COUNT(*) FROM shadow_fills WHERE date=?", (EX.strftime("%Y-%m-%d"),)
                        ).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM shadow_dividends").fetchone()[0] == 0


def test_late_dividend_credited_on_next_session(conn):
    s = _setup(conn)
    f = _fetcher(visible_from=DATES[7])                     # cache misses it on EX, sees it 2 sessions later
    for d in DATES[:9]:
        _run(conn, d, s, f)
    qty = conn.execute("SELECT qty FROM shadow_positions WHERE date=?", (DATES[4].strftime("%Y-%m-%d"),)
                       ).fetchone()[0]
    rows = conn.execute("SELECT ex_date, credited_on, shares FROM shadow_dividends").fetchall()
    assert rows == [(EX.strftime("%Y-%m-%d"), DATES[7].strftime("%Y-%m-%d"), pytest.approx(qty))]
    eq = _equity(conn, s.id)
    assert eq.loc[EX, "daily_return"] < 0                   # price drop on EX ...
    assert eq.loc[DATES[7], "cash"] == pytest.approx(eq.loc[DATES[6], "cash"] + qty * AMOUNT)   # ... made good


def test_unexplained_rewrite_is_flagged(conn):
    s = _setup(conn)
    f = _fetcher()
    no_div = {"BIL": []}
    for d in DATES[:4]:
        _run(conn, d, s, f, divs=no_div)
    # history rewritten by 2% with no cached dividend to explain it (e.g. a split / a missing dividend)
    d = DATES[4]
    m = _store_as_of(d, no_div)
    m._frames["BIL"].loc[m._frames["BIL"].index < d, ["open", "high", "low", "close"]] *= 0.98
    out = shadow.run(conn=conn, market=m, now=datetime(d.year, d.month, d.day, 17, 0, tzinfo=ET), champion=None,
                     resolve=lambda row: s if row["version"] == s.version else None, evaluate=False)
    det = out["strategies"][0]["detail"]
    assert det.startswith(d.strftime("%Y-%m-%d")) and "basis: " in det and "BIL" in det
    assert conn.execute("SELECT detail FROM shadow_runs ORDER BY rowid DESC LIMIT 1").fetchone()[0] == det


def test_spy_benchmark_is_total_return(conn):
    s = _setup(conn)
    f = _fetcher()
    divs = {"BIL": [(EX, AMOUNT)], "SPY": [(EX, 2.0)]}
    for d in DATES:
        _run(conn, d, s, f, divs=divs)
    spy = dict(conn.execute("SELECT date, equity FROM benchmark_equity WHERE series='SPY'"))
    raw = _raw()["SPY"]
    ds = [d.strftime("%Y-%m-%d") for d in DATES]
    assert spy[ds[0]] == pytest.approx(raw[0])
    # the ex-date step includes the 2.0 dividend (store ratio on one basis), not the raw price ratio
    assert spy[ds[5]] / spy[ds[4]] == pytest.approx((raw[5]) / (raw[4] - 2.0), rel=1e-12)
