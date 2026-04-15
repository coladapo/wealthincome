"""
Database — SQLite schema and all read/write functions.
Single source of truth for the trading system.
"""

import sqlite3
import json
import os
import math
import statistics
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from contextlib import contextmanager

DB_PATH = os.environ.get("WEALTHINCOME_DB", "data/wealthincome.db")


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist, run migrations."""
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cycles (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at      TEXT NOT NULL,
                finished_at     TEXT,
                market_open     INTEGER NOT NULL DEFAULT 0,
                decisions       INTEGER NOT NULL DEFAULT 0,
                market_summary  TEXT,
                cycle_notes     TEXT,
                raw_json        TEXT,
                status          TEXT NOT NULL DEFAULT 'running',
                input_tokens    INTEGER,
                output_tokens   INTEGER,
                cache_read_tokens  INTEGER,
                cache_write_tokens INTEGER,
                duration_ms     INTEGER
            );

            CREATE TABLE IF NOT EXISTS trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id        INTEGER REFERENCES cycles(id),
                executed_at     TEXT NOT NULL,
                symbol          TEXT NOT NULL,
                action          TEXT NOT NULL,
                qty             REAL NOT NULL,
                signal_price    REAL,
                confidence      REAL,
                reasoning       TEXT,
                order_id        TEXT,
                order_status    TEXT,
                take_profit     REAL,
                stop_loss       REAL,
                order_group_id  INTEGER,
                position_lifecycle_id INTEGER,
                ai_decision_id  INTEGER,
                fill_price      REAL,
                slippage_pct    REAL
            );

            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS errors (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                error_type  TEXT NOT NULL,
                message     TEXT NOT NULL,
                cycle_id    INTEGER REFERENCES cycles(id)
            );

            -- AI decisions: full prompt context + token usage + calibration
            CREATE TABLE IF NOT EXISTS ai_decisions (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id                INTEGER NOT NULL REFERENCES cycles(id),
                decided_at              TEXT NOT NULL,
                prompt_user             TEXT,
                prompt_system           TEXT,
                market_snapshot_json    TEXT,
                account_snapshot_json   TEXT,
                positions_snapshot_json TEXT,
                raw_response            TEXT,
                parsed_decisions_json   TEXT,
                input_tokens            INTEGER,
                output_tokens           INTEGER,
                cache_read_tokens       INTEGER,
                cache_write_tokens      INTEGER,
                duration_ms             INTEGER,
                decisions_made          INTEGER DEFAULT 0,
                decisions_executed      INTEGER DEFAULT 0,
                decisions_profitable    INTEGER DEFAULT 0,
                avg_confidence          REAL,
                calibration_score       REAL
            );

            -- Order groups: links parent buy order to TP + SL children
            CREATE TABLE IF NOT EXISTS order_groups (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id            INTEGER REFERENCES trades(id),
                cycle_id            INTEGER REFERENCES cycles(id),
                symbol              TEXT NOT NULL,
                created_at          TEXT NOT NULL,
                parent_order_id     TEXT NOT NULL UNIQUE,
                parent_side         TEXT NOT NULL,
                parent_qty          REAL NOT NULL,
                parent_status       TEXT,
                parent_filled_qty   REAL DEFAULT 0,
                parent_fill_price   REAL,
                parent_filled_at    TEXT,
                tp_order_id         TEXT,
                tp_limit_price      REAL,
                tp_status           TEXT,
                tp_filled_qty       REAL DEFAULT 0,
                tp_fill_price       REAL,
                tp_filled_at        TEXT,
                sl_order_id         TEXT,
                sl_stop_price       REAL,
                sl_status           TEXT,
                sl_filled_qty       REAL DEFAULT 0,
                sl_fill_price       REAL,
                sl_filled_at        TEXT,
                exit_trigger        TEXT,
                resolved_at         TEXT,
                realized_pnl        REAL
            );

            -- Full Alpaca order lifecycle audit log
            CREATE TABLE IF NOT EXISTS orders (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                order_group_id      INTEGER REFERENCES order_groups(id),
                trade_id            INTEGER REFERENCES trades(id),
                cycle_id            INTEGER REFERENCES cycles(id),
                alpaca_order_id     TEXT NOT NULL,
                symbol              TEXT NOT NULL,
                side                TEXT NOT NULL,
                order_type          TEXT NOT NULL,
                order_class         TEXT,
                time_in_force       TEXT,
                qty                 REAL NOT NULL,
                limit_price         REAL,
                stop_price          REAL,
                status              TEXT NOT NULL,
                previous_status     TEXT,
                status_updated_at   TEXT NOT NULL,
                filled_qty          REAL DEFAULT 0,
                filled_avg_price    REAL,
                filled_at           TEXT,
                signal_price        REAL,
                slippage_dollars    REAL,
                slippage_pct        REAL,
                created_at          TEXT,
                submitted_at        TEXT,
                expired_at          TEXT,
                canceled_at         TEXT,
                failed_at           TEXT,
                raw_json            TEXT
            );

            -- Position lifecycle: one row per opened position
            CREATE TABLE IF NOT EXISTS position_lifecycle (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol                  TEXT NOT NULL,
                opened_at               TEXT NOT NULL,
                entry_cycle_id          INTEGER REFERENCES cycles(id),
                entry_trade_id          INTEGER REFERENCES trades(id),
                entry_order_group_id    INTEGER REFERENCES order_groups(id),
                entry_price             REAL NOT NULL,
                entry_qty               REAL NOT NULL,
                entry_cost_basis        REAL NOT NULL,
                closed_at               TEXT,
                exit_cycle_id           INTEGER REFERENCES cycles(id),
                exit_trade_id           INTEGER REFERENCES trades(id),
                exit_price              REAL,
                exit_qty                REAL,
                close_reason            TEXT,
                realized_pnl            REAL,
                realized_pnl_pct        REAL,
                commission              REAL DEFAULT 0,
                hold_duration_seconds   INTEGER,
                entry_rsi               REAL,
                entry_macd_histogram    REAL,
                entry_atr_pct           REAL,
                entry_confidence        REAL,
                status                  TEXT NOT NULL DEFAULT 'open'
            );

            -- Equity curve: one snapshot per cycle
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id            INTEGER REFERENCES cycles(id),
                snapshot_at         TEXT NOT NULL,
                portfolio_value     REAL NOT NULL,
                cash                REAL NOT NULL,
                long_market_value   REAL NOT NULL DEFAULT 0,
                buying_power        REAL,
                unrealized_pnl      REAL,
                realized_pnl_today  REAL,
                daily_return_pct    REAL,
                open_positions      INTEGER NOT NULL DEFAULT 0,
                peak_value          REAL,
                drawdown_pct        REAL
            );

            -- Daily summaries: for Sharpe/Sortino/Calmar
            CREATE TABLE IF NOT EXISTS daily_summaries (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL UNIQUE,
                open_equity     REAL,
                close_equity    REAL,
                high_equity     REAL,
                low_equity      REAL,
                daily_return    REAL,
                daily_return_pct REAL,
                cycles_run      INTEGER DEFAULT 0,
                trades_taken    INTEGER DEFAULT 0,
                winning_trades  INTEGER DEFAULT 0,
                losing_trades   INTEGER DEFAULT 0,
                gross_pnl       REAL DEFAULT 0,
                net_pnl         REAL DEFAULT 0,
                max_drawdown_pct REAL,
                volatility      REAL,
                total_input_tokens  INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0,
                total_cache_read    INTEGER DEFAULT 0,
                total_cache_write   INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_trades_executed ON trades(executed_at);
            CREATE INDEX IF NOT EXISTS idx_cycles_started ON cycles(started_at);
            CREATE INDEX IF NOT EXISTS idx_errors_occurred ON errors(occurred_at);
            CREATE INDEX IF NOT EXISTS idx_ai_decisions_cycle ON ai_decisions(cycle_id);
            CREATE INDEX IF NOT EXISTS idx_ai_decisions_at ON ai_decisions(decided_at);
            CREATE INDEX IF NOT EXISTS idx_order_groups_parent ON order_groups(parent_order_id);
            CREATE INDEX IF NOT EXISTS idx_order_groups_tp ON order_groups(tp_order_id);
            CREATE INDEX IF NOT EXISTS idx_order_groups_sl ON order_groups(sl_order_id);
            CREATE INDEX IF NOT EXISTS idx_order_groups_symbol ON order_groups(symbol);
            CREATE INDEX IF NOT EXISTS idx_orders_alpaca_id ON orders(alpaca_order_id);
            CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
            CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
            CREATE INDEX IF NOT EXISTS idx_pos_lifecycle_symbol ON position_lifecycle(symbol);
            CREATE INDEX IF NOT EXISTS idx_pos_lifecycle_status ON position_lifecycle(status);
            CREATE INDEX IF NOT EXISTS idx_equity_snapshot_at ON equity_snapshots(snapshot_at);
            CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_summaries(date);
        """)

        # Migrations — safe no-ops if column already exists
        migrations = [
            ("cycles", "input_tokens",       "INTEGER"),
            ("cycles", "output_tokens",      "INTEGER"),
            ("cycles", "cache_read_tokens",  "INTEGER"),
            ("cycles", "cache_write_tokens", "INTEGER"),
            ("cycles", "duration_ms",        "INTEGER"),
            ("trades", "order_group_id",     "INTEGER"),
            ("trades", "position_lifecycle_id", "INTEGER"),
            ("trades", "ai_decision_id",     "INTEGER"),
            ("trades", "fill_price",         "REAL"),
            ("trades", "slippage_pct",       "REAL"),
        ]
        for table, col, typedef in migrations:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
            except Exception:
                pass


