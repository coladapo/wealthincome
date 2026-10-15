"""Shadow runner: a daily, prospective, virtual track record for every challenger.

Nothing here places an order, talks to a broker, or touches the live trader.

What it tracks (each a frozen registry (id, version)):
  * every strategy with registry status ``shadow`` (the challengers) and ``champion``
    (so a promoted strategy keeps a record the demotion rules can read);
  * the reference strategies in REFERENCE_IDS (SPY held under the caps / uncapped),
    whatever their status (except retired) -- comparison series only, never
    promotable (research.promotion and the promote CLI refuse them).

Daily timeline (run after the close; launchd: weekdays 13:30 PT):
  1. refresh the bar store (research.bars), unless --no-refresh;
  2. as_of = the latest COMPLETED session in the bar store (SPY calendar; a bar
     dated today counts only once the clock is past 16:00 ET);
  3. for each tracked strategy and each session d after its last recorded day,
     up to as_of, run the simulator (research.portfolio_sim.simulate) for exactly
     that one session on the saved state:
        open of d   the targets stored at the close of the previous session are
                    filled VIRTUALLY at d's open under the simulator's own rules
                    (caps, 10 bp/side costs, daily loss limit, sells first);
        close of d  mark to close, 12% trailing stop on close;
        close of d  d's targets are computed from AsOfData(d) and stored as
                    PENDING -- they settle at the next session's open, on the next run.
     So today's run settles yesterday's targets at today's open, then queues
     today's targets. Missed days are caught up in order on the next run.
  4. benchmark_equity: SPY's daily close (series "SPY"; the first recorded value is
     kept -- frozen, see the dividend note) and the champion's REAL paper equity
     (series "champion", read-only from data/wealthincome.db) on the same dates;
  5. research.promotion: every challenger is evaluated for a promotion PROPOSAL and
     the registry champion (if any) for automatic demotion.

A strategy's first recorded day is the day the runner first sees it tracked: it
starts with $100,000 cash, nothing is filled that day (no pending targets yet), and
its first targets are queued. Idempotent per (strategy, version, date): a recorded
day is never recomputed; re-running the same day is a no-op.

A registered version whose source no longer hashes to that version (the file was
edited) is NOT run (status ``version_mismatch``): a record belongs to a frozen rule.

Tables (data/research.db):
  shadow_runs(run_at, as_of, strategy_id, version, status, detail)
  shadow_equity(strategy_id, version, date, equity, cash, invested_pct, daily_return, costs)
  shadow_positions(strategy_id, version, date, symbol, qty, avg_price)   end-of-day book
  shadow_fills(strategy_id, version, date, symbol, side, qty, price, notional, cost, reason)
  shadow_pending_targets(strategy_id, version, as_of, symbol, weight, settled_on)
  shadow_state(strategy_id, version, date, state_json)                   simulator state
  benchmark_equity(series, date, equity)

Caveats (stated, not fixed):
  * Bars are yfinance auto-adjusted. When a dividend re-adjusts history, a held position
    simply marks at the new (ex-dividend) close: shadow records are PRICE returns,
    dividends are not credited. The SPY benchmark is frozen at the close as first
    recorded for the same reason (price return, consistent with the shadows). The
    champion's real equity DOES include dividends received -- a small bias in its favour.
  * A close recorded at 13:30 PT is yfinance's end-of-day value at that time; a later
    revision is not picked up for days already recorded.

CLI:
  venv/bin/python -m research.shadow run [--no-refresh] [--no-post]
  venv/bin/python -m research.shadow status
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, time, timezone
from typing import Callable, Dict, List, Optional

import pandas as pd

from research import strategy as registry
from research.asof import ET, MarketData
from research.bars import DEFAULT_DB_PATH, REPO_ROOT, connect
from research.portfolio_sim import Position, SimState, simulate

log = logging.getLogger("research.shadow")

START_EQUITY = 100_000.0
REFERENCE_IDS = ("spy_hold_capped", "spy_hold_uncapped")
TRACKED_STATUSES = ("shadow", "champion")
SPY_SERIES = "SPY"
CHAMPION_SERIES = "champion"

SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_runs (
    run_at      TEXT NOT NULL,
    as_of       TEXT,
    strategy_id TEXT NOT NULL,
    version     TEXT NOT NULL,
    status      TEXT NOT NULL,
    detail      TEXT
);
CREATE TABLE IF NOT EXISTS shadow_equity (
    strategy_id  TEXT NOT NULL,
    version      TEXT NOT NULL,
    date         TEXT NOT NULL,
    equity       REAL NOT NULL,
    cash         REAL NOT NULL,
    invested_pct REAL NOT NULL,
    daily_return REAL NOT NULL,
    costs        REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (strategy_id, version, date)
);
CREATE TABLE IF NOT EXISTS shadow_positions (
    strategy_id TEXT NOT NULL,
    version     TEXT NOT NULL,
    date        TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    qty         REAL NOT NULL,
    avg_price   REAL NOT NULL,
    PRIMARY KEY (strategy_id, version, date, symbol)
);
CREATE TABLE IF NOT EXISTS shadow_fills (
    strategy_id TEXT NOT NULL,
    version     TEXT NOT NULL,
    date        TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    qty         REAL NOT NULL,
    price       REAL NOT NULL,
    notional    REAL NOT NULL,
    cost        REAL NOT NULL,
    reason      TEXT
);
CREATE TABLE IF NOT EXISTS shadow_pending_targets (
    strategy_id TEXT NOT NULL,
    version     TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    weight      REAL NOT NULL,
    settled_on  TEXT,
    PRIMARY KEY (strategy_id, version, as_of, symbol)
);
CREATE TABLE IF NOT EXISTS shadow_state (
    strategy_id TEXT NOT NULL,
    version     TEXT NOT NULL,
    date        TEXT NOT NULL,
    state_json  TEXT NOT NULL,
    PRIMARY KEY (strategy_id, version, date)
);
CREATE TABLE IF NOT EXISTS benchmark_equity (
    series TEXT NOT NULL,
    date   TEXT NOT NULL,
    equity REAL NOT NULL,
    PRIMARY KEY (series, date)
);
CREATE INDEX IF NOT EXISTS idx_shadow_fills ON shadow_fills(strategy_id, version, date);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    registry.ensure_schema(conn)
    conn.executescript(SCHEMA)
    conn.commit()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- state (de)serialisation

def state_to_json(st: SimState, avg: Dict[str, float]) -> str:
    return json.dumps({"cash": st.cash, "last_equity": st.last_equity,
                       "positions": {s: asdict(p) for s, p in st.positions.items()},
                       "prev_targets": st.prev_targets, "pending": st.pending,
                       "stopped": st.stopped, "avg_price": avg}, sort_keys=True)


def state_from_json(text: str):
    d = json.loads(text)
    st = SimState(cash=float(d["cash"]), last_equity=float(d["last_equity"]),
                  positions={s: Position(**p) for s, p in d["positions"].items()},
                  prev_targets={s: float(v) for s, v in d["prev_targets"].items()},
                  pending=None if d["pending"] is None else {s: float(v) for s, v in d["pending"].items()},
                  stopped=dict(d["stopped"]))
    return st, {s: float(v) for s, v in d.get("avg_price", {}).items()}


# --------------------------------------------------------------------------- what to track

def tracked_rows(conn: sqlite3.Connection) -> List[Dict]:
    """Registry rows the runner records: shadow + champion statuses, plus the reference series."""
    return [r for r in registry.list_strategies(conn=conn)
            if r["status"] in TRACKED_STATUSES or (r["id"] in REFERENCE_IDS and r["status"] != "retired")]


def load_strategy(row: Dict):
    """Instantiate a registered (id, params) from its source file; None if it can't be found."""
    path = os.path.abspath(row.get("source_path") or "")
    rel = os.path.relpath(path, REPO_ROOT)
    if rel.startswith("..") or not rel.endswith(".py"):
        # registered from another checkout of the repo: resolve by the package-relative path
        marker = os.sep + "research" + os.sep
        if marker not in path:
            return None
        rel = "research" + os.sep + path.rsplit(marker, 1)[1]
    mod = importlib.import_module(rel[:-3].replace(os.sep, "."))
    for obj in vars(mod).values():
        if (isinstance(obj, type) and issubclass(obj, registry.Strategy)
                and getattr(obj, "id", None) == row["id"]):
            return obj(row["params"])
    return None


