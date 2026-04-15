"""
Technical Indicators — pure numpy/pandas implementation.
No TA-Lib or pandas-ta required (Python 3.14 compatible).
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, List


def rsi(closes: List[float], period: int = 14) -> float:
    """Relative Strength Index — 0-100, <30 oversold, >70 overbought"""
    if len(closes) < period + 1:
        return 50.0
    arr = np.array(closes, dtype=float)
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD line, signal line, histogram"""
    if len(closes) < slow + signal:
        return {"macd": 0.0, "signal": 0.0, "histogram": 0.0, "bullish": False, "bearish": False}
    s = pd.Series(closes, dtype=float)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return {
        "macd": round(float(macd_line.iloc[-1]), 4),
        "signal": round(float(signal_line.iloc[-1]), 4),
        "histogram": round(float(hist.iloc[-1]), 4),
        "bullish": bool(hist.iloc[-1] > 0 and hist.iloc[-2] <= 0),   # crossover up
        "bearish": bool(hist.iloc[-1] < 0 and hist.iloc[-2] >= 0),   # crossover down
    }


def bollinger_bands(closes: List[float], period: int = 20, std_dev: float = 2.0):
    """Bollinger Bands — upper, middle, lower, %B, bandwidth"""
    if len(closes) < period:
        price = closes[-1] if closes else 0
        return {"upper": price, "middle": price, "lower": price, "pct_b": 0.5, "bandwidth": 0.0}
    arr = np.array(closes[-period:], dtype=float)
    middle = np.mean(arr)
    std = np.std(arr, ddof=1)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    price = closes[-1]
    bandwidth = (upper - lower) / middle if middle else 0
    pct_b = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
    return {
        "upper": round(upper, 2),
        "middle": round(middle, 2),
        "lower": round(lower, 2),
        "pct_b": round(pct_b, 3),       # >1 above upper, <0 below lower
        "bandwidth": round(bandwidth, 4),
    }


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    """Average True Range — volatility measure for position sizing"""
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return round(float(np.mean(trs[-period:])), 4)


def volume_analysis(volumes: List[float], closes: List[float], period: int = 20):
    """Volume vs average — confirms breakouts"""
    if len(volumes) < period:
        return {"ratio": 1.0, "above_avg": False, "trend": "neutral"}
    avg_vol = np.mean(volumes[-period:])
    cur_vol = volumes[-1]
    ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0
    # Volume trend: is recent volume expanding?
    recent_avg = np.mean(volumes[-5:])
    prior_avg = np.mean(volumes[-20:-5]) if len(volumes) >= 20 else avg_vol
    trend = "expanding" if recent_avg > prior_avg * 1.1 else "contracting" if recent_avg < prior_avg * 0.9 else "neutral"
    return {
        "ratio": round(ratio, 2),
        "above_avg": bool(ratio > 1.2),
        "trend": trend,
    }


def sma(closes: List[float], period: int) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    return round(float(np.mean(closes[-period:])), 2)


def ema(closes: List[float], period: int) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    s = pd.Series(closes, dtype=float)
    return round(float(s.ewm(span=period, adjust=False).mean().iloc[-1]), 2)


def support_resistance(closes: List[float], highs: List[float], lows: List[float]):
    """Simple support/resistance from recent swing highs/lows"""
    if len(closes) < 10:
        return {"support": None, "resistance": None}
    resistance = round(max(highs[-20:]), 2) if len(highs) >= 20 else round(max(highs), 2)
    support = round(min(lows[-20:]), 2) if len(lows) >= 20 else round(min(lows), 2)
    return {"support": support, "resistance": resistance}


def compute_all(bars: List[Dict]) -> Dict[str, Any]:
    """
    Given a list of OHLCV bar dicts with keys o/h/l/c/v,
    compute all indicators and return a single context dict for Claude.
    """
    if not bars or len(bars) < 5:
        return {}

    closes = [b["c"] for b in bars]
    highs  = [b["h"] for b in bars]
    lows   = [b["l"] for b in bars]
    vols   = [b["v"] for b in bars]

    price = closes[-1]
    prev_close = closes[-2] if len(closes) > 1 else price
    day_change_pct = round((price / prev_close - 1) * 100, 2)

    sma10  = sma(closes, 10)
    sma20  = sma(closes, 20)
    sma50  = sma(closes, 50)
    ema12  = ema(closes, 12)
    ema26  = ema(closes, 26)

    rsi_val    = rsi(closes, 14)
    macd_val   = macd(closes)
    bb         = bollinger_bands(closes, 20)
    atr_val    = atr(highs, lows, closes, 14)
    vol        = volume_analysis(vols, closes, 20)
    sr         = support_resistance(closes, highs, lows)

    # Position sizing hint: risk 1% of portfolio per ATR unit
    atr_pct = round(atr_val / price * 100, 2) if price else 0

    # Signal summary for Claude
    signals = []
    if rsi_val < 30:
        signals.append("RSI oversold")
    elif rsi_val > 70:
        signals.append("RSI overbought")
    if macd_val["bullish"]:
        signals.append("MACD bullish crossover")
    if macd_val["bearish"]:
        signals.append("MACD bearish crossover")
    if bb["pct_b"] < 0.05:
        signals.append("Price at lower Bollinger Band")
    elif bb["pct_b"] > 0.95:
        signals.append("Price at upper Bollinger Band")
    if vol["above_avg"] and day_change_pct > 0:
        signals.append("Breakout on high volume")
    if vol["above_avg"] and day_change_pct < 0:
        signals.append("Breakdown on high volume")
    if price > sma50 > sma20:
        signals.append("Price above all MAs (bullish structure)")
    if price < sma50 < sma20:
        signals.append("Price below all MAs (bearish structure)")

    return {
        "price": price,
        "day_change_pct": day_change_pct,
        "rsi_14": rsi_val,
        "macd": macd_val,
        "bollinger": bb,
        "atr_14": atr_val,
        "atr_pct": atr_pct,
        "sma_10": sma10,
        "sma_20": sma20,
        "sma_50": sma50,
        "ema_12": ema12,
        "ema_26": ema26,
        "price_vs_sma20_pct": round((price / sma20 - 1) * 100, 2) if sma20 else None,
        "price_vs_sma50_pct": round((price / sma50 - 1) * 100, 2) if sma50 else None,
        "volume": vol,
        "support": sr["support"],
        "resistance": sr["resistance"],
        "signal_summary": signals,
        "bars_available": len(bars),
    }