# ─── Config ─────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "max_position_pct": "0.08",
    "max_open_positions": "8",
    "daily_loss_limit_pct": "0.05",
    "confidence_threshold": "0.72",
    "poll_interval": "300",
    "trade_only_market_hours": "true",
    "watchlist": "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA,SPY,QQQ,AMD",
    "trader_running": "false",
}


def get_config() -> Dict[str, str]:
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM config").fetchall()
        result = dict(DEFAULT_CONFIG)
        result.update({r["key"]: r["value"] for r in rows})
        return result


def set_config(key: str, value: str):
    with db() as conn:
        conn.execute(
            "INSERT INTO config(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )


def set_config_many(updates: Dict[str, str]):
    with db() as conn:
        for key, value in updates.items():
            conn.execute(
                "INSERT INTO config(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value)
            )


# ─── Cycles ─────────────────────────────────────────────────────────────────

def start_cycle(market_open: bool) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO cycles(started_at, market_open, status) VALUES(?,?,?)",
            (datetime.now().isoformat(), int(market_open), "running")
        )
        return cur.lastrowid


def finish_cycle(cycle_id: int, result: Dict, usage: Dict = None, duration_ms: int = None):
    usage = usage or {}
    with db() as conn:
        conn.execute("""
            UPDATE cycles SET
                finished_at = ?,
                decisions = ?,
                market_summary = ?,
                cycle_notes = ?,
                raw_json = ?,
                status = 'done',
                input_tokens = ?,
                output_tokens = ?,
                cache_read_tokens = ?,
                cache_write_tokens = ?,
                duration_ms = ?
            WHERE id = ?
        """, (
            datetime.now().isoformat(),
            len(result.get("decisions", [])),
            result.get("market_summary"),
            result.get("cycle_notes"),
            json.dumps(result),
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            usage.get("cache_read_tokens"),
            usage.get("cache_write_tokens"),
            duration_ms,
            cycle_id,
        ))


