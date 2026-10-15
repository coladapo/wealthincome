"""Earnings-announcement history for research (data/research.db, never the live ledger).

Source: yfinance ``Ticker.get_earnings_dates(limit=N)`` (Yahoo Finance earnings
calendar). Probed 2026-10-14: for AAPL/JPM/NVDA/KO/BRK-B it returns ~50 quarterly
rows back to 2014 with columns ``EPS Estimate``, ``Reported EPS``, ``Surprise(%)``
and a timezone-aware timestamp whose clock time encodes the session:
  before 09:30 ET  -> bmo (before market open)
  at/after 16:00   -> amc (after market close)
  anything else    -> unknown (Yahoo uses odd placeholder hours, e.g. 12:00/15:00)
The upcoming (not yet reported) row has NaN reported EPS and is dropped.

Limitations (read before trusting results):
  * EPS figures are Yahoo's adjusted/"street" EPS, and the estimate is the consensus
    Yahoo has stored for that quarter -- not a guaranteed point-in-time snapshot.
  * Revenue surprise and guidance are not available, only the EPS surprise.
  * Timing comes from the stored timestamp; occasional wrong hours are possible.

Table ``earnings_events(symbol, report_date, timing, eps_estimate, eps_actual,
surprise_pct, source)``; ``earnings_fetch`` records per-symbol fetch status so a
refresh is resumable (symbols fetched OK within ``max_age_days`` are skipped).

CLI:  venv/bin/python -m research.earnings refresh [--symbols ...] [--max-age-days 7]
      venv/bin/python -m research.earnings coverage
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional

import pandas as pd

from research.bars import ETFS, connect, to_yf_symbol

log = logging.getLogger("research.earnings")

SOURCE = "yfinance_earnings_dates"
DEFAULT_LIMIT = 60          # quarters requested per symbol (~15 years)

SCHEMA = """
CREATE TABLE IF NOT EXISTS earnings_events (
    symbol       TEXT NOT NULL,
    report_date  TEXT NOT NULL,
    timing       TEXT NOT NULL,
    eps_estimate REAL,
    eps_actual   REAL,
    surprise_pct REAL,
    source       TEXT,
    PRIMARY KEY (symbol, report_date)
);
CREATE TABLE IF NOT EXISTS earnings_fetch (
    symbol     TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    n_rows     INTEGER,
    status     TEXT
);
"""

# fetcher(symbol) -> DataFrame indexed by tz-aware timestamp with the yfinance columns
Fetcher = Callable[[str], Optional[pd.DataFrame]]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def classify_timing(ts: pd.Timestamp) -> str:
    """bmo / amc / unknown from the announcement's clock time (US/Eastern)."""
    ts = pd.Timestamp(ts)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("America/New_York")
    minutes = ts.hour * 60 + ts.minute
    if minutes < 9 * 60 + 30:
        return "bmo"
    if minutes >= 16 * 60:
        return "amc"
    return "unknown"


def yfinance_fetcher(symbol: str, limit: int = DEFAULT_LIMIT) -> Optional[pd.DataFrame]:
    import yfinance as yf

    return yf.Ticker(symbol).get_earnings_dates(limit=limit)


COLUMNS = ["symbol", "report_date", "timing", "eps_estimate", "eps_actual", "surprise_pct", "source"]


def normalize(symbol: str, raw: Optional[pd.DataFrame]) -> pd.DataFrame:
    """yfinance frame -> rows for earnings_events (reported quarters only)."""
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=COLUMNS)
    idx = pd.DatetimeIndex(raw.index)
    idx = idx.tz_localize("America/New_York") if idx.tz is None else idx.tz_convert("America/New_York")

    def col(name):
        if name not in raw.columns:
            return [float("nan")] * len(raw)
        return pd.to_numeric(raw[name], errors="coerce").values

    out = pd.DataFrame({
        "symbol": symbol,
        "report_date": idx.strftime("%Y-%m-%d"),
        "timing": [classify_timing(t) for t in idx],
        "eps_estimate": col("EPS Estimate"),
        "eps_actual": col("Reported EPS"),
        "surprise_pct": col("Surprise(%)"),
        "source": SOURCE,
    })
    out = out[out["eps_actual"].notna()]
    # one row per (symbol, date); yfinance lists newest first, keep that row
    return out.drop_duplicates(subset=["symbol", "report_date"], keep="first").reset_index(drop=True)[COLUMNS]


def _fetch_with_retry(fetcher: Fetcher, symbol: str, retries: int = 3, backoff: float = 2.0,
                      sleep: Callable[[float], None] = time.sleep) -> Optional[pd.DataFrame]:
    for attempt in range(retries + 1):
        try:
            return fetcher(symbol)
        except Exception as e:
            if attempt == retries:
                raise
            wait = backoff * (2 ** attempt)
            log.warning("%s: earnings fetch error (%s); retry %d in %.0fs", symbol, e, attempt + 1, wait)
            sleep(wait)
    return None


