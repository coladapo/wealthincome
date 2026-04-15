"""
Trader Daemon — standalone process, no Streamlit dependency.
Reads config from DB, runs Claude cycles, writes results to DB, executes on Alpaca.
"""

import os
import sys
import time
import signal
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db import (
    init_db, get_config, set_config,
    start_cycle, finish_cycle, fail_cycle,
    record_trade, update_trade_links, record_error, get_trades_today,
    record_ai_decision, increment_ai_decision_executed,
    record_order_group, record_order,
    open_position_lifecycle, get_open_position_by_symbol, close_position_lifecycle,
    update_order_group_exit,
    record_equity_snapshot, upsert_daily_summary,
)
from core.alpaca_client import AlpacaClient, AlpacaOrderSide
from core.claude_trader import run_claude_decision, SYSTEM_PROMPT
from core.indicators import compute_all
from core.market_regime import get_market_regime, regime_summary_for_claude
from core.watchlist import build_watchlist

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler("logs/trader.log"),
        logging.StreamHandler(),
    ]
)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("peewee").setLevel(logging.WARNING)
logger = logging.getLogger("trader")


# ─── Alpaca client (from env) ────────────────────────────────────────────────

def get_alpaca() -> AlpacaClient:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    paper = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
    if not api_key or not secret_key:
        raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in environment")
    return AlpacaClient(api_key=api_key, secret_key=secret_key, paper=paper)


# ─── Market data ─────────────────────────────────────────────────────────────

def fetch_market_data(watchlist: list, alpaca: AlpacaClient) -> Dict[str, Any]:
    import yfinance as yf
    data = {}
    for symbol in watchlist:
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="60d", interval="1d")
            bars = []
            if not hist.empty:
                for ts, row in hist.iterrows():
                    bars.append({
                        "t": str(ts.date()),
                        "o": round(float(row["Open"]), 2),
                        "h": round(float(row["High"]), 2),
                        "l": round(float(row["Low"]), 2),
                        "c": round(float(row["Close"]), 2),
                        "v": int(row["Volume"]),
                    })
            price = alpaca.get_current_price(symbol)
            if not price and bars:
                price = bars[-1]["c"]

            indicators = compute_all(bars)

            try:
                cal = ticker.calendar
                next_earnings = None
                if cal is not None and not cal.empty:
                    dates = cal.columns.tolist() if hasattr(cal, "columns") else []
                    if dates:
                        next_earnings = str(dates[0])
            except Exception:
                next_earnings = None

            data[symbol] = {
                **indicators,
                "current_price": price,
                "next_earnings": next_earnings,
                "bars": bars[-10:],
            }
        except Exception as e:
            logger.warning(f"Could not fetch data for {symbol}: {e}")
    return data


# ─── PDT guard ───────────────────────────────────────────────────────────────

def count_day_trades_today() -> int:
    trades = get_trades_today()
    symbols_traded = {}
    day_trades = 0
    for t in trades:
        sym = t["symbol"]
        action = t["action"]
        if sym not in symbols_traded:
            symbols_traded[sym] = []
        symbols_traded[sym].append(action)
    for sym, actions in symbols_traded.items():
        buys = actions.count("buy")
        sells = actions.count("sell")
        day_trades += min(buys, sells)
    return day_trades


# ─── Execution ───────────────────────────────────────────────────────────────

