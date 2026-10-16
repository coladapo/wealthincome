"""Minute-bar store: regular-session filtering, incremental + resumable refresh, frozen
universe, minute grid. Offline (mocked fetcher and calendar)."""

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from research import bars as rb
from research import intraday_bars as ib

CAL = [{"date": "2025-01-02", "open": "09:30", "close": "16:00"},
       {"date": "2025-01-03", "open": "09:30", "close": "16:00"},
       {"date": "2025-07-03", "open": "09:30", "close": "13:00"},   # early close (EDT)
       {"date": "2025-07-07", "open": "09:30", "close": "16:00"}]


def calendar(start, end):
    return [c for c in CAL if start <= c["date"] <= end]


class FakeFetcher:
    """Serves 1-minute bars 04:00-20:00 ET (incl. pre/post market) for every requested day."""

    def __init__(self, fail_dates=(), missing=None):
        self.calls, self.fail_dates, self.missing = [], set(fail_dates), missing or {}

    def __call__(self, symbols, start, end):
        self.calls.append((tuple(symbols), start, end))
        d = start[:10]
        if d in self.fail_dates:
            raise RuntimeError("boom")
        s = pd.Timestamp(start).tz_convert("America/New_York").normalize() + pd.Timedelta(hours=4)
        ts = pd.date_range(s, s + pd.Timedelta(hours=16), freq="1min", inclusive="left")
        out = {}
        for sym in symbols:
            rows = []
            for i, t in enumerate(ts):
                tu = t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
                if (sym, tu) in self.missing:
                    continue
                rows.append({"t": tu, "o": 100 + i * 0.01, "h": 100.1 + i * 0.01, "l": 99.9 + i * 0.01,
                             "c": 100 + i * 0.01, "v": 1000, "n": 10, "vw": 100 + i * 0.01})
            out[sym] = rows
        return out


@pytest.fixture()
def conn(tmp_path):
    c = ib.connect(str(tmp_path / "intraday.db"))
    yield c
    c.close()


def _refresh(conn, fetcher, today=date(2025, 7, 8), symbols=("SPY", "AAA")):
    return ib.refresh(years=1, conn=conn, fetcher=fetcher, calendar_fn=calendar, symbols=list(symbols),
                      today=today, workers=1)


def test_refresh_keeps_regular_session_only_and_respects_early_close(conn):
    st = _refresh(conn, FakeFetcher())
    assert st.sessions_fetched == 4 and not st.failed_sessions
    per = dict(conn.execute("SELECT date, n_bars FROM sessions_loaded WHERE symbol='AAA'"))
    assert per == {"2025-01-02": 390, "2025-01-03": 390, "2025-07-03": 210, "2025-07-07": 390}
    lo, hi = conn.execute("SELECT MIN(ts_utc), MAX(ts_utc) FROM minute_bars WHERE ts_utc LIKE '2025-01-02%'").fetchone()
    assert (lo, hi) == ("2025-01-02T14:30:00Z", "2025-01-02T20:59:00Z")       # EST: 09:30-15:59 ET
    hi_jul = conn.execute("SELECT MAX(ts_utc) FROM minute_bars WHERE ts_utc LIKE '2025-07-03%'").fetchone()[0]
    assert hi_jul == "2025-07-03T16:59:00Z"                                    # 12:59 ET on the half day
    assert st.rows_written == 2 * (390 * 3 + 210)


def test_refresh_is_incremental(conn):
    _refresh(conn, FakeFetcher(), today=date(2025, 7, 4))      # loads 3 sessions
    f2 = FakeFetcher()
    st = _refresh(conn, f2, today=date(2025, 7, 8))
    assert [c[1][:10] for c in f2.calls] == ["2025-07-07"]        # only the new session
    assert st.sessions_fetched == 1
    f3 = FakeFetcher()
    _refresh(conn, f3, today=date(2025, 7, 8), symbols=("SPY", "AAA", "BBB"))
    assert all(c[0] == ("BBB",) for c in f3.calls) and len(f3.calls) == 4   # new symbol: only it, all sessions
    f4 = FakeFetcher()
    assert _refresh(conn, f4, symbols=("SPY", "AAA", "BBB")).sessions_fetched == 0 and not f4.calls


