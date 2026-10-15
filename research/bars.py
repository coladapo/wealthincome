"""Daily-bar store for research (data/research.db, never the live ledger).

Universe
--------
``ensure_universe()`` loads the CURRENT S&P 500 constituents (Wikipedia list,
falling back to the committed snapshot research/universe_sp500_snapshot.csv)
plus the SPY/QQQ/IWM ETFs.

SURVIVORSHIP BIAS: a current-constituent list only contains companies that
survived and grew into the index. Names that were dropped, went bankrupt or
were acquired between 2019 and today are missing, which flatters any long
edge (especially "buy the dip" edges: the dips that never recovered belong
disproportionately to the names that are no longer listed). Treat every base
rate from this store as an upper bound. A point-in-time historical-constituents
source is a required follow-up before any edge is trusted with money.

Bars
----
``refresh_bars(symbols, start)`` pulls yfinance daily OHLCV (auto_adjust=True,
i.e. split- and dividend-adjusted) into ``daily_bars``. It is incremental: for
symbols already stored it re-fetches only a short overlap window plus the new
dates. Because adjusted history is rewritten by yfinance after every split or
dividend, the overlap is compared with what is stored; if the stored closes no
longer match (an adjustment happened), that symbol's full history is reloaded so
the series never mixes two adjustment bases.

CLI (safe to run nightly):  venv/bin/python -m research.bars refresh
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

log = logging.getLogger("research.bars")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.environ.get(
    "RESEARCH_DB", os.path.join(REPO_ROOT, "data", "research.db")
)
SNAPSHOT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "universe_sp500_snapshot.csv")
WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
ETFS = [("SPY", "SPDR S&P 500 ETF", "ETF"), ("QQQ", "Invesco QQQ Trust", "ETF"),
        ("IWM", "iShares Russell 2000 ETF", "ETF")]
DEFAULT_START = "2019-01-01"
BATCH_SIZE = 50
OVERLAP_DAYS = 10          # calendar days re-fetched to detect re-adjustment
ADJ_TOLERANCE = 1e-3       # relative close mismatch that triggers a full reload

SCHEMA = """
CREATE TABLE IF NOT EXISTS universe (
    symbol   TEXT PRIMARY KEY,
    name     TEXT,
    sector   TEXT,
    added_at TEXT,
    source   TEXT
);
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS variant_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    edge_name   TEXT NOT NULL,
    run_at      TEXT NOT NULL,
    params_json TEXT,
    n_events    INTEGER,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_variant_runs_edge ON variant_runs(edge_name);