def refresh_earnings(symbols: Iterable[str], conn: Optional[sqlite3.Connection] = None,
                     fetcher: Optional[Fetcher] = None, max_age_days: float = 7,
                     pause: float = 0.5, sleep: Callable[[float], None] = time.sleep) -> Dict[str, int]:
    """Fetch + upsert earnings per symbol; skip symbols fetched OK within max_age_days."""
    own = conn is None
    conn = conn or connect()
    fetcher = fetcher or yfinance_fetcher
    ensure_schema(conn)
    stats = {"symbols": 0, "fetched": 0, "skipped_fresh": 0, "failed": 0, "rows": 0}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat(timespec="seconds")
    fresh = {r[0] for r in conn.execute(
        "SELECT symbol FROM earnings_fetch WHERE status='ok' AND fetched_at >= ?", (cutoff,))}
    try:
        for sym in sorted({to_yf_symbol(s) for s in symbols}):
            stats["symbols"] += 1
            if sym in fresh:
                stats["skipped_fresh"] += 1
                continue
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                rows = normalize(sym, _fetch_with_retry(fetcher, sym, sleep=sleep))
                status = "ok"
            except Exception as e:
                log.error("%s: earnings fetch failed: %s", sym, e)
                rows, status = None, f"error: {str(e)[:200]}"
            if rows is not None and len(rows):
                conn.executemany(
                    "INSERT OR REPLACE INTO earnings_events(symbol, report_date, timing, eps_estimate, "
                    "eps_actual, surprise_pct, source) VALUES (?,?,?,?,?,?,?)",
                    [tuple(None if (isinstance(v, float) and pd.isna(v)) else v for v in r)
                     for r in rows.itertuples(index=False)])
            n = 0 if rows is None else len(rows)
            conn.execute("INSERT OR REPLACE INTO earnings_fetch(symbol, fetched_at, n_rows, status) "
                         "VALUES (?,?,?,?)", (sym, now, n, status))
            conn.commit()
            if status == "ok":
                stats["fetched"] += 1
                stats["rows"] += n
            else:
                stats["failed"] += 1
            if pause:
                sleep(pause)
    finally:
        if own:
            conn.close()
    return stats


def load_earnings(conn: Optional[sqlite3.Connection] = None, start: Optional[str] = None,
                  end: Optional[str] = None, db_path: Optional[str] = None) -> pd.DataFrame:
    own = conn is None
    conn = conn or connect(db_path)
    try:
        ensure_schema(conn)
        q = "SELECT * FROM earnings_events WHERE 1=1"
        params: list = []
        if start:
            q += " AND report_date >= ?"
            params.append(start)
        if end:
            q += " AND report_date <= ?"
            params.append(end)
        return pd.read_sql_query(q + " ORDER BY report_date, symbol", conn, params=params)
    finally:
        if own:
            conn.close()


def coverage(conn: Optional[sqlite3.Connection] = None, since: str = "2019-01-01",
             min_events: int = 20) -> Dict:
    own = conn is None
    conn = conn or connect()
    try:
        ensure_schema(conn)
        ev = load_earnings(conn, start=since)
        fetch = pd.read_sql_query("SELECT * FROM earnings_fetch", conn)
        per = ev.groupby("symbol").size()
        return {
            "since": since,
            "symbols_fetched": int(len(fetch)),
            "symbols_fetch_failed": int((fetch["status"] != "ok").sum()) if len(fetch) else 0,
            "failed_symbols": sorted(fetch.loc[fetch["status"] != "ok", "symbol"]) if len(fetch) else [],
            "symbols_with_events": int(len(per)),
            f"symbols_with_ge_{min_events}_events": int((per >= min_events).sum()),
            "events": int(len(ev)),
            "events_with_surprise": int(ev["surprise_pct"].notna().sum()),
            "timing_counts": {k: int(v) for k, v in ev["timing"].value_counts().items()},
            "first_date": ev["report_date"].min() if len(ev) else None,
            "last_date": ev["report_date"].max() if len(ev) else None,
        }
    finally:
        if own:
            conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.earnings")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh", help="fetch earnings history for the stored universe (resumable)")
    r.add_argument("--db", default=None)
    r.add_argument("--symbols", nargs="*")
    r.add_argument("--max-age-days", type=float, default=7)
    c = sub.add_parser("coverage")
    c.add_argument("--db", default=None)
    c.add_argument("--since", default="2019-01-01")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    conn = connect(args.db)
    try:
        if args.cmd == "refresh":
            syms = args.symbols
            if not syms:
                etfs = {e[0] for e in ETFS}
                syms = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM daily_bars")
                        if r[0] not in etfs]
            print(f"refresh: {refresh_earnings(syms, conn=conn, max_age_days=args.max_age_days)}")
        print(f"coverage: {coverage(conn, since=getattr(args, 'since', '2019-01-01'))}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
