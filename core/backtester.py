"""
Backtester — tests WealthIncome signal strategies against historical data
WITHOUT calling Claude API.

Three strategies tested:
  1. MeanReversion  — original RSI/MACD/Bollinger signals (7% TP / 4% SL)
  2. TrendFollowing — breakout above SMA50, ATR-based exits, holds longer
  3. Hybrid         — trend filter + mean-reversion entries (best of both)

Run:
  python core/backtester.py                        # all 3 strategies, all symbols
  python core/backtester.py --strategy trend        # trend-following only
  python core/backtester.py --symbol AAPL           # single symbol
  python core/backtester.py --optimize --symbol AMD # find best params
  python core/backtester.py --years 5               # longer lookback
"""

import sys
import os
import argparse
import json
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.indicators import compute_all
from backtesting import Backtest, Strategy


# ─── Shared helpers ────────────────────────────────────────────────────────────

def _get_bars(data, idx: int, lookback: int = 60) -> List[Dict]:
    start = max(0, idx - lookback + 1)
    bars = []
    for i in range(start, idx + 1):
        bars.append({
            "o": float(data.Open[i]),
            "h": float(data.High[i]),
            "l": float(data.Low[i]),
            "c": float(data.Close[i]),
            "v": float(data.Volume[i]),
        })
    return bars


def compute_synthetic_confidence(indicators: Dict) -> float:
    """Simulate Claude's confidence score from indicator alignment."""
    if not indicators:
        return 0.0

    signals = indicators.get("signal_summary", [])
    rsi = indicators.get("rsi_14", 50)
    macd = indicators.get("macd", {})
    bb = indicators.get("bollinger", {})
    vol = indicators.get("volume", {})
    atr_pct = indicators.get("atr_pct", 2.0)

    n = len(signals)
    if n == 0:   return 0.40
    elif n == 1: return 0.55
    elif n == 2: base = 0.68
    elif n == 3: base = 0.76
    else:        base = min(0.90, 0.76 + (n - 3) * 0.04)

    confidence = base

    bullish = [s for s in signals if any(w in s for w in
        ("oversold", "bullish", "Breakout", "above all MA", "lower Bollinger"))]
    bearish = [s for s in signals if any(w in s for w in
        ("overbought", "bearish", "Breakdown", "below all MA"))]

    if len(bullish) >= 2: confidence += 0.05
    if len(bearish) >= 2: confidence += 0.05
    if rsi < 25:          confidence += 0.06
    elif rsi > 80:        confidence += 0.06
    if macd.get("bullish", False) or macd.get("bearish", False):
        confidence += 0.04

    pct_b = bb.get("pct_b", 0.5)
    if pct_b < 0.02 or pct_b > 0.98: confidence += 0.04
    if vol.get("above_avg") and vol.get("trend") == "expanding":
        confidence += 0.03
    if atr_pct > 4:   confidence -= 0.05
    elif atr_pct > 3: confidence -= 0.02

    return round(min(0.95, max(0.40, confidence)), 3)


def fetch_df(symbol: str, start: str, end: str) -> Optional[pd.DataFrame]:
    import yfinance as yf
    df = yf.download(symbol, start=start, end=end,
                     progress=False, timeout=15, auto_adjust=True)
    if df.empty or len(df) < 100:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.dropna(inplace=True)
    return df if len(df) >= 100 else None


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY 1: Mean Reversion (original — RSI/MACD/Bollinger signals)
# ═══════════════════════════════════════════════════════════════════════════════