# --------------------------------------------------------------------------- calendar

def latest_complete_session(market: MarketData, now: Optional[datetime] = None) -> Optional[pd.Timestamp]:
    now = now or datetime.now(ET)
    now = now.replace(tzinfo=ET) if now.tzinfo is None else now.astimezone(ET)
    td = market.trading_days()
    td = td[td <= pd.Timestamp(now.date())]
    if len(td) and td[-1] == pd.Timestamp(now.date()) and now.time() < time(16, 0):
        td = td[:-1]
    return td[-1] if len(td) else None


# --------------------------------------------------------------------------- one strategy

def last_day(conn, sid, ver) -> Optional[str]:
    r = conn.execute("SELECT MAX(date) FROM shadow_equity WHERE strategy_id=? AND version=?",
                     (sid, ver)).fetchone()
    return r[0] if r else None


def _load_state(conn, sid, ver, day):
    r = conn.execute("SELECT state_json FROM shadow_state WHERE strategy_id=? AND version=? AND date=?",
                     (sid, ver, day)).fetchone()
    if r is None:
        raise RuntimeError(f"{sid} {ver[:12]}: equity recorded for {day} but no saved state")
    return state_from_json(r[0])


def advance(conn: sqlite3.Connection, strat, version: str, market: MarketData,
            as_of: pd.Timestamp, now: Optional[datetime] = None) -> List[str]:
    """Record every session after the strategy's last recorded day, up to as_of. Idempotent.

    Returns the dates recorded by this call (empty when already up to date)."""
    sid = strat.id
    last = last_day(conn, sid, version)
    if last is None:
        st, avg = SimState(cash=START_EQUITY, last_equity=START_EQUITY), {}
        days = [as_of]                       # enters shadow today: $100k, nothing to settle
    else:
        st, avg = _load_state(conn, sid, version, last)
        td = market.trading_days()
        days = list(td[(td > pd.Timestamp(last)) & (td <= as_of)])
    recorded = []
    for d in days:
        dstr = d.strftime("%Y-%m-%d")
        had_pending = st.pending is not None
        res = simulate(strat, d, d, market, state=st, now=now)
        if len(res.daily) != 1:
            raise RuntimeError(f"{sid}: simulator produced {len(res.daily)} rows for {dstr}")
        row = res.daily.iloc[0]
        held_before = {s: p.shares for s, p in st.positions.items()}
        st = res.state
        qty = dict(held_before)
        for t in res.trades.itertuples(index=False):
            old = qty.get(t.symbol, 0.0)
            if t.side == "buy":
                prev = avg.get(t.symbol, t.price) if old > 1e-12 else t.price
                avg[t.symbol] = (old * prev + t.shares * t.price) / (old + t.shares)
                qty[t.symbol] = old + t.shares
            else:
                qty[t.symbol] = max(old - t.shares, 0.0)
                if qty[t.symbol] <= 1e-12:
                    avg.pop(t.symbol, None)
        avg = {s: v for s, v in avg.items() if s in st.positions}
        with conn:
            conn.execute("INSERT INTO shadow_equity(strategy_id, version, date, equity, cash, invested_pct, "
                         "daily_return, costs) VALUES (?,?,?,?,?,?,?,?)",
                         (sid, version, dstr, float(row["equity"]), float(row["cash"]),
                          float(row["exposure"]), float(row["ret"]), float(row["costs"])))
            conn.executemany("INSERT INTO shadow_fills(strategy_id, version, date, symbol, side, qty, price, "
                             "notional, cost, reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             [(sid, version, t.date, t.symbol, t.side, float(t.shares), float(t.price),
                               float(t.notional), float(t.cost), t.reason)
                              for t in res.trades.itertuples(index=False)])
            conn.executemany("INSERT INTO shadow_positions(strategy_id, version, date, symbol, qty, avg_price) "
                             "VALUES (?,?,?,?,?,?)",
                             [(sid, version, dstr, s, float(p.shares), float(avg.get(s, p.last_price)))
                              for s, p in sorted(st.positions.items())])
            if had_pending:
                conn.execute("UPDATE shadow_pending_targets SET settled_on=? WHERE strategy_id=? AND "
                             "version=? AND settled_on IS NULL AND as_of<?", (dstr, sid, version, dstr))
            conn.executemany("INSERT INTO shadow_pending_targets(strategy_id, version, as_of, symbol, weight) "
                             "VALUES (?,?,?,?,?)",
                             [(sid, version, dstr, s, float(w)) for s, w in sorted((st.pending or {}).items())])
            conn.execute("INSERT INTO shadow_state(strategy_id, version, date, state_json) VALUES (?,?,?,?)",
                         (sid, version, dstr, state_to_json(st, avg)))
        recorded.append(dstr)
    return recorded


