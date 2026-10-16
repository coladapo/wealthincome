"""core_weekly_200: weekly re-check timing, no look-ahead on the 200-day SMA, BIL fallback. Offline."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from research import strategy as registry
from research.asof import AsOfData, MarketData
from research.portfolio_sim import simulate
from research.strategies.core_weekly_200 import CoreWeekly200
from research_synth import frame

# Presidents' Day 2024-02-19 (Mon) is not a session: that ISO week's check day is Tue 2024-02-20
DATES = pd.bdate_range("2023-01-02", "2024-06-28").drop(pd.Timestamp("2024-02-19"))


def market(spy_closes, dates=DATES, bil=True, sgov=True, bil_dates=None):
    frames = {"SPY": frame(dates, spy_closes)}
    if bil:
        bd = dates if bil_dates is None else bil_dates
        frames["BIL"] = frame(bd, 91.0 * (1 + 0.0002) ** np.arange(len(bd)))
    if sgov:
        frames["SGOV"] = frame(dates, 100.0 * (1 + 0.0002) ** np.arange(len(dates)))
    return MarketData(frames=frames, universe=[])


def tw(m, d, s=None):
    return (s or CoreWeekly200()).target_weights(d, AsOfData(d, m))


def flat_with(overrides):
    c = np.full(len(DATES), 100.0)
    for d, v in overrides.items():
        c[DATES.get_loc(pd.Timestamp(d))] = v
    return c


def _cross_series():
    """Up to 2024-02-09, then -4/day to 2024-03-08, then +8/day."""
    c = []
    for d in DATES:
        if d <= pd.Timestamp("2024-02-09"):
            c.append(100 + 0.1 * len(c))
        elif d <= pd.Timestamp("2024-03-08"):
            c.append(c[-1] - 4.0)
        else:
            c.append(c[-1] + 8.0)
    return np.array(c)


# --------------------------------------------------------------------------- re-check timing

def test_check_day_is_the_first_session_of_each_iso_week():
    m = market(np.full(len(DATES), 100.0))
    for d, want in ((date(2024, 3, 4), "2024-03-04"), (date(2024, 3, 8), "2024-03-04"),
                    (date(2024, 3, 11), "2024-03-11"),
                    (date(2024, 2, 23), "2024-02-20")):      # holiday Monday -> Tuesday
        assert CoreWeekly200.last_check_day(AsOfData(d, m)) == pd.Timestamp(want)


def test_signal_is_frozen_between_weekly_checks():
    # check day Mon Mar 4 is above the SMA; Tue..Fri collapse far below it -> still SPY until Mon Mar 11
    m = market(flat_with({"2024-03-04": 101.0, "2024-03-05": 60.0, "2024-03-06": 60.0,
                          "2024-03-07": 60.0, "2024-03-08": 60.0, "2024-03-11": 60.0}))
    for d in (date(2024, 3, 4), date(2024, 3, 6), date(2024, 3, 8)):
        assert tw(m, d) == {"SPY": 1.0}
    assert tw(m, date(2024, 3, 11)) == {"BIL": 1.0}         # next week's check reacts


def test_weekly_check_reacts_within_the_month():
    m = market(_cross_series())
    assert tw(m, date(2024, 2, 9)) == {"SPY": 1.0}
    assert tw(m, date(2024, 2, 23)) == {"BIL": 1.0}         # Tue Feb 20 check: below the SMA
    assert tw(m, date(2024, 3, 28)) == {"SPY": 1.0}         # Mon Mar 25 check: above again


def test_holiday_week_uses_tuesday_close():
    up = market(flat_with({"2024-02-20": 101.0}))
    dn = market(flat_with({"2024-02-20": 99.0, "2024-02-21": 150.0}))
    assert tw(up, date(2024, 2, 23)) == {"SPY": 1.0}
    assert tw(dn, date(2024, 2, 23)) == {"BIL": 1.0}        # Wed's spike is not a check day


# --------------------------------------------------------------------------- no look-ahead

def test_sma_uses_closes_up_to_the_check_day_only():
    c = flat_with({})
    assert tw(market(flat_with({"2024-03-04": 101.0})), date(2024, 3, 4)) == {"SPY": 1.0}
    assert tw(market(flat_with({"2024-03-04": 99.0})), date(2024, 3, 4)) == {"BIL": 1.0}
    # a later close never feeds the check: Tue's spike leaves Mon's (flat == not above) decision
    assert tw(market(flat_with({"2024-03-05": 150.0})), date(2024, 3, 6)) == {"BIL": 1.0}
    # the window is exactly 200 closes: a crash 201 sessions back does not count, 200 back does
    k = DATES.get_loc(pd.Timestamp("2024-03-04"))
    out = c.copy()
    out[k - 200] = 1.0                  # outside the window
    out[k] = 100.0001
    assert tw(market(out), date(2024, 3, 4)) == {"SPY": 1.0}
    inside = c.copy()
    inside[k - 199] = 1e5               # inside the window: SMA far above the close
    inside[k] = 100.0001
    assert tw(market(inside), date(2024, 3, 4)) == {"BIL": 1.0}


def test_targets_do_not_depend_on_the_future():
    base = _cross_series()
    full = market(base)
    for cut in ("2024-02-05", "2024-02-20", "2024-02-22", "2024-03-01", "2024-03-18", "2024-03-27"):
        k = int((DATES <= cut).sum())
        alt = base.copy()
        alt[k:] = alt[k:] * 0.5 + 300 * (np.arange(len(alt) - k) % 2)   # wild, different future
        truncated = market(base[:k], dates=DATES[:k])
        d = pd.Timestamp(cut).date()
        for m in (market(alt), truncated):
            assert tw(m, d) == tw(full, d)


def test_fewer_than_200_closes_is_risk_off():
    m = market(np.linspace(50, 200, len(DATES)))                 # steady uptrend: above any SMA
    n_closes = {d: DATES.get_loc(CoreWeekly200.last_check_day(AsOfData(d.date(), m))) + 1
                for d in DATES[150:260]}
    short = [d for d, n in n_closes.items() if n < 200]
    enough = [d for d, n in n_closes.items() if n >= 200]
    assert short and enough
    assert all(tw(m, d.date()) == {"BIL": 1.0} for d in short)  # no signal yet
    assert all(tw(m, d.date()) == {"SPY": 1.0} for d in enough)


# --------------------------------------------------------------------------- BIL fallback

def test_risk_off_holds_bil_then_sgov_then_nothing():
    c = flat_with({})
    assert tw(market(c), date(2024, 3, 6)) == {"BIL": 1.0}
    stale = market(c, bil_dates=DATES[DATES <= "2024-03-05"])
    assert tw(stale, date(2024, 3, 5)) == {"BIL": 1.0}
    assert tw(stale, date(2024, 3, 6)) == {"SGOV": 1.0}         # stale BIL is not used
    assert tw(market(c, bil=False, sgov=False), date(2024, 3, 6)) == {}


# --------------------------------------------------------------------------- frozen identity + sim

def test_frozen_params_and_sleeve():
    s = CoreWeekly200()
    assert s.horizon == "long" and not s.reference_only
    assert s.chassis.max_positions == 1 and s.chassis.max_weight == 1.0
    assert s.chassis.trailing_stop is None and s.chassis.cost_bps == 10.0
    with pytest.raises(ValueError):
        CoreWeekly200({"sma_days": 150})                        # no rule knobs
    with pytest.raises(ValueError):
        CoreWeekly200({"extra_slippage_bps": -1})
    hi = CoreWeekly200({"extra_slippage_bps": 10})
    assert hi.chassis.cost_bps == 20.0 and not hi.reference_only
    assert CoreWeekly200.chassis_override.cost_bps == 10.0      # class policy untouched
    assert hi.version != s.version


def test_registers_as_long_candidate(tmp_path):
    db = str(tmp_path / "r.db")
    row = registry.register(CoreWeekly200(), hypothesis_id="core_weekly_200", db_path=db)
    assert row["horizon"] == "long" and row["status"] == "candidate" and not row["reference_only"]


def test_sim_switches_at_the_next_open_with_one_full_position():
    m = market(_cross_series())
    start = DATES[DATES >= "2024-01-02"][0]
    lo = simulate(CoreWeekly200(), start, DATES[-1], m)
    hi = simulate(CoreWeekly200({"extra_slippage_bps": 10}), start, DATES[-1], m)
    t = lo.trades
    # Tue Feb 20 check is below -> the Feb 20 close queues BIL -> traded at the Feb 21 open
    feb21 = t[t["date"] == "2024-02-21"]
    assert set(zip(feb21["symbol"], feb21["side"])) == {("SPY", "sell"), ("BIL", "buy")}
    assert t[t["date"] == "2024-02-20"].empty
    assert lo.daily["n_positions"].max() == 1
    assert lo.daily["exposure"].iloc[-1] > 0.99
    assert list(hi.trades["date"]) == list(t["date"])           # same decisions, higher cost
    assert hi.trades["cost"].sum() > lo.trades["cost"].sum() * 1.9