def execute_decision(
    d: Dict,
    account,
    positions: Dict,
    cfg: Dict,
    alpaca: AlpacaClient,
    cycle_id: int,
    ai_decision_id: int,
    market_data: Dict,
) -> Optional[int]:
    """Execute a single Claude decision. Returns trade_id or None."""
    symbol = d.get("symbol", "").upper()
    action = d.get("action", "hold").lower()
    confidence = float(d.get("confidence", 0))
    reasoning = d.get("reasoning", "")
    position_size_pct = float(d.get("position_size_pct", float(cfg["max_position_pct"])))

    if action == "hold" or not symbol:
        return None

    conf_threshold = float(cfg["confidence_threshold"])
    if confidence < conf_threshold:
        logger.info(f"Skipping {symbol} — confidence {confidence:.0%} below {conf_threshold:.0%}")
        return None

    current_price = alpaca.get_current_price(symbol)
    if not current_price:
        logger.warning(f"No price for {symbol} — skipping")
        return None

    max_pos = int(cfg["max_open_positions"])
    max_pos_pct = float(cfg["max_position_pct"])

    if action == "buy":
        if symbol in positions:
            logger.info(f"Already holding {symbol} — skipping")
            return None
        if len(positions) >= max_pos:
            logger.info(f"Max positions ({max_pos}) reached — skipping {symbol}")
            return None

        if not alpaca.paper:
            day_trades = count_day_trades_today()
            if day_trades >= 3:
                logger.warning(f"PDT limit approaching ({day_trades} today) — skipping {symbol}")
                record_error("pdt_guard", f"Blocked buy {symbol} — {day_trades} day trades today", cycle_id)
                return None

        capped_pct = min(position_size_pct, max_pos_pct)
        max_value = account.portfolio_value * capped_pct
        qty = int(min(max_value, account.cash * 0.95) / current_price)
        if qty < 1:
            logger.info(f"Insufficient cash for {symbol} @ ${current_price:.2f}")
            return None

        take_profit = round(current_price * 1.07, 2)
        stop_loss = round(current_price * 0.96, 2)

        order = alpaca.place_bracket_order(
            symbol=symbol, qty=qty, side=AlpacaOrderSide.BUY,
            take_profit_price=take_profit, stop_loss_price=stop_loss,
        )
        logger.info(f"BUY {qty} {symbol} @ ~${current_price:.2f} | {confidence:.0%} | {reasoning[:80]}")

        # Record trade (returns id now)
        trade_id = record_trade(cycle_id, {
            "symbol": symbol, "action": action, "qty": qty,
            "signal_price": current_price, "confidence": confidence,
            "reasoning": reasoning, "order_id": order.id,
            "order_status": order.status,
            "take_profit": take_profit, "stop_loss": stop_loss,
            "ai_decision_id": ai_decision_id,
        })

        # Extract TP/SL child order IDs from bracket legs
        tp_order_id = None
        sl_order_id = None
        if order.legs:
            for leg in order.legs:
                leg_type = leg.get("type", "")
                if leg_type == "limit":
                    tp_order_id = leg["id"]
                elif leg_type in ("stop", "stop_limit"):
                    sl_order_id = leg["id"]

        # Record order group (links parent + TP + SL)
        group_id = record_order_group(
            trade_id=trade_id, cycle_id=cycle_id, symbol=symbol,
            parent_order_id=order.id, parent_side="buy", parent_qty=qty,
            tp_order_id=tp_order_id, tp_limit_price=take_profit,
            sl_order_id=sl_order_id, sl_stop_price=stop_loss,
        )

        # Record orders audit log
        record_order(
            alpaca_order_id=order.id, symbol=symbol, side="buy",
            order_type="market", qty=qty, status=order.status,
            signal_price=current_price, order_class="bracket",
            time_in_force="gtc", cycle_id=cycle_id, trade_id=trade_id,
            order_group_id=group_id,
            raw_json=json.dumps(order.raw) if order.raw else None,
        )
        if tp_order_id:
            record_order(
                alpaca_order_id=tp_order_id, symbol=symbol, side="sell",
                order_type="limit", qty=qty, status="held",
                limit_price=take_profit, order_class="bracket",
                time_in_force="gtc", trade_id=trade_id, order_group_id=group_id,
            )
        if sl_order_id:
            record_order(
                alpaca_order_id=sl_order_id, symbol=symbol, side="sell",
                order_type="stop", qty=qty, status="held",
                stop_price=stop_loss, order_class="bracket",
                time_in_force="gtc", trade_id=trade_id, order_group_id=group_id,
            )

        # Open position lifecycle (entry price = signal_price placeholder; reconciler updates on fill)
        sym_data = market_data.get(symbol, {})
        pos_id = open_position_lifecycle(
            symbol=symbol, entry_price=current_price,
            entry_qty=qty, cycle_id=cycle_id, trade_id=trade_id,
            order_group_id=group_id,
            entry_rsi=sym_data.get("rsi_14"),
            entry_macd_histogram=sym_data.get("macd", {}).get("histogram") if sym_data.get("macd") else None,
            entry_atr_pct=sym_data.get("atr_pct"),
            entry_confidence=confidence,
        )

        update_trade_links(trade_id, order_group_id=group_id, position_lifecycle_id=pos_id)
        increment_ai_decision_executed(ai_decision_id)

        return trade_id

    elif action == "sell":
        if symbol not in positions:
            logger.info(f"Not holding {symbol} — skipping sell")
            return None

        qty = positions[symbol].qty
        order = alpaca.place_market_order(
            symbol=symbol, qty=qty, side=AlpacaOrderSide.SELL
        )
        take_profit = None
        stop_loss = None
        logger.info(f"SELL {qty} {symbol} @ ~${current_price:.2f} | {confidence:.0%} | {reasoning[:80]}")

        trade_id = record_trade(cycle_id, {
            "symbol": symbol, "action": action, "qty": qty,
            "signal_price": current_price, "confidence": confidence,
            "reasoning": reasoning, "order_id": order.id,
            "order_status": order.status,
            "take_profit": None, "stop_loss": None,
            "ai_decision_id": ai_decision_id,
        })

        record_order(
            alpaca_order_id=order.id, symbol=symbol, side="sell",
            order_type="market", qty=qty, status=order.status,
            signal_price=current_price, cycle_id=cycle_id, trade_id=trade_id,
        )

        # Close position lifecycle
        open_pos = get_open_position_by_symbol(symbol)
        if open_pos:
            close_position_lifecycle(
                position_id=open_pos["id"],
                exit_price=current_price,
                exit_qty=qty,
                close_reason="ai_sell",
                exit_cycle_id=cycle_id,
                exit_trade_id=trade_id,
            )
            # Resolve order group if one exists
            if open_pos.get("entry_order_group_id"):
                realized_pnl = (current_price - open_pos["entry_price"]) * qty
                update_order_group_exit(
                    parent_order_id=open_pos.get("entry_order_group_id_str", ""),
                    exit_trigger="manual_sell",
                    exit_fill_price=current_price,
                    exit_filled_at=datetime.now().isoformat(),
                    realized_pnl=realized_pnl,
                )

        increment_ai_decision_executed(ai_decision_id)
        return trade_id

    return None


