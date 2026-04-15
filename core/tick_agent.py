"""
Tick Agent — 1-minute bar collector + VWAP computation.

Runs as a background loop during market hours (or can be called on-demand).
Stores intraday snapshots in tick_snapshots table.
Provides VWAP-based signal block for Claude's trading prompt.

Why VWAP matters:
  - Institutional algos benchmark execution against VWAP.
  - Price above VWAP = buyers in control, momentum favors long.
  - Price below VWAP = sellers in control, avoid new longs.
  - Used as intraday entry timing: same stock, better price if near VWAP vs 2% above.

Data source: Alpaca free-tier /v2/stocks/{symbol}/bars with timeframe=1Min
"""

import os
import json
import time
import logging
import sqlite3
from datetime import datetime, date
from typing import Dict, List, Optional, Any

import requests

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("WEALTHINCOME_DB", "data/wealthincome.db")

ALPACA_API_KEY    = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_PAPER      = os.environ.get("ALPACA_PAPER", "true").lower() != "false"

DATA_BASE_URL = "https://data.alpaca.markets/v2"
PAPER_BASE_URL = "https://paper-api.alpaca.markets/v2"
LIVE_BASE_URL  = "https://api.alpaca.markets/v2"

_HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_table():
    """Create tick_snapshots table if it doesn't exist."""
    conn = _get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tick_snapshots (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol                  TEXT NOT NULL,
                snapshot_at             TEXT NOT NULL,
                date                    TEXT NOT NULL,
                last_price              REAL,
                bid                     REAL,
                ask                     REAL,
                bid_ask_spread_pct      REAL,
                intraday_high           REAL,
                intraday_low            REAL,
                intraday_open           REAL,
                vwap                    REAL,
                distance_from_vwap_pct  REAL,
                vwap_signal             TEXT,
                bars_used               INTEGER,
                cumulative_volume       INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_tick_symbol_date ON tick_snapshots(symbol, date);
            CREATE INDEX IF NOT EXISTS idx_tick_at ON tick_snapshots(snapshot_at);
        """)
        conn.commit()
    finally:
        conn.close()


def _save_snapshot(row: Dict):
    conn = _get_conn()
    try:
        conn.execute("""
            INSERT INTO tick_snapshots
              (symbol, snapshot_at, date, last_price, bid, ask, bid_ask_spread_pct,
               intraday_high, intraday_low, intraday_open, vwap,
               distance_from_vwap_pct, vwap_signal, bars_used, cumulative_volume)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            row["symbol"], row["snapshot_at"], row["date"],
            row.get("last_price"), row.get("bid"), row.get("ask"),
            row.get("bid_ask_spread_pct"),
            row.get("intraday_high"), row.get("intraday_low"), row.get("intraday_open"),
            row.get("vwap"), row.get("distance_from_vwap_pct"), row.get("vwap_signal"),
            row.get("bars_used"), row.get("cumulative_volume"),
        ))
        conn.commit()
    finally:
        conn.close()


def get_latest_snapshots(symbols: List[str]) -> Dict[str, Dict]:
    """
    Return most recent tick snapshot per symbol.
    Returns {} for any symbol with no data today.
    """
    if not symbols:
        return {}
    today = date.today().isoformat()
    conn = _get_conn()
    try:
        result = {}
        for sym in symbols:
            row = conn.execute("""
                SELECT * FROM tick_snapshots
                WHERE symbol=? AND date=?
                ORDER BY snapshot_at DESC LIMIT 1
            """, (sym, today)).fetchone()
            if row:
                result[sym] = dict(row)
        return result
    finally:
        conn.close()


# ─── Alpaca data helpers ──────────────────────────────────────────────────────

def _is_market_open() -> bool:
    base = PAPER_BASE_URL if ALPACA_PAPER else LIVE_BASE_URL
    try:
        resp = requests.get(f"{base}/clock", headers=_HEADERS, timeout=8)
        resp.raise_for_status()
        return resp.json().get("is_open", False)
    except Exception:
        return False


