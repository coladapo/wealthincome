"""
Layer 1: Macro Defense — Market Regime Detection

Detects the current market environment and sets a regime flag that controls
how aggressively Claude trades. Three regimes:

  BULL   — trend intact, be aggressive, take entries freely
  CAUTION — mixed signals, reduce size, only best setups
  BEAR   — trend broken or fear elevated, stop entries, tighten stops, go to cash

Data sources (all free via yfinance):
  SPY   — overall market trend (SMA50/200)
  VIX   — fear gauge (^VIX)
  HYG   — high-yield bond ETF (credit stress proxy)
  TLT   — long-term treasury ETF (flight to safety proxy)
  ^TNX  — 10-year yield (rising = risk-on, crashing = risk-off panic)
  XLK/XLE/XLF/XLV — sector ETFs for rotation signals
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import numpy as np

logger = logging.getLogger(__name__)


def _download(symbol: str, days: int = 60) -> Optional[Any]:
    """Download recent OHLCV data, returns DataFrame or None."""
    try:
        import yfinance as yf
        import pandas as pd
        import warnings
        warnings.filterwarnings("ignore")
        end = datetime.now()
        start = end - timedelta(days=days)
        df = yf.download(symbol, start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"),
                         progress=False, timeout=10, auto_adjust=True)
        if df.empty or len(df) < 5:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        logger.warning(f"Failed to download {symbol}: {e}")
        return None


def _sma(series, period: int) -> float:
    """Simple moving average of last N values. Accepts list or pandas Series."""
    if hasattr(series, "dropna"):
        arr = list(series.dropna())
    else:
        arr = [x for x in series if x is not None]
    if len(arr) < period:
        return arr[-1] if arr else 0.0
    return float(np.mean(arr[-period:]))


def _rsi(series, period: int = 14) -> float:
    if hasattr(series, "dropna"):
        arr = list(series.dropna())
    else:
        arr = [x for x in series if x is not None]
    if len(arr) < period + 1:
        return 50.0
    deltas = np.diff(np.array(arr))
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 2)


# ─── Individual signal collectors ─────────────────────────────────────────────

def get_spy_regime() -> Dict[str, Any]:
    """SPY trend — the most important single signal."""
    df = _download("SPY", days=120)
    if df is None:
        return {"available": False}

    price = float(df["Close"].iloc[-1])
    sma20 = _sma(df["Close"], 20)
    sma50 = _sma(df["Close"], 50)
    sma200 = _sma(df["Close"], min(200, len(df) - 1))
    rsi = _rsi(df["Close"])

    # Count consecutive days below SMA50 (trend break confirmation)
    closes = list(df["Close"].dropna())
    sma50_series = [_sma(closes[:i+1], min(50, i+1)) for i in range(len(closes))]
    consecutive_below = 0
    for i in range(len(closes) - 1, max(len(closes) - 10, 0) - 1, -1):
        if closes[i] < sma50_series[i]:
            consecutive_below += 1
        else:
            break

    above_sma50 = price > sma50
    above_sma200 = price > sma200
    golden_cross = sma50 > sma200  # SMA50 > SMA200 = bull market structure

    if above_sma50 and above_sma200 and golden_cross:
        trend = "strong_bull"
    elif above_sma50 and above_sma200:
        trend = "bull"
    elif above_sma200 and not above_sma50:
        trend = "pullback"  # correction in bull market
    elif consecutive_below >= 3:
        trend = "bear"
    else:
        trend = "mixed"

    return {
        "available": True,
        "price": round(price, 2),
        "sma20": round(sma20, 2),
        "sma50": round(sma50, 2),
        "sma200": round(sma200, 2),
        "rsi": rsi,
        "above_sma50": above_sma50,
        "above_sma200": above_sma200,
        "golden_cross": golden_cross,
        "consecutive_below_sma50": consecutive_below,
        "trend": trend,
        "price_vs_sma50_pct": round((price / sma50 - 1) * 100, 2) if sma50 else 0,
        "price_vs_sma200_pct": round((price / sma200 - 1) * 100, 2) if sma200 else 0,
    }


def get_vix_regime() -> Dict[str, Any]:
    """VIX fear gauge — high VIX = fear = reduce risk."""
    df = _download("^VIX", days=30)
    if df is None:
        return {"available": False}

    vix = float(df["Close"].iloc[-1])
    vix_5d_avg = _sma(df["Close"], min(5, len(df)))
    vix_20d_avg = _sma(df["Close"], min(20, len(df)))

    # VIX interpretation
    if vix < 15:
        fear_level = "low"        # complacency — bull market
    elif vix < 20:
        fear_level = "normal"     # healthy market
    elif vix < 25:
        fear_level = "elevated"   # caution
    elif vix < 35:
        fear_level = "high"       # fear — reduce positions
    else:
        fear_level = "extreme"    # panic — go to cash

    # Spike detection: VIX jumped >20% in last 3 days
    recent_closes = list(df["Close"].dropna())
    spike = False
    if len(recent_closes) >= 4:
        spike = recent_closes[-1] > recent_closes[-4] * 1.20

    return {
        "available": True,
        "vix": round(vix, 2),
        "vix_5d_avg": round(vix_5d_avg, 2),
        "vix_20d_avg": round(vix_20d_avg, 2),
        "fear_level": fear_level,
        "spike_detected": spike,
        "rising": vix_5d_avg > vix_20d_avg,
    }


def get_credit_stress() -> Dict[str, Any]:
    """
    HYG/LQD ratio — high-yield vs investment-grade credit.
    Falling ratio = credit stress = institutions pricing in recession/defaults.
    This often leads equity weakness by days to weeks.
    """
    hyg = _download("HYG", days=60)
    lqd = _download("LQD", days=60)

    if hyg is None or lqd is None:
        return {"available": False}

    hyg_price = float(hyg["Close"].iloc[-1])
    lqd_price = float(lqd["Close"].iloc[-1])

    # Normalize ratio to recent history
    hyg_closes = list(hyg["Close"].dropna())
    lqd_closes = list(lqd["Close"].dropna())

    min_len = min(len(hyg_closes), len(lqd_closes))
    ratios = [hyg_closes[i] / lqd_closes[i] for i in range(min_len)]

    current_ratio = ratios[-1]
    ratio_sma20 = float(np.mean(ratios[-20:])) if len(ratios) >= 20 else current_ratio
    ratio_sma5 = float(np.mean(ratios[-5:])) if len(ratios) >= 5 else current_ratio

    # Stress = ratio falling below its 20-day average
    stress = ratio_sma5 < ratio_sma20 * 0.99

    return {
        "available": True,
        "hyg_price": round(hyg_price, 2),
        "lqd_price": round(lqd_price, 2),
        "hyg_lqd_ratio": round(current_ratio, 4),
        "ratio_sma20": round(ratio_sma20, 4),
        "stress_detected": stress,
        "ratio_vs_avg_pct": round((current_ratio / ratio_sma20 - 1) * 100, 2),
    }


def get_put_call_ratio() -> Dict[str, Any]:
    """
    CBOE Put/Call ratio via ^PCCE (equity P/C ratio from yfinance).
    >1.0 = more puts than calls = fear/hedging = contrarian bullish signal
    <0.5 = extreme complacency = warning of potential correction
    """
    df = _download("^PCCE", days=30)
    if df is None:
        # Fallback: try the total P/C ratio
        df = _download("^VXAPL", days=30)
        if df is None:
            return {"available": False}

    pc = float(df["Close"].iloc[-1])
    pc_5d = _sma(df["Close"], min(5, len(df)))
    pc_20d = _sma(df["Close"], min(20, len(df)))

    if pc > 1.0:
        sentiment = "fear"       # contrarian bullish
    elif pc > 0.7:
        sentiment = "neutral"
    elif pc > 0.5:
        sentiment = "complacent"
    else:
        sentiment = "extreme_greed"  # warning

    return {
        "available": True,
        "put_call_ratio": round(pc, 3),
        "pc_5d_avg": round(pc_5d, 3),
        "pc_20d_avg": round(pc_20d, 3),
        "sentiment": sentiment,
        "rising": pc_5d > pc_20d,  # rising P/C = increasing fear
    }


def get_sector_rotation() -> Dict[str, Any]:
    """
    Track 11 SPDR sector ETFs. Identify which sectors are in uptrend vs downtrend.
    Strong sectors: price > SMA20 > SMA50
    Weak sectors: price < SMA20 < SMA50
    """
    sectors = {
        "XLK": "Technology",
        "XLV": "Healthcare",
        "XLF": "Financials",
        "XLE": "Energy",
        "XLI": "Industrials",
        "XLY": "Consumer Disc",
        "XLP": "Consumer Staples",
        "XLU": "Utilities",
        "XLB": "Materials",
        "XLRE": "Real Estate",
        "XLC": "Communication",
    }

    results = {}
    strong = []
    weak = []

    for ticker, name in sectors.items():
        df = _download(ticker, days=80)
        if df is None:
            continue
        price = float(df["Close"].iloc[-1])
        sma20 = _sma(df["Close"], 20)
        sma50 = _sma(df["Close"], 50)
        rsi = _rsi(df["Close"])

        # 1-month return
        closes = list(df["Close"].dropna())
        ret_1m = round((closes[-1] / closes[-21] - 1) * 100, 2) if len(closes) >= 21 else 0
        ret_3m = round((closes[-1] / closes[-63] - 1) * 100, 2) if len(closes) >= 63 else 0

        trend = "strong" if price > sma20 > sma50 else \
                "bull" if price > sma50 else \
                "weak" if price < sma20 < sma50 else "mixed"

        results[ticker] = {
            "name": name,
            "price": round(price, 2),
            "sma20": round(sma20, 2),
            "sma50": round(sma50, 2),
            "rsi": rsi,
            "trend": trend,
            "ret_1m_pct": ret_1m,
            "ret_3m_pct": ret_3m,
        }

        if trend in ("strong", "bull"):
            strong.append(ticker)
        elif trend == "weak":
            weak.append(ticker)

    # Sort sectors by 3-month return
    ranked = sorted(
        [(t, results[t]["ret_3m_pct"]) for t in results],
        key=lambda x: x[1], reverse=True
    )

    return {
        "available": True,
        "sectors": results,
        "strong_sectors": strong,
        "weak_sectors": weak,
        "top_3_by_momentum": [t for t, _ in ranked[:3]],
        "bottom_3_by_momentum": [t for t, _ in ranked[-3:]],
        "n_strong": len(strong),
        "n_weak": len(weak),
        "breadth": round(len(strong) / max(len(results), 1), 2),  # % of sectors in uptrend
    }


# ─── Master regime assessment ──────────────────────────────────────────────────

def get_market_regime(include_sectors: bool = True) -> Dict[str, Any]:
    """
    Master function — combines all signals into a single regime verdict.

    Returns:
      regime: "BULL" | "CAUTION" | "BEAR"
      score: 0-100 (higher = more bullish)
      max_position_pct: how large positions should be
      new_entries_allowed: bool
      signals: list of active signals
      detail: full breakdown of each indicator
    """
    logger.info("Fetching market regime data...")

    spy = get_spy_regime()
    vix = get_vix_regime()
    credit = get_credit_stress()
    sectors = get_sector_rotation() if include_sectors else {"available": False}

    # ─── Scoring (0-100) ──────────────────────────────────────────────────────
    score = 50  # neutral baseline
    signals = []
    warnings = []

    # SPY trend (most weight — 40 points)
    if spy.get("available"):
        trend = spy.get("trend", "mixed")
        if trend == "strong_bull":
            score += 20
            signals.append("SPY strong uptrend — golden cross, above SMA50 + SMA200")
        elif trend == "bull":
            score += 15
            signals.append("SPY bull — above SMA50 and SMA200")
        elif trend == "pullback":
            score += 5
            signals.append("SPY pullback in bull market — above SMA200, below SMA50")
        elif trend == "mixed":
            score -= 5
        elif trend == "bear":
            score -= 25
            warnings.append("SPY bear — consecutive closes below SMA50")

        if spy.get("consecutive_below_sma50", 0) >= 5:
            score -= 10
            warnings.append(f"SPY below SMA50 for {spy['consecutive_below_sma50']} days")

    # VIX (30 points)
    if vix.get("available"):
        fear = vix.get("fear_level", "normal")
        vix_val = vix.get("vix", 20)
        if fear == "low":
            score += 15
            signals.append(f"VIX {vix_val:.1f} — low fear, bull market conditions")
        elif fear == "normal":
            score += 8
            signals.append(f"VIX {vix_val:.1f} — normal")
        elif fear == "elevated":
            score -= 5
            warnings.append(f"VIX {vix_val:.1f} elevated — be selective")
        elif fear == "high":
            score -= 20
            warnings.append(f"VIX {vix_val:.1f} HIGH — reduce position sizes")
        elif fear == "extreme":
            score -= 35
            warnings.append(f"VIX {vix_val:.1f} EXTREME FEAR — go to cash")

        if vix.get("spike_detected"):
            score -= 15
            warnings.append("VIX spike detected — sudden fear event")

    # Credit stress (15 points)
    if credit.get("available"):
        if credit.get("stress_detected"):
            score -= 15
            warnings.append(f"Credit stress — HYG/LQD ratio falling ({credit.get('ratio_vs_avg_pct', 0):+.1f}% vs avg)")
        else:
            score += 5
            signals.append("Credit markets stable")

    # Sector breadth (15 points)
    if sectors.get("available"):
        breadth = sectors.get("breadth", 0.5)
        n_strong = sectors.get("n_strong", 0)
        n_weak = sectors.get("n_weak", 0)
        if breadth >= 0.7:
            score += 15
            signals.append(f"Broad sector participation — {n_strong}/11 sectors in uptrend")
        elif breadth >= 0.5:
            score += 8
            signals.append(f"Mixed sector breadth — {n_strong} strong, {n_weak} weak")
        elif breadth < 0.3:
            score -= 15
            warnings.append(f"Narrow market — only {n_strong}/11 sectors healthy")

    # ─── Regime verdict ───────────────────────────────────────────────────────
    score = max(0, min(100, score))

    if score >= 65:
        regime = "BULL"
        max_position_pct = 0.10      # full size
        new_entries_allowed = True
    elif score >= 40:
        regime = "CAUTION"
        max_position_pct = 0.06      # reduced size
        new_entries_allowed = True   # but only best setups
    else:
        regime = "BEAR"
        max_position_pct = 0.04      # minimal — existing positions only
        new_entries_allowed = False  # no new longs

    # Override: VIX extreme always = BEAR regardless of score
    if vix.get("fear_level") == "extreme":
        regime = "BEAR"
        new_entries_allowed = False
        max_position_pct = 0.03

    result = {
        "regime": regime,
        "score": score,
        "max_position_pct": max_position_pct,
        "new_entries_allowed": new_entries_allowed,
        "signals": signals,
        "warnings": warnings,
        "spy": spy,
        "vix": vix,
        "credit": credit,
        "sectors": sectors,
        "fetched_at": datetime.now().isoformat(),
    }

    logger.info(
        f"Market regime: {regime} (score={score}) | "
        f"VIX={vix.get('vix', '?')} | "
        f"SPY trend={spy.get('trend', '?')} | "
        f"signals={len(signals)} | warnings={len(warnings)}"
    )

    return result


def regime_summary_for_claude(regime_data: Dict) -> str:
    """
    Compact text summary injected into Claude's trading prompt.
    Tells Claude exactly what regime it's operating in and what to do.
    """
    r = regime_data
    regime = r.get("regime", "CAUTION")
    score = r.get("score", 50)
    vix = r.get("vix", {})
    spy = r.get("spy", {})
    sectors = r.get("sectors", {})

    lines = [
        f"MARKET REGIME: {regime} (confidence score: {score}/100)",
        f"New entries allowed: {'YES' if r.get('new_entries_allowed') else 'NO — defensive mode'}",
        f"Max position size: {r.get('max_position_pct', 0.08):.0%} of portfolio",
        "",
    ]

    if spy.get("available"):
        lines.append(f"SPY: ${spy.get('price')} | trend={spy.get('trend')} | "
                     f"vs SMA50: {spy.get('price_vs_sma50_pct', 0):+.1f}% | "
                     f"vs SMA200: {spy.get('price_vs_sma200_pct', 0):+.1f}%")

    if vix.get("available"):
        lines.append(f"VIX: {vix.get('vix')} ({vix.get('fear_level')}) | "
                     f"{'SPIKE ALERT ' if vix.get('spike_detected') else ''}"
                     f"{'RISING' if vix.get('rising') else 'falling'}")

    if sectors.get("available"):
        top3 = sectors.get("top_3_by_momentum", [])
        bot3 = sectors.get("bottom_3_by_momentum", [])
        breadth = sectors.get("breadth", 0)
        lines.append(f"Sectors: {breadth:.0%} in uptrend | "
                     f"Leaders: {', '.join(top3)} | Laggards: {', '.join(bot3)}")

    if r.get("signals"):
        lines.append(f"\nBULLISH SIGNALS: {' | '.join(r['signals'])}")
    if r.get("warnings"):
        lines.append(f"WARNINGS: {' | '.join(r['warnings'])}")

    lines.append(f"\nREGIME INSTRUCTION:")
    if regime == "BULL":
        lines.append("  → Markets healthy. Take quality momentum entries freely. Full position sizing.")
    elif regime == "CAUTION":
        lines.append("  → Mixed conditions. Only highest-conviction setups. Reduce position sizes by 25-40%.")
    else:
        lines.append("  → Risk-off. NO new long entries. Monitor existing positions for exit triggers. Raise cash.")

    return "\n".join(lines)


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s — %(message)s")
    print("Fetching market regime...\n")
    data = get_market_regime()
    print(regime_summary_for_claude(data))
    print(f"\n{'─'*60}")
    print("Full sector breakdown:")
    if data["sectors"].get("available"):
        for ticker, s in sorted(data["sectors"]["sectors"].items(),
                                key=lambda x: x[1]["ret_3m_pct"], reverse=True):
            print(f"  {ticker} ({s['name']:<18}) trend={s['trend']:<8} "
                  f"1m={s['ret_1m_pct']:+.1f}%  3m={s['ret_3m_pct']:+.1f}%  RSI={s['rsi']:.0f}")