class MeanReversionStrategy(Strategy):
    """
    Original strategy: buy oversold reversals, fixed bracket exits.
    Entry: ≥2 bullish signals, confidence ≥ threshold
    Exit: 7% TP or 4% SL
    """
    tp_pct = 0.07
    sl_pct = 0.04
    confidence_threshold = 0.72
    max_position_pct = 0.08

    def init(self):
        self._entry_price = 0.0
        self._tp = 0.0
        self._sl = 0.0

    def next(self):
        idx = len(self.data.Close) - 1
        bars = _get_bars(self.data, idx, 60)
        if len(bars) < 15:
            return

        indicators = compute_all(bars)
        signals = indicators.get("signal_summary", [])

        if not self.position:
            bullish = [s for s in signals if any(w in s for w in
                ("oversold", "bullish crossover", "lower Bollinger", "above all MA"))]
            if len(bullish) >= 2:
                conf = compute_synthetic_confidence(indicators)
                if conf >= self.confidence_threshold:
                    price = self.data.Close[-1]
                    size = min(0.95, self.max_position_pct * self.equity / (price + 1e-9) * price / self.equity)
                    self.buy(size=size)
                    self._entry_price = price
                    self._tp = price * (1 + self.tp_pct)
                    self._sl = price * (1 - self.sl_pct)
        else:
            h = self.data.High[-1]
            l = self.data.Low[-1]
            if h >= self._tp or l <= self._sl:
                self.position.close()


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY 2: Trend Following (new — momentum + breakout)
# ═══════════════════════════════════════════════════════════════════════════════

class TrendFollowingStrategy(Strategy):
    """
    Trend-following: buy when price breaks above SMA50 with volume confirmation.
    Exit: ATR-based trailing stop (3× ATR) — lets winners run.

    This is the right approach for bull markets where RSI stays "overbought"
    for months — trending signals, not reversal signals.

    Entry conditions:
      - Price > SMA50 (uptrend confirmed)
      - Price > SMA20 (short-term momentum)
      - Volume above average (breakout confirmed)
      - RSI 50–70 (trending, not overbought extreme)
      - Price crossed above SMA20 in last 3 bars (fresh entry)

    Exit: 3× ATR trailing stop (wider than 4% fixed — lets trends run)
    """
    atr_multiplier = 3.0    # trailing stop = entry_price - (atr_multiplier * ATR)
    max_position_pct = 0.08
    rsi_min = 45            # don't buy if RSI already too low
    rsi_max = 72            # don't chase if RSI overbought

    def init(self):
        self._entry_price = 0.0
        self._trailing_stop = 0.0
        self._atr = 0.0

    def next(self):
        idx = len(self.data.Close) - 1
        bars = _get_bars(self.data, idx, 60)
        if len(bars) < 22:
            return

        indicators = compute_all(bars)
        price = self.data.Close[-1]
        rsi = indicators.get("rsi_14", 50)
        sma20 = indicators.get("sma_20", 0)
        sma50 = indicators.get("sma_50", 0)
        atr = indicators.get("atr_14", price * 0.02)
        vol = indicators.get("volume", {})

        if not self.position:
            # Trend entry: price above both MAs, fresh momentum, confirmed volume
            above_sma20 = sma20 > 0 and price > sma20
            above_sma50 = sma50 > 0 and price > sma50
            rsi_in_range = self.rsi_min <= rsi <= self.rsi_max
            vol_ok = vol.get("above_avg", False) or vol.get("trend") == "expanding"

            # Check if price just crossed above SMA20 (within last 3 bars)
            fresh_cross = False
            if idx >= 3 and sma20 > 0:
                prev_bars = _get_bars(self.data, idx - 1, 60)
                if len(prev_bars) >= 15:
                    prev_ind = compute_all(prev_bars)
                    prev_price = self.data.Close[-2] if idx > 0 else price
                    prev_sma20 = prev_ind.get("sma_20", 0)
                    if prev_price <= prev_sma20 and price > sma20:
                        fresh_cross = True

            # Also enter if strong uptrend structure (above all MAs)
            strong_trend = "Price above all MAs (bullish structure)" in indicators.get("signal_summary", [])

            if above_sma50 and rsi_in_range and vol_ok and (fresh_cross or strong_trend):
                size = min(0.95, self.max_position_pct)
                self.buy(size=size)
                self._entry_price = price
                self._atr = atr
                self._trailing_stop = price - (self.atr_multiplier * atr)

        else:
            # Update trailing stop upward (never move it down)
            new_stop = price - (self.atr_multiplier * atr)
            if new_stop > self._trailing_stop:
                self._trailing_stop = new_stop

            # Exit if price falls through trailing stop
            if self.data.Low[-1] <= self._trailing_stop:
                self.position.close()
            # Also exit if RSI goes very overbought (>80) AND starts turning down
            elif rsi > 80 and idx > 0:
                prev_bars2 = _get_bars(self.data, idx - 1, 60)
                if len(prev_bars2) >= 15:
                    prev_rsi = compute_all(prev_bars2).get("rsi_14", 50)
                    if rsi < prev_rsi:   # RSI turning down from extreme
                        self.position.close()


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY 3: Hybrid (trend filter + mean-reversion entry)
# ═══════════════════════════════════════════════════════════════════════════════

