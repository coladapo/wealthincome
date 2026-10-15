"""The incumbent champion: the live Claude picker, replayed from its logged decisions.

Reads the live ledger data/wealthincome.db READ-ONLY (sqlite URI mode=ro; override
the path with the LIVE_DB environment variable) and never writes to it.

Timestamps: ai_decisions.decided_at, position_lifecycle.closed_at and
equity_snapshots.snapshot_at are naive LOCAL times of the trading host
(America/Los_Angeles): cycles cluster 06:30-13:00, i.e. US market hours in Pacific
time. (The prompt text labels the same clock "ET", which is a mislabel.) They are
converted to US/Eastern before the as-of cutoff is applied.

source="decisions" (default) -- the replay the plan asks for
  Every parsed decision in ai_decisions with decided_at <= the as-of cutoff (close
  of as_of, 16:00 ET) is applied in order to a target book:
    buy   weight += position_size_pct  (missing -> default_size; 0 -> ignored;
          > 1 is read as a percent). A buy of a 9th name while the book already
          holds 8 is dropped (the live chassis rejects it). The simulator clips to 8%.
    sell  reduce_pct in (0,1) -> weight *= (1 - reduce_pct); otherwise exit.
    hold / skip -> no change.
  honor_chassis_exits=True also applies the live chassis's own non-LLM exits from
  position_lifecycle (sma50_breach, position_closed, sell_filled) as exits.
  The 12% trailing stop is NOT replayed from the record; the simulator applies its
  own. reenter_after_stop=False: a name the simulator stopped out is not re-bought
  until the picker's book drops it and a new buy arrives.

source="holdings" -- a fidelity check, not the champion's rule
  The actual broker positions logged with each cycle (positions_snapshot_json /
  account_snapshot_json of the last cycle <= the cutoff) as weights, so the
  simulator holds what the live account held, one session late and under the caps.

Known fidelity gaps (decisions mode): live orders filled intraday at the decision
time, the replay fills at the NEXT session's open; buys the live validator,
calendar block, PDT guard or a broker rejection stopped are replayed anyway (only
the simulator's caps / loss limit can reject them); live position sizes were not
always capped at 8% early on; partial fills and the ledger gaps are not modelled.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from research.strategy import Strategy

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOST_TZ = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
CHASSIS_EXIT_REASONS = ("sma50_breach", "position_closed", "sell_filled")
MAX_BOOK_NAMES = 8


def live_db_path() -> str:
    return os.environ.get("LIVE_DB", os.path.join(REPO_ROOT, "data", "wealthincome.db"))


def connect_live_ro(path: Optional[str] = None) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path or live_db_path()}?mode=ro", uri=True, timeout=30)


def host_to_et(ts: str) -> pd.Timestamp:
    t = datetime.fromisoformat(str(ts)[:26])
    return pd.Timestamp(t.replace(tzinfo=HOST_TZ).astimezone(ET))


def load_decision_events(conn: sqlite3.Connection, honor_chassis_exits: bool = True) -> List[Tuple]:
    """Chronological (ts_et, seq, symbol, action, size, reduce_pct, origin) events."""
    ev = []
    for rid, decided_at, pj in conn.execute(
            "SELECT id, decided_at, parsed_decisions_json FROM ai_decisions "
            "WHERE parsed_decisions_json IS NOT NULL ORDER BY decided_at, id"):
        try:
            decs = json.loads(pj or "[]")
        except (TypeError, ValueError):
            continue
        if isinstance(decs, dict):
            decs = decs.get("decisions", [])
        ts = host_to_et(decided_at)
        for k, d in enumerate(decs or []):
            if not isinstance(d, dict):
                continue
            act = str(d.get("action", "")).lower()
            sym = str(d.get("symbol", "")).upper().replace(".", "-")
            if act in ("buy", "sell") and sym:
                ev.append((ts, rid * 1000 + k, sym, act, d.get("position_size_pct"),
                           d.get("reduce_pct"), "ai"))
    if honor_chassis_exits:
        q = (f"SELECT id, symbol, closed_at FROM position_lifecycle WHERE closed_at IS NOT NULL "
             f"AND close_reason IN ({','.join('?' * len(CHASSIS_EXIT_REASONS))})")
        for pid, sym, closed_at in conn.execute(q, CHASSIS_EXIT_REASONS):
            ev.append((host_to_et(closed_at), 10**12 + pid, sym.upper().replace(".", "-"),
                       "sell", None, None, "chassis"))
    ev.sort(key=lambda e: (e[0], e[1]))
    return ev


def load_holdings_snapshots(conn: sqlite3.Connection) -> List[Tuple[pd.Timestamp, Dict[str, float], float]]:
    out = []
    for decided_at, pj, aj in conn.execute(
            "SELECT decided_at, positions_snapshot_json, account_snapshot_json FROM ai_decisions "
            "ORDER BY decided_at, id"):
        try:
            pos = json.loads(pj or "{}").get("positions", [])
            pv = float(json.loads(aj or "{}").get("portfolio_value") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if pv <= 0:
            continue
        book = {str(p["symbol"]).upper().replace(".", "-"): float(p.get("market_value") or 0) / pv
                for p in pos if float(p.get("qty") or 0) > 0}
        qty = {str(p["symbol"]).upper().replace(".", "-"): float(p.get("qty") or 0) for p in pos}
        out.append((host_to_et(decided_at), book, qty))
    return out


def load_real_snapshots(path: Optional[str] = None) -> pd.DataFrame:
    """Last equity snapshot of each ET date: equity, invested (long market value), positions."""
    conn = connect_live_ro(path)
    try:
        df = pd.read_sql_query("SELECT snapshot_at, portfolio_value, long_market_value, open_positions "
                               "FROM equity_snapshots ORDER BY snapshot_at, id", conn)
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame(columns=["equity", "invested", "n_positions"])
    df["date"] = [pd.Timestamp(host_to_et(s).date()) for s in df["snapshot_at"]]
    g = df.groupby("date").last()
    out = pd.DataFrame({"equity": g["portfolio_value"].astype(float),
                        "invested": g["long_market_value"].astype(float),
                        "n_positions": g["open_positions"]})
    out.index.name = "date"
    return out


def load_real_equity(path: Optional[str] = None) -> pd.Series:
    """The champion's REAL paper equity: last equity snapshot of each ET trading date."""
    return load_real_snapshots(path)["equity"].rename("equity")