def fail_cycle(cycle_id: int, error: str):
    with db() as conn:
        conn.execute(
            "UPDATE cycles SET finished_at=?, status='error', cycle_notes=? WHERE id=?",
            (datetime.now().isoformat(), error, cycle_id)
        )


def get_cycles(limit: int = 20) -> List[Dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM cycles ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_last_cycle() -> Optional[Dict]:
    cycles = get_cycles(limit=1)
    return cycles[0] if cycles else None


# ─── Trades ─────────────────────────────────────────────────────────────────

def record_trade(cycle_id: int, trade: Dict) -> int:
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO trades(cycle_id, executed_at, symbol, action, qty,
                signal_price, confidence, reasoning, order_id, order_status,
                take_profit, stop_loss, ai_decision_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            cycle_id,
            datetime.now().isoformat(),
            trade.get("symbol"),
            trade.get("action"),
            trade.get("qty"),
            trade.get("signal_price"),
            trade.get("confidence"),
            trade.get("reason") or trade.get("reasoning"),
            trade.get("order_id"),
            trade.get("order_status"),
            trade.get("take_profit"),
            trade.get("stop_loss"),
            trade.get("ai_decision_id"),
        ))
        return cur.lastrowid


def update_trade_links(trade_id: int, order_group_id: int = None,
                       position_lifecycle_id: int = None, fill_price: float = None,
                       slippage_pct: float = None):
    with db() as conn:
        if order_group_id is not None:
            conn.execute("UPDATE trades SET order_group_id=? WHERE id=?", (order_group_id, trade_id))
        if position_lifecycle_id is not None:
            conn.execute("UPDATE trades SET position_lifecycle_id=? WHERE id=?", (position_lifecycle_id, trade_id))
        if fill_price is not None:
            conn.execute("UPDATE trades SET fill_price=? WHERE id=?", (fill_price, trade_id))
        if slippage_pct is not None:
            conn.execute("UPDATE trades SET slippage_pct=? WHERE id=?", (slippage_pct, trade_id))


