"""Minute-bar store for the day sleeve's research (data/intraday.db, separate from
research.db and never the live ledger).

Universe
--------
SPY + QQQ + the 50 most liquid single names in research.db's `universe` (current
S&P 500), ranked by average daily dollar volume (close x volume) over the last 60
sessions in research.db `daily_bars` (read-only). The ranking is computed once and
frozen in `intraday_universe` so incremental refreshes never mix universes; pass
--rerank to recompute. SURVIVORSHIP: today's most liquid names, ranked with today's
data, looked back two years -- a mild look-ahead that flatters long intraday edges.

Bars
----
Alpaca SIP 1-minute bars, raw (unadjusted) prices: every intraday signal opens and
closes inside one session, so splits/dividends between sessions do not matter.
Regular session only: open <= t < close per the exchange calendar (Alpaca
/v2/calendar), so early-close days (13:00 ET) are cut correctly. One request per
session for all symbols missing that session (paginated). A (symbol, date) pair is
marked in `sessions_loaded` only after its whole session fetched, so the load is
resumable and the refresh incremental (only missing sessions are requested).
Only sessions that closed before today are requested (SIP history on the free plan
is delayed ~15 min).

CLI:  venv/bin/python -m research.intraday_bars refresh [--years 2]
      venv/bin/python -m research.intraday_bars status
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

log = logging.getLogger("research.intraday_bars")

ET = ZoneInfo("America/New_York")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.environ.get("INTRADAY_DB", os.path.join(REPO_ROOT, "data", "intraday.db"))
DEFAULT_RESEARCH_DB = os.environ.get("RESEARCH_DB", os.path.join(REPO_ROOT, "data", "research.db"))
BENCH_ETFS = ("SPY", "QQQ")
TOP_N = 50
RANK_WINDOW = 60
MIN_REQUEST_INTERVAL = 0.35     # seconds between API calls across threads (~170/min < 200/min)

SCHEMA = """
CREATE TABLE IF NOT EXISTS minute_bars (
    symbol      TEXT NOT NULL,
    ts_utc      TEXT NOT NULL,          -- bar start, 'YYYY-MM-DDTHH:MM:SSZ'
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, trade_count INTEGER, vwap REAL,
    PRIMARY KEY (symbol, ts_utc)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS sessions (
    date      TEXT PRIMARY KEY,         -- ET session date
    open_utc  TEXT NOT NULL,
    close_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions_loaded (
    symbol    TEXT NOT NULL,
    date      TEXT NOT NULL,
    n_bars    INTEGER NOT NULL,
    loaded_at TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS intraday_universe (
    symbol         TEXT PRIMARY KEY,
    rank           INTEGER,
    avg_dollar_vol REAL,
    ranked_at      TEXT,
    source         TEXT
);
CREATE TABLE IF NOT EXISTS intraday_variant_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    edge_name   TEXT NOT NULL,
    run_at      TEXT NOT NULL,
    params_json TEXT,
    n_trades    INTEGER,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_loaded_date ON sessions_loaded(date);
CREATE INDEX IF NOT EXISTS idx_intraday_variant_edge ON intraday_variant_runs(edge_name);
"""

# fetcher(symbols, start_rfc3339, end_rfc3339) -> {symbol: [{"t","o","h","l","c","v","n","vw"}]}
Fetcher = Callable[[List[str], str, str], Dict[str, List[Dict]]]
# calendar_fn(start "YYYY-MM-DD", end "YYYY-MM-DD") -> [{"date","open":"HH:MM","close":"HH:MM"}]
CalendarFn = Callable[[str, str], List[Dict]]


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    if os.path.basename(path) in ("wealthincome.db", "research.db"):
        raise ValueError("intraday store must be its own database")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=60, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def to_alpaca_symbol(sym: str) -> str:
    """research.db uses yfinance's BRK-B; Alpaca wants BRK.B."""
    return sym.strip().upper().replace("-", ".")


def _iso_z(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_bounds(d: str, open_hm: str, close_hm: str):
    day = date.fromisoformat(d)
    o = datetime.combine(day, datetime.strptime(open_hm, "%H:%M").time(), ET)
    c = datetime.combine(day, datetime.strptime(close_hm, "%H:%M").time(), ET)
    return _iso_z(o), _iso_z(c)


# --------------------------------------------------------------------------- universe

def rank_universe(research_db: Optional[str] = None, top_n: int = TOP_N,
                  window: int = RANK_WINDOW) -> pd.DataFrame:
    """[symbol (yfinance form), avg_dollar_vol] for the top_n single names, read-only."""
    path = research_db or DEFAULT_RESEARCH_DB
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        dates = [r[0] for r in conn.execute(
            "SELECT date FROM daily_bars WHERE symbol='SPY' ORDER BY date DESC LIMIT ?", (window,))]
        if not dates:
            raise ValueError("research.db has no SPY daily bars to define the ranking window")
        df = pd.read_sql_query(
            """SELECT b.symbol, AVG(b.close * b.volume) AS avg_dollar_vol, COUNT(*) AS n
               FROM daily_bars b JOIN universe u ON u.symbol = b.symbol
               WHERE b.date >= ? AND b.date <= ? AND COALESCE(u.source,'') != 'etf'
               GROUP BY b.symbol""", conn, params=(min(dates), max(dates)))
    finally:
        conn.close()
    df = df[df["n"] >= int(window * 0.8)]
    return df.sort_values("avg_dollar_vol", ascending=False).head(top_n)[["symbol", "avg_dollar_vol"]]


def ensure_universe(conn: sqlite3.Connection, research_db: Optional[str] = None,
                    rerank: bool = False, ranker: Optional[Callable[[], pd.DataFrame]] = None) -> List[str]:
    """Frozen universe (Alpaca symbols): SPY, QQQ + top-50 single names. Ranks once unless rerank."""
    have = [r[0] for r in conn.execute("SELECT symbol FROM intraday_universe ORDER BY rank, symbol")]
    if have and not rerank:
        return have
    ranked = ranker() if ranker else rank_universe(research_db)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute("DELETE FROM intraday_universe")
    rows = [(s, 0, None, now, "benchmark") for s in BENCH_ETFS]
    rows += [(to_alpaca_symbol(r.symbol), i + 1, float(r.avg_dollar_vol), now, f"top{len(ranked)}_dollar_vol")
             for i, r in enumerate(ranked.itertuples())]
    conn.executemany("INSERT OR REPLACE INTO intraday_universe VALUES (?,?,?,?,?)", rows)
    conn.commit()
    return [r[0] for r in conn.execute("SELECT symbol FROM intraday_universe ORDER BY rank, symbol")]


# --------------------------------------------------------------------------- sessions

def ensure_sessions(conn: sqlite3.Connection, start: str, end: str, calendar_fn: CalendarFn) -> List[str]:
    """Upsert the exchange calendar for [start, end]; return the session dates in range."""
    cal = calendar_fn(start, end)
    rows = [(c["date"], *_utc_bounds(c["date"], c["open"], c["close"])) for c in cal]
    conn.executemany("INSERT OR REPLACE INTO sessions(date, open_utc, close_utc) VALUES (?,?,?)", rows)
    conn.commit()
    return [r[0] for r in conn.execute(
        "SELECT date FROM sessions WHERE date >= ? AND date <= ? ORDER BY date", (start, end))]


def missing_sessions(conn: sqlite3.Connection, symbols: Sequence[str],
                     dates: Sequence[str]) -> Dict[str, List[str]]:
    """{date: [symbols not yet loaded for that session]} for dates with anything missing."""
    loaded: Dict[str, set] = {}
    for s, d in conn.execute("SELECT symbol, date FROM sessions_loaded"):
        loaded.setdefault(d, set()).add(s)
    out = {}
    for d in dates:
        miss = [s for s in symbols if s not in loaded.get(d, ())]
        if miss:
            out[d] = miss
    return out


# --------------------------------------------------------------------------- refresh

class _RateLimiter:
    def __init__(self, interval: float):
        self.interval, self.lock, self.last = interval, threading.Lock(), 0.0

    def wait(self, sleep: Callable[[float], None] = time.sleep) -> None:
        with self.lock:
            now = time.monotonic()
            gap = self.last + self.interval - now
            if gap > 0:
                sleep(gap)
            self.last = time.monotonic()


def alpaca_fetcher(limiter: Optional[_RateLimiter] = None) -> Fetcher:
    from research import alpaca_data

    limiter = limiter or _RateLimiter(MIN_REQUEST_INTERVAL)

    class _Session:  # rate-limit every page request, not just every session
        def get(self, *a, **k):
            import requests
            limiter.wait()
            return requests.get(*a, **k)

    sess = _Session()
    return lambda syms, s, e: alpaca_data.fetch_bars(syms, s, e, session=sess)


def alpaca_calendar(start: str, end: str) -> List[Dict]:
    from research import alpaca_data
    return alpaca_data.fetch_calendar(start, end)


def _rows_for(symbol: str, bars: List[Dict], open_utc: str, close_utc: str) -> List[tuple]:
    out = []
    for b in bars:
        t = b["t"]
        if len(t) > 20:  # normalise e.g. '2024-10-14T13:30:00.000Z' / '+00:00'
            t = _iso_z(pd.Timestamp(t).to_pydatetime())
        if open_utc <= t < close_utc:
            out.append((symbol, t, b.get("o"), b.get("h"), b.get("l"), b.get("c"), b.get("v"),
                        b.get("n"), b.get("vw")))
    return out


@dataclass
class RefreshStats:
    symbols: int = 0
    sessions_in_range: int = 0
    sessions_fetched: int = 0
    rows_written: int = 0
    failed_sessions: List[str] = field(default_factory=list)
    empty_symbols: List[str] = field(default_factory=list)
    seconds: float = 0.0

    def __str__(self) -> str:
        return (f"symbols={self.symbols} sessions_in_range={self.sessions_in_range} "
                f"sessions_fetched={self.sessions_fetched} rows_written={self.rows_written} "
                f"failed_sessions={len(self.failed_sessions)} seconds={self.seconds:.1f}")


def refresh(years: float = 2.0, conn: Optional[sqlite3.Connection] = None,
            fetcher: Optional[Fetcher] = None, calendar_fn: Optional[CalendarFn] = None,
            symbols: Optional[Iterable[str]] = None, research_db: Optional[str] = None,
            rerank: bool = False, today: Optional[date] = None, workers: int = 3,
            max_sessions: Optional[int] = None) -> RefreshStats:
    """Fetch every (symbol, session) not yet in sessions_loaded for the last `years` years."""
    t0 = time.time()
    own = conn is None
    conn = conn or connect()
    fetcher = fetcher or alpaca_fetcher()
    calendar_fn = calendar_fn or alpaca_calendar
    today = today or datetime.now(ET).date()
    try:
        syms = list(symbols) if symbols is not None else ensure_universe(conn, research_db, rerank)
        start = (today - timedelta(days=int(round(365.25 * years)))).isoformat()
        end = (today - timedelta(days=1)).isoformat()      # finished sessions only
        dates = ensure_sessions(conn, start, end, calendar_fn)
        bounds = {d: (o, c) for d, o, c in conn.execute("SELECT date, open_utc, close_utc FROM sessions")}
        todo = sorted(missing_sessions(conn, syms, dates).items())
        if max_sessions is not None:
            todo = todo[:max_sessions]
        stats = RefreshStats(symbols=len(syms), sessions_in_range=len(dates))

        def job(item):
            d, miss = item
            o, c = bounds[d]
            end_incl = _iso_z(datetime.strptime(c, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                              - timedelta(seconds=1))
            return d, miss, fetcher(miss, o, end_incl)

        def handle(d, miss, got):
            o, c = bounds[d]
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            batch, marks = [], []
            for s in miss:
                rows = _rows_for(s, got.get(s) or [], o, c)
                batch.extend(rows)
                marks.append((s, d, len(rows), now))
            conn.executemany("INSERT OR REPLACE INTO minute_bars VALUES (?,?,?,?,?,?,?,?,?)", batch)
            conn.executemany("INSERT OR REPLACE INTO sessions_loaded VALUES (?,?,?,?)", marks)
            conn.commit()
            stats.rows_written += len(batch)
            stats.sessions_fetched += 1

        if workers <= 1:
            for item in todo:
                try:
                    handle(*job(item))
                except Exception as e:
                    log.error("session %s failed: %s", item[0], e)
                    stats.failed_sessions.append(item[0])
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {item[0]: ex.submit(job, item) for item in todo}
                for i, (d, fut) in enumerate(futs.items()):
                    try:
                        handle(*fut.result())
                    except Exception as e:
                        log.error("session %s failed: %s", d, e)
                        stats.failed_sessions.append(d)
                    if (i + 1) % 25 == 0:
                        log.info("%d/%d sessions, %d rows, %.0fs", i + 1, len(todo),
                                 stats.rows_written, time.time() - t0)
        stats.empty_symbols = [s for s in syms if not conn.execute(
            "SELECT 1 FROM sessions_loaded WHERE symbol=? AND n_bars > 0 LIMIT 1", (s,)).fetchone()]
    finally:
        if own:
            conn.close()
    stats.seconds = time.time() - t0
    return stats


def status(conn: sqlite3.Connection) -> Dict:
    nsess = conn.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM sessions").fetchone()
    per = pd.read_sql_query(
        """SELECT symbol, COUNT(*) AS sessions, SUM(n_bars) AS bars, MIN(date) AS first, MAX(date) AS last,
                  SUM(CASE WHEN n_bars = 0 THEN 1 ELSE 0 END) AS empty_sessions
           FROM sessions_loaded GROUP BY symbol ORDER BY symbol""", conn)
    rows = conn.execute("SELECT SUM(n_bars) FROM sessions_loaded").fetchone()[0] or 0
    return {"sessions_in_calendar": nsess[0], "calendar_first": nsess[1], "calendar_last": nsess[2],
            "symbols": len(per), "rows": int(rows), "per_symbol": per}


# --------------------------------------------------------------------------- read access

@dataclass
class SessionBars:
    """One symbol-session on a minute grid: index m = minutes since the session open.
    Missing minutes (no trades) are NaN prices with volume 0."""
    symbol: str
    date: str
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    vwap: np.ndarray
    flat_idx: int          # first minute at which everything must be flat (15:55 or close-5)

    @property
    def n(self) -> int:
        return len(self.open)


def flat_index(n_minutes: int, flat_before_close: int = 5) -> int:
    return max(0, n_minutes - flat_before_close)


_FIELDS = ("open", "high", "low", "close", "vwap")


def _grid(symbol: str, d: str, n: int, m: np.ndarray, cols: Dict[str, np.ndarray],
          flat_before_close: int) -> SessionBars:
    arr = {k: np.full(n, np.nan) for k in _FIELDS}
    vol = np.zeros(n)
    ok = (m >= 0) & (m < n)
    for k in _FIELDS:
        arr[k][m[ok]] = cols[k][ok]
    vol[m[ok]] = np.nan_to_num(cols["volume"][ok].astype(float))
    return SessionBars(symbol, d, arr["open"], arr["high"], arr["low"], arr["close"], vol,
                       arr["vwap"], flat_index(n, flat_before_close))


def _epoch(ts: pd.Series) -> np.ndarray:
    """Unix seconds (unit-safe: pandas may parse to ns or us resolution)."""
    t = pd.to_datetime(ts, utc=True)
    return ((t - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)).values.astype(np.int64)


def to_session_bars(symbol: str, d: str, open_utc: str, close_utc: str, df: pd.DataFrame,
                    flat_before_close: int = 5) -> SessionBars:
    """df: rows of one symbol-session with columns ts_utc (str), open..vwap, volume."""
    o, c = pd.Timestamp(open_utc), pd.Timestamp(close_utc)
    n = int((c - o).total_seconds() // 60)
    m = ((_epoch(df["ts_utc"]) - int(o.timestamp())) // 60).astype(int) if len(df) else np.zeros(0, int)
    cols = {k: df[k].values.astype(float) for k in _FIELDS + ("volume",)} if len(df) else \
        {k: np.zeros(0) for k in _FIELDS + ("volume",)}
    return _grid(symbol, d, n, m, cols, flat_before_close)


class IntradayStore:
    """Read access. `IntradayStore(path)` for tests, `IntradayStore()` for the default."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.conn = connect(self.db_path)
        self._bounds = {d: (o, c) for d, o, c in self.conn.execute(
            "SELECT date, open_utc, close_utc FROM sessions")}

    def universe(self) -> List[str]:
        u = [r[0] for r in self.conn.execute("SELECT symbol FROM intraday_universe ORDER BY rank, symbol")]
        return u or [r[0] for r in self.conn.execute("SELECT DISTINCT symbol FROM sessions_loaded ORDER BY symbol")]

    def sessions(self, start: Optional[str] = None, end: Optional[str] = None) -> List[str]:
        """Calendar sessions that have at least one loaded symbol."""
        q = "SELECT DISTINCT date FROM sessions_loaded WHERE n_bars > 0"
        p: list = []
        if start:
            q += " AND date >= ?"
            p.append(start)
        if end:
            q += " AND date <= ?"
            p.append(end)
        return [r[0] for r in self.conn.execute(q + " ORDER BY date", p)]

    def symbol_sessions(self, symbol: str, start: Optional[str] = None,
                        end: Optional[str] = None) -> Dict[str, SessionBars]:
        """{date: SessionBars} for every loaded session of `symbol` in [start, end]."""
        q = ("SELECT ts_utc, open, high, low, close, volume, vwap FROM minute_bars WHERE symbol=?")
        p: list = [symbol]
        if start:
            q += " AND ts_utc >= ?"
            p.append(start)
        if end:
            q += " AND ts_utc < ?"
            p.append((date.fromisoformat(end) + timedelta(days=1)).isoformat())
        df = pd.read_sql_query(q + " ORDER BY ts_utc", self.conn, params=p)
        out: Dict[str, SessionBars] = {}
        if df.empty:
            return out
        ep = _epoch(df["ts_utc"])
        day = df["ts_utc"].str.slice(0, 10).values   # regular session is within one UTC date
        cols = {k: df[k].values.astype(float) for k in _FIELDS + ("volume",)}
        cuts = np.flatnonzero(day[1:] != day[:-1]) + 1
        for lo, hi in zip(np.r_[0, cuts], np.r_[cuts, len(df)]):
            d = day[lo]
            if d not in self._bounds:
                continue
            o, c = self._bounds[d]
            o_ep, c_ep = int(pd.Timestamp(o).timestamp()), int(pd.Timestamp(c).timestamp())
            m = (ep[lo:hi] - o_ep) // 60
            out[d] = _grid(symbol, d, (c_ep - o_ep) // 60, m, {k: v[lo:hi] for k, v in cols.items()}, 5)
        return out

    def close(self) -> None:
        self.conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.intraday_bars")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh", help="load missing sessions (incremental, resumable)")
    r.add_argument("--years", type=float, default=2.0)
    r.add_argument("--db", default=None)
    r.add_argument("--research-db", default=None, help="read-only source for the liquidity ranking")
    r.add_argument("--rerank", action="store_true", help="recompute the frozen top-50 universe")
    r.add_argument("--workers", type=int, default=3)
    r.add_argument("--max-sessions", type=int, default=None)
    s = sub.add_parser("status")
    s.add_argument("--db", default=None)
    s.add_argument("--per-symbol", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    conn = connect(args.db)
    try:
        if args.cmd == "refresh":
            stats = refresh(args.years, conn=conn, research_db=args.research_db, rerank=args.rerank,
                            workers=args.workers, max_sessions=args.max_sessions)
            print(f"refresh: {stats}")
            if stats.failed_sessions:
                print(f"failed sessions (rerun refresh to retry): {' '.join(stats.failed_sessions)}")
            if stats.empty_symbols:
                print(f"symbols with no bars: {' '.join(stats.empty_symbols)}")
        st = status(conn)
    finally:
        conn.close()
    print(f"store: {st['rows']} rows, {st['symbols']} symbols, calendar {st['sessions_in_calendar']} "
          f"sessions {st['calendar_first']}..{st['calendar_last']}")
    if getattr(args, "per_symbol", False):
        print(st["per_symbol"].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
