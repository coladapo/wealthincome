"""
Order Reconciler — background thread polling Alpaca every 30s.
Resolves open orders, fills, bracket TP/SL exits.
Computes realized P&L and back-fills entry prices from actual fills.
Updates AI decision calibration as positions close.
"""

import threading
import logging
import sqlite3
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger("reconciler")

_reconciler_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def _reconcile_once(alpaca):
    from backend.db import (
        get_open_orders, update_order_status,
        get_open_order_groups, update_order_group_fill, update_order_group_exit,
        get_open_positions_lifecycle, close_position_lifecycle,
        update_ai_decision_calibration, upsert_daily_summary,
        get_daily_summaries, DB_PATH,
    )

    # ── 1. Update status on all open orders ──────────────────────────────────
    open_orders = get_open_orders()
    for order_row in open_orders:
        try:
            alpaca_order = alpaca.get_order_raw(order_row["alpaca_order_id"])
            if not alpaca_order:
                continue
            new_status = alpaca_order.get("status")
            if new_status == order_row["status"]:
                continue

            filled_qty = float(alpaca_order.get("filled_qty") or 0)
            fill_price = (float(alpaca_order["filled_avg_price"])
                          if alpaca_order.get("filled_avg_price") else None)
            filled_at = alpaca_order.get("filled_at")

            update_order_status(
                alpaca_order_id=order_row["alpaca_order_id"],
                new_status=new_status,
                filled_qty=filled_qty,
                filled_avg_price=fill_price,
                filled_at=filled_at,
                previous_status=order_row["status"],
            )
            logger.info(
                f"Order {order_row['alpaca_order_id'][:8]}… "
                f"{order_row['symbol']}: {order_row['status']} → {new_status}"
                + (f" @ ${fill_price:.2f}" if fill_price else "")
            )
        except Exception as e:
            logger.warning(f"Failed to reconcile order {order_row['alpaca_order_id']}: {e}")

    # ── 2. Resolve bracket order groups ──────────────────────────────────────
    open_groups = get_open_order_groups()
    for group in open_groups:
        try:
            parent_raw = alpaca.get_order_raw(group["parent_order_id"])
            if not parent_raw:
                continue

            parent_status = parent_raw.get("status")

            # Back-fill actual entry price when parent fills
            if parent_status == "filled" and group.get("parent_fill_price") is None:
                fill_price = float(parent_raw.get("filled_avg_price") or 0)
                filled_at = parent_raw.get("filled_at", datetime.now().isoformat())
                filled_qty = float(parent_raw.get("filled_qty") or group["parent_qty"])

                update_order_group_fill(
                    parent_order_id=group["parent_order_id"],
                    parent_fill_price=fill_price,
                    parent_filled_qty=filled_qty,
                    parent_filled_at=filled_at,
                    parent_status=parent_status,
                )

                # Update position_lifecycle with actual fill price
                _backfill_entry_price(group["symbol"], fill_price, filled_qty, DB_PATH)
                logger.info(f"Entry filled: {group['symbol']} @ ${fill_price:.2f} (x{filled_qty})")

            # Check if TP or SL triggered
            exit_trigger = None
            exit_price = None
            exit_at = None
            tp_status = group.get("tp_status")
            sl_status = group.get("sl_status")

            if group.get("tp_order_id"):
                tp_raw = alpaca.get_order_raw(group["tp_order_id"])
                if tp_raw and tp_raw.get("status") == "filled":
                    exit_trigger = "take_profit"
                    exit_price = float(tp_raw.get("filled_avg_price") or 0)
                    exit_at = tp_raw.get("filled_at", datetime.now().isoformat())
                    tp_status = "filled"

            if group.get("sl_order_id") and not exit_trigger:
                sl_raw = alpaca.get_order_raw(group["sl_order_id"])
                if sl_raw and sl_raw.get("status") == "filled":
                    exit_trigger = "stop_loss"
                    exit_price = float(sl_raw.get("filled_avg_price") or 0)
                    exit_at = sl_raw.get("filled_at", datetime.now().isoformat())
                    sl_status = "filled"

            if exit_trigger and group.get("parent_fill_price") and exit_price:
                entry_price = group["parent_fill_price"]
                qty = group.get("parent_filled_qty") or group["parent_qty"]
                realized_pnl = (exit_price - entry_price) * qty

                update_order_group_exit(
                    parent_order_id=group["parent_order_id"],
                    exit_trigger=exit_trigger,
                    exit_fill_price=exit_price,
                    exit_filled_at=exit_at,
                    realized_pnl=realized_pnl,
                    tp_status=tp_status,
                    tp_fill_price=exit_price if exit_trigger == "take_profit" else None,
                    sl_status=sl_status,
                    sl_fill_price=exit_price if exit_trigger == "stop_loss" else None,
                )

                # Close position lifecycle
                open_positions = get_open_positions_lifecycle()
                for pos in open_positions:
                    if pos["symbol"] == group["symbol"]:
                        close_position_lifecycle(
                            position_id=pos["id"],
                            exit_price=exit_price,
                            exit_qty=qty,
                            close_reason=exit_trigger,
                        )
                        # Update AI decision calibration
                        _update_calibration(group["parent_order_id"], realized_pnl, DB_PATH)
                        break

                logger.info(
                    f"Bracket resolved: {group['symbol']} via {exit_trigger} | "
                    f"entry=${entry_price:.2f} exit=${exit_price:.2f} | "
                    f"P&L=${realized_pnl:+.2f}"
                )

        except Exception as e:
            logger.warning(f"Failed to reconcile group {group.get('id')} ({group.get('symbol')}): {e}")

    # ── 3. Upsert daily summary for yesterday if missing ─────────────────────
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now().replace(hour=0, minute=0, second=0)
                     .__class__.fromisoformat(today) -
                     __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")
        existing = {r["date"] for r in get_daily_summaries(days=2)}
        if yesterday not in existing:
            upsert_daily_summary(yesterday)
    except Exception as e:
        logger.debug(f"Daily summary check: {e}")


