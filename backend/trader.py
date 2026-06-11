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
import threading
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
    update_order_group_exit, update_position_trailing_stop,
    record_equity_snapshot, upsert_daily_summary,
    get_performance_summary,
)
from core.alpaca_client import AlpacaClient, AlpacaOrderSide, AlpacaTimeInForce
from core.claude_trader import SYSTEM_PROMPT, build_session_feedback_block
from core.llm_router import run_decision
from core.validation_agent import validate_decisions, record_validation, BLOCK
from core.trade_rag import build_portfolio_rag_block
from core.indicators import compute_all
from core.market_regime import get_market_regime, regime_summary_for_claude
from core.watchlist import build_watchlist
from core.trade_analyzer import build_feedback_block_for_claude
from core.economic_calendar import get_calendar_summary_for_claude, is_high_risk_window
from core.news_sentiment import get_news_summary, build_news_block_for_claude
from core.portfolio_risk import (
    compute_correlation_matrix, check_entry_correlation,
    build_correlation_heatmap_text, compute_portfolio_concentration,
)
from core.tick_agent import (
    ensure_table as ensure_tick_table,
    snapshot_symbols as tick_snapshot_symbols,
    get_latest_snapshots as tick_get_latest,
    build_vwap_block_for_claude,
    run_tick_loop,
)
from core.options_flow import (
    get_options_flow,
    build_options_flow_block_for_claude,
    save_options_flow_to_db,
)
from core.edgar_agent import (
    get_insider_signals,
    build_insider_block_for_claude,
    save_insider_signals_to_db,
    ensure_edgar_table,
)
from core.signal_enricher import get_enriched_context

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.FileHandler("logs/trader.log"),
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

