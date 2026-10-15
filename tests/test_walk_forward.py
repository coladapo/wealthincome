"""Walk-forward windows, stitching, fit hook, excess-return stats. Offline."""

import numpy as np
import pandas as pd
import pytest

from research.portfolio_sim import simulate
from research.strategy import Strategy
from research.walk_forward import excess_from_equity, excess_stats, make_windows, walk_forward
from research_synth import random_market


class Hold(Strategy):
    """Hold AAA and BBB (test helper)."""
    id = "wf_hold"
    default_params = {"w": 0.08}

    def target_weights(self, as_of, data):
        return {"AAA": self.params["w"], "BBB": self.params["w"]}


class Fitting(Hold):
    """Records each fit call; picks w from the train window length (test helper)."""
    id = "wf_fitting"
    calls = []

    def fit(self, train):
        Fitting.calls.append(train.as_of)
        assert train.bars("SPY").index.max() <= pd.Timestamp(train.as_of)
        return {"w": 0.04 if len(Fitting.calls) % 2 else 0.08}


def test_windows_default_layout():
    w = make_windows("2019-01-01", "2021-12-31")
    assert [x.as_dict()["test_start"] for x in w] == ["2021-01-01", "2021-07-01"]
    assert w[0].as_dict() == {"train_start": "2019-01-01", "train_end": "2020-12-31",
                              "test_start": "2021-01-01", "test_end": "2021-06-30"}
    assert w[1].as_dict()["train_start"] == "2019-07-01"
    assert w[-1].as_dict()["test_end"] == "2021-12-31"
    with pytest.raises(ValueError):
        make_windows("2019-01-01", "2021-12-31", step_months=3)


def test_stitched_equals_one_continuous_run():
    m = random_market("2019-01-01", "2021-12-31")
    res = walk_forward(Hold(), m, start="2019-01-01", cost_mults=(1.0, 2.0), n_boot=200)
    d = res.base.daily
    assert d.index.is_unique and d.index.is_monotonic_increasing
    assert d.index[0] >= pd.Timestamp("2021-01-01")
    assert set(d["window"]) == {0, 1}
    cont = simulate(Hold(), d.index[0], d.index[-1], m)
    np.testing.assert_allclose(d["equity"].values, cont.daily["equity"].values)
    assert res.runs[2.0].summary["total_costs"] > res.runs[1.0].summary["total_costs"]
    assert res.vs_spy[1.0]["n_days"] == len(d) - 1


def test_fit_hook_called_per_window_with_train_data():
    Fitting.calls = []
    m = random_market("2019-01-01", "2021-12-31")
    res = walk_forward(Fitting(), m, start="2019-01-01", cost_mults=(1.0,), n_boot=200)
    assert [str(c) for c in Fitting.calls] == ["2020-12-31", "2021-06-30"]
    t = res.base.trades
    w0 = t[(t["window"] == 0) & (t["side"] == "buy")]["notional"].iloc[0]
    assert w0 == pytest.approx(0.04 * 100_000, rel=0.01)     # first window used fitted w=0.04
    assert (t[t["window"] == 1]["side"] == "buy").any()       # resized up to 0.08 in window 2


def test_excess_from_equity_compares_same_intervals_across_gaps():
    idx = pd.bdate_range("2024-01-01", periods=10)
    strat = pd.Series(100 * 1.01 ** np.arange(10), idx)        # +1% every day
    bench = strat.drop(idx[[3, 4, 7]])                          # same curve, days missing
    e = excess_from_equity(strat, bench, n_boot=200)
    assert e["n_days"] == 6
    assert e["mean_daily"] == pytest.approx(0.0, abs=1e-12)     # multi-day vs multi-day


def test_excess_stats_signs():
    idx = pd.bdate_range("2024-01-01", periods=120)
    rng = np.random.default_rng(0)
    b = pd.Series(rng.normal(0, 0.01, len(idx)), idx)
    s = b + 0.001
    e = excess_stats(s, b, n_boot=300)
    assert e["mean_daily"] == pytest.approx(0.001)
    assert e["ci_low"] == pytest.approx(0.001) and e["ann_excess"] == pytest.approx(0.252)
