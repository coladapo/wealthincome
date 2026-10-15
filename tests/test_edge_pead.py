"""PEAD event mapping, direction/threshold logic, earnings normalisation, diff CI. Offline."""

import numpy as np
import pandas as pd
import pytest

from research import earnings as re_
from research.edges import pead

# Mon 2024-01-08 .. Fri 2024-01-19 (2024-01-15 MLK holiday removed)
TDAYS = pd.DatetimeIndex([d for d in pd.bdate_range("2024-01-08", "2024-01-19") if d != pd.Timestamp("2024-01-15")])


@pytest.mark.parametrize("report,timing,expected", [
    ("2024-01-09", "bmo", "2024-01-09"),       # before open on a session -> same day
    ("2024-01-09", "amc", "2024-01-10"),       # after close -> next session
    ("2024-01-09", "unknown", "2024-01-10"),   # unknown -> next session (conservative)
    ("2024-01-12", "amc", "2024-01-16"),       # Friday amc, Monday holiday -> Tuesday
    ("2024-01-13", "bmo", "2024-01-16"),       # Saturday "bmo" (e.g. BRK) -> next session
    ("2024-01-15", "bmo", "2024-01-16"),       # holiday bmo -> next session
])
def test_reaction_day_mapping(report, timing, expected):
    assert pead.reaction_day(report, timing, TDAYS) == pd.Timestamp(expected)
    vec = pead.map_reaction_days(pd.DataFrame({"report_date": [report], "timing": [timing]}), TDAYS)
    assert vec.iloc[0] == pd.Timestamp(expected)


def test_reaction_day_past_end_is_none():
    assert pead.reaction_day("2024-01-19", "amc", TDAYS) is None
    vec = pead.map_reaction_days(pd.DataFrame({"report_date": ["2024-01-19"], "timing": ["amc"]}), TDAYS)
    assert pd.isna(vec.iloc[0])


def test_reports_before_the_calendar_are_dropped_not_collapsed_onto_day_one():
    e = pd.DataFrame({"report_date": ["2018-10-02", "2024-01-05", "2024-01-09"],
                      "timing": ["bmo", "amc", "amc"]})
    out = pead.attach_reaction(e, TDAYS)
    assert out["report_date"].tolist() == ["2024-01-05", "2024-01-09"]
    assert out["reaction_date"].tolist() == [pd.Timestamp("2024-01-08"), pd.Timestamp("2024-01-10")]
    # the first calendar day has no previous session inside the calendar
    assert pd.isna(out["prev_session"].iloc[0])
    assert out["prev_session"].iloc[1] == pd.Timestamp("2024-01-09")


def test_classify_timing_from_clock_time():
    tz = "America/New_York"
    assert re_.classify_timing(pd.Timestamp("2024-01-09 07:00", tz=tz)) == "bmo"
    assert re_.classify_timing(pd.Timestamp("2024-01-09 16:00", tz=tz)) == "amc"
    assert re_.classify_timing(pd.Timestamp("2024-01-09 16:05", tz=tz)) == "amc"
    assert re_.classify_timing(pd.Timestamp("2024-01-09 12:00", tz=tz)) == "unknown"
    # UTC input converted to Eastern: 21:00 UTC = 16:00 EST
    assert re_.classify_timing(pd.Timestamp("2024-01-09 21:00", tz="UTC")) == "amc"


def test_normalize_drops_unreported_and_maps_columns():
    idx = pd.DatetimeIndex(["2026-11-02 16:00", "2026-07-30 16:00", "2026-04-30 07:00"]).tz_localize(
        "America/New_York")
    raw = pd.DataFrame({"EPS Estimate": [1.98, 1.89, 1.94], "Reported EPS": [np.nan, 2.02, 2.01],
                        "Surprise(%)": [np.nan, 6.74, 3.46]}, index=idx)
    out = re_.normalize("AAPL", raw)
    assert list(out["report_date"]) == ["2026-07-30", "2026-04-30"]
    assert list(out["timing"]) == ["amc", "bmo"]
    assert out["surprise_pct"].tolist() == [6.74, 3.46]
    assert (out["source"] == re_.SOURCE).all()