def get_trades(limit: int = 50, symbol: str = None) -> List[Dict]:
    with db() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM trades WHERE symbol=? ORDER BY executed_at DESC LIMIT ?",
                (symbol, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY executed_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_trades_today() -> List[Dict]:
    today = datetime.now().strftime("%Y-%m-%d")
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE executed_at LIKE ? ORDER BY executed_at DESC",
            (f"{today}%",)
        ).fetchall()
        return [dict(r) for r in rows]


# ─── AI Decisions ────────────────────────────────────────────────────────────

def record_ai_decision(
    cycle_id: int,
    prompt_user: str,
    prompt_system: str,
    market_snapshot: Dict,
    account_snapshot: Dict,
    positions_snapshot: Dict,
    raw_response: str,
    parsed_decisions: List[Dict],
    usage: Dict,
    duration_ms: int,
) -> int:
    non_holds = [d for d in parsed_decisions if d.get("action", "hold") != "hold"]
    avg_conf = (sum(d.get("confidence", 0) for d in non_holds) / len(non_holds)
                if non_holds else None)
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO ai_decisions(
                cycle_id, decided_at, prompt_user, prompt_system,
                market_snapshot_json, account_snapshot_json, positions_snapshot_json,
                raw_response, parsed_decisions_json,
                input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, duration_ms,
                decisions_made, decisions_executed, avg_confidence
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            cycle_id,
            datetime.now().isoformat(),
            prompt_user,
            prompt_system,
            json.dumps(market_snapshot),
            json.dumps(account_snapshot),
            json.dumps(positions_snapshot),
            raw_response,
            json.dumps(parsed_decisions),
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            usage.get("cache_read_tokens"),
            usage.get("cache_write_tokens"),
            duration_ms,
            len(non_holds),
            0,  # decisions_executed — updated as orders are placed
            avg_conf,
        ))
        return cur.lastrowid


def increment_ai_decision_executed(decision_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE ai_decisions SET decisions_executed = decisions_executed + 1 WHERE id=?",
            (decision_id,)
        )


def update_ai_decision_calibration(decision_id: int, was_profitable: bool):
    with db() as conn:
        conn.execute("""
            UPDATE ai_decisions SET
                decisions_profitable = decisions_profitable + ?,
                calibration_score = CAST(decisions_profitable + ? AS REAL) / NULLIF(decisions_made, 0)
            WHERE id = ?
        """, (1 if was_profitable else 0, 1 if was_profitable else 0, decision_id))


def get_ai_decisions(limit: int = 20, cycle_id: int = None) -> List[Dict]:
    with db() as conn:
        if cycle_id:
            rows = conn.execute(
                "SELECT * FROM ai_decisions WHERE cycle_id=? ORDER BY decided_at DESC LIMIT ?",
                (cycle_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ai_decisions ORDER BY decided_at DESC LIMIT ?", (limit,)
            ).fetchall()
        # Exclude full prompt/response from list view to keep payloads small
        result = []
        for r in rows:
            d = dict(r)
            d.pop("prompt_user", None)
            d.pop("prompt_system", None)
            d.pop("market_snapshot_json", None)
            d.pop("raw_response", None)
            result.append(d)
        return result


def get_ai_decision_detail(decision_id: int) -> Optional[Dict]:
    """Full detail including prompt and response."""
    with db() as conn:
        row = conn.execute("SELECT * FROM ai_decisions WHERE id=?", (decision_id,)).fetchone()
        return dict(row) if row else None


# ─── Order Groups ────────────────────────────────────────────────────────────

def record_order_group(
    trade_id: int,
    cycle_id: int,
    symbol: str,
    parent_order_id: str,
    parent_side: str,
    parent_qty: float,
    tp_order_id: Optional[str] = None,
    tp_limit_price: Optional[float] = None,
    sl_order_id: Optional[str] = None,
    sl_stop_price: Optional[float] = None,
) -> int:
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO order_groups(
                trade_id, cycle_id, symbol, created_at,
                parent_order_id, parent_side, parent_qty, parent_status,
                tp_order_id, tp_limit_price,
                sl_order_id, sl_stop_price
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trade_id, cycle_id, symbol, datetime.now().isoformat(),
            parent_order_id, parent_side, parent_qty, "new",
            tp_order_id, tp_limit_price,
            sl_order_id, sl_stop_price,
        ))
        return cur.lastrowid