class ChampionReplay(Strategy):
    """The live picker's parsed buy/sell decisions replayed as target weights."""

    id = "champion_replay"
    default_params = {"source": "decisions", "honor_chassis_exits": True, "default_size": 0.08}
    rebalance = "drift"
    reenter_after_stop = False

    def __init__(self, params=None, db_path: Optional[str] = None):
        super().__init__(params)
        if self.params["source"] not in ("decisions", "holdings"):
            raise ValueError("source must be 'decisions' or 'holdings'")
        self.db_path = db_path
        self._events = None
        self._snaps = None
        self._reset()
        self.missing_symbols: Dict[str, int] = {}

    def with_params(self, params):
        if not params:
            return self
        return type(self)({**self.params, **params}, db_path=self.db_path)

    def _reset(self):
        self._i = 0
        self._book: Dict[str, float] = {}
        self._last_cut = None
        self._held_qty: Dict[str, float] = {}
        self._held_w: Dict[str, float] = {}
        self.rejected_full_book = 0

    def _load(self):
        conn = connect_live_ro(self.db_path)
        try:
            if self.params["source"] == "decisions":
                self._events = load_decision_events(conn, bool(self.params["honor_chassis_exits"]))
            else:
                self._snaps = load_holdings_snapshots(conn)
        finally:
            conn.close()

    def first_decision_date(self):
        if self._events is None and self._snaps is None:
            self._load()
        seq = self._events if self.params["source"] == "decisions" else self._snaps
        return seq[0][0].date() if seq else None

    def _apply(self, e):
        _, _, sym, act, size, reduce_pct, _ = e
        if act == "buy":
            try:
                size = float(size) if size is not None else float(self.params["default_size"])
            except (TypeError, ValueError):
                size = float(self.params["default_size"])
            if size > 1:
                size /= 100.0
            if size > 0:
                if sym not in self._book and len(self._book) >= MAX_BOOK_NAMES:
                    self.rejected_full_book += 1   # live chassis rejects a 9th name
                    return
                self._book[sym] = self._book.get(sym, 0.0) + size
        else:
            try:
                r = float(reduce_pct) if reduce_pct is not None else None
            except (TypeError, ValueError):
                r = None
            if r is not None and 0 < r < 1 and sym in self._book:
                self._book[sym] *= (1 - r)
            else:
                self._book.pop(sym, None)

    def target_weights(self, as_of, data):
        if self._events is None and self._snaps is None:
            self._load()
        cut = pd.Timestamp(data.earnings_cutoff)   # close of as_of (16:00 ET), never after `now`
        if self._last_cut is not None and cut < self._last_cut:
            self._reset()
        self._last_cut = cut
        if self.params["source"] == "decisions":
            while self._i < len(self._events) and self._events[self._i][0] <= cut:
                self._apply(self._events[self._i])
                self._i += 1
            book = dict(self._book)
        else:
            snap = None
            while self._i < len(self._snaps) and self._snaps[self._i][0] <= cut:
                snap = self._snaps[self._i]
                self._i += 1
            if snap is not None:
                _, w, qty = snap
                new_w = {}
                for s, q in qty.items():
                    if q <= 0:
                        continue
                    # weight only moves when the share count changed (no daily drift churn)
                    if s in self._held_qty and abs(self._held_qty[s] - q) < 1e-9:
                        new_w[s] = self._held_w[s]
                    else:
                        new_w[s] = w.get(s, 0.0)
                self._held_qty = {s: q for s, q in qty.items() if q > 0}
                self._held_w = new_w
            book = dict(self._held_w)
        out = {}
        for s, w in book.items():
            if w <= 0:
                continue
            if data.close(s) is None:
                self.missing_symbols[s] = self.missing_symbols.get(s, 0) + 1
                continue
            out[s] = w
        return out
