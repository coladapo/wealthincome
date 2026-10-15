"""
Ledger reconciliation — rebuild realized P&L from the broker's own fills.

position_lifecycle.realized_pnl only prices the FINAL exit fill
((exit - entry) * exit_qty), so partial exits, fills at prices other than the
signal price, and broker-side exits are lost or mispriced. This module pulls
every FILL activity from Alpaca (read-only, full history, paginated), rebuilds
per-symbol FIFO lots, and writes:

  broker_fills   one row per broker fill (activity id is the key)
  fifo_realized  one row per (closing fill, opening lot) match with its P&L

position_lifecycle is never modified. reconciliation_status() is a DB-only
read for the real-money gate.
"""

import json
import sqlite3
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

STARTING_CAPITAL = 100_000.0
EPS = 1e-9

_DDL = """
CREATE TABLE IF NOT EXISTS broker_fills (
    id                TEXT PRIMARY KEY,      -- Alpaca activity id
    order_id          TEXT,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL,
    qty               REAL NOT NULL,
    price             REAL NOT NULL,
    transaction_time  TEXT NOT NULL,         -- UTC ISO from Alpaca
    fill_type         TEXT,                  -- fill | partial_fill
    cum_qty           REAL,
    leaves_qty        REAL,
    order_status      TEXT,
    raw_json          TEXT,
    fetched_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_broker_fills_symbol ON broker_fills(symbol, transaction_time);
CREATE INDEX IF NOT EXISTS idx_broker_fills_order ON broker_fills(order_id);

CREATE TABLE IF NOT EXISTS fifo_realized (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT NOT NULL,
    close_fill_id    TEXT NOT NULL REFERENCES broker_fills(id),
    open_fill_id     TEXT NOT NULL REFERENCES broker_fills(id),
    direction        TEXT NOT NULL,          -- long | short
    qty              REAL NOT NULL,
    open_price       REAL NOT NULL,
    close_price      REAL NOT NULL,
    realized_pnl     REAL NOT NULL,
    open_time        TEXT NOT NULL,
    close_time       TEXT NOT NULL,
    lifecycle_id     INTEGER,                -- best-effort attribution, NULL if none
    computed_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fifo_symbol ON fifo_realized(symbol, close_time);
CREATE INDEX IF NOT EXISTS idx_fifo_close_fill ON fifo_realized(close_fill_id);
"""


def _db_path(db_path: Optional[str]) -> str:
    if db_path:
        return db_path
    from backend import db as _db
    return _db.DB_PATH


def _connect(db_path: Optional[str]) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_tables(conn: sqlite3.Connection):
    conn.executescript(_DDL)


def _utc(ts: Optional[str]) -> Optional[datetime]:
    """Alpaca times are UTC (Z); ledger times are naive local — convert both to aware UTC."""
    if not ts:
        return None
    t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.astimezone()  # naive = this machine's local time (how db.py writes it)
    return t.astimezone(timezone.utc)


# ─── Broker pull (read-only) ─────────────────────────────────────────────────

def fetch_activities(alpaca) -> Tuple[List[Dict], List[Dict]]:
    """(fills, other_activities) for the account's full history, oldest first."""
    acts = alpaca.iter_account_activities()
    fills = [a for a in acts if a.get("activity_type") == "FILL"]
    other = [a for a in acts if a.get("activity_type") != "FILL"]
    return fills, other


def store_fills(conn: sqlite3.Connection, fills: List[Dict], fetched_at: str):
    conn.executemany(
        """INSERT INTO broker_fills(id, order_id, symbol, side, qty, price, transaction_time,
               fill_type, cum_qty, leaves_qty, order_status, raw_json, fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET fetched_at=excluded.fetched_at""",
        [(
            f["id"], f.get("order_id"), f["symbol"], f["side"], float(f["qty"]), float(f["price"]),
            f["transaction_time"], f.get("type"), float(f.get("cum_qty") or 0),
            float(f.get("leaves_qty") or 0), f.get("order_status"), json.dumps(f), fetched_at,
        ) for f in fills],
    )


# ─── FIFO rebuild ────────────────────────────────────────────────────────────