class HybridStrategy(Strategy):
    """
    Best of both: only take mean-reversion entries when in an uptrend.
    Avoids catching falling knives (mean-reversion in downtrends).

    Entry:
      - Trend filter: price > SMA50 (we're in uptrend)
      - Mean-reversion entry: RSI pulled back to 40-55 range AND MACD bullish
      - Volume confirming
    Exit:
      - ATR-based trailing stop (2× ATR) — tighter than pure trend but wider than fixed 4%
      - OR take profit at 12% (let runners run more than original 7%)
    """
    tp_pct = 0.12
    atr_multiplier = 2.0
    max_position_pct = 0.08
    confidence_threshold = 0.68  # slightly lower — trend filter does the heavy lifting

    def init(self):
        self._entry_price = 0.0
        self._trailing_stop = 0.0
        self._tp = 0.0

    def next(self):
        idx = len(self.data.Close) - 1
        bars = _get_bars(self.data, idx, 60)
        if len(bars) < 22:
            return

        indicators = compute_all(bars)
        price = self.data.Close[-1]
        rsi = indicators.get("rsi_14", 50)
        sma50 = indicators.get("sma_50", 0)
        sma20 = indicators.get("sma_20", 0)
        macd = indicators.get("macd", {})
        atr = indicators.get("atr_14", price * 0.02)
        vol = indicators.get("volume", {})
        signals = indicators.get("signal_summary", [])

        if not self.position:
            # Gate 1: must be in uptrend
            in_uptrend = sma50 > 0 and price > sma50

            if not in_uptrend:
                return

            # Gate 2: pullback entry (RSI pulled back from overbought)
            pullback = 38 <= rsi <= 58
            macd_turning = macd.get("bullish", False) or macd.get("histogram", 0) > 0
            vol_ok = vol.get("above_avg", False) or vol.get("ratio", 1) > 0.9

            # Or: strong signal alignment even without pullback
            bullish_signals = [s for s in signals if any(w in s for w in
                ("oversold", "bullish crossover", "lower Bollinger"))]

            conf = compute_synthetic_confidence(indicators)

            entry_condition = (
                (pullback and macd_turning and vol_ok) or
                (len(bullish_signals) >= 2 and conf >= self.confidence_threshold)
            )

            if entry_condition:
                size = min(0.95, self.max_position_pct)
                self.buy(size=size)
                self._entry_price = price
                self._trailing_stop = price - (self.atr_multiplier * atr)
                self._tp = price * (1 + self.tp_pct)

        else:
            # Update trailing stop
            new_stop = price - (self.atr_multiplier * atr)
            if new_stop > self._trailing_stop:
                self._trailing_stop = new_stop

            h = self.data.High[-1]
            l = self.data.Low[-1]
            if h >= self._tp:
                self.position.close()
            elif l <= self._trailing_stop:
                self.position.close()


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY 4: Momentum Hold (trend-following with NO fixed TP — ride the trend)
# ═══════════════════════════════════════════════════════════════════════════════