def _get_intraday_bars(symbol: str, limit: int = 390, for_date: str = None) -> List[Dict]:
    """
    Fetch up to `limit` 1-minute bars for `for_date` (default: today).
    Alpaca free tier provides delayed data (15 min) but it's fine for
    intraday VWAP — we care about the pattern, not the last tick.
    """
    from datetime import timedelta
    target = for_date or date.today().isoformat()
    url = f"{DATA_BASE_URL}/stocks/{symbol}/bars"
    params = {
        "timeframe": "1Min",
        "start": f"{target}T09:30:00-04:00",
        "end":   f"{target}T20:00:00-04:00",
        "limit": limit,
        "feed": "iex",   # IEX is free tier; omit for paid SIP data
    }
    try:
        resp = requests.get(url, headers=_HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("bars", [])
    except Exception as e:
        logger.debug(f"Intraday bars failed for {symbol}: {e}")
        return []


def _get_latest_quote(symbol: str) -> Optional[Dict]:
    url = f"{DATA_BASE_URL}/stocks/{symbol}/quotes/latest"
    try:
        resp = requests.get(url, headers=_HEADERS, params={"feed": "iex"}, timeout=8)
        resp.raise_for_status()
        return resp.json().get("quote")
    except Exception:
        return None


# ─── VWAP computation ─────────────────────────────────────────────────────────

def compute_vwap(bars: List[Dict]) -> Optional[float]:
    """
    True VWAP = sum(typical_price * volume) / sum(volume)
    typical_price = (high + low + close) / 3
    Resets each trading day (bars should be for current day only).
    """
    if not bars:
        return None
    cum_pv = 0.0
    cum_v  = 0
    for bar in bars:
        h = float(bar.get("h", 0))
        l = float(bar.get("l", 0))
        c = float(bar.get("c", 0))
        v = int(bar.get("v", 0))
        if v > 0:
            typical = (h + l + c) / 3.0
            cum_pv += typical * v
            cum_v  += v
    if cum_v == 0:
        return None
    return cum_pv / cum_v


def _vwap_signal(distance_pct: float) -> str:
    """
    Classify position relative to VWAP into a trading signal.
    - above_vwap_strong:  > +1.5%  — well above, momentum confirmed
    - above_vwap:         0 to +1.5% — favorable for long entries
    - at_vwap:            ±0.5%   — institutions near fair value, neutral
    - below_vwap:         -1.5% to 0 — caution for new longs
    - below_vwap_strong:  < -1.5%  — sellers in control, avoid longs
    """
    if distance_pct > 1.5:
        return "above_vwap_strong"
    elif distance_pct > 0.3:
        return "above_vwap"
    elif distance_pct >= -0.3:
        return "at_vwap"
    elif distance_pct >= -1.5:
        return "below_vwap"
    else:
        return "below_vwap_strong"


# ─── Core: snapshot a single symbol ──────────────────────────────────────────

def snapshot_symbol(symbol: str, for_date: str = None) -> Optional[Dict]:
    """
    Fetch 1-min bars + latest quote for symbol, compute VWAP, save to DB.
    for_date defaults to today; pass a YYYY-MM-DD string to backfill a prior day.
    Returns the snapshot dict or None on failure.
    """
    bars = _get_intraday_bars(symbol, for_date=for_date)
    if not bars:
        return None

    quote = _get_latest_quote(symbol)

    # Price from latest bar close (or quote if available)
    last_bar = bars[-1]
    last_price = float(last_bar.get("c", 0))
    if quote:
        bid = float(quote.get("bp", 0))
        ask = float(quote.get("ap", 0))
        mid = (bid + ask) / 2 if bid and ask else last_price
        last_price = mid if mid else last_price
        spread_pct = ((ask - bid) / bid * 100) if bid else 0.0
    else:
        bid = ask = None
        spread_pct = None

    # OHLC for the day
    daily_high  = max(float(b.get("h", 0)) for b in bars)
    daily_low   = min(float(b.get("l", float("inf"))) for b in bars)
    daily_open  = float(bars[0].get("o", 0))
    cum_volume  = sum(int(b.get("v", 0)) for b in bars)

    vwap = compute_vwap(bars)
    dist_pct = ((last_price - vwap) / vwap * 100) if vwap else None
    signal   = _vwap_signal(dist_pct) if dist_pct is not None else "unknown"

    now = datetime.now()
    snap = {
        "symbol":                symbol,
        "snapshot_at":           now.isoformat(),
        "date":                  now.date().isoformat(),
        "last_price":            round(last_price, 4),
        "bid":                   round(bid, 4) if bid else None,
        "ask":                   round(ask, 4) if ask else None,
        "bid_ask_spread_pct":    round(spread_pct, 4) if spread_pct is not None else None,
        "intraday_high":         round(daily_high, 4),
        "intraday_low":          round(daily_low, 4),
        "intraday_open":         round(daily_open, 4),
        "vwap":                  round(vwap, 4) if vwap else None,
        "distance_from_vwap_pct": round(dist_pct, 4) if dist_pct is not None else None,
        "vwap_signal":           signal,
        "bars_used":             len(bars),
        "cumulative_volume":     cum_volume,
    }

    _save_snapshot(snap)
    vwap_str = f"{vwap:.2f}" if vwap else "n/a"
    dist_str = f"{dist_pct:+.2f}%" if dist_pct is not None else "n/a"
    logger.debug(
        f"Tick [{symbol}] price={snap['last_price']:.2f} vwap={vwap_str} dist={dist_str} signal={signal}"
    )
    return snap


def snapshot_symbols(symbols: List[str], for_date: str = None) -> Dict[str, Dict]:
    """Snapshot a list of symbols. Returns {symbol: snap_dict}."""
    result = {}
    for sym in symbols:
        snap = snapshot_symbol(sym, for_date=for_date)
        if snap:
            result[sym] = snap
    return result


# ─── Claude prompt block ──────────────────────────────────────────────────────

def build_vwap_block_for_claude(
    snapshots: Dict[str, Dict],
    positions: Optional[List[Dict]] = None,
) -> str:
    """
    Build a compact VWAP context block for Claude's trading prompt.
    Only includes symbols where the signal is actionable (not at_vwap / unknown).
    Highlights open positions where price is below VWAP (exit risk).
    """
    if not snapshots:
        return ""

    held_symbols = {p.get("symbol") for p in (positions or [])}

    lines = []
    warnings = []
    bullish = []

    for sym, snap in sorted(snapshots.items()):
        sig  = snap.get("vwap_signal", "unknown")
        dist = snap.get("distance_from_vwap_pct")
        vwap = snap.get("vwap")
        price = snap.get("last_price")
        if dist is None or vwap is None:
            continue

        dist_str = f"{dist:+.1f}%"
        is_held = sym in held_symbols

        if sig == "above_vwap_strong":
            bullish.append(f"  + {sym}: {dist_str} above VWAP ${vwap:.2f} — strong institutional buying")
        elif sig == "above_vwap":
            bullish.append(f"  + {sym}: {dist_str} above VWAP ${vwap:.2f} — favorable for entry")
        elif sig == "below_vwap" and is_held:
            warnings.append(f"  ⚠ {sym} (HELD): {dist_str} below VWAP ${vwap:.2f} — selling pressure, monitor exit")
        elif sig == "below_vwap_strong" and is_held:
            warnings.append(f"  ⚠ {sym} (HELD): {dist_str} below VWAP ${vwap:.2f} — sellers in control, consider exit")
        elif sig == "below_vwap_strong":
            lines.append(f"  - {sym}: {dist_str} below VWAP ${vwap:.2f} — avoid new longs today")
        elif sig == "below_vwap":
            lines.append(f"  - {sym}: {dist_str} below VWAP ${vwap:.2f} — suboptimal entry timing")

    if not (lines or warnings or bullish):
        return ""

    output = ["=== VWAP (Intraday Institutional Benchmark) ==="]
    if warnings:
        output.extend(warnings)
    if bullish:
        output.extend(bullish)
    if lines:
        output.extend(lines)
    output.append("RULE: Prefer entries within 1.5% of VWAP. Flag held positions below VWAP for review.")
    return "\n".join(output)


# ─── Background loop ──────────────────────────────────────────────────────────

def run_tick_loop(
    symbols: List[str],
    interval_seconds: int = 60,
    market_only: bool = True,
):
    """
    Background loop: snapshot all symbols every `interval_seconds`.
    Designed to run as a thread or subprocess alongside the main trader.
    """
    ensure_table()
    logger.info(f"Tick agent started — {len(symbols)} symbols, {interval_seconds}s interval")

    while True:
        try:
            if market_only and not _is_market_open():
                logger.debug("Market closed — tick agent sleeping 5m")
                time.sleep(300)
                continue

            snaps = snapshot_symbols(symbols)
            logger.info(f"Tick snapshot: {len(snaps)}/{len(symbols)} symbols captured")

        except Exception as e:
            logger.error(f"Tick loop error: {e}")

        time.sleep(interval_seconds)


# ─── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")

    # Re-read env keys (useful when running directly)
    _HEADERS["APCA-API-KEY-ID"]     = os.environ.get("ALPACA_API_KEY", "")
    _HEADERS["APCA-API-SECRET-KEY"] = os.environ.get("ALPACA_SECRET_KEY", "")

    symbols = (sys.argv[1:] if len(sys.argv) > 1
               else ["AAPL", "MSFT", "NVDA", "SPY", "QQQ"])

    print(f"\nTick Agent Live Test — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Symbols: {', '.join(symbols)}\n")

    ensure_table()

    # Try today first; fall back to yesterday if market hasn't opened
    from datetime import timedelta
    test_date = None
    snaps = snapshot_symbols(symbols)
    if not snaps:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        # Skip weekends
        d = date.today() - timedelta(days=1)
        while d.weekday() >= 5:  # 5=Sat 6=Sun
            d -= timedelta(days=1)
        yesterday = d.isoformat()
        print(f"[Market closed — testing with last trading day: {yesterday}]")
        test_date = yesterday
        snaps = snapshot_symbols(symbols, for_date=yesterday)

    if not snaps:
        print("No snapshots returned — check API keys.")
        sys.exit(0)

    # Print table
    print(f"{'Symbol':<8} {'Price':>8} {'VWAP':>8} {'Dist':>7} {'Signal':<20} {'Vol':>10}")
    print("-" * 65)
    for sym, s in sorted(snaps.items()):
        price = s.get("last_price") or 0
        vwap  = s.get("vwap") or 0
        dist  = s.get("distance_from_vwap_pct") or 0
        sig   = s.get("vwap_signal", "unknown")
        vol   = s.get("cumulative_volume") or 0
        bars  = s.get("bars_used", 0)
        print(f"{sym:<8} {price:>8.2f} {vwap:>8.2f} {dist:>+6.2f}% {sig:<20} {vol:>10,}  ({bars} bars)")

    # Show Claude block
    print("\n--- Claude VWAP Block ---")
    block = build_vwap_block_for_claude(snaps, positions=[{"symbol": "AAPL"}])
    print(block if block else "(no actionable signals)")

    # Verify DB
    print("\n--- DB Verification ---")
    conn = _get_conn()
    today = date.today().isoformat()
    count = conn.execute(
        "SELECT COUNT(*) FROM tick_snapshots WHERE date=?", (today,)
    ).fetchone()[0]
    conn.close()
    print(f"Rows saved today: {count}")

    print("\nPASS — tick agent working.")
