"""Event-study harness on synthetic bars: known returns, costs, slices, no look-ahead. Offline."""

import numpy as np
import pandas as pd
import pytest

from research import bars as rb
from research.event_study import bootstrap_mean_ci, run_event_study, variants_log, variants_tried


def _flat_bars(days, px=100.0):
    return pd.DataFrame({"open": px, "high": px, "low": px, "close": px, "volume": 1e6},
                        index=pd.DatetimeIndex(days))


@pytest.fixture()
def store_path(tmp_path):
    return str(tmp_path / "research.db")


def _store_with(store_path, frames):
    conn = rb.connect(store_path)
    for sym, df in frames.items():
        rb._write(conn, sym, df, replace_all=True)
    conn.commit()
    conn.close()
    return rb.BarStore(store_path)


def test_entry_is_next_open_not_event_close(store_path):
    days = pd.bdate_range("2024-01-01", periods=40)
    df = _flat_bars(days)
    e = 10                                    # event day index
    df.iloc[e, df.columns.get_loc("close")] = 50.0      # event-day close: must NOT be the entry
    df.iloc[e + 1, df.columns.get_loc("open")] = 100.0  # entry
    df.iloc[e + 1, df.columns.get_loc("close")] = 102.0  # h=1 exit (t+1 close)
    df.iloc[e + 5, df.columns.get_loc("close")] = 110.0  # h=5 exit (t+5 close)
    store = _store_with(store_path, {"AAA": df})
    ev = pd.DataFrame({"symbol": ["AAA"], "event_date": [days[e]], "direction": [1]})
    r = run_event_study(ev, horizons=(1, 5), cost_bps_per_side=0, store=store, bear_windows=())
    t = r.trades.iloc[0]
    assert t["entry_date"] == days[e + 1]
    assert t["entry_px"] == 100.0
    assert t["ret_1"] == pytest.approx(0.02)
    assert t["ret_5"] == pytest.approx(0.10)
    store.close()


def test_short_direction_and_costs(store_path):
    days = pd.bdate_range("2024-01-01", periods=30)
    df = _flat_bars(days)
    df.iloc[6, df.columns.get_loc("close")] = 95.0      # h=1 from event at 5
    store = _store_with(store_path, {"AAA": df})
    ev = pd.DataFrame({"symbol": ["AAA"], "event_date": [days[5]], "direction": [-1]})
    r = run_event_study(ev, horizons=(1,), cost_bps_per_side=10, stress_mult=2.0, store=store,
                        bear_windows=())
    assert r.get("all", 1)["mean"] == pytest.approx(0.05 - 0.002)
    assert r.get("stress x2 cost", 1)["mean"] == pytest.approx(0.05 - 0.004)
    assert r.get("all", 1)["ev_per_100"] == pytest.approx(4.8)
    store.close()


def test_known_mean_win_rate_and_slices(store_path):
    # 10 symbols, one event each on increasing dates; h=1 gross returns known.
    days = pd.bdate_range("2022-01-03", periods=200)
    gross = [0.01, -0.02, 0.03, 0.01, 0.02, -0.01, 0.04, 0.00, 0.02, 0.05]
    frames, rows = {}, []
    for i, g in enumerate(gross):
        df = _flat_bars(days)
        e = 10 + 15 * i
        df.iloc[e + 1, df.columns.get_loc("close")] = 100.0 * (1 + g)
        frames[f"S{i}"] = df
        rows.append((f"S{i}", days[e], 1))
    store = _store_with(store_path, frames)
    ev = pd.DataFrame(rows, columns=["symbol", "event_date", "direction"])
    bear = (str(days[0].date()), str(days[60].date()))          # covers events 0..3
    r = run_event_study(ev, horizons=(1,), cost_bps_per_side=0, store=store, bear_windows=[bear],
                        holdout_frac=0.4)
    allr = r.get("all", 1)
    assert allr["n"] == 10
    assert allr["mean"] == pytest.approx(np.mean(gross))
    assert allr["win_rate"] == pytest.approx(7 / 10)
    assert allr["ci_low"] <= allr["mean"] <= allr["ci_high"]
    assert r.get("holdout", 1)["n"] == 4
    assert r.get("holdout", 1)["mean"] == pytest.approx(np.mean(gross[6:]))
    assert r.get("train", 1)["n"] == 6
    bear_row = next(x for x in r.rows if x["slice"].startswith("bear"))
    assert bear_row["n"] == 4 and bear_row["mean"] == pytest.approx(np.mean(gross[:4]))
    # one event per date -> date-clustered bootstrap == iid bootstrap with the same seed
    lo, hi = bootstrap_mean_ci(np.array(gross), None, n_boot=1000, seed=7)
    assert (allr["ci_low"], allr["ci_high"]) == pytest.approx((lo, hi))
    store.close()


def test_bootstrap_matches_manual_and_degenerate():
    v = np.array([0.01, 0.02, 0.03, -0.01, 0.05])
    lo, hi = bootstrap_mean_ci(v, n_boot=1000, seed=3)
    rng = np.random.default_rng(3)
    means = v[rng.integers(0, 5, size=(1000, 5))].mean(axis=1)
    assert (lo, hi) == pytest.approx(tuple(np.quantile(means, [0.025, 0.975])))
    assert bootstrap_mean_ci(np.full(50, 0.01)) == pytest.approx((0.01, 0.01))


def test_cluster_bootstrap_is_wider_for_correlated_dates():
    rng = np.random.default_rng(0)
    date_effect = rng.normal(0, 0.03, 40)
    vals = np.repeat(date_effect, 25) + rng.normal(0, 0.001, 1000)
    groups = np.repeat(np.arange(40), 25)
    lo_i, hi_i = bootstrap_mean_ci(vals, None)
    lo_c, hi_c = bootstrap_mean_ci(vals, groups)
    assert (hi_c - lo_c) > 3 * (hi_i - lo_i)


def test_events_past_data_end_are_dropped(store_path):
    days = pd.bdate_range("2024-01-01", periods=12)
    store = _store_with(store_path, {"AAA": _flat_bars(days)})
    ev = pd.DataFrame({"symbol": ["AAA", "AAA"], "event_date": [days[2], days[-1]], "direction": [1, 1]})
    r = run_event_study(ev, horizons=(1, 20), cost_bps_per_side=0, store=store, bear_windows=())
    assert r.n_events_used == 1                    # last-day event has no next open
    assert r.get("all", 1)["n"] == 1 and r.get("all", 20)["n"] == 0
    assert "all" in r.summary() and r.to_dict()["n_events_used"] == 1
    store.close()


def test_variants_log_counts_distinct_params(store_path):
    days = pd.bdate_range("2024-01-01", periods=12)
    store = _store_with(store_path, {"AAA": _flat_bars(days)})
    ev = pd.DataFrame({"symbol": ["AAA"], "event_date": [days[2]], "direction": [1]})
    r = run_event_study(ev, horizons=(1,), store=store, bear_windows=())
    store.close()
    assert variants_log("edge_x", {"a": 1}, r, db_path=store_path) == 1
    assert variants_log("edge_x", {"a": 1}, r, db_path=store_path) == 1
    assert variants_log("edge_x", {"a": 2}, r, db_path=store_path) == 2
    assert variants_tried("edge_y", db_path=store_path) == 0
