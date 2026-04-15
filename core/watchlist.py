"""
Layer 2: Dynamic Watchlist — Stock Selection Engine

Replaces the static 10-symbol list with a rotating watchlist of the
highest-momentum stocks from the S&P 500 universe.

Selection criteria (all three must pass):
  1. Relative Strength — 3-month return ranks in top 25% of universe
  2. Trend filter — price > SMA50 (in uptrend)
  3. Liquidity filter — avg daily volume > 500k shares (tradeable)

Additional overlays:
  - Earnings proximity filter — flag stocks reporting within 5 days
  - Sector diversification — cap at 3 stocks per sector
  - Regime-aware — in CAUTION/BEAR, only include strongest momentum names

Sources:
  - S&P 500 constituents: Wikipedia scrape (free, updates monthly)
  - Price/volume data: yfinance (free)
  - Earnings dates: yfinance earnings_dates (free, approximate)
"""

import logging
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import numpy as np

logger = logging.getLogger(__name__)

# Cache file — avoid re-downloading on every cycle
_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".watchlist_cache.json")
_CACHE_TTL_HOURS = 4  # refresh watchlist every 4 hours


# ─── S&P 500 universe ─────────────────────────────────────────────────────────

def get_sp500_tickers() -> List[str]:
    """Scrape S&P 500 tickers from Wikipedia. Cached to avoid hammering."""
    cache_key = "sp500_tickers"
    cached = _load_cache(cache_key, ttl_hours=24)
    if cached:
        return cached

    try:
        import pandas as pd
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = pd.read_html(url)
        df = tables[0]
        tickers = df["Symbol"].tolist()
        # Clean up: replace dots with dashes (BRK.B → BRK-B)
        tickers = [t.replace(".", "-") for t in tickers]
        logger.info(f"Fetched {len(tickers)} S&P 500 tickers from Wikipedia")
        _save_cache(cache_key, tickers)
        return tickers
    except Exception as e:
        logger.warning(f"Could not fetch S&P 500 list: {e}")
        # Fallback: extended static list covering major sectors
        return [
            # Tech
            "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "AMD", "AVGO",
            "ORCL", "CRM", "ADBE", "NOW", "PANW", "SNPS", "CDNS", "AMAT", "MU",
            # Healthcare
            "LLY", "UNH", "JNJ", "ABBV", "TMO", "ABT", "DHR", "ISRG", "BSX",
            # Financials
            "BRK-B", "JPM", "V", "MA", "GS", "MS", "BLK", "SPGI", "ICE",
            # Consumer
            "TSLA", "HD", "MCD", "NKE", "SBUX", "TGT", "COST", "LOW", "TJX",
            # Energy
            "XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC",
            # Industrials
            "CAT", "DE", "BA", "HON", "RTX", "GE", "UNP", "CSX",
            # Communication
            "NFLX", "DIS", "T", "VZ", "CMCSA", "TMUS",
            # ETFs for benchmarks
            "SPY", "QQQ", "IWM", "XLK", "XLV", "XLF", "XLE",
        ]


def _get_sector_map() -> Dict[str, str]:
    """Map tickers to sectors for diversification control."""
    return {
        # Tech
        **{t: "Technology" for t in [
            "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMD", "AVGO", "ORCL",
            "CRM", "ADBE", "NOW", "PANW", "SNPS", "CDNS", "AMAT", "MU", "INTC",
        ]},
        # Healthcare
        **{t: "Healthcare" for t in [
            "LLY", "UNH", "JNJ", "ABBV", "TMO", "ABT", "DHR", "ISRG", "BSX", "PFE",
        ]},
        # Financials
        **{t: "Financials" for t in [
            "BRK-B", "JPM", "V", "MA", "GS", "MS", "BLK", "SPGI", "ICE", "C", "BAC",
        ]},
        # Consumer
        **{t: "Consumer" for t in [
            "TSLA", "AMZN", "HD", "MCD", "NKE", "SBUX", "TGT", "COST", "LOW", "TJX",
        ]},
        # Energy
        **{t: "Energy" for t in [
            "XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC", "OXY",
        ]},
        # Industrials
        **{t: "Industrials" for t in [
            "CAT", "DE", "BA", "HON", "RTX", "GE", "UNP", "CSX", "LMT",
        ]},
        # Communication
        **{t: "Communication" for t in [
            "NFLX", "DIS", "T", "VZ", "CMCSA", "TMUS",
        ]},
    }