class MomentumHoldStrategy(Strategy):
    """
    Momentum strategy designed for trending bull markets.
    Key insight: fixed TP (7-12%) is the killer in bull runs — cap the upside.
    This strategy rides winners until the trend actually breaks.

    Entry:
      - Price crosses above SMA50 (uptrend entry)
      - RSI 45-75 (trending momentum range)
      - Volume confirming (above average OR expanding)

    Exit (trend break signals only — NO take profit ceiling):
      - Price falls below SMA50 for 2+ consecutive bars (trend broken)
      - OR price falls below SMA20 AND RSI drops below 40 (momentum collapse)
      - OR 15% drawdown from peak (protect against catastrophic loss)

    Position sizing: 10% of equity per position (vs 8% before)
    """
    max_position_pct = 0.10
    drawdown_exit_pct = 0.15   # 15% from peak triggers exit
    rsi_min = 40
    rsi_max = 78

    def init(self):
        self._entry_price = 0.0
        self._peak_price = 0.0
        self._below_sma50_bars = 0

    def next(self):
        idx = len(self.data.Close) - 1
        bars = _get_bars(self.data, idx, 60)
        if len(bars) < 22:
            return

        indicators = compute_all(bars)
        price = self.data.Close[-1]
        rsi = indicators.get("rsi_14", 50)
        sma20 = indicators.get("sma_20", 0)
        sma50 = indicators.get("sma_50", 0)
        vol = indicators.get("volume", {})

        if not self.position:
            # Entry: price just crossed above SMA50 OR strong uptrend + pullback buy
            above_sma50 = sma50 > 0 and price > sma50
            above_sma20 = sma20 > 0 and price > sma20
            rsi_ok = self.rsi_min <= rsi <= self.rsi_max
            vol_ok = vol.get("above_avg", False) or vol.get("trend") in ("expanding", "neutral")

            if above_sma50 and above_sma20 and rsi_ok and vol_ok:
                # Check we're not buying into an already-extended move
                # Only enter if RSI < 70 (not already overbought) or fresh cross
                if rsi < 70 or (idx > 0 and sma50 > 0):
                    size = min(0.95, self.max_position_pct)
                    self.buy(size=size)
                    self._entry_price = price
                    self._peak_price = price
                    self._below_sma50_bars = 0

        else:
            # Update peak for drawdown calculation
            if price > self._peak_price:
                self._peak_price = price

            # Check exit conditions (NO fixed take profit)
            below_sma50 = sma50 > 0 and price < sma50
            drawdown_from_peak = (self._peak_price - price) / self._peak_price if self._peak_price > 0 else 0

            if below_sma50:
                self._below_sma50_bars += 1
            else:
                self._below_sma50_bars = 0

            # Exit 1: stayed below SMA50 for 2+ bars (trend broken)
            if self._below_sma50_bars >= 2:
                self.position.close()
            # Exit 2: price below SMA20 AND momentum collapsed (RSI < 40)
            elif sma20 > 0 and price < sma20 and rsi < 40:
                self.position.close()
            # Exit 3: protect against 15% drawdown from peak
            elif drawdown_from_peak >= self.drawdown_exit_pct:
                self.position.close()


# ─── Run one strategy on one symbol ───────────────────────────────────────────

def run_one(strategy_cls, symbol: str, start: str, end: str,
            cash: float = 100_000.0) -> Optional[Dict]:
    df = fetch_df(symbol, start, end)
    if df is None:
        return None
    try:
        bt = Backtest(df, strategy_cls, cash=cash,
                      commission=0.001, exclusive_orders=True)
        stats = bt.run()
        n_trades = len(stats._trades) if stats._trades is not None else 0
        ret = float(stats["Return [%]"])
        bh = float(stats["Buy & Hold Return [%]"])
        return {
            "symbol": symbol,
            "trades": n_trades,
            "return_pct": round(ret, 2),
            "buy_hold_pct": round(bh, 2),
            "alpha_pct": round(ret - bh, 2),
            "sharpe": round(float(stats.get("Sharpe Ratio") or 0), 3),
            "max_drawdown_pct": round(float(stats["Max. Drawdown [%]"]), 2),
            "win_rate_pct": round(float(stats.get("Win Rate [%]") or 0), 1),
            "profit_factor": round(float(stats.get("Profit Factor") or 0), 3),
            "avg_trade_pct": round(float(stats.get("Avg. Trade [%]") or 0), 2),
            "exposure_pct": round(float(stats.get("Exposure Time [%]") or 0), 1),
        }
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}