def update_order_group_fill(
    parent_order_id: str,
    parent_fill_price: float,
    parent_filled_qty: float,
    parent_filled_at: str,
    parent_status: str,
):
    with db() as conn:
        conn.execute("""
            UPDATE order_groups SET
                parent_fill_price=?, parent_filled_qty=?,
                parent_filled_at=?, parent_status=?
            WHERE parent_order_id=?
        """, (parent_fill_price, parent_filled_qty, parent_filled_at, parent_status, parent_order_id))


def update_order_group_exit(
    parent_order_id: str,
    exit_trigger: str,
    exit_fill_price: float,
    exit_filled_at: str,
    realized_pnl: float,
    tp_status: Optional[str] = None,
    tp_fill_price: Optional[float] = None,
    sl_status: Optional[str] = None,
    sl_fill_price: Optional[float] = None,
):
    with db() as conn:
        conn.execute("""
            UPDATE order_groups SET
                exit_trigger=?, resolved_at=?, realized_pnl=?,
                tp_status=COALESCE(?, tp_status), tp_fill_price=COALESCE(?, tp_fill_price),
                sl_status=COALESCE(?, sl_status), sl_fill_price=COALESCE(?, sl_fill_price)
            WHERE parent_order_id=?
        """, (
            exit_trigger, exit_filled_at, realized_pnl,
            tp_status, tp_fill_price,
            sl_status, sl_fill_price,
            parent_order_id,
        ))