def rebuild_fifo(fills: List[Dict]) -> Tuple[List[Dict], Dict[str, List[Dict]]]:
    """Match fills FIFO per symbol. Returns (matches, open_lots_by_symbol).

    A buy first covers any open short lots, a sell first closes open long lots;
    whatever is left opens a new lot. Each match is one closing-fill/lot pair.
    """
    ordered = sorted(fills, key=lambda f: (_utc(f["transaction_time"]), f["id"]))
    lots: Dict[str, deque] = defaultdict(deque)   # symbol -> deque of open lots (signed qty)
    matches: List[Dict] = []
    for f in ordered:
        sym = f["symbol"].upper()
        side = f["side"].lower()
        sign = 1.0 if side == "buy" else -1.0     # sell_short counts as a sell
        remaining = float(f["qty"])
        price = float(f["price"])
        book = lots[sym]
        while remaining > EPS and book and (book[0]["qty"] * sign) < 0:
            lot = book[0]
            take = min(remaining, abs(lot["qty"]))
            direction = "long" if lot["qty"] > 0 else "short"
            pnl = (price - lot["price"]) * take if direction == "long" else (lot["price"] - price) * take
            matches.append({
                "symbol": sym, "close_fill_id": f["id"], "open_fill_id": lot["fill_id"],
                "direction": direction, "qty": take, "open_price": lot["price"],
                "close_price": price, "realized_pnl": pnl,
                "open_time": lot["time"], "close_time": f["transaction_time"],
            })
            lot["qty"] -= take * (1 if lot["qty"] > 0 else -1)
            remaining -= take
            if abs(lot["qty"]) <= EPS:
                book.popleft()
        if remaining > EPS:
            book.append({"qty": sign * remaining, "price": price,
                         "fill_id": f["id"], "time": f["transaction_time"]})
    open_lots = {s: [dict(l) for l in b] for s, b in lots.items() if b}
    return matches, open_lots


def attribute_to_lifecycle(matches: List[Dict], lifecycle_rows: List[Dict],
                           before_s: int = 120, after_s: int = 900) -> None:
    """Best-effort: tag each match with the lifecycle row whose open window
    [opened_at - before_s, closed_at + after_s] contains the closing fill, picking
    the most recently opened row on overlap. Sets match['lifecycle_id'] (or None)."""
    by_sym: Dict[str, List[Dict]] = defaultdict(list)
    now = datetime.now(timezone.utc)
    for r in lifecycle_rows:
        by_sym[r["symbol"].upper()].append({
            "id": r["id"], "opened": _utc(r["opened_at"]),
            "closed": _utc(r["closed_at"]) if r.get("closed_at") else now,
        })
    for m in matches:
        t = _utc(m["close_time"])
        best = None
        for r in by_sym.get(m["symbol"], []):
            if (r["opened"] - t).total_seconds() <= before_s and (t - r["closed"]).total_seconds() <= after_s:
                if best is None or r["opened"] > best["opened"]:
                    best = r
        m["lifecycle_id"] = best["id"] if best else None