# ─── Run all 3 strategies and compare ─────────────────────────────────────────

def run_comparison(symbols: List[str], start: str, end: str,
                   cash: float = 100_000.0) -> Dict:

    strategies = [
        ("MeanReversion", MeanReversionStrategy),
        ("TrendFollowing", TrendFollowingStrategy),
        ("Hybrid",        HybridStrategy),
        ("MomentumHold",  MomentumHoldStrategy),
    ]

    all_results = {name: [] for name, _ in strategies}

    for name, cls in strategies:
        print(f"\n{'═'*70}")
        print(f"STRATEGY: {name}")
        print(f"{'─'*70}")

        for symbol in symbols:
            print(f"  {symbol:8s}", end="", flush=True)
            r = run_one(cls, symbol, start, end, cash)
            if r and "error" not in r:
                all_results[name].append(r)
                alpha = r["alpha_pct"]
                sign = "+" if alpha > 0 else ""
                print(f"  {r['trades']:3d} trades | "
                      f"return={r['return_pct']:+6.1f}% | "
                      f"B&H={r['buy_hold_pct']:+6.1f}% | "
                      f"alpha={sign}{alpha:.1f}% | "
                      f"sharpe={r['sharpe']:.2f} | "
                      f"win={r['win_rate_pct']:.0f}%")
            elif r:
                print(f"  ERROR: {r.get('error','')[:50]}")
            else:
                print("  SKIP")

    # Aggregate summary table
    print(f"\n{'═'*70}")
    print("STRATEGY COMPARISON SUMMARY")
    print(f"{'─'*70}")
    print(f"{'Strategy':<18} {'Symbols':>7} {'Trades':>7} {'Avg Ret':>9} "
          f"{'Avg B&H':>9} {'Alpha':>8} {'Sharpe':>8} {'WinRate':>8} {'PF':>7}")
    print(f"{'─'*70}")

    summary = {}
    for name, _ in strategies:
        results = all_results[name]
        if not results:
            continue
        n = len(results)
        avg_ret = sum(r["return_pct"] for r in results) / n
        avg_bh = sum(r["buy_hold_pct"] for r in results) / n
        avg_alpha = avg_ret - avg_bh
        avg_sh = sum(r["sharpe"] for r in results) / n
        avg_wr = sum(r["win_rate_pct"] for r in results) / n
        pf_vals = [r["profit_factor"] for r in results if r["profit_factor"] > 0]
        avg_pf = sum(pf_vals) / len(pf_vals) if pf_vals else 0
        total_trades = sum(r["trades"] for r in results)
        beat_bh = sum(1 for r in results if r["alpha_pct"] > 0)

        sign = "+" if avg_alpha > 0 else ""
        print(f"  {name:<16} {n:>7} {total_trades:>7} {avg_ret:>+8.1f}% "
              f"{avg_bh:>+8.1f}% {sign}{avg_alpha:>6.1f}% "
              f"{avg_sh:>8.3f} {avg_wr:>7.1f}% {avg_pf:>7.3f}")

        summary[name] = {
            "symbols": n,
            "total_trades": total_trades,
            "avg_return_pct": round(avg_ret, 2),
            "avg_buy_hold_pct": round(avg_bh, 2),
            "avg_alpha_pct": round(avg_alpha, 2),
            "avg_sharpe": round(avg_sh, 3),
            "avg_win_rate_pct": round(avg_wr, 1),
            "avg_profit_factor": round(avg_pf, 3),
            "beat_buy_hold": beat_bh,
            "individual": results,
        }

    print(f"{'─'*70}")

    # Verdict
    print("\nVERDICT:")
    best_name = max(summary, key=lambda k: summary[k]["avg_sharpe"]) if summary else None
    if best_name:
        best = summary[best_name]
        if best["avg_alpha_pct"] > 5 and best["avg_sharpe"] > 1.0:
            print(f"  ✓ {best_name} has STRONG EDGE — deploy this strategy")
        elif best["avg_alpha_pct"] > 0:
            print(f"  ⚠ {best_name} has marginal edge — refine before live trading")
        else:
            print(f"  ✗ No strategy beats buy-and-hold — need better signals")
        print(f"    Best strategy: {best_name} | alpha={best['avg_alpha_pct']:+.1f}% | "
              f"sharpe={best['avg_sharpe']:.3f}")

    # What to tell Claude — which strategy to deploy
    print("\nCLAUDE DEPLOYMENT RECOMMENDATION:")
    recommended = max(summary, key=lambda k: summary[k]["avg_sharpe"]) if summary else "MeanReversion"

    rec = summary.get(recommended, {})
    print(f"  Deploy: {recommended}")
    print(f"  Expected alpha: {rec.get('avg_alpha_pct', 0):+.1f}%/period vs buy-and-hold")
    print(f"  Expected win rate: {rec.get('avg_win_rate_pct', 0):.1f}%")
    print()

    return {
        "start": start, "end": end,
        "symbols": symbols,
        "recommended_strategy": recommended,
        "strategies": summary,
    }


