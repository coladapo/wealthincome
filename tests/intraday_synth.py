"""Synthetic minute bars for the intraday tests (offline)."""

import numpy as np

from research.intraday_bars import SessionBars, flat_index


def session(symbol="AAA", d="2025-01-02", n=390, px=100.0, vol=1e6, range_bp=0.0, **overrides):
    """Flat-price session: open=high=low=close=px unless overridden per minute.

    overrides: {"open": {m: v}, "high": {...}, "low": {...}, "close": {...}, "volume": {...}}.
    range_bp widens high/low of EVERY bar symmetrically (drives the spread estimate)."""
    a = {k: np.full(n, float(px)) for k in ("open", "high", "low", "close", "vwap")}
    a["high"] *= 1 + range_bp / 2e4
    a["low"] *= 1 - range_bp / 2e4
    a["volume"] = np.full(n, float(vol))
    for k, mv in overrides.items():
        for m, v in mv.items():
            a[k][m] = v
    return SessionBars(symbol, d, a["open"], a["high"], a["low"], a["close"], a["volume"], a["vwap"],
                       flat_index(n))
