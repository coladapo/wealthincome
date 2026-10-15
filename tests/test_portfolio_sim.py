"""Portfolio simulator: next-open execution, chassis caps, costs, trailing stop, loss limit. Offline."""

import numpy as np
import pandas as pd
import pytest

from research.portfolio_sim import Chassis, simulate
from research.strategy import Strategy
from research_synth import frame, flat_market

DATES = pd.bdate_range("2024-01-01", periods=12)


class Fixed(Strategy):
    """Constant target book (test helper)."""
    id = "fixed"
    default_params = {"book": {}}

    def target_weights(self, as_of, data):
        return dict(self.params["book"])


class FromDay(Strategy):
    """Book switches on a given date (test helper)."""
    id = "from_day"
    default_params = {"day": "2024-01-03", "before": {}, "after": {}}

    def target_weights(self, as_of, data):
        return dict(self.params["after"] if pd.Timestamp(as_of) >= pd.Timestamp(self.params["day"])
                    else self.params["before"])


class FixedNoReenter(Fixed):
    """Constant book, no re-entry after a stop (test helper)."""
    id = "fixed_no_reenter"
    reenter_after_stop = False


class FixedTarget(Fixed):
    """Constant book, rebalanced to target (test helper)."""
    id = "fixed_target"
    rebalance = "target"


def test_signal_at_close_trades_at_next_open():
    opens = np.arange(100, 112, dtype=float) + 0.5
    closes = np.arange(100, 112, dtype=float)
    m = flat_market(DATES, overrides={"AAA": frame(DATES, closes, opens)})
    res = simulate(Fixed({"book": {"AAA": 0.08}}), DATES[0], DATES[-1], m)
    first = res.trades.iloc[0]
    assert first["date"] == DATES[1].strftime("%Y-%m-%d")     # computed at day-0 close
    assert first["price"] == pytest.approx(opens[1])           # filled at day-1 OPEN
    assert first["side"] == "buy"
    assert res.daily["n_positions"].iloc[0] == 0               # nothing held on day 0
    assert len(res.trades) == 1                                # drift mode: never re-traded


def test_caps_enforced_and_violations_counted():
    names = [f"S{i:02d}" for i in range(10)]
    m = flat_market(DATES, symbols=tuple(names))
    book = {s: 0.2 for s in names}
    book["SHORTME"] = -0.05
    res = simulate(Fixed({"book": book}), DATES[0], DATES[-1], m)
    assert res.daily["n_positions"].max() == 8
    buys = res.trades[res.trades["side"] == "buy"]
    assert (buys["notional"] <= 0.08 * 100_000 + 1e-6).all()
    assert res.daily["exposure"].max() <= 0.641          # 8 x 8%, a hair over after costs shrink equity
    assert res.violations["over_cap_clipped"] > 0
    assert res.violations["too_many_names"] > 0
    assert res.violations["short_requested"] > 0
    assert (res.daily["cash"] >= -1e-9).all()


def test_costs_applied_per_side_and_stress_doubles_them():
    strat = FromDay({"day": "2024-01-05", "before": {"AAA": 0.08}, "after": {}})
    m = flat_market(DATES)
    base = simulate(strat, DATES[0], DATES[-1], m)
    stress = simulate(strat, DATES[0], DATES[-1], m, cost_mult=2.0)
    assert list(base.trades["side"]) == ["buy", "sell"]
    expect = 8000 * 0.001 + 8000 * 0.001                       # 10 bp each side on $8k, flat price
    assert base.daily["costs"].sum() == pytest.approx(expect, rel=1e-6)
    assert base.daily["equity"].iloc[-1] == pytest.approx(100_000 - expect, rel=1e-9)
    assert stress.daily["costs"].sum() == pytest.approx(2 * expect, rel=1e-6)


def test_trailing_stop_fires_on_close_and_reentry_rules():
    closes = [100, 100, 110, 120, 115, 105, 104, 104, 104, 104, 104, 104]
    m = flat_market(DATES, overrides={"AAA": frame(DATES, closes)})
    res = simulate(Fixed({"book": {"AAA": 0.08}}), DATES[0], DATES[-1], m)
    stops = res.trades[res.trades["reason"] == "trailing_stop"]
    assert len(stops) == 1
    assert stops.iloc[0]["date"] == DATES[5].strftime("%Y-%m-%d")   # 105 <= 120 * 0.88 = 105.6
    assert stops.iloc[0]["price"] == 105
    rebuys = res.trades[(res.trades["side"] == "buy") & (res.trades["date"] > stops.iloc[0]["date"])]
    assert len(rebuys) == 1 and rebuys.iloc[0]["date"] == DATES[6].strftime("%Y-%m-%d")
    res2 = simulate(FixedNoReenter({"book": {"AAA": 0.08}}), DATES[0], DATES[-1], m)
    assert (res2.trades["side"] == "buy").sum() == 1                # stopped out, never re-bought


def test_daily_loss_limit_blocks_buys_but_allows_exits():
    big = Chassis(max_weight=1.0)
    opens = [100] * 12
    opens[4] = 90                                                # -10% gap at the open of day 4
    closes = [100] * 12
    closes[4] = 90
    m = flat_market(DATES, overrides={"AAA": frame(DATES, [c for c in closes], opens)})
    strat = FromDay({"day": "2024-01-04", "before": {"AAA": 0.9}, "after": {"BBB": 0.5}})
    res = simulate(strat, DATES[0], DATES[-1], m, chassis=big)
    day4 = DATES[4].strftime("%Y-%m-%d")
    t4 = res.trades[res.trades["date"] == day4]
    assert bool(res.daily.loc[DATES[4], "loss_limit_hit"])
    assert list(t4["side"]) == ["sell"]                           # AAA exit allowed, BBB buy blocked
    assert res.violations["loss_limit_blocked_buy"] == 1
    assert not (res.trades["symbol"] == "BBB").any()              # rejection is final in drift mode


def test_drift_lets_winners_run_target_mode_trims():
    closes = list(np.linspace(100, 160, 12))
    m = flat_market(DATES, overrides={"AAA": frame(DATES, closes)}, symbols=("AAA",))
    drift = simulate(Fixed({"book": {"AAA": 0.08}}), DATES[0], DATES[-1], m)
    tgt = simulate(FixedTarget({"book": {"AAA": 0.08}}), DATES[0], DATES[-1], m)
    assert len(drift.trades) == 1
    assert (tgt.trades["reason"] == "target_reduce").any()


def test_deterministic_and_state_continues():
    m = flat_market(DATES)
    strat = Fixed({"book": {"AAA": 0.08, "BBB": 0.05}})
    a = simulate(strat, DATES[0], DATES[-1], m)
    b = simulate(strat, DATES[0], DATES[-1], m)
    pd.testing.assert_frame_equal(a.daily, b.daily)
    pd.testing.assert_frame_equal(a.trades, b.trades)
    first = simulate(strat, DATES[0], DATES[5], m)
    rest = simulate(strat, DATES[6], DATES[-1], m, state=first.state)
    joined = pd.concat([first.daily, rest.daily])
    np.testing.assert_allclose(joined["equity"].values, a.daily["equity"].values)