# ─── Optimize one strategy on one symbol ──────────────────────────────────────

def optimize_one(strategy_cls, strategy_name: str, symbol: str,
                 start: str, end: str) -> Dict:
    df = fetch_df(symbol, start, end)
    if df is None:
        print(f"No data for {symbol}")
        return {}

    print(f"\nOptimizing {strategy_name} on {symbol} ({start} → {end})")
    print("─" * 50)

    bt = Backtest(df, strategy_cls, cash=100_000, commission=0.001, exclusive_orders=True)

    if strategy_cls == MeanReversionStrategy:
        stats, _ = bt.optimize(
            tp_pct=[0.06, 0.07, 0.08, 0.10, 0.12, 0.15],
            sl_pct=[0.03, 0.04, 0.05, 0.06, 0.07],
            confidence_threshold=[0.60, 0.65, 0.68, 0.72, 0.75, 0.80],
            maximize="Sharpe Ratio",
            constraint=lambda p: p.tp_pct > p.sl_pct,
            return_heatmap=True,
        )
        print(f"  Best TP={strategy_cls.tp_pct:.0%}  SL={strategy_cls.sl_pct:.0%}  "
              f"conf={strategy_cls.confidence_threshold:.0%}")

    elif strategy_cls == TrendFollowingStrategy:
        stats, _ = bt.optimize(
            atr_multiplier=[1.5, 2.0, 2.5, 3.0, 4.0],
            rsi_min=[40, 45, 50, 55],
            rsi_max=[65, 70, 72, 75, 80],
            maximize="Sharpe Ratio",
            return_heatmap=True,
        )
        print(f"  Best atr_mult={strategy_cls.atr_multiplier}  "
              f"rsi_range={strategy_cls.rsi_min}-{strategy_cls.rsi_max}")

    elif strategy_cls == HybridStrategy:
        stats, _ = bt.optimize(
            tp_pct=[0.08, 0.10, 0.12, 0.15, 0.18],
            atr_multiplier=[1.5, 2.0, 2.5, 3.0],
            confidence_threshold=[0.60, 0.65, 0.68, 0.72],
            maximize="Sharpe Ratio",
            return_heatmap=True,
        )
        print(f"  Best TP={strategy_cls.tp_pct:.0%}  "
              f"atr_mult={strategy_cls.atr_multiplier}  "
              f"conf={strategy_cls.confidence_threshold:.0%}")

    n_trades = len(stats._trades) if stats._trades is not None else 0
    print(f"  Sharpe={stats['Sharpe Ratio']:.3f}  "
          f"Return={stats['Return [%]']:.1f}%  "
          f"B&H={stats['Buy & Hold Return [%]']:.1f}%  "
          f"Trades={n_trades}")

    return {
        "symbol": symbol,
        "strategy": strategy_name,
        "sharpe": round(float(stats["Sharpe Ratio"]), 3),
        "return_pct": round(float(stats["Return [%]"]), 2),
        "buy_hold_pct": round(float(stats["Buy & Hold Return [%]"]), 2),
        "trades": n_trades,
    }


