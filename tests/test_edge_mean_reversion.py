"""Mean-reversion trigger logic on crafted series. Offline."""

import numpy as np
import pandas as pd

from research import bars as rb
from research.edges import mean_reversion as mr


def _series(closes, volume=1e6):
    days = pd.bdate_range("2020-01-01", periods=len(closes))
    c = np.asarray(closes, float)
    return pd.DataFrame({"open": c, "high": c * 1.005, "low": c * 0.995, "close": c,
                         "volume": volume}, index=days)


def _uptrend_then_drop(n=320, drop_at=300, drop=0.08):
    c = list(100 + 0.3 * np.arange(n))
    for i in range(drop_at, n):
        c[i] = c[drop_at - 1] * (1 - drop) + 0.3 * (i - drop_at)
    return c


def test_dip_in_uptrend_fires_on_the_drop_day_only():
    df = _series(_uptrend_then_drop())       # price ~190, vol 1e6 -> ~$190M/day
    sig = mr.signals(df)
    ev = sig.index[sig["event"]]
    assert len(ev) >= 1
    assert ev[0] == df.index[300]
    assert not sig["event"].iloc[:300].any()
    assert sig["rsi"].iloc[300] < 10


def test_illiquid_names_are_filtered():
    df = _series(_uptrend_then_drop(), volume=1e3)    # ~$0.2M/day
    assert not mr.signals(df)["event"].any()


def test_downtrend_60_days_ago_is_filtered():
    n = 320
    c = list(300 - 0.5 * np.arange(n))       # always below its SMA200
    c[300] = c[299] * 0.9
    sig = mr.signals(_series(c, volume=1e7))
    assert sig["raw_trigger"].iloc[300] == False  # noqa: E712
    assert not sig["event"].any()


def test_atr_trigger_without_rsi():
    df = _series(_uptrend_then_drop())
    sig = mr.signals(df, rsi_max=-1)          # RSI arm disabled
    assert sig["event"].iloc[300]
    assert not mr.signals(df, rsi_max=-1, use_atr_trigger=False)["event"].any()


def test_cooldown_suppresses_repeat_triggers():
    c = _uptrend_then_drop(n=320, drop_at=300, drop=0.04)
    for i in range(301, 305):                 # keep falling: repeated triggers
        c[i] = c[i - 1] * 0.97
    df = _series(c)
    raw = mr.signals(df)["event"]
    cooled = mr.signals(df, cooldown=5)["event"]
    assert raw.iloc[300:305].sum() >= 3
    assert cooled.iloc[300:306].sum() == 1


def test_generate_events_from_store(tmp_path):
    path = str(tmp_path / "research.db")
    conn = rb.connect(path)
    rb._write(conn, "AAA", _series(_uptrend_then_drop()), replace_all=True)
    rb._write(conn, "SPY", _series(_uptrend_then_drop()), replace_all=True)   # ETFs excluded
    conn.commit()
    conn.close()
    store = rb.BarStore(path)
    ev = mr.generate_events(store, start="2021-01-01")
    store.close()
    assert set(ev["symbol"]) == {"AAA"}
    assert (ev["direction"] == 1).all()
    assert ev["event_date"].min() >= pd.Timestamp("2021-01-01")