def get_open_order_groups() -> List[Dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM order_groups WHERE exit_trigger IS NULL ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_order_groups(limit: int = 50) -> List[Dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM order_groups ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ─── Orders ──────────────────────────────────────────────────────────────────

def record_order(
    alpaca_order_id: str,
    symbol: str,
    side: str,
    order_type: str,
    qty: float,
    status: str,
    signal_price: Optional[float] = None,
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    order_class: Optional[str] = None,
    time_in_force: Optional[str] = None,
    cycle_id: Optional[int] = None,
    trade_id: Optional[int] = None,
    order_group_id: Optional[int] = None,
    raw_json: Optional[str] = None,
) -> int:
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO orders(
                alpaca_order_id, symbol, side, order_type, order_class, time_in_force,
                qty, limit_price, stop_price, status, status_updated_at,
                signal_price, cycle_id, trade_id, order_group_id, raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            alpaca_order_id, symbol, side, order_type, order_class, time_in_force,
            qty, limit_price, stop_price, status, datetime.now().isoformat(),
            signal_price, cycle_id, trade_id, order_group_id, raw_json,
        ))
        return cur.lastrowid


def update_order_status(
    alpaca_order_id: str,
    new_status: str,
    filled_qty: float = 0,
    filled_avg_price: Optional[float] = None,
    filled_at: Optional[str] = None,
    previous_status: Optional[str] = None,
):
    with db() as conn:
        row = conn.execute(
            "SELECT signal_price, status FROM orders WHERE alpaca_order_id=? ORDER BY id DESC LIMIT 1",
            (alpaca_order_id,)
        ).fetchone()
        slippage_dollars = None
        slippage_pct = None
        if row and filled_avg_price and row["signal_price"]:
            slippage_dollars = filled_avg_price - row["signal_price"]
            slippage_pct = slippage_dollars / row["signal_price"] * 100
        prev = previous_status or (row["status"] if row else None)

        conn.execute("""
            UPDATE orders SET
                status=?, previous_status=?, status_updated_at=?,
                filled_qty=?, filled_avg_price=?, filled_at=?,
                slippage_dollars=?, slippage_pct=?
            WHERE alpaca_order_id=?
        """, (
            new_status, prev, datetime.now().isoformat(),
            filled_qty, filled_avg_price, filled_at,
            slippage_dollars, slippage_pct,
            alpaca_order_id,
        ))


def get_open_orders() -> List[Dict]:
    with db() as conn:
        rows = conn.execute("""
            SELECT * FROM orders
            WHERE status IN ('new','partially_filled','accepted','pending_new','held')
            ORDER BY status_updated_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_orders_history(limit: int = 100, status: str = None, symbol: str = None) -> List[Dict]:
    with db() as conn:
        query = "SELECT * FROM orders WHERE 1=1"
        params = []
        if status:
            query += " AND status=?"
            params.append(status)
        if symbol:
            query += " AND symbol=?"
            params.append(symbol)
        query += " ORDER BY status_updated_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


# ─── Position Lifecycle ──────────────────────────────────────────────────────

def open_position_lifecycle(
    symbol: str,
    entry_price: float,
    entry_qty: float,
    cycle_id: int,
    trade_id: int,
    order_group_id: int,
    entry_rsi: Optional[float] = None,
    entry_macd_histogram: Optional[float] = None,
    entry_atr_pct: Optional[float] = None,
    entry_confidence: Optional[float] = None,
) -> int:
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO position_lifecycle(
                symbol, opened_at, entry_cycle_id, entry_trade_id, entry_order_group_id,
                entry_price, entry_qty, entry_cost_basis, status,
                entry_rsi, entry_macd_histogram, entry_atr_pct, entry_confidence
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            symbol, datetime.now().isoformat(), cycle_id, trade_id, order_group_id,
            entry_price, entry_qty, entry_price * entry_qty, "open",
            entry_rsi, entry_macd_histogram, entry_atr_pct, entry_confidence,
        ))
        return cur.lastrowid


def close_position_lifecycle(
    position_id: int,
    exit_price: float,
    exit_qty: float,
    close_reason: str,
    exit_cycle_id: Optional[int] = None,
    exit_trade_id: Optional[int] = None,
):
    with db() as conn:
        row = conn.execute(
            "SELECT entry_price, entry_qty, entry_cost_basis, opened_at FROM position_lifecycle WHERE id=?",
            (position_id,)
        ).fetchone()
        if not row:
            return
        realized_pnl = (exit_price - row["entry_price"]) * exit_qty
        realized_pnl_pct = realized_pnl / row["entry_cost_basis"] * 100 if row["entry_cost_basis"] else 0
        now = datetime.now().isoformat()
        try:
            opened = datetime.fromisoformat(row["opened_at"])
            hold_seconds = int((datetime.now() - opened).total_seconds())
        except Exception:
            hold_seconds = None

        conn.execute("""
            UPDATE position_lifecycle SET
                closed_at=?, exit_cycle_id=?, exit_trade_id=?,
                exit_price=?, exit_qty=?, close_reason=?,
                realized_pnl=?, realized_pnl_pct=?,
                hold_duration_seconds=?, status='closed'
            WHERE id=?
        """, (
            now, exit_cycle_id, exit_trade_id,
            exit_price, exit_qty, close_reason,
            realized_pnl, realized_pnl_pct,
            hold_seconds, position_id,
        ))


def get_open_positions_lifecycle() -> List[Dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM position_lifecycle WHERE status='open' ORDER BY opened_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_open_position_by_symbol(symbol: str) -> Optional[Dict]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM position_lifecycle WHERE symbol=? AND status='open' ORDER BY opened_at DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        return dict(row) if row else None


def get_closed_positions(limit: int = 100, symbol: str = None) -> List[Dict]:
    with db() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM position_lifecycle WHERE status='closed' AND symbol=? ORDER BY closed_at DESC LIMIT ?",
                (symbol, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM position_lifecycle WHERE status='closed' ORDER BY closed_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


# ─── Equity Snapshots ───────────────────────────────────────────────────────

def record_equity_snapshot(
    cycle_id: int,
    portfolio_value: float,
    cash: float,
    long_market_value: float,
    buying_power: float,
    unrealized_pnl: float,
    realized_pnl_today: float,
    open_positions: int,
) -> int:
    with db() as conn:
        # Get previous snapshot for daily return and peak tracking
        prev = conn.execute(
            "SELECT portfolio_value, peak_value FROM equity_snapshots ORDER BY snapshot_at DESC LIMIT 1"
        ).fetchone()

        daily_return_pct = None
        if prev and prev["portfolio_value"]:
            daily_return_pct = (portfolio_value - prev["portfolio_value"]) / prev["portfolio_value"] * 100

        peak_value = portfolio_value
        if prev and prev["peak_value"]:
            peak_value = max(prev["peak_value"], portfolio_value)

        drawdown_pct = (portfolio_value - peak_value) / peak_value * 100 if peak_value else 0

        cur = conn.execute("""
            INSERT INTO equity_snapshots(
                cycle_id, snapshot_at, portfolio_value, cash, long_market_value,
                buying_power, unrealized_pnl, realized_pnl_today, daily_return_pct,
                open_positions, peak_value, drawdown_pct
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            cycle_id, datetime.now().isoformat(), portfolio_value, cash, long_market_value,
            buying_power, unrealized_pnl, realized_pnl_today, daily_return_pct,
            open_positions, peak_value, drawdown_pct,
        ))
        return cur.lastrowid


def get_equity_curve(days: int = 90) -> List[Dict]:
    since = (datetime.now() - timedelta(days=days)).isoformat()
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM equity_snapshots WHERE snapshot_at >= ? ORDER BY snapshot_at ASC",
            (since,)
        ).fetchall()
        return [dict(r) for r in rows]