def _backfill_entry_price(symbol: str, fill_price: float, filled_qty: float, db_path: str):
    """Update position_lifecycle with actual Alpaca fill price."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        pos = conn.execute(
            "SELECT id FROM position_lifecycle WHERE symbol=? AND status='open' ORDER BY opened_at DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        if pos:
            conn.execute(
                "UPDATE position_lifecycle SET entry_price=?, entry_cost_basis=? WHERE id=?",
                (fill_price, fill_price * filled_qty, pos["id"])
            )
            conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Could not back-fill entry price for {symbol}: {e}")


def _update_calibration(parent_order_id: str, realized_pnl: float, db_path: str):
    """After a position closes, update ai_decisions calibration score."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        row = conn.execute(
            "SELECT ai_decision_id FROM trades WHERE order_id=?",
            (parent_order_id,)
        ).fetchone()
        if row and row["ai_decision_id"]:
            dec_id = row["ai_decision_id"]
            profitable = 1 if realized_pnl > 0 else 0
            conn.execute("""
                UPDATE ai_decisions SET
                    decisions_profitable = decisions_profitable + ?,
                    calibration_score = CAST(decisions_profitable + ? AS REAL) / NULLIF(decisions_made, 0)
                WHERE id = ?
            """, (profitable, profitable, dec_id))
            conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Could not update calibration for order {parent_order_id}: {e}")


def reconciler_loop(alpaca, interval_seconds: int = 30):
    logger.info(f"Reconciler started — polling every {interval_seconds}s")
    while not _stop_event.is_set():
        try:
            _reconcile_once(alpaca)
        except Exception as e:
            logger.error(f"Reconciler error: {e}", exc_info=True)
        _stop_event.wait(interval_seconds)
    logger.info("Reconciler stopped")


def start_reconciler(alpaca) -> threading.Thread:
    """Start reconciler in a daemon thread. Returns the thread."""
    global _reconciler_thread
    _stop_event.clear()
    _reconciler_thread = threading.Thread(
        target=reconciler_loop,
        args=(alpaca,),
        daemon=True,
        name="reconciler",
    )
    _reconciler_thread.start()
    return _reconciler_thread


def stop_reconciler():
    _stop_event.set()
    if _reconciler_thread and _reconciler_thread.is_alive():
        _reconciler_thread.join(timeout=5)