# --------------------------------------------------------------------------- benchmarks

def record_benchmarks(conn: sqlite3.Connection, market: MarketData, as_of: pd.Timestamp,
                      champion: Optional[pd.Series] = None) -> Dict[str, int]:
    """SPY close (first value kept) and the champion's real equity on every session from the
    first shadow day up to as_of. Returns rows written per series."""
    r = conn.execute("SELECT MIN(date) FROM shadow_equity").fetchone()
    first = pd.Timestamp(r[0]) if r and r[0] else as_of
    spy = market.frame("SPY")["close"]
    spy = spy[(spy.index >= first) & (spy.index <= as_of)]
    n = {SPY_SERIES: 0, CHAMPION_SERIES: 0}
    with conn:
        for d, v in spy.items():
            cur = conn.execute("INSERT OR IGNORE INTO benchmark_equity(series, date, equity) VALUES (?,?,?)",
                               (SPY_SERIES, d.strftime("%Y-%m-%d"), float(v)))
            n[SPY_SERIES] += cur.rowcount
        if champion is not None:
            c = champion.dropna()
            c.index = pd.DatetimeIndex(pd.to_datetime(c.index)).normalize()
            c = c[(c.index >= first) & (c.index <= as_of)]
            for d, v in c.items():
                conn.execute("INSERT OR REPLACE INTO benchmark_equity(series, date, equity) VALUES (?,?,?)",
                             (CHAMPION_SERIES, d.strftime("%Y-%m-%d"), float(v)))
                n[CHAMPION_SERIES] += 1
    return n


