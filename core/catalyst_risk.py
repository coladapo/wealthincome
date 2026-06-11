"""
Catalyst Risk Framework — Tiered exit and stop logic around binary events.

Based on quant industry research: trailing stops fail during discontinuous events
(earnings gaps, FOMC surprises, ex-div drops, halts, index rebalances). This module
classifies the catalyst environment for any symbol and returns structured risk tiers
that drive stop placement, position sizing, and exit mode decisions.

Tier 0 — Clear:         Normal conditions. Place trailing stop at entry.
Tier 1 — Widen:         Mild catalyst risk. Widen stop 1.5x ATR, continue monitoring.
Tier 2 — Suspend:       Binary event within window. Skip trailing stop; use AI+SMA50.
Tier 3 — Reduce+Suspend: Hard block on new entries OR forced size reduction; no stop.

Integration points:
  - execute_decision():  reads tier to determine stop placement + position size modifier
  - check_stop_losses(): reads tier to decide whether to enforce hard stop this cycle
  - run_cycle():         passes enriched catalyst context per symbol
"""

import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────────

EARNINGS_SUSPEND_DAYS    = 7     # skip trailing stop if earnings within this window
EARNINGS_REDUCE_DAYS     = 14   # reduce position size if earnings within this window
EARNINGS_REDUCE_PCT      = 0.5  # multiply position size by this near earnings

FOMC_SUSPEND_DAYS        = 2    # skip trailing stop if FOMC within this window
FOMC_REDUCE_DAYS         = 5    # reduce size if FOMC within this window
FOMC_REDUCE_PCT          = 0.6  # multiply position size by this near FOMC

MACRO_SUSPEND_DAYS       = 1    # CPI/NFP: suspend stop day-of
MACRO_REDUCE_DAYS        = 2    # reduce size within 2 days
MACRO_REDUCE_PCT         = 0.7

EX_DIV_WIDEN_DAYS        = 2    # widen (not suspend) stop around ex-div date
EX_DIV_WIDEN_MULTIPLIER  = 1.5  # multiply trail_percent by this on ex-div window

REBALANCE_SUSPEND_DAYS   = 2    # index rebalance: suspend stop
REBALANCE_REDUCE_PCT     = 0.7

STOP_WIDEN_MULTIPLIER    = 2.0  # default widen factor for Tier 1 events


# ─── Data class ───────────────────────────────────────────────────────────────

@dataclass
class CatalystRisk:
    tier: int                        # 0=clear, 1=widen, 2=suspend, 3=reduce+suspend
    suspend_trailing_stop: bool      # do not place Alpaca trailing stop
    widen_stop_multiplier: float     # multiply trail_percent by this (1.0 = no change)
    position_size_multiplier: float  # multiply position_size_pct by this (1.0 = no change)
    block_new_entry: bool            # hard block on new buy
    events: List[str]                # human-readable list of detected events
    primary_reason: str              # single most important reason

    def log_summary(self, symbol: str):
        if self.tier == 0:
            return
        logger.info(
            f"CatalystRisk [{symbol}] tier={self.tier} "
            f"suspend_stop={self.suspend_trailing_stop} "
            f"size_mult={self.position_size_multiplier:.1f} "
            f"block={self.block_new_entry} | {self.primary_reason}"
        )


def _clear() -> CatalystRisk:
    return CatalystRisk(
        tier=0, suspend_trailing_stop=False, widen_stop_multiplier=1.0,
        position_size_multiplier=1.0, block_new_entry=False,
        events=[], primary_reason="clear",
    )


# ─── Ex-dividend lookup ───────────────────────────────────────────────────────

def _get_ex_dividend_date(symbol: str) -> Optional[date]:
    """Fetch next ex-dividend date via yfinance. Returns None if not available."""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        info = t.info or {}
        ex_div = info.get("exDividendDate")
        if ex_div:
            # yfinance returns Unix timestamp
            if isinstance(ex_div, (int, float)):
                return date.fromtimestamp(ex_div)
            return date.fromisoformat(str(ex_div)[:10])
    except Exception as e:
        logger.debug(f"Ex-dividend lookup failed for {symbol}: {e}")
    return None


# ─── Index rebalance dates (quarterly, hardcoded) ────────────────────────────