"""

# downloader(symbols, start "YYYY-MM-DD", end "YYYY-MM-DD" exclusive)
#   -> {symbol: DataFrame[Open, High, Low, Close, Volume] indexed by date}
Downloader = Callable[[List[str], str, str], Dict[str, pd.DataFrame]]


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    if os.path.basename(path) == "wealthincome.db":
        raise ValueError("research store must never point at the live ledger")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def to_yf_symbol(sym: str) -> str:
    """Wikipedia uses BRK.B; yfinance wants BRK-B."""
    return sym.strip().upper().replace(".", "-")


# --------------------------------------------------------------------------- universe

def _fetch_wikipedia_sp500() -> pd.DataFrame:
    import requests

    resp = requests.get(WIKI_SP500_URL, timeout=30,
                        headers={"User-Agent": "wealthincome-research/1.0 (daily-bar store)"})
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    t = next(tb for tb in tables if "Symbol" in tb.columns)
    df = pd.DataFrame({
        "symbol": t["Symbol"].map(to_yf_symbol),
        "name": t["Security"].astype(str),
        "sector": t["GICS Sector"].astype(str),
    })
    if len(df) < 400:
        raise ValueError(f"wikipedia list looks truncated ({len(df)} rows)")
    return df


def load_sp500_list(fetcher: Optional[Callable[[], pd.DataFrame]] = None):
    """Return (DataFrame[symbol,name,sector], source). Falls back to the snapshot."""
    try:
        return (fetcher or _fetch_wikipedia_sp500)(), "wikipedia_current"
    except Exception as e:  # network / layout change
        log.warning("wikipedia S&P 500 fetch failed (%s); using snapshot", e)
        return pd.read_csv(SNAPSHOT_CSV), "snapshot_csv"


def write_snapshot(df: pd.DataFrame, path: str = SNAPSHOT_CSV) -> None:
    df[["symbol", "name", "sector"]].sort_values("symbol").to_csv(path, index=False)


def ensure_universe(conn: Optional[sqlite3.Connection] = None,
                    fetcher: Optional[Callable[[], pd.DataFrame]] = None) -> List[str]:
    """Upsert the current S&P 500 + SPY/QQQ/IWM into `universe`; return all symbols."""
    own = conn is None
    conn = conn or connect()
    try:
        df, source = load_sp500_list(fetcher)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = [(r.symbol, r.name, r.sector, now, source) for r in df.itertuples()]
        rows += [(s, n, sec, now, "etf") for s, n, sec in ETFS]
        conn.executemany(
            """INSERT INTO universe(symbol, name, sector, added_at, source) VALUES (?,?,?,?,?)
               ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, sector=excluded.sector,
               source=excluded.source""",
            rows,
        )
        conn.commit()
        return [r[0] for r in conn.execute("SELECT symbol FROM universe ORDER BY symbol")]
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------------------- bars

def yfinance_downloader(symbols: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
    import yfinance as yf

    raw = yf.download(symbols, start=start, end=end, auto_adjust=True, group_by="ticker",
                      threads=True, progress=False)
    out: Dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0))
        for s in symbols:
            if s in level0:
                out[s] = raw[s]
    elif len(symbols) == 1:
        out[symbols[0]] = raw
    return out


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df = df.dropna(subset=["close"])
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df[~df.index.duplicated(keep="last")].sort_index()


def _download_with_retry(downloader: Downloader, symbols: List[str], start: str, end: str,
                         retries: int = 3, backoff: float = 2.0,
                         sleep: Callable[[float], None] = time.sleep) -> Dict[str, pd.DataFrame]:
    for attempt in range(retries + 1):
        try:
            return downloader(symbols, start, end)
        except Exception as e:
            if attempt == retries:
                log.error("download failed for %d symbols from %s: %s", len(symbols), start, e)
                return {}
            wait = backoff * (2 ** attempt)
            log.warning("download error (%s); retry %d in %.0fs", e, attempt + 1, wait)
            sleep(wait)
    return {}


def _last_dates(conn: sqlite3.Connection) -> Dict[str, str]:
    return dict(conn.execute("SELECT symbol, MAX(date) FROM daily_bars GROUP BY symbol"))


def _write(conn: sqlite3.Connection, symbol: str, df: pd.DataFrame, replace_all: bool) -> int:
    if replace_all:
        conn.execute("DELETE FROM daily_bars WHERE symbol=?", (symbol,))
    rows = [(symbol, d.strftime("%Y-%m-%d"), float(r.open), float(r.high), float(r.low),
             float(r.close), float(r.volume) if pd.notna(r.volume) else None)
            for d, r in df.iterrows()]
    conn.executemany(
        "INSERT OR REPLACE INTO daily_bars(symbol,date,open,high,low,close,volume) "
        "VALUES (?,?,?,?,?,?,?)", rows)
    return len(rows)


@dataclass
class RefreshStats:
    symbols: int = 0
    rows_written: int = 0
    full_reloads: int = 0
    failed: List[str] = None
    seconds: float = 0.0

    def __str__(self) -> str:
        return (f"symbols={self.symbols} rows_written={self.rows_written} "
                f"full_reloads={self.full_reloads} failed={len(self.failed or [])} "
                f"seconds={self.seconds:.1f}")


def refresh_bars(symbols: Iterable[str], start: str = DEFAULT_START, end: Optional[str] = None,
                 conn: Optional[sqlite3.Connection] = None,
                 downloader: Optional[Downloader] = None,
                 batch_size: int = BATCH_SIZE,
                 sleep: Callable[[float], None] = time.sleep,
                 pause: float = 1.0) -> RefreshStats:
    """Incrementally fill `daily_bars` for `symbols` from `start` up to `end` (exclusive)."""
    t0 = time.time()
    own = conn is None
    conn = conn or connect()
    downloader = downloader or yfinance_downloader
    end = end or (date.today() + timedelta(days=1)).isoformat()
    symbols = sorted({to_yf_symbol(s) for s in symbols})
    stats = RefreshStats(symbols=len(symbols), failed=[])
    try:
        last = _last_dates(conn)
        # Group symbols by fetch start so each batch shares one date range.
        groups: Dict[str, List[str]] = {}
        for s in symbols:
            if s in last and last[s] >= start:
                fs = (pd.Timestamp(last[s]) - timedelta(days=OVERLAP_DAYS)).strftime("%Y-%m-%d")
                fs = max(fs, start)
            else:
                fs = start
            if fs < end:
                groups.setdefault(fs, []).append(s)

        reload_full: List[str] = []
        for fs, syms in sorted(groups.items()):
            for i in range(0, len(syms), batch_size):
                batch = syms[i:i + batch_size]
                got = _download_with_retry(downloader, batch, fs, end, sleep=sleep)
                for s in batch:
                    df = got.get(s)
                    if df is None or df.dropna(how="all").empty:
                        if s not in last:
                            stats.failed.append(s)
                        continue
                    df = _clean(df)
                    if s in last and fs > start:
                        if _adjustment_changed(conn, s, df):
                            reload_full.append(s)
                            continue
                    stats.rows_written += _write(conn, s, df, replace_all=False)
                conn.commit()
                if pause:
                    sleep(pause)

        for i in range(0, len(reload_full), batch_size):
            batch = reload_full[i:i + batch_size]
            got = _download_with_retry(downloader, batch, start, end, sleep=sleep)
            for s in batch:
                df = got.get(s)
                if df is None or df.dropna(how="all").empty:
                    stats.failed.append(s)
                    continue
                stats.rows_written += _write(conn, s, _clean(df), replace_all=True)
                stats.full_reloads += 1
            conn.commit()
    finally:
        stats.seconds = time.time() - t0
        if own:
            conn.close()
    return stats


def _adjustment_changed(conn: sqlite3.Connection, symbol: str, fresh: pd.DataFrame) -> bool:
    """True when stored closes on overlapping dates disagree with freshly adjusted ones."""
    if fresh.empty:
        return False
    lo, hi = fresh.index.min().strftime("%Y-%m-%d"), fresh.index.max().strftime("%Y-%m-%d")
    stored = pd.read_sql_query(
        "SELECT date, close FROM daily_bars WHERE symbol=? AND date BETWEEN ? AND ?",
        conn, params=(symbol, lo, hi), parse_dates=["date"]).set_index("date")["close"]
    # The newest stored bar may have been a partial intraday bar; don't treat its revision
    # as a re-adjustment.
    common = stored.index.intersection(fresh.index)
    if len(stored):
        common = common[common < stored.index.max()]
    if len(common) == 0:
        return False
    a, b = stored.loc[common].values, fresh.loc[common, "close"].values
    return bool(np.any(np.abs(a / b - 1.0) > ADJ_TOLERANCE))


class BarStore:
    """Read access to the bar store. `BarStore(path)` for tests, `BarStore()` for the default."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._conn = connect(self.db_path)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def symbols(self, etfs: bool = True) -> List[str]:
        q = "SELECT DISTINCT symbol FROM daily_bars ORDER BY symbol"
        syms = [r[0] for r in self._conn.execute(q)]
        if not etfs:
            syms = [s for s in syms if s not in {e[0] for e in ETFS}]
        return syms

    def bars(self, symbol: str, start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
        q = "SELECT date, open, high, low, close, volume FROM daily_bars WHERE symbol=?"
        params: list = [symbol]
        if start:
            q += " AND date >= ?"
            params.append(str(pd.Timestamp(start).date()))
        if end:
            q += " AND date <= ?"
            params.append(str(pd.Timestamp(end).date()))
        df = pd.read_sql_query(q + " ORDER BY date", self._conn, params=params, parse_dates=["date"])
        return df.set_index("date")

    def row_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]

    def close(self) -> None:
        self._conn.close()


