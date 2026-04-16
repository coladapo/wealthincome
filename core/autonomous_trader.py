"""
Autonomous Trader - Claude-managed live/paper trading via Alpaca
Generates signals, applies risk rules, and executes orders automatically.
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from core.alpaca_client import (
    AlpacaClient,
    AlpacaOrderSide,
    AlpacaTimeInForce,
)
from core.llm_router import run_decision

logger = logging.getLogger(__name__)

# Default watchlist — symbols Claude will monitor and trade
DEFAULT_WATCHLIST = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "SPY", "QQQ", "AMD",
]


@dataclass
class TradeDecision:
    symbol: str
    action: str          # "buy", "sell", "hold"
    qty: float
    reason: str
    confidence: float
    signal_price: float
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "qty": self.qty,
            "reason": self.reason,
            "confidence": self.confidence,
            "signal_price": self.signal_price,
            "take_profit": self.take_profit,
            "stop_loss": self.stop_loss,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class RiskConfig:
    max_position_pct: float = 0.08       # Max 8% of portfolio per position
    max_open_positions: int = 8          # Never hold more than 8 stocks
    daily_loss_limit_pct: float = 0.05   # Hard stop: halt trading if down 5% today
    confidence_threshold: float = 0.72   # Minimum signal confidence to act
    min_signal_score: float = 0.0        # Reserved for future scoring layer
    trade_only_market_hours: bool = True  # Skip pre/post market


class AutonomousTrader:
    """
    Fully autonomous trading engine.
    Runs on a background thread, generates signals via AIEngine,
    applies risk rules, and executes orders via AlpacaClient.
    """

    def __init__(
        self,
        alpaca: AlpacaClient,
        risk: RiskConfig = None,
        watchlist: List[str] = None,
        poll_interval: int = 300,  # seconds between cycles (default: 5 min)
    ):
        self.alpaca = alpaca
        self.risk = risk or RiskConfig()
        self.watchlist = watchlist or DEFAULT_WATCHLIST
        self.poll_interval = poll_interval

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.last_claude_output: Optional[Dict] = None
        self.last_cycle_time: Optional[str] = None
        self._lock = threading.Lock()

        # Audit log — all decisions made this session
        self.decision_log: List[TradeDecision] = []
        self.trade_log: List[Dict] = []
        self.error_log: List[Dict] = []

        # Track portfolio value at session start for daily loss calc
        self._session_start_value: Optional[float] = None
        self._today_start_value: Optional[float] = None
        self._last_date: Optional[str] = None

    # ─── Lifecycle ──────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            logger.warning("AutonomousTrader already running")
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("AutonomousTrader started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=30)
        logger.info("AutonomousTrader stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ─── Main Loop ──────────────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                self._cycle()
            except Exception as e:
                logger.error(f"Cycle error: {e}", exc_info=True)
                self._log_error("cycle_error", str(e))
            time.sleep(self.poll_interval)

    def _cycle(self):
        """One full decision cycle — powered by Claude CLI"""
        logger.info(f"Starting trading cycle — {datetime.now().isoformat()}")

        # 1. Check market hours
        if self.risk.trade_only_market_hours and not self.alpaca.is_market_open():
            logger.info("Market closed — skipping cycle")
            return

        # 2. Get account state
        account = self.alpaca.get_account()
        if account.trading_blocked:
            logger.warning("Account trading blocked — skipping cycle")
            return

        portfolio_value = account.portfolio_value

        # 3. Daily loss circuit breaker
        self._refresh_daily_baseline(portfolio_value)
        if self._is_daily_loss_limit_hit(portfolio_value):
            logger.warning("Daily loss limit hit — halting all trading for today")
            self._log_error("daily_loss_limit", f"Portfolio: ${portfolio_value:.2f}")
            return

        # 4. Get current positions
        positions = {p.symbol: p for p in self.alpaca.get_positions()}

        # 5. Fetch market data for watchlist
        market_data = self._fetch_market_data()

        # 6. Build portfolio snapshot for Claude
        portfolio_snapshot = {
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
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
            "daily_pnl": (account.portfolio_value - self._today_start_value) if self._today_start_value else None,
            "daily_pnl_pct": (
                (account.portfolio_value - self._today_start_value) / self._today_start_value * 100
                if self._today_start_value else None
            ),
        }

        # 7. Ask LLM what to do (provider set in DB config: llm_provider / llm_model)
        logger.info(f"Asking LLM to analyze {len(self.watchlist)} symbols...")
        result = run_decision(
            watchlist=self.watchlist,
            market_data=market_data,
            portfolio=portfolio_snapshot,
            account=account_snapshot,
        )

        self.last_cycle_time = datetime.now().isoformat()

        if not result:
            logger.warning("Claude returned no decision — skipping execution")
            self._log_error("claude_no_decision", "Claude CLI returned empty or invalid response")
            return

        self.last_claude_output = result
        logger.info(f"Market summary: {result.get('market_summary', '')}")

        # 8. Execute Claude's decisions
        decisions = result.get("decisions", [])
        for d in decisions:
            try:
                self._execute_claude_decision(d, account, positions)
            except Exception as e:
                logger.error(f"Execution error for {d.get('symbol')}: {e}")
                self._log_error("execution_error", f"{d.get('symbol')}: {e}")

        # 9. Check stop losses on existing positions
        self._check_stop_losses(positions)

    # ─── Market Data ────────────────────────────────────────────────────────

    def _fetch_market_data(self) -> Dict[str, Any]:
        """Fetch OHLCV bars via yfinance + compute full technical indicators"""
        import yfinance as yf
        from core.indicators import compute_all

        data = {}
        for symbol in self.watchlist:
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

                # Current price: try Alpaca first, fall back to last bar close
                price = self.alpaca.get_current_price(symbol)
                if not price and bars:
                    price = bars[-1]["c"]

                # Full technical indicators
                indicators = compute_all(bars)

                # Earnings calendar
                try:
                    cal = ticker.calendar
                    next_earnings = None
                    if cal is not None and not cal.empty:
                        dates = cal.columns.tolist() if hasattr(cal, 'columns') else []
                        if dates:
                            next_earnings = str(dates[0])
                except Exception:
                    next_earnings = None

                data[symbol] = {
                    **indicators,
                    "current_price": price or (bars[-1]["c"] if bars else None),
                    "next_earnings": next_earnings,
                    "bars": bars[-10:],  # last 10 days for context, full set used for indicators
                }
            except Exception as e:
                logger.warning(f"Could not fetch data for {symbol}: {e}")
        return data

    # ─── Claude Decision Execution ───────────────────────────────────────────

    def _execute_claude_decision(self, d: Dict, account, positions: Dict):
        """Validate and execute a single decision returned by Claude"""
        symbol = d.get("symbol", "").upper()
        action = d.get("action", "hold").lower()
        confidence = float(d.get("confidence", 0))
        reasoning = d.get("reasoning", "")
        position_size_pct = float(d.get("position_size_pct", self.risk.max_position_pct))

        if action == "hold" or not symbol:
            return

        # Confidence gate
        if confidence < self.risk.confidence_threshold:
            logger.info(f"Skipping {symbol} — confidence {confidence:.0%} below threshold")
            return

        current_price = self.alpaca.get_current_price(symbol)
        if not current_price:
            logger.warning(f"No price for {symbol} — skipping")
            return

        with self._lock:
            if action == "buy":
                # Already holding
                if symbol in positions:
                    logger.info(f"Already holding {symbol} — skipping buy")
                    return

                # Max positions guard
                if len(positions) >= self.risk.max_open_positions:
                    logger.info(f"Max positions reached — skipping {symbol}")
                    return

                # Size the position
                capped_pct = min(position_size_pct, self.risk.max_position_pct)
                max_value = account.portfolio_value * capped_pct
                qty = int(min(max_value, account.cash * 0.95) / current_price)
                if qty < 1:
                    logger.info(f"Insufficient cash for {symbol} @ ${current_price:.2f}")
                    return

                # Calculate TP/SL (7% profit target, 4% stop)
                take_profit = round(current_price * 1.07, 2)
                stop_loss = round(current_price * 0.96, 2)

                order = self.alpaca.place_bracket_order(
                    symbol=symbol,
                    qty=qty,
                    side=AlpacaOrderSide.BUY,
                    take_profit_price=take_profit,
                    stop_loss_price=stop_loss,
                )
                logger.info(f"BUY {qty} {symbol} @ ~${current_price:.2f} | {confidence:.0%} | {reasoning[:80]}")

            elif action == "sell":
                if symbol not in positions:
                    logger.info(f"Not holding {symbol} — skipping sell")
                    return

                pos = positions[symbol]
                qty = pos.qty
                order = self.alpaca.place_market_order(
                    symbol=symbol,
                    qty=qty,
                    side=AlpacaOrderSide.SELL,
                )
                logger.info(f"SELL {qty} {symbol} @ ~${current_price:.2f} | {confidence:.0%} | {reasoning[:80]}")

            else:
                return

            decision = TradeDecision(
                symbol=symbol,
                action=action,
                qty=qty,
                reason=reasoning,
                confidence=confidence,
                signal_price=current_price,
            )
            self.decision_log.append(decision)
            self.trade_log.append({
                **decision.to_dict(),
                "order_id": order.id,
                "order_status": order.status,
            })

    # ─── Stop Loss Monitor ───────────────────────────────────────────────────

    def _check_stop_losses(self, positions: Dict):
        """Emergency stop: close any position that has hit its stop loss"""
        for symbol, pos in positions.items():
            if pos.unrealized_plpc < -self.risk.max_position_pct:
                logger.warning(
                    f"Stop loss triggered for {symbol}: "
                    f"{pos.unrealized_plpc:.1%} unrealized loss"
                )
                try:
                    self.alpaca.close_position(symbol)
                    self._log_error(
                        "stop_loss_triggered",
                        f"{symbol} closed at {pos.unrealized_plpc:.1%} loss",
                    )
                except Exception as e:
                    logger.error(f"Failed to close {symbol}: {e}")

    # ─── Daily Loss Circuit Breaker ─────────────────────────────────────────

    def _refresh_daily_baseline(self, current_value: float):
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_date != today:
            self._today_start_value = current_value
            self._last_date = today
            logger.info(f"New trading day. Baseline: ${current_value:.2f}")
        if self._session_start_value is None:
            self._session_start_value = current_value

    def _is_daily_loss_limit_hit(self, current_value: float) -> bool:
        if not self._today_start_value:
            return False
        daily_return = (current_value - self._today_start_value) / self._today_start_value
        return daily_return <= -self.risk.daily_loss_limit_pct

    # ─── Manual Controls ────────────────────────────────────────────────────

    def run_cycle_now(self):
        """Trigger a manual cycle immediately (non-blocking)"""
        t = threading.Thread(target=self._cycle, daemon=True)
        t.start()

    def emergency_stop(self):
        """Stop the loop AND close all open positions"""
        self.stop()
        logger.warning("EMERGENCY STOP — closing all positions and cancelling orders")
        try:
            self.alpaca.cancel_all_orders()
            self.alpaca.close_all_positions()
        except Exception as e:
            logger.error(f"Emergency stop error: {e}")

    def update_watchlist(self, symbols: List[str]):
        self.watchlist = symbols
        logger.info(f"Watchlist updated: {symbols}")

    def update_risk_config(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self.risk, k):
                setattr(self.risk, k, v)
                logger.info(f"Risk config updated: {k}={v}")

    # ─── Status & Reporting ─────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        try:
            account = self.alpaca.get_account()
            positions = self.alpaca.get_positions()
            orders = self.alpaca.get_orders(status="open")
            clock = self.alpaca.get_clock()
        except Exception as e:
            return {"error": str(e)}

        daily_pnl = None
        if self._today_start_value:
            daily_pnl = account.portfolio_value - self._today_start_value

        return {
            "running": self._running,
            "market_open": clock.get("is_open", False),
            "next_open": clock.get("next_open"),
            "next_close": clock.get("next_close"),
            "account": {
                "portfolio_value": account.portfolio_value,
                "cash": account.cash,
                "buying_power": account.buying_power,
                "equity": account.equity,
            },
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": (
                (daily_pnl / self._today_start_value * 100)
                if daily_pnl is not None and self._today_start_value
                else None
            ),
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "avg_entry": p.avg_entry_price,
                    "current_price": p.current_price,
                    "market_value": p.market_value,
                    "unrealized_pl": p.unrealized_pl,
                    "unrealized_plpc": f"{p.unrealized_plpc:.2%}",
                }
                for p in positions
            ],
            "open_orders": len(orders),
            "trades_today": len([
                t for t in self.trade_log
                if t["timestamp"][:10] == datetime.now().strftime("%Y-%m-%d")
            ]),
            "total_decisions": len(self.decision_log),
            "risk_config": {
                "max_position_pct": self.risk.max_position_pct,
                "max_open_positions": self.risk.max_open_positions,
                "daily_loss_limit_pct": self.risk.daily_loss_limit_pct,
                "confidence_threshold": self.risk.confidence_threshold,
            },
            "watchlist": self.watchlist,
            "last_cycle_time": self.last_cycle_time,
            "last_claude_output": self.last_claude_output,
            "errors_today": len([
                e for e in self.error_log
                if e["timestamp"][:10] == datetime.now().strftime("%Y-%m-%d")
            ]),
        }

    def get_trade_log(self, limit: int = 50) -> List[Dict]:
        return list(reversed(self.trade_log))[:limit]

    def get_error_log(self, limit: int = 20) -> List[Dict]:
        return list(reversed(self.error_log))[:limit]

    def _log_error(self, error_type: str, message: str):
        self.error_log.append({
            "timestamp": datetime.now().isoformat(),
            "type": error_type,
            "message": message,
        })