# ─── Daily Summaries ─────────────────────────────────────────────────────────

def upsert_daily_summary(date: str):
    """Compute and upsert daily summary from raw data for a given date."""
    with db() as conn:
        # Equity range for the day
        snapshots = conn.execute(
            "SELECT portfolio_value, snapshot_at FROM equity_snapshots WHERE snapshot_at LIKE ? ORDER BY snapshot_at",
            (f"{date}%",)
        ).fetchall()

        if not snapshots:
            return

        open_eq = float(snapshots[0]["portfolio_value"])
        close_eq = float(snapshots[-1]["portfolio_value"])
        high_eq = max(float(s["portfolio_value"]) for s in snapshots)
        low_eq = min(float(s["portfolio_value"]) for s in snapshots)
        daily_return = (close_eq - open_eq) / open_eq if open_eq else 0
        daily_return_pct = daily_return * 100

        # Drawdown within day
        peak = open_eq
        max_dd = 0.0
        for s in snapshots:
            v = float(s["portfolio_value"])
            peak = max(peak, v)
            dd = (v - peak) / peak if peak else 0
            max_dd = min(max_dd, dd)

        # Cycle count
        cycles_run = conn.execute(
            "SELECT COUNT(*) FROM cycles WHERE started_at LIKE ? AND status='done'",
            (f"{date}%",)
        ).fetchone()[0]

        # Trade stats
        trades = conn.execute(
            "SELECT * FROM position_lifecycle WHERE closed_at LIKE ?",
            (f"{date}%",)
        ).fetchall()
        trades_taken = len(trades)
        winning = sum(1 for t in trades if (t["realized_pnl"] or 0) > 0)
        losing = trades_taken - winning
        gross_pnl = sum(float(t["realized_pnl"] or 0) for t in trades)

        # Token usage
        token_row = conn.execute("""
            SELECT
                COALESCE(SUM(input_tokens), 0) as inp,
                COALESCE(SUM(output_tokens), 0) as out,
                COALESCE(SUM(cache_read_tokens), 0) as cr,
                COALESCE(SUM(cache_write_tokens), 0) as cw
            FROM cycles WHERE started_at LIKE ? AND status='done'
        """, (f"{date}%",)).fetchone()

        conn.execute("""
            INSERT INTO daily_summaries(
                date, open_equity, close_equity, high_equity, low_equity,
                daily_return, daily_return_pct, cycles_run,
                trades_taken, winning_trades, losing_trades, gross_pnl, net_pnl,
                max_drawdown_pct, total_input_tokens, total_output_tokens,
                total_cache_read, total_cache_write
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET
                close_equity=excluded.close_equity,
                high_equity=excluded.high_equity,
                low_equity=excluded.low_equity,
                daily_return=excluded.daily_return,
                daily_return_pct=excluded.daily_return_pct,
                cycles_run=excluded.cycles_run,
                trades_taken=excluded.trades_taken,
                winning_trades=excluded.winning_trades,
                losing_trades=excluded.losing_trades,
                gross_pnl=excluded.gross_pnl,
                net_pnl=excluded.net_pnl,
                max_drawdown_pct=excluded.max_drawdown_pct,
                total_input_tokens=excluded.total_input_tokens,
                total_output_tokens=excluded.total_output_tokens,
                total_cache_read=excluded.total_cache_read,
                total_cache_write=excluded.total_cache_write
        """, (
            date, open_eq, close_eq, high_eq, low_eq,
            daily_return, daily_return_pct, cycles_run,
            trades_taken, winning, losing, gross_pnl, gross_pnl,
            max_dd * 100,
            token_row["inp"], token_row["out"], token_row["cr"], token_row["cw"],
        ))


def get_daily_summaries(days: int = 90) -> List[Dict]:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_summaries WHERE date >= ? ORDER BY date ASC",
            (since,)
        ).fetchall()
        return [dict(r) for r in rows]