def live_champion_equity() -> Optional[pd.Series]:
    """The champion's real paper equity (read-only from the live ledger), or None."""
    from research.pipeline import champion_equity
    return champion_equity()


# --------------------------------------------------------------------------- the daily run

def run(conn: Optional[sqlite3.Connection] = None, market: Optional[MarketData] = None,
        now: Optional[datetime] = None, champion="auto",
        resolve: Callable[[Dict], object] = load_strategy, evaluate: bool = True,
        post: bool = True, db_path: Optional[str] = None, poster=None,
        out_dir: Optional[str] = None) -> Dict:
    """Advance every tracked strategy to the latest completed session; record benchmarks;
    evaluate promotion / demotion."""
    own_conn = conn is None
    conn = conn or connect(db_path)
    ensure_schema(conn)
    own_market = market is None
    market = market or MarketData(db_path or DEFAULT_DB_PATH)
    run_at = _now_utc()
    out: Dict = {"run_at": run_at, "as_of": None, "strategies": [], "benchmarks": {}}
    try:
        as_of = latest_complete_session(market, now)
        if as_of is None:
            raise RuntimeError("no completed session in the bar store")
        out["as_of"] = as_of.strftime("%Y-%m-%d")
        for row in tracked_rows(conn):
            sid, ver = row["id"], row["version"]
            try:
                strat = resolve(row)
                if strat is None:
                    status, detail = "not_found", f"no Strategy with id {sid} in {row.get('source_path')}"
                elif strat.version != ver:
                    status, detail = "version_mismatch", (f"source now hashes to {strat.version[:12]}; "
                                                         "the registered version is frozen, not run")
                else:
                    had = last_day(conn, sid, ver)
                    rec = advance(conn, strat, ver, market, as_of, now=now)
                    status = "up_to_date" if not rec else ("started" if had is None else "settled")
                    detail = ",".join(rec) if rec else None
            except Exception as e:  # one broken strategy must not stop the others
                status, detail = "error", f"{type(e).__name__}: {e}"
                log.exception("shadow %s %s failed", sid, ver[:12])
            with conn:
                conn.execute("INSERT INTO shadow_runs(run_at, as_of, strategy_id, version, status, detail) "
                             "VALUES (?,?,?,?,?,?)", (run_at, out["as_of"], sid, ver, status, detail))
            out["strategies"].append({"id": sid, "version": ver, "status": status, "detail": detail})
        champ = live_champion_equity() if isinstance(champion, str) and champion == "auto" else champion
        out["benchmarks"] = record_benchmarks(conn, market, as_of, champ)
        if evaluate:
            from research import promotion
            out["promotion"] = promotion.evaluate_all(conn=conn, post=post, poster=poster,
                                                      out_dir=out_dir, today=as_of)
        return out
    finally:
        if own_market:
            market.close()
        if own_conn:
            conn.close()


