"""Treasury sweep + trend-filtered core: as-of correctness of targets, chassis_override in the sim. Offline."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from research.asof import AsOfData, MarketData
from research.portfolio_sim import simulate
from research.strategies.treasury_sweep import TreasurySweep
from research.strategies.trend_filtered_core import TrendFilteredCore
from research_synth import frame

DATES = pd.bdate_range("2024-01-01", "2024-04-30")


def market(spy_closes, dates=DATES, bil=True, sgov=True, bil_dates=None):
    frames = {"SPY": frame(dates, spy_closes)}
    if bil:
        bd = dates if bil_dates is None else bil_dates
        frames["BIL"] = frame(bd, 91.0 * (1 + 0.0002) ** np.arange(len(bd)))
    if sgov:
        frames["SGOV"] = frame(dates, 100.0 * (1 + 0.0002) ** np.arange(len(dates)))
    return MarketData(frames=frames, universe=[])


def tw(strat, m, d):
    return strat.target_weights(d, AsOfData(d, m))


# --------------------------------------------------------------------------- treasury sweep

def test_treasury_holds_bil_with_the_whole_sleeve():
    m = market(np.full(len(DATES), 100.0))
    assert tw(TreasurySweep(), m, date(2024, 2, 1)) == {"BIL": 1.0}


def test_treasury_falls_back_to_sgov_when_bil_has_no_bar_on_the_latest_session():
    m = market(np.full(len(DATES), 100.0), bil_dates=DATES[DATES <= "2024-02-15"])
    assert tw(TreasurySweep(), m, date(2024, 2, 15)) == {"BIL": 1.0}
    assert tw(TreasurySweep(), m, date(2024, 2, 16)) == {"SGOV": 1.0}     # stale BIL is not used
    m2 = market(np.full(len(DATES), 100.0), bil=False, sgov=False)
    assert tw(TreasurySweep(), m2, date(2024, 2, 16)) == {}


def test_treasury_sim_is_one_full_position_without_stops():
    m = market(np.full(len(DATES), 100.0))
    res = simulate(TreasurySweep(), DATES[0], DATES[-1], m)
    d = res.daily
    assert res.chassis.max_weight == 1.0 and res.chassis.trailing_stop is None
    assert d["n_positions"].iloc[-1] == 1
    assert d["exposure"].iloc[-1] > 0.99                    # not clipped to 8%
    assert d["equity"].iloc[-1] > d["equity"].iloc[1]       # earns the (synthetic) T-bill drift
    assert len(res.trades) == 1 and "over_cap_clipped" not in res.violations


# --------------------------------------------------------------------------- trend-filtered core

def _cross_series():
    """SPY rises to Feb 9, falls hard from Feb 12 to Mar 8, recovers from Mar 11."""
    c = []
    for d in DATES:
        if d <= pd.Timestamp("2024-02-09"):
            c.append(100 + 0.5 * len(c))
        elif d <= pd.Timestamp("2024-03-08"):
            c.append(c[-1] - 2.0)
        else:
            c.append(c[-1] + 2.5)
    return np.array(c)


def test_core_uses_the_first_session_of_the_month_and_holds_the_signal():
    m = market(_cross_series())
    s = TrendFilteredCore({"sma_days": 5})
    assert tw(s, m, date(2024, 2, 1)) == {"SPY": 1.0}         # Feb 1 check: above SMA
    # mid-February SPY is far below its 5-day SMA, but the monthly re-check holds the signal
    assert tw(s, m, date(2024, 2, 28)) == {"SPY": 1.0}
    assert tw(s, m, date(2024, 3, 1)) == {"BIL": 1.0}         # Mar 1 check: below -> T-bills
    assert tw(s, m, date(2024, 3, 28)) == {"BIL": 1.0}        # recovered mid-March: still out
    assert tw(s, m, date(2024, 4, 1)) == {"SPY": 1.0}         # Apr 1 check: back in


def test_core_weekly_recheck_reacts_within_the_month():
    m = market(_cross_series())
    s = TrendFilteredCore({"sma_days": 5, "recheck": "weekly"})
    assert tw(s, m, date(2024, 2, 9)) == {"SPY": 1.0}         # week of Feb 5: checked Mon Feb 5
    assert tw(s, m, date(2024, 2, 23)) == {"BIL": 1.0}        # Mon Feb 19 check: below
    assert tw(s, m, date(2024, 3, 22)) == {"SPY": 1.0}        # Mon Mar 18 check: above again


def test_core_signal_is_as_of_no_look_ahead():
    """Targets at d are identical whether or not the future after d exists or differs."""
    base = _cross_series()
    s = TrendFilteredCore({"sma_days": 5, "recheck": "weekly"})
    full = market(base)
    for cut in ("2024-02-05", "2024-02-20", "2024-03-01", "2024-03-18"):
        k = int((DATES <= cut).sum())
        alt = base.copy()
        alt[k:] = alt[k:] * 0.5 + 300 * (np.arange(len(alt) - k) % 2)   # wild, different future
        truncated = market(base[:k], dates=DATES[:k])
        for m in (market(alt), truncated):
            assert tw(s, m, pd.Timestamp(cut).date()) == tw(s, full, pd.Timestamp(cut).date())


def test_core_sma_uses_closes_up_to_the_check_day_only():
    """Flip the check day's own close across the SMA: the target flips; flip the next day: no change."""
    c = np.full(len(DATES), 100.0)
    m_up = market(np.where(DATES == pd.Timestamp("2024-03-01"), 101.0, c))
    m_dn = market(np.where(DATES == pd.Timestamp("2024-03-01"), 99.0, c))
    m_next = market(np.where(DATES == pd.Timestamp("2024-03-04"), 150.0, c))
    s = TrendFilteredCore({"sma_days": 20})
    assert tw(s, m_up, date(2024, 3, 1)) == {"SPY": 1.0}
    assert tw(s, m_dn, date(2024, 3, 1)) == {"BIL": 1.0}
    assert tw(s, m_next, date(2024, 3, 4)) == {"BIL": 1.0}   # flat == not above; Mar 4 is not a check day


def test_core_without_enough_history_is_risk_off():
    m = market(_cross_series())
    assert tw(TrendFilteredCore({"sma_days": 200}), m, date(2024, 4, 1)) == {"BIL": 1.0}


def test_core_params_validated():
    with pytest.raises(ValueError):
        TrendFilteredCore({"recheck": "daily"})
    with pytest.raises(ValueError):
        TrendFilteredCore({"sma_days": 1})


def test_core_sim_switches_at_the_next_open_with_one_full_position():
    m = market(_cross_series())
    res = simulate(TrendFilteredCore({"sma_days": 5}), DATES[0], DATES[-1], m)
    t = res.trades
    assert res.chassis.max_positions == 1 and res.chassis.max_weight == 1.0
    # Mar 1 (Fri) close says risk-off -> sell SPY / buy BIL at Mon Mar 4 open
    mar4 = t[t["date"] == "2024-03-04"]
    assert set(zip(mar4["symbol"], mar4["side"])) == {("SPY", "sell"), ("BIL", "buy")}
    apr2 = t[t["date"] == "2024-04-02"]
    assert set(zip(apr2["symbol"], apr2["side"])) == {("BIL", "sell"), ("SPY", "buy")}
    assert res.daily["n_positions"].max() == 1
    assert res.daily["exposure"].iloc[-1] > 0.99
    assert "max_positions_blocked_entry" not in res.violations     # sells settle before buys
