"""Gap-fill trigger logic, direction handling, fill-rate math and the difference CI. Offline."""

import numpy as np
import pandas as pd
import pytest

from research import bars as rb
from research import compare as cmp
from research.edges import gap_fill as gf
from research.event_study import run_event_study


def _flat(n=60, px=100.0, volume=1e6, start="2024-01-01"):
    days = pd.bdate_range(start, periods=n)
    return pd.DataFrame({"open": px, "high": px, "low": px, "close": px, "volume": volume},
                        index=days)


def _gap(df, i, gap, close=None, high=None, low=None):
    """Make day i open at prior close * (1 + gap)."""
    prev = df["close"].iloc[i - 1]
    o = prev * (1 + gap)
    c = o if close is None else close
    df.iloc[i, df.columns.get_indexer(["open", "close"])] = [o, c]
    df.iloc[i, df.columns.get_loc("high")] = max(o, c) if high is None else high
    df.iloc[i, df.columns.get_loc("low")] = min(o, c) if low is None else low
    return df


def _store(tmp_path, frames):
    path = str(tmp_path / "research.db")
    conn = rb.connect(path)
    for sym, df in frames.items():
        rb._write(conn, sym, df, replace_all=True)
    conn.commit()
    conn.close()
    return rb.BarStore(path)


# ---- trigger logic -------------------------------------------------------------------------

def test_gap_down_fires_only_past_threshold():
    df = _flat()                                   # $100M/day, liquid
    _gap(df, 30, -0.025)
    _gap(df, 40, -0.015)                           # too small
    sig = gf.signals(df)
    assert list(sig.index[sig["event"]]) == [df.index[30]]
    assert sig["gap"].iloc[30] == pytest.approx(-0.025)
    assert sig["level"].iloc[30] == pytest.approx(100.0)


def test_illiquid_names_are_filtered():
    df = _flat(volume=1e3)                         # $0.1M/day
    _gap(df, 30, -0.05)
    assert not gf.signals(df)["event"].any()


def test_liquidity_uses_prior_days_only():
    df = _flat(volume=1e3)
    df.iloc[30, df.columns.get_loc("volume")] = 1e9     # huge volume ON the gap day only
    _gap(df, 30, -0.05)
    assert not gf.signals(df)["event"].iloc[30]


def test_direction_handling_up_side_is_short():
    df = _flat()
    _gap(df, 30, -0.03, close=100.0)               # close back at 100 so the next open is not a gap
    _gap(df, 45, 0.03, close=100.0)
    dn, up = gf.signals(df, side="down"), gf.signals(df, side="up")
    assert list(dn.index[dn["event"]]) == [df.index[30]] and (dn["direction"] == 1).all()
    assert list(up.index[up["event"]]) == [df.index[45]] and (up["direction"] == -1).all()


def test_three_day_down_and_sma_filters():
    n = 260
    df = _flat(n)
    c = 100 + 0.1 * np.arange(n)                   # steady uptrend -> above SMA200
    df["open"] = df["high"] = df["low"] = df["close"] = c
    _gap(df, 240, -0.03)
    assert gf.signals(df, require_above_sma=True)["event"].iloc[240]
    assert not gf.signals(df, require_down=True)["event"].iloc[240]      # no down run before it
    for k in (237, 238, 239):                      # three straight down closes before day 240
        df.iloc[k, df.columns.get_indexer(["open", "high", "low", "close"])] = c[236] - (k - 236)
    _gap(df, 240, -0.03)
    assert gf.signals(df, require_down=True)["event"].iloc[240]


def test_entry_modes_shift_event_date(tmp_path):
    df = _flat()
    _gap(df, 30, -0.04)
    store = _store(tmp_path, {"AAA": df})
    nxt = gf.generate_events(store, symbols=["AAA"])
    opn = gf.generate_events(store, symbols=["AAA"], entry="gap_open")
    assert nxt["event_date"].iloc[0] == df.index[30]
    assert opn["event_date"].iloc[0] == df.index[29]
    assert (nxt["gap_date"] == opn["gap_date"]).all()
    # gap_open: the harness enters AT the gap-day open
    r = run_event_study(opn[["symbol", "event_date", "direction"]], horizons=(1,), cost_bps_per_side=0,
                        store=store, bear_windows=())
    assert r.trades["entry_px"].iloc[0] == pytest.approx(96.0)
    store.close()


def test_baseline_is_same_filter_no_trigger(tmp_path):
    df = _flat()
    _gap(df, 30, -0.04)
    store = _store(tmp_path, {"AAA": df})
    base = gf.generate_events(store, symbols=["AAA"], baseline=True)
    sig = gf.signals(df)
    assert len(base) == int(sig["filter"].sum()) > 1
    assert (base["direction"] == 1).all()
    store.close()


def test_in_index_only_drops_pre_addition_days():
    ev = pd.DataFrame({"symbol": ["AAA", "AAA", "BBB", "CCC"],
                       "event_date": pd.to_datetime(["2020-01-02", "2021-06-01", "2020-01-02", "2020-01-02"]),
                       "direction": 1})
    mem = pd.Series(pd.to_datetime(["2021-01-01", "1990-01-01"]), index=["AAA", "BBB"])
    kept = gf.in_index_only(ev, mem)
    assert list(zip(kept["symbol"], kept["event_date"].dt.year)) == [("AAA", 2021), ("BBB", 2020)]