def bars(symbol: str, start: Optional[str] = None, end: Optional[str] = None,
         db_path: Optional[str] = None) -> pd.DataFrame:
    store = BarStore(db_path)
    try:
        return store.bars(symbol, start, end)
    finally:
        store.close()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.bars")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh", help="update universe + bars (incremental, nightly-safe)")
    r.add_argument("--start", default=DEFAULT_START)
    r.add_argument("--db", default=None)
    r.add_argument("--symbols", nargs="*", help="override the universe")
    sub.add_parser("snapshot", help="regenerate research/universe_sp500_snapshot.csv")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    if args.cmd == "snapshot":
        df = _fetch_wikipedia_sp500()
        write_snapshot(df)
        print(f"wrote {len(df)} symbols to {SNAPSHOT_CSV}")
        return 0

    conn = connect(args.db)
    try:
        syms = args.symbols or ensure_universe(conn)
        stats = refresh_bars(syms, start=args.start, conn=conn)
        total = conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
        nsym = conn.execute("SELECT COUNT(DISTINCT symbol) FROM daily_bars").fetchone()[0]
    finally:
        conn.close()
    print(f"refresh: {stats}")
    print(f"store: {total} rows, {nsym} symbols")
    if stats.failed:
        print(f"failed symbols: {' '.join(stats.failed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