def compute_risk_metrics(days: int = 252) -> Dict:
    """Compute Sharpe, Sortino, Calmar, win rate from daily_summaries."""
    rows = get_daily_summaries(days=days)
    if len(rows) < 2:
        return {"note": "insufficient data — need at least 2 days of history"}

    returns = [r["daily_return"] for r in rows if r.get("daily_return") is not None]
    if not returns:
        return {"note": "no return data yet"}

    risk_free_daily = 0.05 / 252
    avg_return = sum(returns) / len(returns)
    std_return = statistics.stdev(returns) if len(returns) > 1 else 0

    sharpe = ((avg_return - risk_free_daily) / std_return * math.sqrt(252)
              if std_return > 0 else 0)

    downside = [r for r in returns if r < risk_free_daily]
    downside_std = statistics.stdev(downside) if len(downside) > 1 else std_return
    sortino = ((avg_return - risk_free_daily) / downside_std * math.sqrt(252)
               if downside_std > 0 else 0)

    equity_vals = [r["close_equity"] for r in rows if r.get("close_equity")]
    peak = equity_vals[0] if equity_vals else 1
    max_dd = 0.0
    for v in equity_vals:
        peak = max(peak, v)
        dd = (v - peak) / peak if peak > 0 else 0
        max_dd = min(max_dd, dd)

    first_eq = equity_vals[0] if equity_vals else 1
    last_eq = equity_vals[-1] if equity_vals else 1
    n = len(equity_vals)
    annual_return = (last_eq / first_eq) ** (252 / n) - 1 if n > 1 and first_eq > 0 else 0
    calmar = annual_return / abs(max_dd) if max_dd < 0 else 0

    closed = get_closed_positions(limit=10000)
    total_closed = len(closed)
    winners = sum(1 for p in closed if (p.get("realized_pnl") or 0) > 0)
    losers = total_closed - winners

    wins_pnl = [p["realized_pnl"] for p in closed if (p.get("realized_pnl") or 0) > 0]
    loss_pnl = [p["realized_pnl"] for p in closed if (p.get("realized_pnl") or 0) <= 0]
    avg_win = sum(wins_pnl) / len(wins_pnl) if wins_pnl else 0
    avg_loss = sum(loss_pnl) / len(loss_pnl) if loss_pnl else 0
    profit_factor = (sum(wins_pnl) / abs(sum(loss_pnl))
                     if loss_pnl and sum(loss_pnl) != 0 else None)
    win_rate = winners / total_closed if total_closed else None
    expectancy = ((win_rate * avg_win + (1 - win_rate) * avg_loss)
                  if win_rate is not None else None)

    return {
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "calmar": round(calmar, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "annualized_return_pct": round(annual_return * 100, 2),
        "volatility_pct": round(std_return * math.sqrt(252) * 100, 2),
        "win_rate": round(win_rate, 3) if win_rate is not None else None,
        "avg_win_dollars": round(avg_win, 2),
        "avg_loss_dollars": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 3) if profit_factor else None,
        "expectancy_dollars": round(expectancy, 2) if expectancy is not None else None,
        "total_closed_positions": total_closed,
        "winners": winners,
        "losers": losers,
        "days_of_data": len(rows),
    }


# ─── Token Usage ─────────────────────────────────────────────────────────────

def get_token_usage(days: int = 1) -> Dict:
    since = (datetime.now() - timedelta(days=days)).isoformat()
    with db() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*)                              AS cycles,
                COALESCE(SUM(input_tokens), 0)        AS input_tokens,
                COALESCE(SUM(output_tokens), 0)       AS output_tokens,
                COALESCE(SUM(cache_read_tokens), 0)   AS cache_read_tokens,
                COALESCE(SUM(cache_write_tokens), 0)  AS cache_write_tokens,
                COALESCE(AVG(duration_ms), 0)         AS avg_duration_ms
            FROM cycles
            WHERE started_at >= ? AND status = 'done'
        """, (since,)).fetchone()
        d = dict(row)
        d["total_tokens"] = d["input_tokens"] + d["output_tokens"]
        d["days"] = days
        return d


# ─── Errors ─────────────────────────────────────────────────────────────────

def record_error(error_type: str, message: str, cycle_id: int = None):
    with db() as conn:
        conn.execute(
            "INSERT INTO errors(occurred_at, error_type, message, cycle_id) VALUES(?,?,?,?)",
            (datetime.now().isoformat(), error_type, message, cycle_id)
        )


def get_errors(limit: int = 20) -> List[Dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM errors ORDER BY occurred_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