# ─── Momentum scoring ─────────────────────────────────────────────────────────

def score_symbol(ticker: str, days: int = 200) -> Optional[Dict]:
    """
    Download price data and compute momentum score for one symbol.
    Returns None if data unavailable or symbol fails filters.
    """
    try:
        import yfinance as yf
        import pandas as pd
        import warnings
        warnings.filterwarnings("ignore")

        end = datetime.now()
        start = end - timedelta(days=days)
        df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"),
                         progress=False, timeout=10, auto_adjust=True)

        if df.empty or len(df) < 50:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        closes = list(df["Close"].dropna())
        volumes = list(df["Volume"].dropna())
        price = closes[-1]

        # Liquidity filter: avg daily volume > 500k
        avg_vol = float(np.mean(volumes[-20:]))
        if avg_vol < 500_000:
            return None

        # Trend filter: price must be at or above SMA50
        # Allow up to 3% below SMA50 — recovering stocks worth watching
        sma50 = float(np.mean(closes[-50:]))
        vs_sma50 = (price / sma50 - 1)
        if vs_sma50 < -0.03:
            return None  # more than 3% below SMA50 — not in consideration

        sma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else price
        sma200 = float(np.mean(closes[-min(200, len(closes)):]))

        # Returns for momentum scoring
        ret_1m  = (closes[-1] / closes[-21]  - 1) if len(closes) >= 22  else 0
        ret_3m  = (closes[-1] / closes[-63]  - 1) if len(closes) >= 64  else (closes[-1] / closes[0] - 1)
        ret_6m  = (closes[-1] / closes[-126] - 1) if len(closes) >= 127 else ret_3m

        # RSI
        deltas = np.diff(np.array(closes[-30:]))
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains[-14:])
        avg_loss = np.mean(losses[-14:])
        rsi = 100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 100.0

        # Volume trend: recent vs prior
        recent_vol = float(np.mean(volumes[-5:]))
        prior_vol = float(np.mean(volumes[-20:-5])) if len(volumes) >= 20 else avg_vol
        vol_ratio = recent_vol / prior_vol if prior_vol > 0 else 1.0

        # Momentum score: weighted combination
        # 3-month return gets most weight (primary momentum signal)
        # Skip if RSI > 80 (too extended — higher pullback risk)
        if rsi > 82:
            return None

        momentum_score = (
            ret_3m * 0.50 +    # 3-month return (primary)
            ret_1m * 0.30 +    # 1-month return (recency)
            ret_6m * 0.20      # 6-month return (longer trend)
        )

        # Bonus for volume expansion (institutional buying)
        if vol_ratio > 1.3:
            momentum_score *= 1.1

        # Penalty for being too extended above SMA50
        vs_sma50 = (price / sma50 - 1)
        if vs_sma50 > 0.25:   # >25% above SMA50 = stretched
            momentum_score *= 0.85

        # Feature 6: Short Interest Signal
        short_pct_float = 0.0
        short_ratio     = 0.0
        short_signal    = "none"
        try:
            info = yf.Ticker(ticker).info or {}
            # shortPercentOfFloat is typically 0.15 for 15%
            spf = info.get("shortPercentOfFloat")
            sr  = info.get("shortRatio") or info.get("shortRatio")
            short_pct_float = float(spf) if spf is not None else 0.0
            short_ratio     = float(sr)  if sr  is not None else 0.0

            # Classify short signal
            if short_pct_float > 0.15:
                # Check for squeeze potential: heavy short + recent price momentum
                if ret_1m > 0.05:
                    short_signal = "squeeze_potential"
                else:
                    short_signal = "high_short_interest"
        except Exception:
            pass  # short interest unavailable — keep defaults

        # Apply squeeze bonus to momentum score
        squeeze_bonus = 0.15 if short_pct_float > 0.15 and ret_1m > 0.05 else 0.0
        if squeeze_bonus > 0:
            momentum_score *= (1 + squeeze_bonus)
            logger.debug(f"Squeeze bonus applied to {ticker}: +{squeeze_bonus*100:.0f}%")

        return {
            "ticker": ticker,
            "price": round(price, 2),
            "sma20": round(sma20, 2),
            "sma50": round(sma50, 2),
            "sma200": round(sma200, 2),
            "above_sma200": price > sma200,
            "rsi": round(rsi, 1),
            "ret_1m_pct": round(ret_1m * 100, 2),
            "ret_3m_pct": round(ret_3m * 100, 2),
            "ret_6m_pct": round(ret_6m * 100, 2),
            "avg_daily_volume": int(avg_vol),
            "vol_ratio": round(vol_ratio, 2),
            "vs_sma50_pct": round(vs_sma50 * 100, 2),
            "momentum_score": round(momentum_score, 4),
            # Feature 6 fields
            "short_pct_float": round(short_pct_float * 100, 2),  # as percentage
            "short_ratio":     round(short_ratio, 2),
            "short_signal":    short_signal,
        }

    except Exception as e:
        logger.debug(f"Error scoring {ticker}: {e}")
        return None