def refresh_bars_now() -> None:
    """The nightly bar refresh (same as `python -m research.bars refresh`)."""
    from research.bars import ensure_universe, refresh_bars
    conn = connect()
    try:
        print(f"bars refresh: {refresh_bars(ensure_universe(conn), conn=conn)}", flush=True)
    finally:
        conn.close()


def status_table(conn: sqlite3.Connection) -> str:
    ensure_schema(conn)
    lines = ["strategy                 version       days  first       last          equity  invested"]
    for sid, ver, n, first, last in conn.execute(
            "SELECT strategy_id, version, COUNT(*), MIN(date), MAX(date) FROM shadow_equity "
            "GROUP BY strategy_id, version ORDER BY strategy_id, version").fetchall():
        eq, inv = conn.execute("SELECT equity, invested_pct FROM shadow_equity WHERE strategy_id=? AND "
                               "version=? AND date=?", (sid, ver, last)).fetchone()
        lines.append(f"{sid:24s} {ver[:12]}  {n:4d}  {first}  {last}  {eq:12,.2f}  {inv:7.1%}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.shadow")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="daily shadow run (after the close)")
    r.add_argument("--no-refresh", action="store_true", help="skip the bar refresh")
    r.add_argument("--no-post", action="store_true", help="do not post promotion/demotion lines")
    sub.add_parser("status", help="one line per tracked series")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    if args.cmd == "status":
        conn = connect()
        try:
            print(status_table(conn))
        finally:
            conn.close()
        return 0
    print(f"{datetime.now().isoformat(timespec='seconds')} shadow run starting", flush=True)
    if not args.no_refresh:
        try:
            refresh_bars_now()
        except Exception as e:   # stale bars only mean no new session is recorded today
            print(f"bars refresh failed: {e}", file=sys.stderr)
    out = run(post=not args.no_post)
    print(f"as_of {out['as_of']}")
    for s in out["strategies"]:
        print(f"  {s['id']:24s} {s['version'][:12]}  {s['status']:16s} {s['detail'] or ''}")
    print(f"  benchmarks written: {out['benchmarks']}")
    promo = out.get("promotion") or {}
    for p in promo.get("proposals", []):
        print(f"  PROMOTION PROPOSAL: {p}")
    for d in promo.get("demotions", []):
        print(f"  DEMOTION: {d}")
    print(f"{datetime.now().isoformat(timespec='seconds')} shadow run done", flush=True)
    return 1 if any(s["status"] == "error" for s in out["strategies"]) else 0


if __name__ == "__main__":
    sys.exit(main())