# ─── Save results to DB ────────────────────────────────────────────────────────

def save_results_to_db(results: Dict):
    try:
        from backend.db import get_connection
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_runs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                ran_at              TEXT NOT NULL,
                config_json         TEXT NOT NULL,
                summary_json        TEXT NOT NULL,
                recommended_strategy TEXT
            )
        """)
        conn.execute(
            "INSERT INTO backtest_runs(ran_at, config_json, summary_json, recommended_strategy) VALUES(?,?,?,?)",
            (
                datetime.now().isoformat(),
                json.dumps({"start": results.get("start"), "end": results.get("end")}),
                json.dumps(results),
                results.get("recommended_strategy", ""),
            )
        )
        conn.commit()
        conn.close()
        print("Results saved to DB (backtest_runs table)")
    except Exception as e:
        print(f"Could not save to DB: {e}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="WealthIncome Backtester")
    parser.add_argument("--symbol", help="Single symbol (default: full watchlist)")
    parser.add_argument("--strategy", choices=["mean", "trend", "hybrid", "momentum", "all"],
                        default="all", help="Which strategy to test")
    parser.add_argument("--years", type=int, default=3, help="Years of history (default: 3)")
    parser.add_argument("--optimize", action="store_true", help="Optimize parameters")
    parser.add_argument("--save", action="store_true", help="Save results to SQLite")
    args = parser.parse_args()

    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=args.years * 365)).strftime("%Y-%m-%d")

    DEFAULT_SYMBOLS = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
        "META", "TSLA", "SPY", "QQQ", "AMD",
    ]
    symbols = [args.symbol.upper()] if args.symbol else DEFAULT_SYMBOLS

    strategy_map = {
        "mean":     ("MeanReversion",  MeanReversionStrategy),
        "trend":    ("TrendFollowing", TrendFollowingStrategy),
        "hybrid":   ("Hybrid",         HybridStrategy),
        "momentum": ("MomentumHold",   MomentumHoldStrategy),
    }

    print(f"\nWealthIncome Backtester")
    print(f"Period: {start} → {end}  ({args.years} years)")
    print(f"Symbols: {', '.join(symbols)}")

    if args.optimize:
        sym = symbols[0]
        if args.strategy == "all":
            for key, (name, cls) in strategy_map.items():
                optimize_one(cls, name, sym, start, end)
        else:
            name, cls = strategy_map[args.strategy]
            optimize_one(cls, name, sym, start, end)
        return

    if args.strategy == "all":
        results = run_comparison(symbols, start, end)
    else:
        name, cls = strategy_map[args.strategy]
        print(f"\n{'═'*70}")
        print(f"STRATEGY: {name}")
        print(f"{'─'*70}")
        individual = []
        for sym in symbols:
            print(f"  {sym:8s}", end="", flush=True)
            r = run_one(cls, sym, start, end)
            if r and "error" not in r:
                individual.append(r)
                print(f"  {r['trades']:3d} trades | return={r['return_pct']:+6.1f}% | "
                      f"B&H={r['buy_hold_pct']:+6.1f}% | alpha={r['alpha_pct']:+.1f}% | "
                      f"sharpe={r['sharpe']:.2f}")
            elif r:
                print(f"  ERROR: {r.get('error','')[:50]}")
            else:
                print("  SKIP")
        results = {"strategy": name, "individual": individual,
                   "start": start, "end": end}

    if args.save:
        save_results_to_db(results)


if __name__ == "__main__":
    main()
