"""Synthetic market data for the research-pipeline tests (offline, deterministic)."""

import numpy as np
import pandas as pd

from research.asof import MarketData


def frame(dates, closes, opens=None):
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    closes = np.asarray(closes, dtype=float)
    opens = closes if opens is None else np.asarray(opens, dtype=float)
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) * 1.001,
                         "low": np.minimum(opens, closes) * 0.999, "close": closes,
                         "volume": 1e6}, index=idx)


def flat_market(dates, symbols=("AAA", "BBB"), price=100.0, earnings=None, overrides=None):
    """SPY + `symbols`, all flat at `price` unless overridden with {sym: DataFrame}."""
    frames = {"SPY": frame(dates, [price] * len(dates))}
    for s in symbols:
        frames[s] = frame(dates, [price] * len(dates))
    frames.update(overrides or {})
    return MarketData(frames=frames, earnings=earnings, universe=list(symbols))


def random_market(start="2019-01-01", end="2021-12-31", symbols=("AAA", "BBB", "CCC"), seed=1):
    dates = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    frames = {}
    for i, s in enumerate(("SPY",) + tuple(symbols)):
        r = rng.normal(0.0004, 0.01, len(dates))
        c = 100 * np.cumprod(1 + r)
        o = c * (1 + rng.normal(0, 0.002, len(dates)))
        frames[s] = frame(dates, c, o)
    return MarketData(frames=frames, universe=list(symbols))