# ---- fill rate ------------------------------------------------------------------------------

def test_fill_rate_math(tmp_path):
    df = _flat(80)
    # event A: gap down 4% on day 20, no fill on gap day, touches 100 on day 23 (j+3)
    _gap(df, 20, -0.04, close=96.0, high=96.5)
    df.iloc[21:23, df.columns.get_indexer(["open", "high", "low", "close"])] = 97.0
    df.iloc[23, df.columns.get_indexer(["open", "high", "low", "close"])] = [98.0, 100.2, 97.5, 99.0]
    df.iloc[24:40, df.columns.get_indexer(["open", "high", "low", "close"])] = 99.0
    # event B: gap down 5% on day 50, never comes back
    df.iloc[50:, df.columns.get_indexer(["open", "high", "low", "close"])] = 90.0
    _gap(df, 50, -0.05, close=90.0)
    df.iloc[50:, df.columns.get_indexer(["high", "low", "close"])] = 94.0
    df.iloc[51:, df.columns.get_indexer(["open"])] = 94.0
    store = _store(tmp_path, {"AAA": df})
    ev = gf.generate_events(store, symbols=["AAA"])
    assert list(ev["gap_date"]) == [df.index[20], df.index[50]]
    fl = gf.fill_flags(ev, store, (1, 5, 20))
    a, b = fl.iloc[0], fl.iloc[1]
    assert a["fill_1"] == 0 and a["fill_5"] == 1 and a["fill_20"] == 1    # filled on bar j+3
    assert b["fill_1"] == 0 and b["fill_5"] == 0 and b["fill_20"] == 0
    fr = gf.fill_rates(fl, (1, 5, 20))
    assert fr[5] == {"n": 2, "rate": 0.5}
    assert not ev["filled_gap_day"].any()
    store.close()


def test_fill_rate_short_uses_low_and_nan_past_data(tmp_path):
    df = _flat(30)
    _gap(df, 25, 0.03, close=103.0, low=102.0)
    df.iloc[26, df.columns.get_loc("low")] = 99.5          # touches prior close next day
    store = _store(tmp_path, {"AAA": df})
    ev = gf.generate_events(store, symbols=["AAA"], side="up")
    fl = gf.fill_flags(ev, store, (1, 10))
    assert fl["fill_1"].iloc[0] == 1
    assert np.isnan(fl["fill_10"].iloc[0])                 # window runs past the data
    store.close()


# ---- difference vs benchmark ---------------------------------------------------------------

def test_excess_returns_signed_and_spy_modes():
    d = pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-03"])
    trades = pd.DataFrame({"event_date": d, "direction": [1, -1, 1], "ret_1": [0.03, 0.02, -0.01]})
    bench = pd.DataFrame({"ret_1": [0.01, 0.005]}, index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
    ex = cmp.excess_returns(trades, bench, (1,), signed=True)
    assert ex["ex_1"].tolist() == pytest.approx([0.02, 0.03, -0.015])
    ex2 = cmp.excess_returns(trades, bench, (1,), rt_cost=0.002, signed=False)
    assert ex2["ex_1"].tolist() == pytest.approx([0.018, 0.008, -0.017])


def test_per_date_mean_undoes_direction():
    t = pd.DataFrame({"event_date": pd.to_datetime(["2024-01-02"] * 2), "direction": [1, -1],
                      "ret_1": [0.02, -0.04]})
    assert cmp.per_date_mean(t, (1,))["ret_1"].iloc[0] == pytest.approx(0.03)


def test_compare_and_bar_on_synthetic_edge(tmp_path):
    """Edge names jump +1% the day after entry; the baseline name is flat -> diff CI > 0."""
    days = pd.bdate_range("2021-01-01", periods=420)
    frames = {"SPY": _flat(420, start="2021-01-01"), "BBB": _flat(420, start="2021-01-01")}
    ev_rows = []
    for k in range(10):
        df = _flat(420, start="2021-01-01")
        for i in range(30 + k, 400, 25):
            df.iloc[i + 1, df.columns.get_loc("close")] = 101.0
            ev_rows.append((f"E{k}", days[i]))
        frames[f"E{k}"] = df
    store = _store(tmp_path, frames)
    ev = pd.DataFrame(ev_rows, columns=["symbol", "event_date"]).assign(direction=1)
    res = run_event_study(ev, horizons=(1,), cost_bps_per_side=0, store=store, n_boot=200,
                          bear_windows=(("2022-01-03", "2022-12-30"),))
    bev = pd.DataFrame({"symbol": "BBB", "event_date": days, "direction": 1})
    from research.event_study import compute_trades
    bench = cmp.per_date_mean(compute_trades(bev, store, (1,)), (1,))
    spy = cmp.spy_by_date(store, days, (1,))
    vb = cmp.compare(res, bench, "baseline", n_boot=200)
    vs = cmp.compare(res, spy, "spy", n_boot=200)
    allb = cmp.get_row(vb, "all", 1)
    assert allb["n"] == len(ev) and allb["diff"] == pytest.approx(0.01)
    assert allb["ci_low"] > 0
    assert cmp.get_row(vs, "all", 1)["diff"] == pytest.approx(0.01)
    chk = cmp.tightened_bar(res, vb, vs)[1]
    assert chk["beats baseline (CI>0)"] and chk["beats SPY (CI>0)"] and chk["n>=200"] is False
    assert chk["CLEARS BAR"] is False
    store.close()
