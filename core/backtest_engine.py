"""Lightweight rule-replay backtest engine (G4 in AUDIT-2026-06-11.md).

Replays the LIVE mechanical strategy rules (backend/trader.py execution +
reconciler SMA50 monitor + claude_trader.py prompt rules) over daily bars so
any rule change gets a measured win rate BEFORE it trades a dollar.

Deliberately pandas-only and per-symbol: relative win rates between rule
variants are what we're measuring; portfolio-level sizing effects come later
(VISION-RETHINK Phase B). Enter at the signal day's close, exits evaluated
daily — identical treatment across variants keeps comparisons honest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger("backtest_engine")


@dataclass(frozen=True)
class RuleSet:
    """One strategy variant. Defaults mirror the live system as of 2026-06-11."""
    name: str = "live_baseline"
    rsi_min: float = 45.0
    rsi_max: float = 75.0
    breach_exit: bool = True          # reconciler SMA50 monitor
    breach_bars: int = 2              # consecutive closes below SMA50
    grace_window: bool = True         # no breach exit on fresh entries unless loss > 8%
    grace_days: int = 1
    grace_loss_pct: float = 8.0
    momentum_collapse_exit: bool = True   # below SMA20 AND RSI < 40
    trail_atr_mult: float = 2.5
    trail_floor_pct: float = 12.0
    trail_cap_pct: float = 25.0
    catastrophic_dd_pct: float = 15.0     # from peak


@dataclass
class Trade:
    symbol: str
    entry_date: str
    exit_date: str
    entry: float
    exit: float
    pnl_pct: float
    hold_days: int
    exit_reason: str


def _wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def _atr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - df["Close"].shift()).abs(),
            (df["Low"] - df["Close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean() / df["Close"] * 100


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Add the indicator columns the rules read."""
    out = df.copy()
    out["sma20"] = out["Close"].rolling(20).mean()
    out["sma50"] = out["Close"].rolling(50).mean()
    out["rsi"] = _wilder_rsi(out["Close"])
    out["atr_pct"] = _atr_pct(out)
    out["vol20"] = out["Volume"].rolling(20).mean()
    return out


def replay_symbol(symbol: str, df: pd.DataFrame, rules: RuleSet) -> List[Trade]:
    """Run one symbol through the rule set. df must be prepare()'d."""
    trades: List[Trade] = []
    in_pos = False
    entry = peak = 0.0
    entry_i = 0
    breach_count = 0

    rows = df.dropna(subset=["sma50", "rsi", "atr_pct", "vol20"])
    closes = rows["Close"].values
    lows = rows["Low"].values
    dates = rows.index

    for i in range(len(rows)):
        price = float(closes[i])
        r = rows.iloc[i]

        if not in_pos:
            above50 = price > r["sma50"]
            above20 = price > r["sma20"]
            rsi_ok = rules.rsi_min <= r["rsi"] <= rules.rsi_max
            vol_ok = r["Volume"] >= r["vol20"] * 0.8  # not drying up
            if above50 and above20 and rsi_ok and vol_ok:
                in_pos = True
                entry = peak = price
                entry_i = i
                breach_count = 0
            continue

        # ── position management ──────────────────────────────────────────
        peak = max(peak, price)
        trail_pct = min(max(r["atr_pct"] * rules.trail_atr_mult,
                            rules.trail_floor_pct), rules.trail_cap_pct)
        stop_level = peak * (1 - trail_pct / 100)
        pnl_pct = (price - entry) / entry * 100
        exit_reason: Optional[str] = None
        exit_price = price

        # 1. trailing stop (intraday touch)
        if float(lows[i]) <= stop_level:
            exit_reason = "trailing_stop"
            exit_price = stop_level
        else:
            # 2. SMA50 breach counter (reconciler monitor)
            if price < r["sma50"]:
                breach_count += 1
            else:
                breach_count = 0
            if rules.breach_exit and breach_count >= rules.breach_bars:
                fresh = (i - entry_i) <= rules.grace_days
                if not (rules.grace_window and fresh and pnl_pct > -rules.grace_loss_pct):
                    exit_reason = "sma50_breach"
            # 3. momentum collapse
            if (exit_reason is None and rules.momentum_collapse_exit
                    and price < r["sma20"] and r["rsi"] < 40):
                exit_reason = "momentum_collapse"
            # 4. catastrophic drawdown from peak
            if exit_reason is None and peak > 0 and (peak - price) / peak * 100 >= rules.catastrophic_dd_pct:
                exit_reason = "catastrophic_dd"

        if exit_reason:
            trades.append(Trade(
                symbol=symbol,
                entry_date=str(dates[entry_i].date()),
                exit_date=str(dates[i].date()),
                entry=round(entry, 2),
                exit=round(exit_price, 2),
                pnl_pct=round((exit_price - entry) / entry * 100, 3),
                hold_days=i - entry_i,
                exit_reason=exit_reason,
            ))
            in_pos = False

    return trades


def aggregate(trades: List[Trade]) -> Dict:
    if not trades:
        return {"trades": 0}
    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses)) or 1e-9
    by_reason: Dict[str, Dict] = {}
    for t in trades:
        d = by_reason.setdefault(t.exit_reason, {"n": 0, "wins": 0, "pnl_pct_sum": 0.0})
        d["n"] += 1
        d["wins"] += 1 if t.pnl_pct > 0 else 0
        d["pnl_pct_sum"] = round(d["pnl_pct_sum"] + t.pnl_pct, 2)
    return {
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1),
        "expectancy_pct": round(sum(pnls) / len(pnls), 3),
        "avg_win_pct": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss_pct": round(sum(losses) / len(losses), 2) if losses else 0,
        "profit_factor": round(gross_win / gross_loss, 2),
        "avg_hold_days": round(sum(t.hold_days for t in trades) / len(trades), 1),
        "worst_trade_pct": round(min(pnls), 2),
        "by_exit_reason": by_reason,
    }


def run_variant(rules: RuleSet, data: Dict[str, pd.DataFrame]) -> Dict:
    """data: symbol → prepare()'d DataFrame (shared across variants)."""
    all_trades: List[Trade] = []
    for symbol, df in data.items():
        try:
            all_trades.extend(replay_symbol(symbol, df, rules))
        except Exception as e:
            logger.warning(f"{rules.name}: {symbol} replay failed: {e}")
    result = aggregate(all_trades)
    result["variant"] = rules.name
    result["rules"] = asdict(rules)
    return result
