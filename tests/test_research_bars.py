"""Bar store: round-trip, incremental fetch, re-adjustment reload, universe fallback. Offline."""

import pandas as pd
import pytest

from research import bars as rb


def _frame(dates, start_px=100.0, scale=1.0):
    idx = pd.to_datetime(dates)
    px = [(start_px + i) * scale for i in range(len(idx))]
    return pd.DataFrame({"Open": px, "High": [p + 1 for p in px], "Low": [p - 1 for p in px],
                         "Close": px, "Volume": [1e6] * len(idx)}, index=idx)


class FakeDownloader:
    """Serves a fixed 'true' history per symbol, sliced to the requested [start, end)."""

    def __init__(self, history):
        self.history = history
        self.calls = []

    def __call__(self, symbols, start, end):
        self.calls.append((tuple(symbols), start, end))
        out = {}
        for s in symbols:
            h = self.history.get(s)
            if h is None:
                continue
            out[s] = h[(h.index >= pd.Timestamp(start)) & (h.index < pd.Timestamp(end))]
        return out


@pytest.fixture()
def conn(tmp_path):
    c = rb.connect(str(tmp_path / "research.db"))
    yield c
    c.close()


def test_refuses_live_ledger(tmp_path):
    with pytest.raises(ValueError):
        rb.connect(str(tmp_path / "wealthincome.db"))


