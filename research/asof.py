"""Point-in-time ("as-of") data access for strategies. No look-ahead by construction.

A strategy only ever sees ``AsOfData(as_of)``. Everything it can read is what was
knowable at the close of ``as_of`` (US/Eastern):

Session-complete rule (bars)
  A daily bar dated D is a COMPLETED session once the clock passes D 16:00 ET.
  * ``after_close=True`` (default; the simulator computes targets at the close):
    the bar dated ``as_of`` is included only if its session is complete, i.e.
    ``now >= as_of 16:00 ET``. For any historical date that is always true; when a
    live/shadow runner asks for TODAY during market hours the partial bar that the
    store may hold for today is EXCLUDED (bar date < as_of only).
  * ``after_close=False`` (decisions made before the open of ``as_of``): only bars
    dated strictly before ``as_of``.
  ``now`` defaults to the real clock and is injectable for tests.

Earnings (``earnings``)
  The store keeps report_date + timing (bmo / amc / unknown), not a clock time. Each
  event is given a "known at" timestamp: bmo -> report_date 09:00 ET; amc and
  unknown -> report_date 16:01 ET (unknown is treated as after the close, the
  conservative choice). An event is visible iff known_at <= cutoff, where the
  cutoff is as_of 16:00 ET (after_close) or as_of 09:30 ET (pre-open), and never
  later than ``now``. So an after-close report on D is first visible on D+1.
  Caveat: Yahoo's EPS estimate is not a guaranteed point-in-time consensus.

Asking for anything dated after ``as_of`` raises ``LookAheadError``.

Universe: current S&P 500 constituents from the bar store's ``universe`` table that
have at least one completed bar -- SURVIVORSHIP-BIASED (see research/bars.py).

Symbol aliases: ``"SPY#3"`` reads SPY's bars. This lets a strategy hold the same
instrument in several chassis slots (each slot is its own 8%-capped position).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from research.bars import ETFS, DEFAULT_DB_PATH

ET = ZoneInfo("America/New_York")
SESSION_CLOSE = time(16, 0)
SESSION_OPEN = time(9, 30)
BMO_KNOWN_AT = time(9, 0)
AMC_KNOWN_AT = time(16, 1)
BAR_COLS = ["open", "high", "low", "close", "volume"]


class LookAheadError(ValueError):
    """A strategy asked for data dated after its as-of date."""


def base_symbol(symbol: str) -> str:
    return symbol.split("#", 1)[0]


def _to_date(d) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return pd.Timestamp(d).date()


class MarketData:
    """In-memory, lazily loaded view of the bar + earnings store (shared by all AsOfData).

    MarketData(db_path)                         -> reads data/research.db (read-only)
    MarketData(frames={...}, earnings=df, universe=[...])  -> synthetic data for tests
    """

    def __init__(self, db_path: Optional[str] = None, frames: Optional[Dict[str, pd.DataFrame]] = None,
                 earnings: Optional[pd.DataFrame] = None, universe: Optional[List[str]] = None,
                 calendar_symbol: str = "SPY"):
        self._frames: Dict[str, pd.DataFrame] = {}
        self._conn: Optional[sqlite3.Connection] = None
        self.calendar_symbol = calendar_symbol
        if frames is not None:
            for s, df in frames.items():
                self._frames[s] = self._norm(df)
            self._universe = list(universe if universe is not None else
                                  [s for s in frames if s not in {e[0] for e in ETFS}])
            self._earnings = self._prep_earnings(earnings)
        else:
            path = db_path or DEFAULT_DB_PATH
            self._conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
            self._universe = None
            self._earnings = None
        self._tdays: Optional[pd.DatetimeIndex] = None

    @staticmethod
    def _norm(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.index = pd.DatetimeIndex(pd.to_datetime(df.index)).normalize()
        df.index.name = "date"
        return df[BAR_COLS].sort_index().astype(float)

    @staticmethod
    def _prep_earnings(e: Optional[pd.DataFrame]) -> pd.DataFrame:
        cols = ["symbol", "report_date", "timing", "eps_estimate", "eps_actual", "surprise_pct"]
        if e is None or len(e) == 0:
            out = pd.DataFrame(columns=cols + ["known_at"])
            out["known_at"] = pd.to_datetime(out["known_at"]).dt.tz_localize(ET)
            return out
        e = e[[c for c in cols if c in e.columns]].copy()
        d = pd.to_datetime(e["report_date"]).dt.normalize()
        bmo = e["timing"].values == "bmo"
        offset = np.where(bmo, BMO_KNOWN_AT.hour * 60 + BMO_KNOWN_AT.minute,
                          AMC_KNOWN_AT.hour * 60 + AMC_KNOWN_AT.minute)
        e["known_at"] = (d + pd.to_timedelta(offset, unit="m")).dt.tz_localize(ET)
        return e.sort_values(["known_at", "symbol"]).reset_index(drop=True)

    def frame(self, symbol: str) -> pd.DataFrame:
        s = base_symbol(symbol)
        if s not in self._frames:
            if self._conn is None:
                self._frames[s] = pd.DataFrame(columns=BAR_COLS, index=pd.DatetimeIndex([], name="date"))
            else:
                df = pd.read_sql_query(
                    "SELECT date, open, high, low, close, volume FROM daily_bars WHERE symbol=? "
                    "ORDER BY date", self._conn, params=(s,), parse_dates=["date"]).set_index("date")
                self._frames[s] = self._norm(df)
        return self._frames[s]

    def preload(self, symbols: Optional[List[str]] = None) -> None:
        """Load many symbols in one query (faster than lazy loading for wide strategies)."""
        if self._conn is None:
            return
        syms = symbols or self.universe_all() + [e[0] for e in ETFS]
        todo = [s for s in {base_symbol(x) for x in syms} if s not in self._frames]
        if not todo:
            return
        q = ("SELECT symbol, date, open, high, low, close, volume FROM daily_bars WHERE symbol IN "
             f"({','.join('?' * len(todo))}) ORDER BY symbol, date")
        df = pd.read_sql_query(q, self._conn, params=todo, parse_dates=["date"])
        for s, g in df.groupby("symbol"):
            self._frames[s] = self._norm(g.set_index("date"))

    def trading_days(self) -> pd.DatetimeIndex:
        if self._tdays is None:
            self._tdays = self.frame(self.calendar_symbol).index
        return self._tdays

    def universe_all(self) -> List[str]:
        if self._universe is None:
            etfs = {e[0] for e in ETFS}
            self._universe = [r[0] for r in self._conn.execute("SELECT symbol FROM universe ORDER BY symbol")
                              if r[0] not in etfs]
        return self._universe

    def earnings_all(self) -> pd.DataFrame:
        if self._earnings is None:
            try:
                e = pd.read_sql_query("SELECT * FROM earnings_events", self._conn)
            except Exception:
                e = None
            self._earnings = self._prep_earnings(e)
        return self._earnings

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class AsOfData:
    """Everything knowable at the close (or, after_close=False, the open) of `as_of`."""

    def __init__(self, as_of, market: Optional[MarketData] = None, after_close: bool = True,
                 now: Optional[datetime] = None):
        self.as_of: date = _to_date(as_of)
        self.market = market if market is not None else MarketData()
        self.after_close = after_close
        if now is None:
            now = datetime.now(ET)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=ET)
        self.now = now.astimezone(ET)
        close_ts = datetime.combine(self.as_of, SESSION_CLOSE, tzinfo=ET)
        open_ts = datetime.combine(self.as_of, SESSION_OPEN, tzinfo=ET)
        self.session_complete = self.now >= close_ts
        self.include_as_of_bar = after_close and self.session_complete
        self.earnings_cutoff = min(close_ts if after_close else open_ts, self.now)
        # last bar date visible (inclusive)
        d = pd.Timestamp(self.as_of)
        self._bar_cut = d if self.include_as_of_bar else d - pd.Timedelta(days=1)

    # ------------------------------------------------------------------ guards
    def _check(self, d) -> pd.Timestamp:
        t = pd.Timestamp(_to_date(d))
        if t > pd.Timestamp(self.as_of):
            raise LookAheadError(f"requested {t.date()} but as_of is {self.as_of}")
        return t

    def at(self, d) -> "AsOfData":
        """A view as of an EARLIER (or the same) date; later dates raise."""
        self._check(d)
        return AsOfData(d, self.market, self.after_close, self.now)

    # ------------------------------------------------------------------ bars
    def bars(self, symbol: str, lookback: Optional[int] = None, until=None) -> pd.DataFrame:
        """Completed daily bars <= the cut, optionally ending at `until` (<= as_of), last `lookback` rows."""
        cut = self._bar_cut
        if until is not None:
            cut = min(cut, self._check(until))
        df = self.market.frame(symbol)
        i = df.index.searchsorted(cut, side="right")
        lo = 0 if lookback is None else max(0, i - int(lookback))
        return df.iloc[lo:i]

    def close(self, symbol: str) -> Optional[float]:
        b = self.bars(symbol, lookback=1)
        return float(b["close"].iloc[-1]) if len(b) else None

    def spy(self, lookback: Optional[int] = None) -> pd.DataFrame:
        return self.bars("SPY", lookback)

    def trading_days(self, lookback: Optional[int] = None) -> pd.DatetimeIndex:
        td = self.market.trading_days()
        i = td.searchsorted(self._bar_cut, side="right")
        return td[(0 if lookback is None else max(0, i - lookback)):i]

    def universe(self) -> List[str]:
        out = []
        for s in self.market.universe_all():
            df = self.market.frame(s)
            if len(df) and df.index[0] <= self._bar_cut:
                out.append(s)
        return out

    # ------------------------------------------------------------------ earnings
    def earnings(self, symbol: Optional[str] = None, until=None) -> pd.DataFrame:
        e = self.market.earnings_all()
        cutoff = pd.Timestamp(self.earnings_cutoff)
        if until is not None:
            t = self._check(until)
            cutoff = min(cutoff, pd.Timestamp(datetime.combine(t.date(), SESSION_CLOSE, tzinfo=ET)))
        m = e["known_at"] <= cutoff
        if symbol is not None:
            m &= e["symbol"] == base_symbol(symbol)
        return e[m]
