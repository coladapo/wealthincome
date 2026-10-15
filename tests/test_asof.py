"""As-of data: completed sessions only, earnings by report time, no look-ahead. Offline."""

from datetime import date, datetime

import pandas as pd
import pytest

from research.asof import ET, AsOfData, LookAheadError, MarketData
from research_synth import frame, flat_market

DATES = pd.bdate_range("2024-03-04", "2024-03-15")   # Mon 4th .. Fri 15th


def _market(earnings=None):
    m = flat_market(DATES, symbols=("AAA",), earnings=earnings)
    late = frame(DATES[5:], [50.0] * len(DATES[5:]))      # listed from Mon 11th
    m._frames["NEW"] = MarketData._norm(late)
    m._universe = ["AAA", "NEW"]
    return m


def test_historical_as_of_includes_its_own_completed_bar():
    d = AsOfData(date(2024, 3, 8), _market())
    assert d.session_complete
    assert d.bars("AAA").index[-1] == pd.Timestamp("2024-03-08")
    assert len(d.bars("AAA", lookback=3)) == 3


def test_asking_for_a_later_date_raises():
    d = AsOfData(date(2024, 3, 8), _market())
    with pytest.raises(LookAheadError):
        d.bars("AAA", until=date(2024, 3, 11))
    with pytest.raises(LookAheadError):
        d.at(date(2024, 3, 9))
    with pytest.raises(LookAheadError):
        d.earnings("AAA", until="2024-03-12")
    # the same or an earlier date is fine
    assert d.bars("AAA", until=date(2024, 3, 6)).index[-1] == pd.Timestamp("2024-03-06")
    assert d.at(date(2024, 3, 5)).bars("AAA").index[-1] == pd.Timestamp("2024-03-05")


def test_no_future_rows_ever_visible():
    m = _market()
    for day in DATES:
        d = AsOfData(day.date(), m)
        assert d.bars("AAA").index.max() <= day
        assert d.spy().index.max() <= day
        assert d.trading_days().max() <= day


def test_incomplete_session_bar_is_excluded():
    m = _market()
    intraday = datetime(2024, 3, 8, 12, 0, tzinfo=ET)
    d = AsOfData(date(2024, 3, 8), m, now=intraday)
    assert not d.session_complete
    assert d.bars("AAA").index[-1] == pd.Timestamp("2024-03-07")
    after = AsOfData(date(2024, 3, 8), m, now=datetime(2024, 3, 8, 16, 5, tzinfo=ET))
    assert after.bars("AAA").index[-1] == pd.Timestamp("2024-03-08")
    preopen = AsOfData(date(2024, 3, 8), m, after_close=False)
    assert preopen.bars("AAA").index[-1] == pd.Timestamp("2024-03-07")


def test_earnings_visible_only_after_report_time():
    e = pd.DataFrame([
        {"symbol": "AAA", "report_date": "2024-03-06", "timing": "amc", "eps_estimate": 1,
         "eps_actual": 1.2, "surprise_pct": 20.0},
        {"symbol": "AAA", "report_date": "2024-03-07", "timing": "bmo", "eps_estimate": 1,
         "eps_actual": 0.9, "surprise_pct": -10.0},
        {"symbol": "AAA", "report_date": "2024-03-08", "timing": "unknown", "eps_estimate": 1,
         "eps_actual": 1.0, "surprise_pct": 0.0},
    ])
    m = _market(earnings=e)
    on6 = AsOfData(date(2024, 3, 6), m).earnings("AAA")
    assert list(on6["report_date"]) == []                       # amc on the 6th: not yet
    on7 = AsOfData(date(2024, 3, 7), m).earnings("AAA")
    assert list(on7["report_date"]) == ["2024-03-06", "2024-03-07"]   # bmo same day is known
    on8 = AsOfData(date(2024, 3, 8), m).earnings("AAA")
    assert "2024-03-08" not in list(on8["report_date"])         # unknown treated as after close
    assert "2024-03-08" in list(AsOfData(date(2024, 3, 11), m).earnings("AAA")["report_date"])
    pre = AsOfData(date(2024, 3, 7), m, after_close=False).earnings("AAA")
    assert list(pre["report_date"]) == ["2024-03-06", "2024-03-07"]   # bmo 09:00 < 09:30 open


def test_universe_only_lists_names_with_completed_bars():
    m = _market()
    assert AsOfData(date(2024, 3, 8), m).universe() == ["AAA"]
    assert AsOfData(date(2024, 3, 11), m).universe() == ["AAA", "NEW"]


def test_slot_alias_reads_the_base_symbol():
    d = AsOfData(date(2024, 3, 8), _market())
    assert d.bars("SPY#3").equals(d.bars("SPY"))