# S&P 500 / Russell rebalances: third Friday of March, June, September, December
_REBALANCE_DATES_2026 = [
    "2026-03-20", "2026-06-19", "2026-09-18", "2026-12-18",
]


def _get_rebalance_dates() -> List[str]:
    return _REBALANCE_DATES_2026


# ─── Main assessment ─────────────────────────────────────────────────────────

def assess_catalyst_risk(
    symbol: str,
    days_until_earnings: Optional[int] = None,
    fomc_dates: Optional[List[str]] = None,
    cpi_dates: Optional[List[str]] = None,
    nfp_dates: Optional[List[str]] = None,
    check_ex_dividend: bool = True,
) -> CatalystRisk:
    """
    Assess catalyst risk for a symbol and return a CatalystRisk object.

    Args:
        symbol:               ticker symbol
        days_until_earnings:  pre-computed days to next earnings (from market_data)
        fomc_dates:           list of FOMC date strings (from economic_calendar)
        cpi_dates:            list of CPI date strings
        nfp_dates:            list of NFP date strings
        check_ex_dividend:    whether to look up ex-dividend date (adds ~0.2s yfinance call)

    Returns:
        CatalystRisk with tier, stop/size/block flags, and event list.
    """
    today = date.today()
    events: List[str] = []

    # Accumulators — worst case across all detected events
    suspend_stop    = False
    block_entry     = False
    size_mult       = 1.0
    widen_mult      = 1.0

    # ── Earnings ──────────────────────────────────────────────────────────────
    if days_until_earnings is not None:
        d = days_until_earnings
        if d <= 0:
            # Earnings today — hard block, no stop
            block_entry  = True
            suspend_stop = True
            size_mult    = min(size_mult, 0.0)
            events.append(f"{symbol} earnings TODAY — hard block")
        elif d <= EARNINGS_SUSPEND_DAYS:
            suspend_stop = True
            size_mult    = min(size_mult, EARNINGS_REDUCE_PCT)
            events.append(f"{symbol} earnings in {d}d — stop suspended, size ×{EARNINGS_REDUCE_PCT}")
        elif d <= EARNINGS_REDUCE_DAYS:
            size_mult    = min(size_mult, EARNINGS_REDUCE_PCT)
            events.append(f"{symbol} earnings in {d}d — size reduced ×{EARNINGS_REDUCE_PCT}")

    # ── FOMC ─────────────────────────────────────────────────────────────────
    if fomc_dates:
        for ds in fomc_dates:
            try:
                event_date = date.fromisoformat(ds[:10])
                days_away  = (event_date - today).days
                if 0 <= days_away <= FOMC_SUSPEND_DAYS:
                    suspend_stop = True
                    size_mult    = min(size_mult, FOMC_REDUCE_PCT)
                    events.append(f"FOMC in {days_away}d ({ds}) — stop suspended, size ×{FOMC_REDUCE_PCT}")
                elif FOMC_SUSPEND_DAYS < days_away <= FOMC_REDUCE_DAYS:
                    size_mult    = min(size_mult, FOMC_REDUCE_PCT)
                    events.append(f"FOMC in {days_away}d ({ds}) — size reduced ×{FOMC_REDUCE_PCT}")
            except Exception:
                continue

    # ── CPI ───────────────────────────────────────────────────────────────────
    if cpi_dates:
        for ds in cpi_dates:
            try:
                event_date = date.fromisoformat(ds[:10])
                days_away  = (event_date - today).days
                if 0 <= days_away <= MACRO_SUSPEND_DAYS:
                    suspend_stop = True
                    size_mult    = min(size_mult, MACRO_REDUCE_PCT)
                    events.append(f"CPI in {days_away}d ({ds}) — stop suspended")
                elif MACRO_SUSPEND_DAYS < days_away <= MACRO_REDUCE_DAYS:
                    size_mult    = min(size_mult, MACRO_REDUCE_PCT)
                    events.append(f"CPI in {days_away}d ({ds}) — size reduced ×{MACRO_REDUCE_PCT}")
            except Exception:
                continue

    # ── NFP ───────────────────────────────────────────────────────────────────
    if nfp_dates:
        for ds in nfp_dates:
            try:
                event_date = date.fromisoformat(ds[:10])
                days_away  = (event_date - today).days
                if 0 <= days_away <= MACRO_SUSPEND_DAYS:
                    suspend_stop = True
                    size_mult    = min(size_mult, MACRO_REDUCE_PCT)
                    events.append(f"NFP in {days_away}d ({ds}) — stop suspended")
                elif MACRO_SUSPEND_DAYS < days_away <= MACRO_REDUCE_DAYS:
                    size_mult    = min(size_mult, MACRO_REDUCE_PCT)
                    events.append(f"NFP in {days_away}d ({ds}) — size reduced ×{MACRO_REDUCE_PCT}")
            except Exception:
                continue

    # ── Ex-dividend ───────────────────────────────────────────────────────────
    if check_ex_dividend:
        try:
            ex_div = _get_ex_dividend_date(symbol)
            if ex_div:
                days_away = (ex_div - today).days
                if 0 <= days_away <= EX_DIV_WIDEN_DAYS:
                    # Widen stop rather than suspend — drop is mechanical, not adverse
                    widen_mult = max(widen_mult, EX_DIV_WIDEN_MULTIPLIER)
                    events.append(
                        f"{symbol} ex-div in {days_away}d ({ex_div}) — "
                        f"stop widened ×{EX_DIV_WIDEN_MULTIPLIER}"
                    )
        except Exception as e:
            logger.debug(f"Ex-div check skipped for {symbol}: {e}")

    # ── Index rebalance ───────────────────────────────────────────────────────
    for ds in _get_rebalance_dates():
        try:
            event_date = date.fromisoformat(ds)
            days_away  = (event_date - today).days
            if 0 <= days_away <= REBALANCE_SUSPEND_DAYS:
                suspend_stop = True
                size_mult    = min(size_mult, REBALANCE_REDUCE_PCT)
                events.append(f"Index rebalance in {days_away}d ({ds}) — stop suspended")
        except Exception:
            continue

    # ── Determine tier ────────────────────────────────────────────────────────
    if block_entry or (suspend_stop and size_mult == 0.0):
        tier = 3
    elif suspend_stop:
        tier = 2
    elif widen_mult > 1.0 or size_mult < 1.0:
        tier = 1
    else:
        tier = 0

    primary_reason = events[0] if events else "clear"

    return CatalystRisk(
        tier=tier,
        suspend_trailing_stop=suspend_stop,
        widen_stop_multiplier=widen_mult,
        position_size_multiplier=size_mult,
        block_new_entry=block_entry,
        events=events,
        primary_reason=primary_reason,
    )