# ─── Stop loss monitor ────────────────────────────────────────────────────────

def check_stop_losses(positions: Dict, alpaca: AlpacaClient,
                      max_loss_pct: float, cycle_id: int):
    for symbol, pos in positions.items():
        if pos.unrealized_plpc < -max_loss_pct:
            logger.warning(f"Stop loss: {symbol} at {pos.unrealized_plpc:.1%} — closing")
            try:
                alpaca.close_position(symbol)
                record_error("stop_loss_triggered",
                             f"{symbol} closed at {pos.unrealized_plpc:.1%}", cycle_id)
                # Close position lifecycle
                open_pos = get_open_position_by_symbol(symbol)
                if open_pos:
                    current_price = alpaca.get_current_price(symbol) or pos.current_price
                    close_position_lifecycle(
                        position_id=open_pos["id"],
                        exit_price=current_price,
                        exit_qty=pos.qty,
                        close_reason="stop_loss",
                        exit_cycle_id=cycle_id,
                    )
            except Exception as e:
                logger.error(f"Failed to close {symbol}: {e}")


# ─── Daily loss circuit breaker ───────────────────────────────────────────────

_today_baseline: float = None
_baseline_date: str = None


def is_daily_loss_limit_hit(current_value: float, limit_pct: float) -> bool:
    global _today_baseline, _baseline_date
    today = datetime.now().strftime("%Y-%m-%d")
    if _baseline_date != today:
        _today_baseline = current_value
        _baseline_date = today
        logger.info(f"New day baseline: ${current_value:,.2f}")
    if _today_baseline:
        daily_return = (current_value - _today_baseline) / _today_baseline
        if daily_return <= -limit_pct:
            return True
    return False


