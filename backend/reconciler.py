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
        record_trade_analysis, record_post_exit, backfill_post_exit_prices,
    )
    from core.trade_analyzer import analyze_closed_position

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
                        # Record post-exit tracking skeleton
                        try:
                            record_post_exit(
                                position_id=pos["id"],
                                symbol=pos["symbol"],
                                exit_price=exit_price,
                                exit_date=(exit_at or datetime.now().isoformat())[:10],
                            )
                        except Exception as pe:
                            logger.warning(f"record_post_exit failed for {pos['symbol']}: {pe}")
                        # Update AI decision calibration
                        _update_calibration(group["parent_order_id"], realized_pnl, DB_PATH)
                        # Feature 3: analyze closed position and record
                        try:
                            pos_for_analysis = dict(pos)
                            pos_for_analysis["exit_price"] = exit_price
                            pos_for_analysis["exit_qty"] = qty
                            pos_for_analysis["closed_at"] = exit_at or datetime.now().isoformat()
                            analysis = analyze_closed_position(pos_for_analysis)
                            analysis["symbol"] = pos["symbol"]
                            record_trade_analysis(pos["id"], analysis)
                        except Exception as ae:
                            logger.warning(f"Trade analysis failed for {pos['symbol']}: {ae}")
                        # Recalibrate signal intelligence after every close
                        try:
                            from core.performance_intelligence import run_signal_calibration
                            run_signal_calibration()
                        except Exception:
                            pass
                        break

                logger.info(
                    f"Bracket resolved: {group['symbol']} via {exit_trigger} | "
                    f"entry=${entry_price:.2f} exit=${exit_price:.2f} | "
                    f"P&L=${realized_pnl:+.2f}"
                )

        except Exception as e:
            logger.warning(f"Failed to reconcile group {group.get('id')} ({group.get('symbol')}): {e}")

    # ── 3. Reconcile plain sell fills → close position_lifecycle ────────────────
    # Handles manual sells and Claude market-sell orders that aren't bracket exits.
    # Looks at all filled SELL orders on Alpaca and closes any open position_lifecycle
    # records that don't already have a close event.
    try:
        _reconcile_sell_fills(alpaca, DB_PATH)
    except Exception as e:
        logger.warning(f"Sell fill reconciliation error: {e}")

    # ── 4. SMA50 exit monitor (MomentumHold strategy) ────────────────────────
    # Only run when market is open — avoid false triggers from after-hours prices
    try:
        if alpaca.is_market_open():
            _check_sma50_exits(alpaca, DB_PATH)
    except Exception as e:
        logger.debug(f"SMA50 exit check error: {e}")

    # ── 4. Upsert daily summary for yesterday if missing ─────────────────────
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

    # ── 5. Backfill post-exit follow-up prices ───────────────────────────────
    try:
        backfill_post_exit_prices()
    except Exception as e:
        logger.debug(f"Post-exit backfill error: {e}")


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
        # Also backfill fill_price on the trades record so dashboard shows actuals
        conn.execute(
            """UPDATE trades SET fill_price=?,
               slippage_pct=CASE WHEN signal_price > 0
                                 THEN ROUND((? - signal_price) / signal_price * 100, 4)
                                 ELSE NULL END
               WHERE id = (
                   SELECT id FROM trades
                   WHERE symbol=? AND action='buy' AND fill_price IS NULL
                   ORDER BY executed_at DESC LIMIT 1
               )""",
            (fill_price, fill_price, symbol)
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


def _reconcile_sell_fills(alpaca, db_path: str):
    """
    Close any open position_lifecycle records where Alpaca shows the position
    is no longer held (filled sell orders).

    Covers three cases:
      1. Manual sells placed via /order endpoint (no trade record at all)
      2. Claude market-sell orders (trade recorded, but position_lifecycle not closed)
      3. Overnight bug cleanup — positions closed but lifecycle still shows open

    Strategy: fetch all filled SELL orders from Alpaca (last 100), cross-reference
    with open position_lifecycle records. If a symbol has a filled sell and an open
    lifecycle, close it.
    """
    from backend.db import get_open_positions_lifecycle, close_position_lifecycle, record_post_exit

    try:
        # Get all currently open positions on Alpaca
        alpaca_positions = {p.symbol for p in alpaca.get_positions()}

        # Get our open lifecycle records
        open_lifecycles = get_open_positions_lifecycle()
        if not open_lifecycles:
            return

        # Find lifecycle records for symbols no longer held on Alpaca
        for pos in open_lifecycles:
            symbol = pos["symbol"]
            if symbol in alpaca_positions:
                continue  # Still held — nothing to close

            # Symbol is no longer on Alpaca but lifecycle is still open
            # Find the fill price from recent filled sell orders
            exit_price = None
            exit_at = None
            close_reason = "manual_sell"

            try:
                # Look for a recent filled sell order for this symbol
                # Alpaca status="all" returns filled, cancelled, and open orders
                recent_orders = alpaca.get_orders(status="all", limit=200)
                for o in recent_orders:
                    if o.symbol == symbol and o.side == "sell" and o.status == "filled":
                        exit_price = o.filled_avg_price
                        exit_at = o.filled_at
                        close_reason = "sell_filled"
                        break
            except Exception:
                pass

            # Fallback: use entry price if we can't find fill (marks at cost, not ideal but correct)
            if not exit_price:
                try:
                    current = alpaca.get_current_price(symbol)
                    if current:
                        exit_price = current
                        close_reason = "position_closed"
                except Exception:
                    pass

            if not exit_price:
                exit_price = pos.get("entry_price", 0)

            entry_price = pos.get("entry_price") or 0
            qty = pos.get("entry_qty") or 0
            realized_pnl = (exit_price - entry_price) * qty if entry_price else None

            close_position_lifecycle(
                position_id=pos["id"],
                exit_price=exit_price,
                exit_qty=qty,
                close_reason=close_reason,
            )

            try:
                record_post_exit(
                    position_id=pos["id"],
                    symbol=symbol,
                    exit_price=exit_price,
                    exit_date=(exit_at or datetime.now().isoformat())[:10],
                )
            except Exception:
                pass

            pnl_str = f"${realized_pnl:+.2f}" if realized_pnl is not None else "unknown"
            logger.info(
                f"Lifecycle closed via sell reconciliation: {symbol} "
                f"entry=${entry_price:.2f} exit=${exit_price:.2f} qty={qty} "
                f"P&L={pnl_str} reason={close_reason}"
            )
            try:
                from core.performance_intelligence import run_signal_calibration
                run_signal_calibration()
            except Exception:
                pass

    except Exception as e:
        logger.warning(f"_reconcile_sell_fills failed: {e}")


def _check_sma50_exits(alpaca, db_path: str):
    """
    MomentumHold exit rule: close position if price closes below SMA50 for 2+ bars.
    Also cancels the trailing stop order so Alpaca doesn't double-exit.

    Uses a persistent breach_count in position_lifecycle so restarts don't reset progress.
    """
    import yfinance as yf
    import numpy as np
    import warnings
    warnings.filterwarnings("ignore")

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        open_positions = conn.execute(
            "SELECT id, symbol, entry_price, entry_qty, sma50_breach_count, trailing_stop_order_id "
            "FROM position_lifecycle WHERE status='open'"
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f"SMA50 exit: DB read failed: {e}")
        return

    for pos in open_positions:
        symbol = pos["symbol"]
        try:
            df = yf.download(symbol, period="10d", progress=False,
                             timeout=8, auto_adjust=True)
            if df.empty or len(df) < 3:
                continue
            if hasattr(df.columns, "get_level_values"):
                df.columns = df.columns.get_level_values(0)

            closes = list(df["Close"].dropna())
            if len(closes) < 52:
                # Not enough history for SMA50 — fetch more
                df2 = yf.download(symbol, period="80d", progress=False,
                                  timeout=8, auto_adjust=True)
                if not df2.empty:
                    if hasattr(df2.columns, "get_level_values"):
                        df2.columns = df2.columns.get_level_values(0)
                    closes = list(df2["Close"].dropna())

            if len(closes) < 52:
                continue

            sma50 = float(np.mean(closes[-50:]))
            last_close = closes[-1]
            prev_close = closes[-2]

            below_today = last_close < sma50
            below_yesterday = prev_close < float(np.mean(closes[-51:-1]))

            breach_count = pos["sma50_breach_count"] or 0

            if below_today:
                breach_count += 1
            else:
                if breach_count > 0:
                    logger.info(f"SMA50 breach reset: {symbol} closed above SMA50 (${last_close:.2f} > ${sma50:.2f})")
                breach_count = 0

            # Persist the updated count
            conn2 = sqlite3.connect(db_path)
            conn2.execute("PRAGMA journal_mode=WAL")
            conn2.execute(
                "UPDATE position_lifecycle SET sma50_breach_count=? WHERE id=?",
                (breach_count, pos["id"])
            )
            conn2.commit()
            conn2.close()

            logger.debug(
                f"SMA50 check {symbol}: close=${last_close:.2f} sma50=${sma50:.2f} "
                f"below={below_today} breach_count={breach_count}"
            )

            # EXIT: 2+ consecutive closes below SMA50
            if breach_count >= 2:
                logger.warning(
                    f"SMA50 EXIT TRIGGERED: {symbol} — {breach_count} consecutive closes "
                    f"below SMA50 (${last_close:.2f} < ${sma50:.2f})"
                )

                # Cancel trailing stop first to avoid double-exit
                ts_order_id = pos["trailing_stop_order_id"]
                if ts_order_id:
                    try:
                        alpaca.cancel_order(ts_order_id)
                        logger.info(f"Cancelled trailing stop {ts_order_id[:8]}… for {symbol}")
                    except Exception as e:
                        logger.warning(f"Could not cancel trailing stop for {symbol}: {e}")

                # Close the position via market order
                try:
                    alpaca.close_position(symbol)

                    current_price = alpaca.get_current_price(symbol) or last_close
                    from backend.db import (
                        get_open_positions_lifecycle, close_position_lifecycle,
                    )
                    open_pos_list = get_open_positions_lifecycle()
                    for open_pos in open_pos_list:
                        if open_pos["symbol"] == symbol:
                            close_position_lifecycle(
                                position_id=open_pos["id"],
                                exit_price=current_price,
                                exit_qty=open_pos["entry_qty"],
                                close_reason="sma50_breach",
                            )
                            # Record post-exit tracking skeleton
                            try:
                                from backend.db import record_post_exit
                                record_post_exit(
                                    position_id=open_pos["id"],
                                    symbol=symbol,
                                    exit_price=current_price,
                                    exit_date=datetime.now().strftime("%Y-%m-%d"),
                                )
                            except Exception as pe:
                                logger.warning(f"record_post_exit (sma50) failed for {symbol}: {pe}")
                            _update_calibration(
                                open_pos.get("entry_order_group_id", ""),
                                (current_price - open_pos["entry_price"]) * open_pos["entry_qty"],
                                db_path,
                            )
                            # Feature 3: analyze closed position
                            try:
                                from backend.db import record_trade_analysis
                                from core.trade_analyzer import analyze_closed_position
                                pos_for_analysis = dict(open_pos)
                                pos_for_analysis["exit_price"] = current_price
                                pos_for_analysis["closed_at"] = datetime.now().isoformat()
                                analysis = analyze_closed_position(pos_for_analysis)
                                analysis["symbol"] = symbol
                                record_trade_analysis(open_pos["id"], analysis)
                            except Exception as ae:
                                logger.warning(f"Trade analysis (sma50) failed for {symbol}: {ae}")
                            break

                    logger.info(f"SMA50 exit executed: {symbol} @ ~${current_price:.2f}")

                except Exception as e:
                    logger.error(f"Failed to execute SMA50 exit for {symbol}: {e}")

        except Exception as e:
            logger.warning(f"SMA50 check failed for {symbol}: {e}")


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