# ─── Convenience wrapper used by trader ──────────────────────────────────────

def get_catalyst_risk(
    symbol: str,
    days_until_earnings: Optional[int] = None,
    enriched_calendar: Optional[Dict] = None,
) -> CatalystRisk:
    """
    Entry point for the trader. Pulls FOMC/CPI/NFP from economic_calendar
    (already cached) and runs the full assessment.

    Args:
        symbol:               ticker
        days_until_earnings:  from market_data[symbol]["next_earnings"] (pre-computed)
        enriched_calendar:    optional dict from economic_calendar module with pre-fetched dates

    Returns:
        CatalystRisk
    """
    fomc_dates = cpi_dates = nfp_dates = None
    try:
        from core.economic_calendar import get_fomc_dates, get_cpi_dates, get_nfp_dates
        fomc_dates = get_fomc_dates()
        cpi_dates  = get_cpi_dates()
        nfp_dates  = get_nfp_dates()
    except Exception as e:
        logger.warning(f"Could not load calendar dates for catalyst risk: {e}")

    risk = assess_catalyst_risk(
        symbol=symbol,
        days_until_earnings=days_until_earnings,
        fomc_dates=fomc_dates,
        cpi_dates=cpi_dates,
        nfp_dates=nfp_dates,
        check_ex_dividend=True,
    )
    risk.log_summary(symbol)
    return risk


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    for sym, days in [("SBUX", 6), ("GS", None), ("AMAT", None), ("AAPL", 8)]:
        r = get_catalyst_risk(sym, days_until_earnings=days)
        print(f"{sym}: tier={r.tier} suspend={r.suspend_trailing_stop} "
              f"size_mult={r.position_size_multiplier} block={r.block_new_entry}")
        for e in r.events:
            print(f"  → {e}")