# ─── One cycle ────────────────────────────────────────────────────────────────

def run_cycle(alpaca: AlpacaClient):
    cfg = get_config()
    trade_hours_only = cfg["trade_only_market_hours"].lower() == "true"
    daily_loss_limit = float(cfg["daily_loss_limit_pct"])

    market_open = alpaca.is_market_open()
    cycle_id = start_cycle(market_open)

    try:
        if trade_hours_only and not market_open:
            logger.info("Market closed — skipping cycle")
            fail_cycle(cycle_id, "market_closed")
            return

        account = alpaca.get_account()
        if account.trading_blocked:
            logger.warning("Account trading blocked")
            fail_cycle(cycle_id, "account_blocked")
            return

        if is_daily_loss_limit_hit(account.portfolio_value, daily_loss_limit):
            logger.warning("Daily loss limit hit — halting")
            record_error("daily_loss_limit", f"Portfolio: ${account.portfolio_value:.2f}", cycle_id)
            fail_cycle(cycle_id, "daily_loss_limit")
            return

        positions = {p.symbol: p for p in alpaca.get_positions()}

        # Equity snapshot at cycle start
        unrealized_pnl = sum(p.unrealized_pl for p in positions.values())
        record_equity_snapshot(
            cycle_id=cycle_id,
            portfolio_value=account.portfolio_value,
            cash=account.cash,
            long_market_value=account.long_market_value,
            buying_power=account.buying_power,
            unrealized_pnl=unrealized_pnl,
            realized_pnl_today=0,
            open_positions=len(positions),
        )

        # Layer 1: Macro regime — determines how aggressive we trade
        logger.info("Fetching macro market regime...")
        try:
            regime_data = get_market_regime(include_sectors=True)
            regime = regime_data.get("regime", "CAUTION")
            regime_summary = regime_summary_for_claude(regime_data)
            max_pos_pct_override = regime_data.get("max_position_pct")
            new_entries_allowed = regime_data.get("new_entries_allowed", True)
            logger.info(f"Regime: {regime} (score={regime_data.get('score')}) | "
                        f"entries={'allowed' if new_entries_allowed else 'BLOCKED'}")
        except Exception as e:
            logger.warning(f"Regime fetch failed: {e} — defaulting to CAUTION")
            regime = "CAUTION"
            regime_summary = "MARKET REGIME: CAUTION (data unavailable)\nNew entries allowed: YES\nMax position size: 6%"
            max_pos_pct_override = None
            new_entries_allowed = True

        # Layer 2: Dynamic watchlist — top momentum stocks for current regime
        logger.info("Building dynamic watchlist...")
        try:
            wl_data = build_watchlist(regime=regime, top_n=20)
            watchlist = wl_data.get("symbols", [])
            logger.info(f"Dynamic watchlist: {len(watchlist)} symbols | "
                        f"screened {wl_data.get('universe_screened', 0)}")
        except Exception as e:
            logger.warning(f"Dynamic watchlist failed: {e} — using config watchlist")
            cfg2 = get_config()
            watchlist = [s.strip() for s in cfg2["watchlist"].split(",") if s.strip()]

        logger.info(f"Fetching market data for {len(watchlist)} symbols...")
        market_data = fetch_market_data(watchlist, alpaca)

        portfolio_snapshot = {
            "positions": [
                {
                    "symbol": p.symbol, "qty": p.qty,
                    "avg_entry_price": p.avg_entry_price,
                    "current_price": p.current_price,
                    "market_value": p.market_value,
                    "unrealized_pl": p.unrealized_pl,
                    "unrealized_plpc": p.unrealized_plpc,
                }
                for p in positions.values()
            ]
        }

        account_snapshot = {
            "portfolio_value": account.portfolio_value,
            "cash": account.cash,
            "buying_power": account.buying_power,
            "daily_pnl": (account.portfolio_value - _today_baseline) if _today_baseline else None,
            "daily_pnl_pct": (
                (account.portfolio_value - _today_baseline) / _today_baseline * 100
                if _today_baseline else None
            ),
        }

        logger.info(f"Asking Claude to analyze {len(watchlist)} symbols...")
        result = run_claude_decision(
            watchlist=watchlist,
            market_data=market_data,
            portfolio=portfolio_snapshot,
            account=account_snapshot,
            regime_summary=regime_summary,
        )

        if not result:
            logger.warning("Claude returned no decision")
            record_error("claude_no_decision", "Empty response", cycle_id)
            fail_cycle(cycle_id, "claude_error")
            return

        # Extract metadata before storing
        usage = result.pop("_usage", {})
        duration_ms = result.pop("_duration_ms", None)
        raw_response = result.pop("_raw_response", "")
        user_prompt = result.pop("_user_prompt", "")

        # Record full AI decision with complete context
        ai_decision_id = record_ai_decision(
            cycle_id=cycle_id,
            prompt_user=user_prompt,
            prompt_system=SYSTEM_PROMPT,
            market_snapshot=market_data,
            account_snapshot=account_snapshot,
            positions_snapshot=portfolio_snapshot,
            raw_response=raw_response,
            parsed_decisions=result.get("decisions", []),
            usage=usage,
            duration_ms=duration_ms,
        )

        logger.info(f"Market: {result.get('market_summary', '')}")
        finish_cycle(cycle_id, result, usage=usage, duration_ms=duration_ms)

        for d in result.get("decisions", []):
            try:
                execute_decision(d, account, positions, cfg, alpaca, cycle_id, ai_decision_id, market_data)
            except Exception as e:
                logger.error(f"Execution error {d.get('symbol')}: {e}")
                record_error("execution_error", f"{d.get('symbol')}: {e}", cycle_id)

        check_stop_losses(positions, alpaca, float(cfg["max_position_pct"]), cycle_id)

        # Daily summary — finalize yesterday if new day just started
        today = datetime.now().strftime("%Y-%m-%d")
        if _baseline_date and _baseline_date != today:
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            try:
                upsert_daily_summary(yesterday)
            except Exception as e:
                logger.warning(f"Could not upsert daily summary: {e}")

    except Exception as e:
        logger.error(f"Cycle error: {e}", exc_info=True)
        record_error("cycle_error", str(e), cycle_id)
        fail_cycle(cycle_id, str(e))


# ─── Main loop ────────────────────────────────────────────────────────────────

_running = True


def handle_signal(sig, frame):
    global _running
    logger.info(f"Received signal {sig} — shutting down")
    _running = False


def main():
    global _running

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    os.makedirs("logs", exist_ok=True)
    init_db()
    set_config("trader_running", "true")

    alpaca = get_alpaca()
    logger.info(f"Trader daemon started — {'PAPER' if alpaca.paper else 'LIVE'} mode")

    account = alpaca.get_account()
    logger.info(f"Account: ${account.portfolio_value:,.2f} | Status: {account.status}")

    while _running:
        cfg = get_config()
        if cfg.get("trader_running", "true") == "false":
            logger.info("Stopped via config — waiting...")
            time.sleep(10)
            continue

        run_cycle(alpaca)

        cfg = get_config()
        interval = int(cfg.get("poll_interval", 300))
        logger.info(f"Sleeping {interval}s until next cycle...")

        for _ in range(interval):
            if not _running:
                break
            cfg = get_config()
            if cfg.get("trader_running") == "false":
                break
            time.sleep(1)

    set_config("trader_running", "false")
    logger.info("Trader daemon stopped")


if __name__ == "__main__":
    main()