def test_failed_session_is_not_marked_and_is_retried(conn):
    st = _refresh(conn, FakeFetcher(fail_dates={"2025-01-03"}))
    assert st.failed_sessions == ["2025-01-03"]
    assert conn.execute("SELECT COUNT(*) FROM sessions_loaded WHERE date='2025-01-03'").fetchone()[0] == 0
    f = FakeFetcher()
    _refresh(conn, f)
    assert [c[1][:10] for c in f.calls] == ["2025-01-03"]


def test_only_finished_sessions_requested(conn):
    f = FakeFetcher()
    _refresh(conn, f, today=date(2025, 7, 7))      # 07-07 is "today": not requested
    assert "2025-07-07" not in {c[1][:10] for c in f.calls}


def test_threaded_refresh_matches_serial(tmp_path):
    a, b = ib.connect(str(tmp_path / "a.db")), ib.connect(str(tmp_path / "b.db"))
    try:
        ib.refresh(1, conn=a, fetcher=FakeFetcher(), calendar_fn=calendar, symbols=["AAA"],
                   today=date(2025, 7, 8), workers=1)
        ib.refresh(1, conn=b, fetcher=FakeFetcher(), calendar_fn=calendar, symbols=["AAA"],
                   today=date(2025, 7, 8), workers=3)
        q = "SELECT COUNT(*), SUM(close) FROM minute_bars"
        assert a.execute(q).fetchone() == b.execute(q).fetchone()
    finally:
        a.close()
        b.close()


def test_universe_ranked_once_and_frozen(conn, tmp_path):
    rdb = str(tmp_path / "research.db")
    rc = rb.connect(rdb)
    days = pd.bdate_range("2025-01-01", periods=70).strftime("%Y-%m-%d")
    rows = []
    for s, dv in (("SPY", 9e9), ("BIG", 5e9), ("BRK-B", 3e9), ("SMALL", 1e6)):
        rows += [(s, d, 1, 1, 1, 1.0, dv) for d in days]
    rc.executemany("INSERT INTO daily_bars VALUES (?,?,?,?,?,?,?)", rows)
    rc.executemany("INSERT INTO universe VALUES (?,?,?,?,?)",
                   [("SPY", "", "ETF", "", "etf"), ("BIG", "", "", "", "x"), ("BRK-B", "", "", "", "x"),
                    ("SMALL", "", "", "", "x")])
    rc.commit()
    rc.close()
    ranked = ib.rank_universe(rdb, top_n=2)
    assert list(ranked["symbol"]) == ["BIG", "BRK-B"]               # ETFs excluded from the ranking
    u = ib.ensure_universe(conn, research_db=rdb, ranker=lambda: ranked)
    assert u[:2] == ["QQQ", "SPY"] and u[2:] == ["BIG", "BRK.B"]    # Alpaca symbol form
    calls = []
    assert ib.ensure_universe(conn, ranker=lambda: calls.append(1)) == u and not calls


def test_store_minute_grid(conn, tmp_path):
    missing = {("AAA", "2025-01-02T14:40:00Z")}                     # minute 10 has no trades
    _refresh(conn, FakeFetcher(missing=missing), symbols=("AAA",))
    st = ib.IntradayStore(str(tmp_path / "intraday.db"))
    try:
        ss = st.symbol_sessions("AAA")
        assert sorted(ss) == ["2025-01-02", "2025-01-03", "2025-07-03", "2025-07-07"]
        sb = ss["2025-01-02"]
        assert sb.n == 390 and sb.flat_idx == 385
        assert np.isnan(sb.open[10]) and sb.volume[10] == 0 and np.isfinite(sb.open[11])
        assert sb.open[0] == pytest.approx(100 + 330 * 0.01)       # 09:30 is minute 330 after 04:00
        assert ss["2025-07-03"].n == 210 and ss["2025-07-03"].flat_idx == 205
        only = st.symbol_sessions("AAA", start="2025-07-01", end="2025-07-03")
        assert list(only) == ["2025-07-03"]
        assert st.sessions() == ["2025-01-02", "2025-01-03", "2025-07-03", "2025-07-07"]
    finally:
        st.close()


def test_refuses_shared_databases(tmp_path):
    with pytest.raises(ValueError):
        ib.connect(str(tmp_path / "research.db"))
    with pytest.raises(ValueError):
        ib.connect(str(tmp_path / "wealthincome.db"))


def test_status(conn):
    _refresh(conn, FakeFetcher())
    s = ib.status(conn)
    assert s["symbols"] == 2 and s["rows"] == 2 * (390 * 3 + 210) and s["sessions_in_calendar"] == 4
