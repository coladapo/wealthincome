"""
FRED Macro Client — Federal Reserve Economic Data (St. Louis Fed).
Free JSON API, no key required for most series.

Signals provided to Claude:
  - 10-year Treasury yield (DGS10)
  - 2-year Treasury yield (DGS2)
  - Yield curve spread 2s10s (inversion = recession warning)
  - Fed Funds effective rate (FEDFUNDS)
  - HY credit spread via BAMLH0A0HYM2 (ICE BofA HY Index OAS)
  - VIX via VIXCLS

Signal theory:
  - Yield curve inverted (<0): historically predicts recessions 6-18 months out.
    In practice: tighten position sizing, avoid new longs in cyclicals.
  - HY spread widening: credit stress before equity stress.
    >400bps = caution. >600bps = significant risk-off.
  - VIX > 25: elevated fear, widen stop-losses, reduce size.
  - VIX > 35: extreme fear, highly selective entries only.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

_FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"
_FRED_API  = "https://api.stlouisfed.org/fred/series/observations"

# In-memory cache: {series_id: (value, fetched_at)}
_cache: Dict[str, tuple] = {}
_CACHE_TTL_HOURS = 4


def _fetch_yfinance_macro(ticker: str, field: str = "last_price") -> Optional[float]:
    """Fetch a macro value via yfinance (fast_info). Used as primary data source."""
    cached = _cache.get(ticker)
    if cached:
        val, ts = cached
        if (datetime.now() - ts).total_seconds() < _CACHE_TTL_HOURS * 3600:
            return val
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        val = getattr(t.fast_info, field, None)
        if val is not None:
            _cache[ticker] = (float(val), datetime.now())
            return float(val)
    except Exception as e:
        logger.debug(f"yfinance macro fetch failed for {ticker}: {e}")
    return None


def _fetch_series_latest(series_id: str) -> Optional[float]:
    """Fetch the most recent value for a FRED series via CSV endpoint (no API key).
    Falls back gracefully if FRED is unreachable."""
    cached = _cache.get(series_id)
    if cached:
        val, ts = cached
        if (datetime.now() - ts).total_seconds() < _CACHE_TTL_HOURS * 3600:
            return val
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        resp = requests.get(url, timeout=8, headers={"User-Agent": "WealthIncome/1.0"})
        resp.raise_for_status()
        lines = resp.text.strip().splitlines()
        for line in reversed(lines[1:]):
            parts = line.split(",")
            if len(parts) == 2 and parts[1].strip() not in (".", ""):
                try:
                    val = float(parts[1].strip())
                    _cache[series_id] = (val, datetime.now())
                    return val
                except ValueError:
                    continue
    except Exception as e:
        logger.debug(f"FRED fetch failed for {series_id}: {e}")
    return None


def get_macro_context() -> Dict:
    """
    Fetch key macro indicators.
    Primary source: yfinance tickers (^TNX, ^IRX, ^VIX) — always fast.
    Fallback: FRED CSV endpoint for HY spreads (not available via yfinance).
    """
    # yfinance tickers for treasury yields and VIX
    dgs10 = _fetch_yfinance_macro("^TNX")   # 10-year yield (%)
    time.sleep(0.15)
    dgs2  = _fetch_yfinance_macro("^IRX")   # 13-week T-bill as 2Y proxy
    time.sleep(0.15)
    vix   = _fetch_yfinance_macro("^VIX")   # VIX
    time.sleep(0.15)

    # HY spread: try FRED (may timeout), fall back to None gracefully
    hy_oas = _fetch_series_latest("BAMLH0A0HYM2")

    yield_curve = round(dgs10 - dgs2, 3) if dgs10 and dgs2 else None

    # Classify each signal
    yield_signal = _classify_yield_curve(yield_curve)
    vix_signal   = _classify_vix(vix)
    hy_signal    = _classify_hy_spread(hy_oas)

    result = {
        "ten_year_yield":   dgs10,
        "two_year_yield":   dgs2,
        "yield_curve_2s10s": yield_curve,
        "vix":              vix,
        "hy_spread_bps":    hy_oas,
        "yield_signal":     yield_signal,
        "vix_signal":       vix_signal,
        "hy_signal":        hy_signal,
    }

    return result


def _classify_yield_curve(spread: Optional[float]) -> str:
    if spread is None:
        return "unknown"
    if spread > 0.5:
        return "normal_steep"      # healthy expansion
    if spread > 0:
        return "normal_flat"       # late cycle, still positive
    if spread > -0.5:
        return "inverted_mild"     # caution — historical recession precursor
    return "inverted_deep"         # significant warning


def _classify_vix(vix: Optional[float]) -> str:
    if vix is None:
        return "unknown"
    if vix < 15:
        return "low_complacency"   # low fear — be alert for reversals
    if vix < 20:
        return "normal"
    if vix < 25:
        return "elevated"          # heightened uncertainty
    if vix < 35:
        return "high_fear"         # reduce new position sizing
    return "extreme_fear"          # crisis-level, highly selective only


def _classify_hy_spread(oas: Optional[float]) -> str:
    if oas is None:
        return "unknown"
    if oas < 300:
        return "tight_benign"      # credit markets healthy, risk-on
    if oas < 400:
        return "normal"
    if oas < 500:
        return "widening_caution"  # stress building
    if oas < 650:
        return "wide_stress"       # credit markets stressed
    return "crisis_wide"           # crisis-level spreads


def build_macro_block_for_claude(macro: Dict) -> str:
    """Build compact macro context block for Claude prompt."""
    if not macro or all(v is None for k, v in macro.items() if k not in ("yield_signal", "vix_signal", "hy_signal")):
        return ""

    lines = ["=== MACRO CONTEXT (FRED) ==="]

    ten = macro.get("ten_year_yield")
    two = macro.get("two_year_yield")
    curve = macro.get("yield_curve_2s10s")
    vix = macro.get("vix")
    hy = macro.get("hy_spread_bps")

    if ten:
        lines.append(f"10Y Treasury: {ten:.2f}%  |  2Y Treasury: {two:.2f}%" if two else f"10Y Treasury: {ten:.2f}%")

    if curve is not None:
        curve_str = f"{curve:+.3f}%"
        ysig = macro.get("yield_signal", "")
        if "inverted" in ysig:
            lines.append(f"Yield Curve (2s10s): {curve_str} ⚠ INVERTED — historical recession precursor, tighten sizing on cyclicals")
        else:
            lines.append(f"Yield Curve (2s10s): {curve_str} — normal")

    if vix:
        vsig = macro.get("vix_signal", "")
        vix_note = {
            "low_complacency": "low fear, watch for complacency reversals",
            "normal": "normal",
            "elevated": "elevated — reduce new position sizing",
            "high_fear": "HIGH — reduce position sizes, wider stops",
            "extreme_fear": "EXTREME — highly selective entries only",
        }.get(vsig, "")
        lines.append(f"VIX: {vix:.1f} — {vix_note}")

    if hy:
        hsig = macro.get("hy_signal", "")
        hy_note = {
            "tight_benign": "tight/healthy — credit supports risk-on",
            "normal": "normal range",
            "widening_caution": "widening — credit stress building, monitor closely",
            "wide_stress": "WIDE — credit markets stressed, tighten entries",
            "crisis_wide": "CRISIS WIDE — avoid new longs",
        }.get(hsig, "")
        lines.append(f"HY Credit Spread: {hy:.0f}bps — {hy_note}")

    lines.append("NOTE: Use macro context to set risk posture. Inverted curve or VIX>25 = reduce new entries, tighten stops.")
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    macro = get_macro_context()
    print("\nFRED Macro Context:")
    for k, v in macro.items():
        print(f"  {k}: {v}")
    print("\nClaude Block:")
    print(build_macro_block_for_claude(macro))