# ETFs don't have earnings calendars — calling .calendar on them triggers yfinance 404 every cycle
_ETF_SYMBOLS = {
    "SPY", "QQQ", "IWM", "DIA", "XLK", "XLE", "XLF", "XLV", "XLI",
    "XLU", "XLP", "XLB", "XLRE", "XLY", "XLC", "GLD", "SLV", "TLT",
    "HYG", "LQD", "EEM", "EFA", "VTI", "VNQ", "ARKK", "SQQQ", "TQQQ",
}


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

            # Skip calendar fetch for ETFs — they have no earnings and yfinance returns 404
            next_earnings = None
            if symbol not in _ETF_SYMBOLS:
                try:
                    cal = ticker.calendar
                    if cal is not None and not cal.empty:
                        dates = cal.columns.tolist() if hasattr(cal, "columns") else []
                        if dates:
                            next_earnings = str(dates[0])
                except Exception:
                    pass

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
    corr_matrix=None,
    regime_at_entry: str = None,
    regime_score: int = None,
    news_sentiment: float = None,
    correlation_score: float = None,
    momentum_score: float = None,
    days_until_earnings: int = None,
) -> Optional[int]:
    """Execute a single Claude decision. Returns trade_id or None."""
    def _safe_float(value, default):
        # The LLM occasionally returns explicit None for numeric fields.
        # float(None) raises and silently kills the trade — coerce to default.
        try:
            return float(value if value is not None else default)
        except (TypeError, ValueError):
            return float(default)

    symbol = (d.get("symbol") or "").upper()
    action = (d.get("action") or "hold").lower()
    confidence = _safe_float(d.get("confidence"), 0)
    reasoning = d.get("reasoning") or ""
    position_size_pct = _safe_float(d.get("position_size_pct"), cfg["max_position_pct"])
    reduce_pct = _safe_float(d.get("reduce_pct"), 1.0)  # fraction of position to sell

    # Catalyst risk assessment — tiered framework for stops and sizing
    try:
        from core.catalyst_risk import get_catalyst_risk
        catalyst = get_catalyst_risk(symbol, days_until_earnings=days_until_earnings)
    except Exception as _ce:
        logger.warning(f"Catalyst risk check failed for {symbol}: {_ce} — defaulting to clear")
        from core.catalyst_risk import _clear
        catalyst = _clear()

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

        # Guard: check for pending/open orders on this symbol — prevents double-buying
        # when orders are queued but not yet filled (e.g. pre-market, between cycles)
        try:
            open_orders = alpaca.get_orders(status="open", limit=100)
            pending_syms = {o.symbol for o in open_orders}
            if symbol in pending_syms:
                logger.info(f"Open order already exists for {symbol} — skipping to prevent duplicate")
                return None
        except Exception as e:
            # Fail closed — if we can't verify no duplicate, don't place the order
            logger.warning(f"Could not check open orders for {symbol}: {e} — skipping buy to avoid duplicate")
            return None

        if len(positions) >= max_pos:
            logger.info(f"Max positions ({max_pos}) reached — skipping {symbol}")
            return None

        # Feature 5: Correlation guard — block if too correlated with open positions
        if corr_matrix is not None and len(positions) > 0:
            open_syms = list(positions.keys())
            corr_check = check_entry_correlation(symbol, open_syms, corr_matrix, threshold=0.75)
            if corr_check["blocked"]:
                logger.info(f"Correlation block: {symbol} — {corr_check['reason']}")
                record_error("correlation_blocked", f"{symbol}: {corr_check['reason']}", cycle_id)
                return None

        # Catalyst risk — tiered framework (earnings, FOMC, CPI, NFP, ex-div, rebalance)
        calendar_risk_flag = None
        if catalyst.block_new_entry:
            logger.info(f"Catalyst block [tier {catalyst.tier}]: {symbol} — {catalyst.primary_reason}")
            record_error("calendar_blocked", f"{symbol}: {catalyst.primary_reason}", cycle_id)
            return None
        if catalyst.position_size_multiplier < 1.0:
            position_size_pct = position_size_pct * catalyst.position_size_multiplier
            calendar_risk_flag = catalyst.primary_reason
            logger.info(
                f"Catalyst size reduction [tier {catalyst.tier}]: {symbol} "
                f"×{catalyst.position_size_multiplier:.1f} — {catalyst.primary_reason}"
            )

        if not alpaca.paper:
            day_trades = count_day_trades_today()
            if day_trades >= 3:
                logger.warning(f"PDT limit approaching ({day_trades} today) — skipping {symbol}")
                record_error("pdt_guard", f"Blocked buy {symbol} — {day_trades} day trades today", cycle_id)
                return None

        # Hard guard: never use margin — only deploy actual cash on hand
        available_cash = max(float(account.cash), 0.0)
        if available_cash < 1.0:
            logger.info(f"No cash available (cash=${float(account.cash):,.0f}) — skipping {symbol}")
            return None

        # Hard guard: never deploy more than 80% of portfolio value
        # Prevents over-concentration even if Claude requests a large position
        from core.risk_limits import MAX_DEPLOY_PCT, MAX_SINGLE_POSITION_PCT
        long_market_value = float(getattr(account, "long_market_value", 0) or 0)
        deployed_pct = long_market_value / float(account.portfolio_value) if account.portfolio_value else 0
        if deployed_pct >= MAX_DEPLOY_PCT:
            logger.info(
                f"Portfolio at {deployed_pct:.0%} deployed (max {MAX_DEPLOY_PCT:.0%}) — "
                f"skipping {symbol} to avoid over-concentration"
            )
            return None

        # Hard concentration cap from core/risk_limits.py — single source of truth
        # shared with the manual-order paths. Position sizing beats stop placement
        # as the core risk lever; DB config may size lower, never higher.
        capped_pct = min(position_size_pct, max_pos_pct, MAX_SINGLE_POSITION_PCT)
        max_value = min(
            account.portfolio_value * capped_pct,  # sizing rule
            available_cash * 0.95,                  # cash constraint — never go negative
            account.portfolio_value * (MAX_DEPLOY_PCT - deployed_pct),  # room left under 80% cap
        )
        qty = int(max_value / current_price)
        if qty < 1:
            logger.info(f"Insufficient cash for {symbol} @ ${current_price:.2f} (available=${available_cash:,.0f})")
            return None

        # MomentumHold strategy: market entry + ATR-based trailing stop
        # NO fixed take-profit — we ride the trend until it breaks
        sym_data = market_data.get(symbol, {})
        atr_pct = sym_data.get("atr_pct", 2.0)
        # Trail = 2.5x ATR, floored at 12%, capped at 25%
        # Wider trail = lets winners run through normal volatility
        trail_percent = round(max(12.0, min(25.0, atr_pct * 2.5)), 2)

        # Step 1: Place IOC limit entry at signal price + 0.1% slippage allowance.
        # IOC (Immediate-Or-Cancel): fills at limit price or better, cancels any unfilled
        # portion instantly — no lingering open orders that can queue up between cycles.
        #
        # RETRY LADDER (G3, 2026-06-11): historically ~50% of IOC entries cancelled
        # unfilled (price moved past the 0.1% allowance between signal and placement)
        # and nothing retried — the intended trade silently never existed. Now we
        # verify the IOC outcome and retry ONCE at a fresh price with a 0.5%
        # allowance. If that cancels too, we give up loudly (no price-chasing).
        def _place_ioc(limit_px):
            return alpaca.place_limit_order(
                symbol=symbol, qty=qty, side=AlpacaOrderSide.BUY,
                limit_price=limit_px,
                time_in_force=AlpacaTimeInForce.IOC,
                enforce_cap=False,  # trader sizes against its own cap (core/risk_limits.py)
            )

        def _ioc_unfilled(o):
            """True if the IOC resolved as cancelled with nothing filled."""
            try:
                time.sleep(1.5)  # IOC resolves near-instantly; give Alpaca a beat
                raw = alpaca.get_order_raw(o.id) or {}
                return (raw.get("status") == "canceled"
                        and float(raw.get("filled_qty") or 0) == 0)
            except Exception:
                return False  # can't verify — assume placed; reconciler sorts it out

        limit_price = round(current_price * 1.001, 2)  # allow 0.1% above signal
        order = None
        order_type_used = "limit_ioc"
        try:
            order = _place_ioc(limit_price)
            logger.info(
                f"BUY {qty} {symbol} limit=${limit_price:.2f} (signal=${current_price:.2f} +0.1%) IOC | "
                f"{confidence:.0%} | trail={trail_percent}% | {reasoning[:60]}"
            )
            if _ioc_unfilled(order):
                fresh_price = alpaca.get_current_price(symbol) or current_price
                retry_limit = round(fresh_price * 1.005, 2)  # 0.5% allowance on retry
                logger.warning(
                    f"IOC entry cancelled unfilled for {symbol} (limit ${limit_price:.2f}) — "
                    f"retrying once at ${retry_limit:.2f} (fresh ${fresh_price:.2f} +0.5%)"
                )
                order = _place_ioc(retry_limit)
                order_type_used = "limit_ioc_retry"
                if _ioc_unfilled(order):
                    logger.warning(
                        f"ENTRY MISSED: {symbol} IOC retry also cancelled unfilled — "
                        f"not chasing. Signal ${current_price:.2f}, last try ${retry_limit:.2f}."
                    )
        except Exception as e:
            logger.warning(f"Limit order failed for {symbol}: {e} — falling back to market")
            order = alpaca.place_market_order(symbol=symbol, qty=qty, side=AlpacaOrderSide.BUY, enforce_cap=False)
            order_type_used = "market"
            logger.info(
                f"BUY {qty} {symbol} MARKET @ ~${current_price:.2f} | "
                f"{confidence:.0%} | trail={trail_percent}% | {reasoning[:60]}"
            )

        trade_id = record_trade(cycle_id, {
            "symbol": symbol, "action": action, "qty": qty,
            "signal_price": current_price, "confidence": confidence,
            "reasoning": reasoning, "order_id": order.id,
            "order_status": order.status,
            "take_profit": None,          # no fixed TP — MomentumHold
            "stop_loss": trail_percent,   # store trail% here for reference
            "ai_decision_id": ai_decision_id,
            "trail_percent": trail_percent,
            "calendar_risk_flag": calendar_risk_flag,
            "news_sentiment_score": news_sentiment,
            "correlation_with_portfolio": correlation_score,
            "regime_at_entry": regime_at_entry,
        })

        group_id = record_order_group(
            trade_id=trade_id, cycle_id=cycle_id, symbol=symbol,
            parent_order_id=order.id, parent_side="buy", parent_qty=qty,
            tp_order_id=None, tp_limit_price=None,
            sl_order_id=None, sl_stop_price=None,
        )

        record_order(
            alpaca_order_id=order.id, symbol=symbol, side="buy",
            order_type=order_type_used, qty=qty, status=order.status,
            signal_price=current_price, order_class="simple",
            time_in_force="ioc" if order_type_used == "limit_ioc" else "day",
            cycle_id=cycle_id, trade_id=trade_id,
            order_group_id=group_id,
            raw_json=json.dumps(order.raw) if order.raw else None,
        )

        # Extract signal attribution fields
        macd_data = sym_data.get("macd") or {}
        volume_data = sym_data.get("volume") or {}
        signal_summary = sym_data.get("signal_summary") or {}
        volume_ratio = volume_data.get("ratio") if isinstance(volume_data, dict) else None

        # Scout-quality flags — keys must match SCOUT_SIGNAL_FLAGS in
        # core/scout_quality.py. Booleans only; missing data → False.
        try:
            vwap_above = bool(sym_data.get("vwap") and sym_data.get("price") and
                              sym_data["price"] > sym_data["vwap"])
        except Exception:
            vwap_above = False

        options_flow_block = sym_data.get("options_flow") or {}
        unusual_call_volume = bool(
            isinstance(options_flow_block, dict)
            and options_flow_block.get("call_put_ratio") is not None
            and options_flow_block.get("call_put_ratio") > 1.5
        )

        insider_block = sym_data.get("insider") or {}
        insider_cluster_buy = bool(
            isinstance(insider_block, dict)
            and (insider_block.get("cluster_buy") or insider_block.get("buys_30d", 0) >= 2)
        )

        earnings_block = sym_data.get("earnings") or {}
        days_to_er = (
            earnings_block.get("days_until") if isinstance(earnings_block, dict) else None
        )
        earnings_within_7d = bool(days_to_er is not None and 0 <= days_to_er <= 7)

        rag_block = sym_data.get("rag") or {}
        similar_winrate = (
            rag_block.get("similar_trades_winrate") if isinstance(rag_block, dict) else None
        )
        similar_trades_winrate_high = bool(
            similar_winrate is not None and similar_winrate >= 0.6
        )

        macro_block = sym_data.get("macro") or {}
        macro_supportive = bool(
            isinstance(macro_block, dict)
            and (macro_block.get("regime") in ("bull", "supportive") or macro_block.get("score", 0) > 0.5)
        )

        signal_summary.update({
            "vwap_above": vwap_above,
            "unusual_call_volume": unusual_call_volume,
            "insider_cluster_buy": insider_cluster_buy,
            "earnings_within_7d": earnings_within_7d,
            "similar_trades_winrate_high": similar_trades_winrate_high,
            "macro_supportive": macro_supportive,
        })
        entry_signals_json = json.dumps(signal_summary)

        pos_id = open_position_lifecycle(
            symbol=symbol, entry_price=current_price,
            entry_qty=qty, cycle_id=cycle_id, trade_id=trade_id,
            order_group_id=group_id,
            entry_rsi=sym_data.get("rsi_14"),
            entry_macd_histogram=macd_data.get("histogram"),
            entry_atr_pct=atr_pct,
            entry_confidence=confidence,
            regime_at_entry=regime_at_entry,
            regime_score=regime_score,
            entry_signals_json=entry_signals_json,
            momentum_score_at_entry=momentum_score,
            entry_sma20=sym_data.get("sma_20"),
            entry_sma50=sym_data.get("sma_50"),
            entry_volume_ratio=volume_ratio,
        )

        update_trade_links(trade_id, order_group_id=group_id, position_lifecycle_id=pos_id)

        # Step 2: Trailing stop — tiered catalyst framework
        # Tier 0 (clear):          place stop at ATR-based trail_percent
        # Tier 1 (widen):          place stop at trail_percent * widen_multiplier
        # Tier 2 (suspend):        skip stop; AI + SMA50 monitoring as backup
        # Tier 3 (reduce+suspend): skip stop (entry was already blocked or heavily sized down)
        if catalyst.suspend_trailing_stop:
            logger.info(
                f"Trailing stop suspended [tier {catalyst.tier}] for {symbol} — "
                f"{catalyst.primary_reason}; SMA50 + AI monitoring active"
            )
        else:
            effective_trail = round(trail_percent * catalyst.widen_stop_multiplier, 2)
            if catalyst.widen_stop_multiplier > 1.0:
                logger.info(
                    f"Trailing stop widened [tier {catalyst.tier}] for {symbol}: "
                    f"{trail_percent}% → {effective_trail}% — {catalyst.primary_reason}"
                )
            try:
                ts_order = alpaca.place_trailing_stop_order(
                    symbol=symbol, qty=qty, trail_percent=effective_trail
                )
                update_position_trailing_stop(pos_id, ts_order.id, effective_trail)
                record_order(
                    alpaca_order_id=ts_order.id, symbol=symbol, side="sell",
                    order_type="trailing_stop", qty=qty, status=ts_order.status,
                    order_class="simple", time_in_force="gtc",
                    trade_id=trade_id, order_group_id=group_id,
                )
                logger.info(f"Trailing stop placed: {symbol} trail={effective_trail}% order={ts_order.id}")
            except Exception as e:
                logger.warning(f"Could not place trailing stop for {symbol}: {e} — reconciler will monitor")
                record_error(
                    "trailing_stop_failed",
                    f"{symbol}: {e} — position {pos_id} has no stop, trail={effective_trail}%",
                    cycle_id,
                )

        increment_ai_decision_executed(ai_decision_id)
        return trade_id

    elif action == "sell":
        if symbol not in positions:
            logger.info(f"Not holding {symbol} — skipping sell")
            return None

        full_qty = int(positions[symbol].qty)
        reduce_pct_clamped = max(0.0, min(1.0, reduce_pct))
        qty = max(1, round(full_qty * reduce_pct_clamped))
        is_partial = qty < full_qty
        order = alpaca.place_market_order(
            symbol=symbol, qty=qty, side=AlpacaOrderSide.SELL
        )
        take_profit = None
        stop_loss = None
        sell_label = f"PARTIAL SELL ({reduce_pct_clamped:.0%})" if is_partial else "SELL"
        logger.info(f"{sell_label} {qty}/{full_qty} {symbol} @ ~${current_price:.2f} | {confidence:.0%} | {reasoning[:80]}")

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

        # Close position lifecycle only on full exits
        open_pos = get_open_position_by_symbol(symbol)
        if open_pos and not is_partial:
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
                # Look up the parent_order_id string from the order_groups table
                from backend.db import get_order_groups
                groups = get_order_groups(limit=200)
                parent_oid = None
                for g in groups:
                    if g.get("id") == open_pos["entry_order_group_id"]:
                        parent_oid = g.get("parent_order_id")
                        break
                if parent_oid:
                    update_order_group_exit(
                        parent_order_id=parent_oid,
                        exit_trigger="manual_sell",
                        exit_fill_price=current_price,
                        exit_filled_at=datetime.now().isoformat(),
                        realized_pnl=realized_pnl,
                    )
        elif open_pos and is_partial:
            logger.info(f"Partial sell {qty}/{full_qty} {symbol} — position lifecycle remains open")

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
    _enricher_status = {}  # populated by signal enricher, passed to finish_cycle/fail_cycle

    try:
        if trade_hours_only and not market_open:
            logger.info("Market closed — skipping cycle")
            fail_cycle(cycle_id, "market_closed")
            return

        # Hard failsafe: even if trade_only_market_hours is off, never place new BUY orders
        # when market is closed — orders queue and execute all at once at open, causing
        # position over-concentration (the same symbol gets bought N times before fills land)
        _buys_allowed = market_open

        try:
            account = alpaca.get_account()
            positions = {p.symbol: p for p in alpaca.get_positions()}
        except Exception as e:
            logger.error(f"Alpaca API error at cycle start: {e}")
            record_error("alpaca_api_error", str(e), cycle_id)
            fail_cycle(cycle_id, "alpaca_error")
            return

        if account.trading_blocked:
            logger.warning("Account trading blocked")
            fail_cycle(cycle_id, "account_blocked")
            return

        if is_daily_loss_limit_hit(account.portfolio_value, daily_loss_limit):
            logger.warning("Daily loss limit hit — halting")
            record_error("daily_loss_limit", f"Portfolio: ${account.portfolio_value:.2f}", cycle_id)
            fail_cycle(cycle_id, "daily_loss_limit")
            return

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
        regime_data = {}
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
        wl_data = {}
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

        # Economic Calendar — wired from pre-existing core/economic_calendar.py
        try:
            calendar_context = get_calendar_summary_for_claude(watchlist)
            if calendar_context:
                logger.info("Economic calendar events found — injected into Claude prompt")
        except Exception as e:
            logger.warning(f"Economic calendar failed: {e}")
            calendar_context = ""

        # Feature 3: Performance feedback loop
        try:
            perf_summary = get_performance_summary(lookback_days=30)
            performance_feedback = build_feedback_block_for_claude(perf_summary)
            if performance_feedback:
                logger.info(f"Performance feedback: {perf_summary['total_closed']} closed trades, "
                            f"win_rate={perf_summary['win_rate']:.0%}")
        except Exception as e:
            logger.warning(f"Performance feedback failed: {e}")
            performance_feedback = ""

        # Feature 4: News & Sentiment
        news_summary = {}
        try:
            held_symbols = list(positions.keys())
            news_symbols = list(set(watchlist + held_symbols))[:30]  # cap to limit yfinance calls
            news_summary = get_news_summary(news_symbols)
            news_context = build_news_block_for_claude(news_summary, positions=held_symbols)
            if news_context:
                logger.info(f"News context built for {len([s for s,d in news_summary.items() if d.get('has_news')])} symbols")
        except Exception as e:
            logger.warning(f"News sentiment failed: {e}")
            news_context = ""

        # Feature 5: Correlation & Portfolio Risk
        corr_matrix = None
        portfolio_risk_context = ""
        try:
            all_symbols_for_corr = list(set(watchlist + list(positions.keys())))[:25]
            corr_matrix = compute_correlation_matrix(all_symbols_for_corr, lookback_days=60)
            held_list = list(positions.keys())
            heatmap_text = build_correlation_heatmap_text(corr_matrix, held_list)
            conc = compute_portfolio_concentration(
                [{"symbol": p.symbol, "market_value": p.market_value} for p in positions.values()],
                account.portfolio_value,
            )
            conc_lines = []
            if conc["warnings"]:
                conc_lines = ["CONCENTRATION WARNINGS:"] + [f"  {w}" for w in conc["warnings"]]
            portfolio_risk_context = "\n".join(
                (["=== PORTFOLIO RISK ==="] if (heatmap_text or conc_lines) else [])
                + ([heatmap_text] if heatmap_text else [])
                + conc_lines
            )
            logger.info(f"Correlation matrix computed: {corr_matrix.shape if corr_matrix is not None else 'N/A'}")
        except Exception as e:
            logger.warning(f"Portfolio risk computation failed: {e}")
            corr_matrix = None
            portfolio_risk_context = ""

        # Tick Agent: VWAP intraday context
        vwap_context = ""
        try:
            # Snapshot watchlist + held positions for VWAP
            vwap_symbols = list(set(watchlist + list(positions.keys())))[:30]
            vwap_snaps = tick_snapshot_symbols(vwap_symbols)
            if not vwap_snaps:
                # Fall back to cached snapshots from background thread
                vwap_snaps = tick_get_latest(vwap_symbols)
            vwap_context = build_vwap_block_for_claude(
                vwap_snaps,
                positions=portfolio_snapshot["positions"],
            )
            if vwap_context:
                logger.info(f"VWAP context: {len(vwap_snaps)} symbols snapped")
            _enricher_status["vwap"] = {"ok": True, "symbols": len(vwap_snaps)}
        except Exception as e:
            logger.warning(f"Tick/VWAP context failed: {e}")
            vwap_context = ""
            _enricher_status["vwap"] = {"ok": False, "error": str(e)[:100]}
            record_error("enricher_vwap_failed", str(e)[:200], cycle_id)

        # Options Flow Agent
        options_context = ""
        try:
            options_syms = list(set(watchlist + list(positions.keys())))[:15]
            flow_data = get_options_flow(options_syms)
            options_context = build_options_flow_block_for_claude(
                flow_data,
                positions=portfolio_snapshot["positions"],
            )
            save_options_flow_to_db(flow_data)
            actionable = sum(1 for d in flow_data.values() if d.get("options_signal") not in ("neutral", "no_data"))
            if options_context:
                logger.info(f"Options flow: {actionable} actionable signals out of {len(flow_data)} symbols")
            _enricher_status["options_flow_agent"] = {"ok": True, "symbols": len(flow_data)}
        except Exception as e:
            logger.warning(f"Options flow failed: {e}")
            options_context = ""
            _enricher_status["options_flow_agent"] = {"ok": False, "error": str(e)[:100]}
            record_error("enricher_options_failed", str(e)[:200], cycle_id)

        # EDGAR Insider Signal Agent
        insider_context = ""
        try:
            insider_syms = list(set(watchlist + list(positions.keys())))[:12]
            insider_data = get_insider_signals(insider_syms)
            insider_context = build_insider_block_for_claude(
                insider_data,
                positions=portfolio_snapshot["positions"],
            )
            save_insider_signals_to_db(insider_data)
            strong = sum(1 for d in insider_data.values() if d.get("insider_signal") in ("strong_buy", "buy"))
            if insider_context:
                logger.info(f"Insider signals: {strong} buy signals out of {len(insider_data)} symbols")
            _enricher_status["insider_agent"] = {"ok": True, "symbols": len(insider_data)}
        except Exception as e:
            logger.warning(f"EDGAR insider signals failed: {e}")
            insider_context = ""
            _enricher_status["insider_agent"] = {"ok": False, "error": str(e)[:100]}
            record_error("enricher_insider_failed", str(e)[:200], cycle_id)

        # Signal Enricher — macro (FRED), earnings calendar, options flow (Barchart)
        enricher_context = ""
        try:
            enrich_syms = list(set(watchlist + list(positions.keys())))[:15]
            enriched = get_enriched_context(
                symbols=enrich_syms,
                positions=portfolio_snapshot["positions"],
                include_options=True,
                include_earnings=True,
                include_macro=True,
                include_insider=False,  # insider already handled above by edgar_agent
            )
            enricher_context = enriched.get("combined_block", "")
            # Merge signal_enricher's per-sub-enricher status into our overall status dict
            _enricher_status.update(enriched.get("enricher_status", {}))
            if enricher_context:
                logger.info(
                    f"Signal enricher: macro VIX={enriched.get('macro', {}).get('vix')} "
                    f"yield_curve={enriched.get('macro', {}).get('yield_curve_2s10s')} | "
                    f"earnings blocks={bool(enriched.get('earnings_block'))} | "
                    f"options blocks={bool(enriched.get('options_block'))}"
                )
        except Exception as e:
            logger.warning(f"Signal enricher failed (non-fatal): {e}")
            enricher_context = ""
            _enricher_status["signal_enricher"] = {"ok": False, "error": str(e)[:100]}
            record_error("enricher_signal_failed", str(e)[:200], cycle_id)

        # Session feedback — tell Claude which BUY decisions it already proposed today
        # that didn't execute (prevents TGT-style repeat loops costing $0.14/cycle each)
        session_feedback_context = ""
        try:
            MAX_DEPLOY_PCT = 0.80
            long_market_value = float(getattr(account, "long_market_value", 0) or 0)
            deployed_pct = long_market_value / float(account.portfolio_value) if account.portfolio_value else 0
            session_feedback_context = build_session_feedback_block(
                deployed_pct=deployed_pct,
                max_deploy_pct=MAX_DEPLOY_PCT,
            )
            if session_feedback_context:
                logger.info(
                    f"Session feedback: portfolio at {deployed_pct:.0%} deployed — "
                    f"injecting repeat-proposal warning into prompt"
                )
        except Exception as e:
            logger.warning(f"Session feedback block failed (non-fatal): {e}")

        # RAG — inject trade history context before LLM call
        rag_context = ""
        try:
            wl_items = []
            if 'wl_data' in dir() and wl_data:
                wl_items = wl_data.get("scored", [])
            if wl_items:
                rag_context = build_portfolio_rag_block(wl_items, regime=regime)
                if rag_context:
                    logger.info(f"RAG: injected trade history context ({len(rag_context)} chars)")
        except Exception as e:
            logger.warning(f"RAG block failed (non-fatal): {e}")

        # Combine VWAP + Options + Insider + Enricher + RAG + Session feedback into portfolio_risk_context
        extra_context = "\n\n".join(filter(None, [session_feedback_context, vwap_context, options_context, insider_context, enricher_context, rag_context]))
        if extra_context:
            portfolio_risk_context = (
                (portfolio_risk_context + "\n\n" + extra_context).strip()
                if portfolio_risk_context else extra_context
            )

        cfg = get_config()
        provider = cfg.get("llm_provider", "anthropic_cli")
        model    = cfg.get("llm_model", "claude-sonnet-4-6")
        logger.info(f"Asking {provider}/{model} to analyze {len(watchlist)} symbols...")
        result = run_decision(
            watchlist=watchlist,
            market_data=market_data,
            portfolio=portfolio_snapshot,
            account=account_snapshot,
            regime_summary=regime_summary,
            performance_feedback=performance_feedback,
            news_context=news_context,
            portfolio_risk_context=portfolio_risk_context,
            calendar_context=calendar_context,
        )

        if not result:
            logger.warning("LLM returned no decision")
            record_error("llm_no_decision", "Empty response", cycle_id)
            fail_cycle(cycle_id, "llm_error")
            return

        # Extract metadata before storing
        usage = result.pop("_usage", {})
        duration_ms  = result.pop("_duration_ms", None)
        raw_response = result.pop("_raw_response", "")
        user_prompt  = result.pop("_user_prompt", "")
        # Attach provider/model onto usage so db.py cost functions get them
        usage["_provider"] = result.pop("_provider", provider)
        usage["_model"]    = result.pop("_model", model)

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
        finish_cycle(cycle_id, result, usage=usage, duration_ms=duration_ms, enricher_status=_enricher_status)

        # Validation agent — second-pass check before any trade executes
        raw_decisions = result.get("decisions", [])
        validated_decisions = validate_decisions(
            decisions=raw_decisions,
            market_context=portfolio_risk_context,
            positions={sym: {"symbol": sym} for sym in positions},
            account=account_snapshot,
        )

        # Record validation outcomes to DB
        for d in raw_decisions:
            v = d.get("_validation", {})
            if v:
                try:
                    record_validation(
                        cycle_id=cycle_id,
                        ai_decision_id=ai_decision_id,
                        symbol=d.get("symbol", ""),
                        action=d.get("action", ""),
                        verdict=v.get("verdict", "pass"),
                        risk_score=v.get("risk_score", 0),
                        top_risks=v.get("top_risks", []),
                        block_reason=v.get("block_reason"),
                        source=v.get("_source", ""),
                        duration_ms=v.get("_duration_ms", 0),
                    )
                except Exception:
                    pass

        # Extract regime score for passing to execute_decision
        regime_score_val = regime_data.get("score") if 'regime_data' in dir() else None

        # Extract per-symbol news sentiment
        news_sentiments = {}
        try:
            if news_summary:
                for sym, nd in news_summary.items():
                    news_sentiments[sym] = nd.get("avg_sentiment") or nd.get("sentiment_score")
        except Exception:
            pass

        for d in validated_decisions:
            try:
                sym = d.get("symbol", "").upper()
                sym_news = news_sentiments.get(sym)
                # Max correlation for this symbol against current open positions
                sym_corr = None
                if corr_matrix is not None and len(positions) > 0 and sym:
                    try:
                        open_syms = list(positions.keys())
                        corr_check = check_entry_correlation(sym, open_syms, corr_matrix, threshold=0.75)
                        sym_corr = corr_check.get("max_correlation")
                    except Exception:
                        pass

                # Momentum score from watchlist
                sym_momentum = None
                try:
                    if 'wl_data' in dir() and wl_data:
                        for item in wl_data.get("scored", []):
                            if item.get("symbol") == sym:
                                sym_momentum = item.get("score")
                                break
                except Exception:
                    pass

                # Hard failsafe: never open new positions when market is closed
                # (prevents queued orders from all filling at once at open)
                if d.get("action", "").lower() == "buy" and not _buys_allowed:
                    logger.info(f"Market closed — suppressing BUY for {sym} (would queue and over-concentrate)")
                    continue

                # Compute days_until_earnings from market_data for trailing stop decision
                sym_days_to_earnings = None
                try:
                    ne = market_data.get(sym, {}).get("next_earnings")
                    if ne:
                        from datetime import date as _date
                        sym_days_to_earnings = (_date.fromisoformat(str(ne)[:10]) - _date.today()).days
                except Exception:
                    pass

                execute_decision(
                    d, account, positions, cfg, alpaca, cycle_id, ai_decision_id, market_data,
                    corr_matrix=corr_matrix,
                    regime_at_entry=regime,
                    regime_score=regime_score_val,
                    news_sentiment=sym_news,
                    correlation_score=sym_corr,
                    momentum_score=sym_momentum,
                    days_until_earnings=sym_days_to_earnings,
                )
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
        fail_cycle(cycle_id, str(e), enricher_status=_enricher_status)


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
    ensure_tick_table()
    ensure_edgar_table()
    try:
        from core.edgar_signals import ensure_extended_edgar_tables
        ensure_extended_edgar_tables()
    except Exception as _e:
        logger.warning(f"Could not init extended EDGAR tables (non-fatal): {_e}")
    set_config("trader_running", "true")

    # Write PID file so api.py _is_trader_running() can detect this process
    _pid_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trader.pid")
    with open(_pid_file, "w") as _f:
        _f.write(str(os.getpid()))

    alpaca = get_alpaca()
    logger.info(f"Trader daemon started — {'PAPER' if alpaca.paper else 'LIVE'} mode")

    # Start tick agent as a background thread
    # It independently collects 1-min bars + VWAP for the default watchlist
    default_watchlist = [
        s.strip() for s in get_config().get("watchlist", "").split(",") if s.strip()
    ]
    tick_thread = threading.Thread(
        target=run_tick_loop,
        args=(default_watchlist,),
        kwargs={"interval_seconds": 60, "market_only": True},
        daemon=True,
        name="tick-agent",
    )
    tick_thread.start()
    logger.info(f"Tick agent thread started — {len(default_watchlist)} symbols, 60s interval")

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
    # Only remove the PID file if it's still ours — a replacement instance may
    # have already written its own PID (deleting it would make the health
    # monitor think the new instance is dead and kill it: the May-13 churn bug).
    try:
        with open(_pid_file) as _f:
            if _f.read().strip() == str(os.getpid()):
                os.remove(_pid_file)
    except OSError:
        pass
    logger.info("Trader daemon stopped")


if __name__ == "__main__":
    main()