def write_fifo(conn: sqlite3.Connection, matches: List[Dict], computed_at: str):
    """fifo_realized is fully derived — replace it in one transaction."""
    conn.execute("DELETE FROM fifo_realized")
    conn.executemany(
        """INSERT INTO fifo_realized(symbol, close_fill_id, open_fill_id, direction, qty,
               open_price, close_price, realized_pnl, open_time, close_time, lifecycle_id, computed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(m["symbol"], m["close_fill_id"], m["open_fill_id"], m["direction"], m["qty"],
          m["open_price"], m["close_price"], m["realized_pnl"], m["open_time"], m["close_time"],
          m.get("lifecycle_id"), computed_at) for m in matches],
    )


# ─── Report ──────────────────────────────────────────────────────────────────

def build_report(conn: sqlite3.Connection, matches: List[Dict], open_lots: Dict[str, List[Dict]],
                 other_activities: List[Dict], account: Dict, positions: List, as_of: str) -> Dict:
    lifecycle = [dict(r) for r in conn.execute("SELECT * FROM position_lifecycle ORDER BY id")]
    closed = [r for r in lifecycle if r["status"] == "closed"]
    lifecycle_realized = sum(r["realized_pnl"] or 0 for r in closed)
    broker_realized = sum(m["realized_pnl"] for m in matches)

    # cash activities that move equity without being a fill
    cash_by_type: Dict[str, float] = defaultdict(float)
    for a in other_activities:
        cash_by_type[a.get("activity_type", "?")] += float(a.get("net_amount") or 0)
    deposits = sum(v for k, v in cash_by_type.items() if k in ("JNLC", "JNLS", "CSD", "CSW", "ACATC", "ACATS"))
    non_deposit_cash = sum(v for k, v in cash_by_type.items() if k not in ("JNLC", "JNLS", "CSD", "CSW", "ACATC", "ACATS"))

    equity = float(account.get("equity") or 0)
    alpaca_unrealized = sum(float(p.unrealized_pl or 0) for p in positions)
    prices = {p.symbol.upper(): float(p.current_price or 0) for p in positions}
    fifo_unrealized = 0.0
    for sym, lots in open_lots.items():
        px = prices.get(sym)
        if px:
            fifo_unrealized += sum(l["qty"] * (px - l["price"]) for l in lots)
    lot_qty = {s: sum(l["qty"] for l in ls) for s, ls in open_lots.items()}
    held_qty = {p.symbol.upper(): float(p.qty) for p in positions}
    qty_mismatch = {s: (lot_qty.get(s, 0.0), held_qty.get(s, 0.0))
                    for s in set(lot_qty) | set(held_qty)
                    if abs(lot_qty.get(s, 0.0) - held_qty.get(s, 0.0)) > EPS}

    equity_gap = equity - STARTING_CAPITAL - alpaca_unrealized
    # identity: equity - deposits = fifo_realized + fifo_unrealized + fees/dividends/other cash
    explained_total = broker_realized + fifo_unrealized + non_deposit_cash + (deposits - STARTING_CAPITAL)
    residual = (equity - STARTING_CAPITAL) - explained_total

    # per-lifecycle-row truth from attributed fills
    fills_pnl_by_row: Dict[int, float] = defaultdict(float)
    fills_qty_by_row: Dict[int, float] = defaultdict(float)
    for m in matches:
        if m.get("lifecycle_id") is not None:
            fills_pnl_by_row[m["lifecycle_id"]] += m["realized_pnl"]
            fills_qty_by_row[m["lifecycle_id"]] += m["qty"]
    unattributed = sum(m["realized_pnl"] for m in matches if m.get("lifecycle_id") is None)

    partial_rows = []
    for r in closed:
        if r["exit_qty"] is not None and r["exit_qty"] < r["entry_qty"] - EPS:
            fp = fills_pnl_by_row.get(r["id"], 0.0)
            partial_rows.append({
                "id": r["id"], "symbol": r["symbol"], "entry_qty": r["entry_qty"],
                "exit_qty": r["exit_qty"], "fills_closed_qty": round(fills_qty_by_row.get(r["id"], 0.0), 4),
                "lifecycle_pnl": round(r["realized_pnl"] or 0, 2), "fills_pnl": round(fp, 2),
                "missing_pnl": round(fp - (r["realized_pnl"] or 0), 2),
                "unrecorded_qty": r["entry_qty"] - r["exit_qty"],
            })
    full_rows_diff = sum(fills_pnl_by_row.get(r["id"], 0.0) - (r["realized_pnl"] or 0)
                         for r in closed
                         if not (r["exit_qty"] is not None and r["exit_qty"] < r["entry_qty"] - EPS))
    phantom_rows = [r for r in closed if r["id"] not in fills_qty_by_row]
    open_rows_fill_pnl = sum(fills_pnl_by_row.get(r["id"], 0.0) for r in lifecycle if r["status"] != "closed")

    # per-symbol
    broker_sym: Dict[str, float] = defaultdict(float)
    for m in matches:
        broker_sym[m["symbol"]] += m["realized_pnl"]
    life_sym: Dict[str, float] = defaultdict(float)
    for r in closed:
        life_sym[r["symbol"].upper()] += r["realized_pnl"] or 0
    per_symbol = sorted(
        ({"symbol": s, "broker": round(broker_sym.get(s, 0.0), 2), "lifecycle": round(life_sym.get(s, 0.0), 2),
          "diff": round(broker_sym.get(s, 0.0) - life_sym.get(s, 0.0), 2)}
         for s in set(broker_sym) | set(life_sym)),
        key=lambda x: -abs(x["diff"]),
    )

    # trades-table health (issue 9)
    t_unlinked = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE lower(action)='sell' AND position_lifecycle_id IS NULL").fetchone()[0]
    t_neg = conn.execute(
        "SELECT COUNT(*) FROM (SELECT symbol, SUM(CASE WHEN lower(action)='buy' THEN qty ELSE -qty END) n"
        " FROM trades GROUP BY symbol) WHERE n < 0").fetchone()[0]

    return {
        "as_of": as_of,
        "fill_count": None,  # set by caller
        "matches": len(matches),
        "equity": round(equity, 2),
        "deposits": round(deposits, 2),
        "cash_by_type": {k: round(v, 2) for k, v in cash_by_type.items()},
        "alpaca_unrealized": round(alpaca_unrealized, 2),
        "fifo_unrealized": round(fifo_unrealized, 2),
        "equity_gap": round(equity_gap, 2),
        "broker_realized": round(broker_realized, 2),
        "lifecycle_realized": round(lifecycle_realized, 2),
        "lifecycle_vs_broker_gap": round(broker_realized - lifecycle_realized, 2),
        "non_deposit_cash": round(non_deposit_cash, 2),
        "residual": round(residual, 2),
        "unattributed_fill_pnl": round(unattributed, 2),
        "partial_rows": partial_rows,
        "partial_missing_total": round(sum(p["missing_pnl"] for p in partial_rows), 2),
        "partial_unrecorded_qty": sum(p["unrecorded_qty"] for p in partial_rows),
        "full_rows_diff": round(full_rows_diff, 2),
        "open_rows_fill_pnl": round(open_rows_fill_pnl, 2),
        "phantom_rows": [{"id": r["id"], "symbol": r["symbol"], "lifecycle_pnl": round(r["realized_pnl"] or 0, 2),
                          "close_reason": r["close_reason"]} for r in phantom_rows],
        "per_symbol": per_symbol,
        "qty_mismatch": qty_mismatch,
        "trades_sell_unlinked": t_unlinked,
        "trades_negative_net_symbols": t_neg,
    }


def format_report(r: Dict) -> str:
    L = []
    L.append(f"# Ledger reconciliation — {r['as_of']}")
    L.append("")
    L.append("Read-only pull of every Alpaca FILL activity, rebuilt as FIFO lots. "
             "position_lifecycle untouched; results in `broker_fills` / `fifo_realized`.")
    L.append("")
    L.append("## Headline")
    L.append("")
    L.append("| Measure | $ |")
    L.append("|---|---:|")
    L.append(f"| Equity now | {r['equity']:,.2f} |")
    L.append(f"| Equity − $100,000 − unrealized (Alpaca) = **equity gap** | **{r['equity_gap']:,.2f}** |")
    L.append(f"| Broker realized P&L (FIFO from fills) | {r['broker_realized']:,.2f} |")
    L.append(f"| Lifecycle realized P&L (position_lifecycle) | {r['lifecycle_realized']:,.2f} |")
    L.append(f"| Broker − lifecycle | {r['lifecycle_vs_broker_gap']:,.2f} |")
    L.append(f"| Fees / dividends / other cash (non-deposit) | {r['non_deposit_cash']:,.2f} |")
    L.append(f"| Unrealized — Alpaca (avg cost) | {r['alpaca_unrealized']:,.2f} |")
    L.append(f"| Unrealized — FIFO lots | {r['fifo_unrealized']:,.2f} |")
    L.append(f"| **Residual** (equity − deposits − FIFO realized − FIFO unrealized − cash items) | **{r['residual']:,.2f}** |")
    L.append("")
    L.append(f"Deposits: {r['deposits']:,.2f}. Cash activity by type: "
             + ", ".join(f"{k} {v:,.2f}" for k, v in sorted(r['cash_by_type'].items())))
    L.append(f"Fills: {r['fill_count']} broker fills, {r['matches']} FIFO lot matches.")
    if r["qty_mismatch"]:
        L.append(f"Open-lot vs broker-position qty mismatch: {r['qty_mismatch']}")
    else:
        L.append("Open FIFO lots match broker positions share-for-share.")
    L.append("")
    L.append("## Why lifecycle and broker disagree")
    L.append("")
    L.append(f"- Partial exits never booked (rows with exit_qty < entry_qty): **{r['partial_missing_total']:,.2f}** "
             f"across {len(r['partial_rows'])} rows / {r['partial_unrecorded_qty']:g} shares")
    L.append(f"- Full-exit rows priced at signal/guess instead of the fill: {r['full_rows_diff']:,.2f}")
    L.append(f"- Realized P&L on fills not attributable to any lifecycle row: {r['unattributed_fill_pnl']:,.2f}")
    L.append(f"- Fill P&L sitting on still-open lifecycle rows: {r['open_rows_fill_pnl']:,.2f}")
    L.append(f"- Closed lifecycle rows with no closing fill attributed: {len(r['phantom_rows'])} "
             f"(their booked P&L {sum(p['lifecycle_pnl'] for p in r['phantom_rows']):,.2f}; included in the line above). "
             f"Either a duplicate row opened alongside another for the same symbol, or an entry that never filled")
    L.append(f"- `trades` table: {r['trades_sell_unlinked']} sell rows with no lifecycle link; "
             f"{r['trades_negative_net_symbols']} symbols where recorded sell qty exceeds buy qty "
             f"(all rows, any order status) — not a usable ledger")
    L.append("")
    L.append("## Lifecycle rows with exit_qty < entry_qty (missing P&L from real fills)")
    L.append("")
    L.append("| id | symbol | entry qty | exit qty | qty closed by fills | lifecycle P&L | fills P&L | missing |")
    L.append("|---:|---|---:|---:|---:|---:|---:|---:|")
    for p in r["partial_rows"]:
        L.append(f"| {p['id']} | {p['symbol']} | {p['entry_qty']:g} | {p['exit_qty']:g} | {p['fills_closed_qty']:g} "
                 f"| {p['lifecycle_pnl']:,.2f} | {p['fills_pnl']:,.2f} | {p['missing_pnl']:,.2f} |")
    L.append("")
    L.append("## Per-symbol discrepancies (largest first, |diff| ≥ $1)")
    L.append("")
    L.append("| symbol | broker | lifecycle | diff |")
    L.append("|---|---:|---:|---:|")
    for s in r["per_symbol"]:
        if abs(s["diff"]) >= 1:
            L.append(f"| {s['symbol']} | {s['broker']:,.2f} | {s['lifecycle']:,.2f} | {s['diff']:,.2f} |")
    if r["phantom_rows"]:
        L.append("")
        L.append("## Closed lifecycle rows with no closing fill attributed")
        L.append("")
        L.append("| id | symbol | booked P&L | close reason |")
        L.append("|---:|---|---:|---|")
        for p in r["phantom_rows"]:
            L.append(f"| {p['id']} | {p['symbol']} | {p['lifecycle_pnl']:,.2f} | {p['close_reason']} |")
    return "\n".join(L) + "\n"


def run(alpaca, db_path: Optional[str] = None) -> Dict:
    """Pull fills (GET only), rebuild FIFO, write broker_fills + fifo_realized, return the report."""
    as_of = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fills, other = fetch_activities(alpaca)
    account = alpaca.get_account_raw()
    positions = alpaca.get_positions()
    matches, open_lots = rebuild_fifo(fills)
    conn = _connect(db_path)
    try:
        ensure_tables(conn)
        lifecycle = [dict(r) for r in conn.execute("SELECT id, symbol, opened_at, closed_at FROM position_lifecycle")]
        attribute_to_lifecycle(matches, lifecycle)
        with conn:
            store_fills(conn, fills, as_of)
            write_fifo(conn, matches, as_of)
        report = build_report(conn, matches, open_lots, other, account, positions, as_of)
    finally:
        conn.close()
    report["fill_count"] = len(fills)
    return report


def reconciliation_status(db_path: Optional[str] = None) -> Dict:
    """DB-only read for the real-money gate: broker FIFO realized vs lifecycle
    realized. as_of is when fifo_realized was last rebuilt (None = never run)."""
    conn = sqlite3.connect(f"file:{_db_path(db_path)}?mode=ro", uri=True, timeout=30)
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fifo_realized'").fetchone()
        broker, as_of = (conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0), MAX(computed_at) FROM fifo_realized").fetchone()
            if has_table else (0.0, None))
        lifecycle = conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM position_lifecycle WHERE status='closed'").fetchone()[0]
    finally:
        conn.close()
    return {
        "broker_realized": round(broker, 2),
        "lifecycle_realized": round(lifecycle, 2),
        "gap": round(broker - lifecycle, 2),
        "as_of": as_of,
    }