def test_store_round_trip(tmp_path, conn):
    df = rb._clean(_frame(pd.bdate_range("2024-01-01", periods=5)))
    rb._write(conn, "AAA", df, replace_all=False)
    conn.commit()
    store = rb.BarStore(str(tmp_path / "research.db"))
    got = store.bars("AAA", "2024-01-02", "2024-01-04")
    assert list(got.index.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert got["close"].tolist() == [101.0, 102.0, 103.0]
    assert got["high"].iloc[0] == 102.0
    assert store.row_count() == 5
    store.close()


def test_incremental_fetch_only_requests_new_dates(conn):
    days = pd.bdate_range("2024-01-01", "2024-03-29")
    full = _frame(days)
    fake = FakeDownloader({"AAA": full[full.index <= "2024-02-29"], "BBB": full[full.index <= "2024-02-29"]})
    s1 = rb.refresh_bars(["AAA", "BBB"], start="2024-01-01", end="2024-03-01", conn=conn,
                         downloader=fake, sleep=lambda s: None)
    assert s1.rows_written == 2 * len(days[days <= "2024-02-29"])
    assert fake.calls == [(("AAA", "BBB"), "2024-01-01", "2024-03-01")]   # one batch

    fake.history = {"AAA": full, "BBB": full}
    fake.calls.clear()
    s2 = rb.refresh_bars(["AAA", "BBB"], start="2024-01-01", end="2024-03-30", conn=conn,
                         downloader=fake, sleep=lambda s: None)
    (syms, start, end), = fake.calls
    assert start == (pd.Timestamp("2024-02-29") - pd.Timedelta(days=rb.OVERLAP_DAYS)).strftime("%Y-%m-%d")
    assert s2.full_reloads == 0
    n = conn.execute("SELECT COUNT(*) FROM daily_bars WHERE symbol='AAA'").fetchone()[0]
    assert n == len(days)
    # nothing new -> still only the overlap window is requested, no duplicates
    fake.calls.clear()
    rb.refresh_bars(["AAA"], start="2024-01-01", end="2024-03-30", conn=conn,
                    downloader=fake, sleep=lambda s: None)
    assert fake.calls[0][1] > "2024-03-01"
    assert conn.execute("SELECT COUNT(*) FROM daily_bars WHERE symbol='AAA'").fetchone()[0] == len(days)


def test_batches_of_fifty(conn):
    syms = [f"S{i:03d}" for i in range(120)]
    hist = {s: _frame(pd.bdate_range("2024-01-01", periods=3)) for s in syms}
    fake = FakeDownloader(hist)
    rb.refresh_bars(syms, start="2024-01-01", end="2024-01-10", conn=conn, downloader=fake,
                    sleep=lambda s: None)
    assert [len(c[0]) for c in fake.calls] == [50, 50, 20]


def test_adjustment_change_triggers_full_reload(conn):
    days = pd.bdate_range("2024-01-01", "2024-02-29")
    fake = FakeDownloader({"AAA": _frame(days[:30])})
    rb.refresh_bars(["AAA"], start="2024-01-01", end="2024-03-01", conn=conn, downloader=fake,
                    sleep=lambda s: None)
    # a split/dividend re-adjusts the whole history by 0.5
    fake.history = {"AAA": _frame(days, scale=0.5)}
    s = rb.refresh_bars(["AAA"], start="2024-01-01", end="2024-03-01", conn=conn, downloader=fake,
                        sleep=lambda s: None)
    assert s.full_reloads == 1
    closes = [r[0] for r in conn.execute("SELECT close FROM daily_bars WHERE symbol='AAA' ORDER BY date")]
    assert len(closes) == len(days)
    assert closes[0] == 50.0                                   # old rows rewritten on the new basis


def test_retry_with_backoff_then_success(conn):
    waits = []
    calls = {"n": 0}

    def flaky(symbols, start, end):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("rate limited")
        return {"AAA": _frame(pd.bdate_range("2024-01-01", periods=2))}

    s = rb.refresh_bars(["AAA"], start="2024-01-01", end="2024-01-05", conn=conn, downloader=flaky,
                        sleep=waits.append, pause=0)
    assert s.rows_written == 2
    assert waits == [2.0, 4.0]


def test_failed_symbol_reported(conn):
    fake = FakeDownloader({})
    s = rb.refresh_bars(["ZZZ"], start="2024-01-01", end="2024-01-05", conn=conn, downloader=fake,
                        sleep=lambda s: None)
    assert s.failed == ["ZZZ"]


def test_universe_uses_fetcher_and_adds_etfs(conn):
    fake = lambda: pd.DataFrame({"symbol": ["AAPL", "BRK-B"], "name": ["Apple", "Berkshire"],
                                 "sector": ["IT", "Fin"]})
    syms = rb.ensure_universe(conn, fetcher=fake)
    assert set(syms) == {"AAPL", "BRK-B", "SPY", "QQQ", "IWM", "BIL", "SGOV"}
    src = dict(conn.execute("SELECT symbol, source FROM universe"))
    assert src["AAPL"] == "wikipedia_current" and src["SPY"] == "etf"
    assert src["BIL"] == src["SGOV"] == "etf"          # refreshed nightly with the universe


def test_tbill_etfs_excluded_from_stock_universe_scans(tmp_path):
    from research.asof import AsOfData, MarketData
    from research_synth import frame
    db = str(tmp_path / "u.db")
    c = rb.connect(db)
    rb.ensure_universe(c, fetcher=lambda: pd.DataFrame({"symbol": ["AAPL"], "name": ["Apple"],
                                                         "sector": ["IT"]}))
    dates = pd.bdate_range("2024-01-02", periods=3)
    for s in ("AAPL", "SPY", "BIL", "SGOV"):
        rb._write(c, s, frame(dates, [100.0, 101.0, 102.0]), replace_all=True)
    c.commit()
    c.close()
    store = rb.BarStore(db)
    assert store.symbols(etfs=False) == ["AAPL"]
    store.close()
    m = MarketData(db)
    assert m.universe_all() == ["AAPL"]
    assert AsOfData("2024-01-04", m).close("BIL") == 102.0            # still readable by name
    m.close()


def test_universe_falls_back_to_snapshot(conn):
    def boom():
        raise OSError("offline")

    syms = rb.ensure_universe(conn, fetcher=boom)
    assert len(syms) > 450
    assert {"SPY", "QQQ", "IWM", "AAPL"} <= set(syms)
    assert conn.execute("SELECT COUNT(*) FROM universe WHERE source='snapshot_csv'").fetchone()[0] > 450


def test_symbol_normalisation():
    assert rb.to_yf_symbol("brk.b") == "BRK-B"