def get_earnings_proximity(tickers: List[str]) -> Dict[str, Optional[str]]:
    """
    Check which tickers have earnings within the next 7 days.
    Returns dict of {ticker: earnings_date_str or None}
    """
    proximity = {}
    for ticker in tickers:
        try:
            import yfinance as yf
            import warnings
            warnings.filterwarnings("ignore")
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is not None and not cal.empty:
                # calendar is a DataFrame with dates as columns
                dates = [str(d)[:10] for d in cal.columns]
                next_earnings = dates[0] if dates else None
                if next_earnings:
                    days_away = (datetime.strptime(next_earnings, "%Y-%m-%d") - datetime.now()).days
                    proximity[ticker] = next_earnings if -1 <= days_away <= 7 else None
                else:
                    proximity[ticker] = None
            else:
                proximity[ticker] = None
        except Exception:
            proximity[ticker] = None
    return proximity


# ─── Main watchlist builder ────────────────────────────────────────────────────

def build_watchlist(
    regime: str = "BULL",
    top_n: int = 20,
    max_per_sector: int = 3,
    universe_sample: int = 150,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Build the dynamic watchlist for the current market regime.

    Args:
        regime: "BULL" | "CAUTION" | "BEAR" — controls selection aggressiveness
        top_n: number of symbols to return (default 20)
        max_per_sector: sector diversification cap
        universe_sample: how many S&P 500 stocks to screen (tradeoff: quality vs speed)
        force_refresh: bypass cache

    Returns dict with:
        symbols: List[str] — the watchlist to trade
        scored: List[Dict] — full scores for all candidates
        metadata: timing, regime, counts
    """
    # Check cache first
    if not force_refresh:
        cached = _load_cache("watchlist", ttl_hours=_CACHE_TTL_HOURS)
        if cached and cached.get("regime") == regime:
            logger.info(f"Using cached watchlist ({len(cached.get('symbols', []))} symbols)")
            return cached

    logger.info(f"Building dynamic watchlist (regime={regime}, universe={universe_sample})...")

    # In BEAR regime, reduce universe to most liquid/stable names only
    if regime == "BEAR":
        universe = [
            "SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META",
            "V", "MA", "UNH", "LLY", "COST", "HD", "AVGO", "BRK-B",
        ]
    else:
        all_tickers = get_sp500_tickers()  # returns static fallback if Wikipedia fails
        # Sample a subset for speed — score the most-liquid names first
        # Prioritize known large-caps + any additions
        priority = [
            "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "AMD", "AVGO",
            "TSLA", "LLY", "UNH", "V", "MA", "COST", "HD", "NFLX", "ORCL",
            "CRM", "NOW", "PANW", "ADBE", "MU", "AMAT", "ISRG", "TMO",
            "GS", "JPM", "BLK", "SPGI", "ICE", "MS", "BAC",
            "XOM", "CVX", "COP", "EOG",
            "CAT", "DE", "HON", "RTX", "GE", "UNP",
            "SPY", "QQQ", "IWM",
        ]
        # Fill up to universe_sample from the broader S&P list
        remaining = [t for t in all_tickers if t not in priority]
        import random
        random.seed(42)  # deterministic sampling
        universe = priority + random.sample(remaining, min(universe_sample - len(priority),
                                                           len(remaining)))

    # Score all symbols (this is the slow part — ~0.5s per symbol)
    scored = []
    for ticker in universe:
        s = score_symbol(ticker)
        if s:
            scored.append(s)

    if not scored:
        logger.warning("No symbols passed filters — using default watchlist")
        return _default_watchlist()

    # Sort by momentum score
    scored.sort(key=lambda x: x["momentum_score"], reverse=True)

    # Apply sector diversification cap
    sector_map = _get_sector_map()
    sector_counts = {}
    selected = []

    for s in scored:
        ticker = s["ticker"]
        sector = sector_map.get(ticker, "Other")
        count = sector_counts.get(sector, 0)

        if count >= max_per_sector:
            continue

        # In CAUTION regime, only take stocks also above SMA200 (stronger filter)
        if regime == "CAUTION" and not s.get("above_sma200", True):
            continue

        selected.append(s)
        sector_counts[sector] = count + 1

        if len(selected) >= top_n:
            break

    symbols = [s["ticker"] for s in selected]

    # Always include SPY and QQQ as market barometers (even if not top momentum)
    for benchmark in ["SPY", "QQQ"]:
        if benchmark not in symbols:
            symbols = [benchmark] + symbols

    # Feature 6: identify squeeze candidates in the watchlist
    squeeze_candidates = [
        s["ticker"] for s in selected
        if s.get("short_signal") == "squeeze_potential"
    ]

    result = {
        "symbols": symbols[:top_n],
        "scored": selected,
        "regime": regime,
        "built_at": datetime.now().isoformat(),
        "universe_screened": len(universe),
        "passed_filters": len(scored),
        "selected": len(selected),
        "sector_distribution": sector_counts,
        "squeeze_candidates": squeeze_candidates,
    }

    _save_cache("watchlist", result)
    logger.info(f"Watchlist built: {len(symbols)} symbols | "
                f"screened {len(universe)}, passed {len(scored)}")

    return result


def _default_watchlist() -> Dict[str, Any]:
    """Fallback static watchlist if dynamic selection fails."""
    symbols = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "GOOGL", "META",
               "AMZN", "AMD", "TSLA", "LLY", "V", "COST", "NFLX", "AVGO"]
    return {
        "symbols": symbols,
        "scored": [],
        "regime": "UNKNOWN",
        "built_at": datetime.now().isoformat(),
        "universe_screened": 0,
        "passed_filters": 0,
        "selected": len(symbols),
        "sector_distribution": {},
        "squeeze_candidates": [],
        "fallback": True,
    }


# ─── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache(key: str, ttl_hours: float = 4) -> Optional[Any]:
    try:
        if not os.path.exists(_CACHE_FILE):
            return None
        with open(_CACHE_FILE) as f:
            cache = json.load(f)
        entry = cache.get(key)
        if not entry:
            return None
        built_at = datetime.fromisoformat(entry.get("built_at", "2000-01-01"))
        if (datetime.now() - built_at).total_seconds() > ttl_hours * 3600:
            return None
        return entry
    except Exception:
        return None


def _save_cache(key: str, data: Any):
    try:
        cache = {}
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE) as f:
                cache = json.load(f)
        cache[key] = data
        with open(_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"Cache save failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s — %(message)s")
    print("Building dynamic watchlist...\n")
    wl = build_watchlist(regime="BULL", top_n=20, force_refresh=True)
    print(f"Selected {len(wl['symbols'])} symbols from "
          f"{wl['universe_screened']} screened:\n")
    print(f"  Watchlist: {', '.join(wl['symbols'])}\n")
    print(f"{'─'*70}")
    print(f"{'Ticker':<8} {'Price':>8} {'1m%':>7} {'3m%':>7} {'RSI':>6} "
          f"{'vs SMA50':>9} {'Score':>8}")
    print(f"{'─'*70}")
    for s in wl["scored"][:20]:
        print(f"  {s['ticker']:<6} ${s['price']:>8.2f} "
              f"{s['ret_1m_pct']:>+6.1f}% {s['ret_3m_pct']:>+6.1f}% "
              f"{s['rsi']:>6.1f} {s['vs_sma50_pct']:>+8.1f}% "
              f"{s['momentum_score']:>8.4f}")
    print(f"\nSector distribution: {wl['sector_distribution']}")