def test_refresh_is_resumable(tmp_path):
    from research.bars import connect
    conn = connect(str(tmp_path / "r.db"))
    calls = []

    def fetcher(sym):
        calls.append(sym)
        idx = pd.DatetimeIndex(["2024-01-09 16:00"]).tz_localize("America/New_York")
        return pd.DataFrame({"EPS Estimate": [1.0], "Reported EPS": [1.1], "Surprise(%)": [10.0]}, index=idx)

    s1 = re_.refresh_earnings(["AAA", "BBB"], conn=conn, fetcher=fetcher, pause=0)
    s2 = re_.refresh_earnings(["AAA", "BBB", "CCC"], conn=conn, fetcher=fetcher, pause=0)
    assert s1["fetched"] == 2 and s2["skipped_fresh"] == 2 and s2["fetched"] == 1
    assert calls == ["AAA", "BBB", "CCC"]
    assert len(re_.load_earnings(conn)) == 3
    conn.close()


def test_direction_thresholds():
    s = pd.Series([12.0, 5.0, 4.99, 0.0, -5.0, -20.0, np.nan])
    assert pead.signal_direction(s, None, 5.0, None, "long").tolist() == [1, 1, 0, 0, 0, 0, 0]
    assert pead.signal_direction(s, None, 10.0, None, "long").tolist() == [1, 0, 0, 0, 0, 0, 0]
    assert pead.signal_direction(s, None, 5.0, None, "short").tolist() == [0, 0, 0, 0, -1, -1, 0]
    assert pead.signal_direction(s, None, 5.0, None, "baseline").tolist() == [1, 1, 1, 1, 1, 1, 0]


def test_gap_confirmation_requires_same_direction_and_size():
    s = pd.Series([8.0, 8.0, 8.0, -8.0, -8.0])
    g = pd.Series([3.0, 1.0, -3.0, -2.5, 2.5])
    assert pead.signal_direction(s, g, 5.0, 2.0, "long").tolist() == [1, 0, 0, 0, 0]
    assert pead.signal_direction(s, g, 5.0, 2.0, "short").tolist() == [0, 0, 0, -1, 0]


def test_trailing_decile_uses_only_prior_events():
    dates = pd.date_range("2020-01-01", periods=150, freq="D")
    surprises = list(np.arange(149, dtype=float)) + [1000.0]
    e = pd.DataFrame({"report_date": dates.strftime("%Y-%m-%d"), "surprise_pct": surprises})
    f = pead.trailing_decile_flag(e, min_history=100)
    assert not f.iloc[:100].any()              # not enough history yet
    assert f.iloc[-1]                          # 1000 beats the trailing 90th percentile
    # a series rising every day always beats its own past -> flagged once history exists
    assert f.iloc[100:].all()


def test_generate_events_entry_modes():
    prepared = pd.DataFrame({
        "symbol": ["A", "B", "C"], "surprise_pct": [6.0, 1.0, -9.0], "gap_pct": [1.0, 0.0, -3.0],
        "top_decile": [True, False, False],
        "reaction_date": pd.to_datetime(["2024-01-10", "2024-01-10", "2024-01-11"]),
        "prev_session": pd.to_datetime(["2024-01-09", "2024-01-09", "2024-01-10"]),
    })
    ev = pead.generate_events(prepared)
    assert ev["symbol"].tolist() == ["A"] and ev["event_date"].iloc[0] == pd.Timestamp("2024-01-10")
    ev2 = pead.generate_events(prepared, entry="react_open")
    assert ev2["event_date"].iloc[0] == pd.Timestamp("2024-01-09")
    assert pead.generate_events(prepared, side="baseline")["symbol"].tolist() == ["A", "B", "C"]
    assert pead.generate_events(prepared, side="short")["direction"].tolist() == [-1]
    assert len(pead.generate_events(prepared, gap_min=2.0)) == 0


def test_diff_ci_detects_real_gap_and_not_a_null():
    rng = np.random.default_rng(0)
    dates = np.repeat(pd.date_range("2020-01-01", periods=200).values, 5)
    shock = np.repeat(rng.normal(0, 0.02, 200), 5)           # shared market move per date
    base = shock + rng.normal(0, 0.01, len(dates))
    edge = base + 0.01
    m, lo, hi = pead.diff_ci(edge, dates, base, dates, n_boot=500)
    assert m == pytest.approx(0.01) and lo > 0.008 and hi < 0.012
    m0, lo0, hi0 = pead.diff_ci(base, dates, base + rng.normal(0, 0.01, len(dates)), dates, n_boot=500)
    assert lo0 < 0 < hi0
