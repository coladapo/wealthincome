"""
Layer 1 Extension: Economic Calendar Awareness

Tracks high-impact macro events that move markets:
  - FOMC decisions (Fed rate decisions)
  - CPI / inflation prints
  - Jobs reports (NFP)
  - Individual stock earnings dates

Rules injected into trading:
  - 24h before event: reduce position size 50%
  - Day-of event: block all new entries
  - 2 days before earnings on a held position: flag for Claude to evaluate

Data sources (all free):
  - federalreserve.gov — FOMC calendar
  - bls.gov — CPI, NFP schedule
  - yfinance — individual stock earnings
  - Hardcoded fallback if scraping fails (reviewed quarterly)
"""

import os
import json
import logging
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".calendar_cache.json")
_CACHE_TTL_HOURS = 12


# ─── Hardcoded fallback dates (2026 schedule) ─────────────────────────────────
# Updated quarterly — these are the known remaining dates
_FALLBACK_FOMC = [
    "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16",
    "2026-10-28", "2026-12-09",
]
_FALLBACK_CPI = [
    "2026-05-13", "2026-06-11", "2026-07-15", "2026-08-12",
    "2026-09-10", "2026-10-14", "2026-11-12", "2026-12-10",
]
_FALLBACK_NFP = [
    "2026-05-01", "2026-06-05", "2026-07-02", "2026-08-07",
    "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
]


# ─── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache(key: str, ttl_hours: float = _CACHE_TTL_HOURS) -> Optional[any]:
    try:
        if not os.path.exists(_CACHE_FILE):
            return None
        with open(_CACHE_FILE) as f:
            cache = json.load(f)
        entry = cache.get(key, {})
        if not entry:
            return None
        built = datetime.fromisoformat(entry.get("built_at", "2000-01-01"))
        if (datetime.now() - built).total_seconds() > ttl_hours * 3600:
            return None
        return entry.get("data")
    except Exception:
        return None


def _save_cache(key: str, data):
    try:
        cache = {}
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE) as f:
                cache = json.load(f)
        cache[key] = {"data": data, "built_at": datetime.now().isoformat()}
        with open(_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2, default=str)
    except Exception as e:
        logger.debug(f"Calendar cache save failed: {e}")


# ─── FOMC dates ────────────────────────────────────────────────────────────────

def get_fomc_dates() -> List[str]:
    """Fetch FOMC meeting dates from Fed website. Falls back to hardcoded list."""
    cached = _load_cache("fomc_dates")
    if cached:
        return cached

    try:
        import requests
        from html.parser import HTMLParser

        class FOMCParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.dates = []
                self._in_meeting = False

            def handle_data(self, data):
                data = data.strip()
                # Fed page contains dates like "April 28-29, 2026"
                if any(month in data for month in
                       ["January","February","March","April","May","June",
                        "July","August","September","October","November","December"]):
                    if any(str(y) in data for y in range(2025, 2028)):
                        self.dates.append(data)

        resp = requests.get(
            "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        parser = FOMCParser()
        parser.feed(resp.text)

        # Parse "April 28-29, 2026" → "2026-04-29" (decision day = last day)
        parsed = []
        months = {
            "January":"01","February":"02","March":"03","April":"04",
            "May":"05","June":"06","July":"07","August":"08",
            "September":"09","October":"10","November":"11","December":"12"
        }
        for raw in parser.dates:
            for month, num in months.items():
                if month in raw:
                    try:
                        year = [w for w in raw.split() if len(w)==4 and w.isdigit()][0]
                        # Get the last day in the range (decision day)
                        days_part = raw.replace(month, "").replace(year, "").strip().strip(",")
                        if "-" in days_part:
                            day = days_part.split("-")[-1].strip()
                        else:
                            day = days_part.strip()
                        day = "".join(c for c in day if c.isdigit())
                        if day and len(day) <= 2:
                            date_str = f"{year}-{num}-{day.zfill(2)}"
                            parsed.append(date_str)
                    except Exception:
                        continue

        if len(parsed) >= 3:
            _save_cache("fomc_dates", parsed)
            logger.info(f"FOMC: fetched {len(parsed)} dates from Fed website")
            return parsed

    except Exception as e:
        logger.debug(f"FOMC scrape failed: {e} — using fallback")

    _save_cache("fomc_dates", _FALLBACK_FOMC)
    return _FALLBACK_FOMC


def get_cpi_dates() -> List[str]:
    """Fetch CPI release dates from BLS. Falls back to hardcoded list."""
    cached = _load_cache("cpi_dates")
    if cached:
        return cached

    try:
        import requests
        resp = requests.get(
            "https://www.bls.gov/schedule/news_release/cpi.htm",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        # BLS page contains dates in format "Month DD, YYYY"
        import re
        pattern = r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(202\d)'
        matches = re.findall(pattern, resp.text)
        months = {
            "January":"01","February":"02","March":"03","April":"04",
            "May":"05","June":"06","July":"07","August":"08",
            "September":"09","October":"10","November":"11","December":"12"
        }
        parsed = []
        for month, day, year in matches:
            parsed.append(f"{year}-{months[month]}-{day.zfill(2)}")
        parsed = sorted(set(parsed))
        if len(parsed) >= 3:
            _save_cache("cpi_dates", parsed)
            logger.info(f"CPI: fetched {len(parsed)} dates from BLS")
            return parsed
    except Exception as e:
        logger.debug(f"CPI scrape failed: {e} — using fallback")

    _save_cache("cpi_dates", _FALLBACK_CPI)
    return _FALLBACK_CPI


def get_nfp_dates() -> List[str]:
    """Non-farm payroll dates — hardcoded (always first Friday of month)."""
    cached = _load_cache("nfp_dates")
    if cached:
        return cached
    _save_cache("nfp_dates", _FALLBACK_NFP)
    return _FALLBACK_NFP


def get_earnings_date(symbol: str) -> Optional[str]:
    """Get next earnings date for a symbol via yfinance. Cached 6h."""
    key = f"earnings_{symbol}"
    cached = _load_cache(key, ttl_hours=6)
    if cached is not None:
        return cached or None

    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        cal = t.calendar
        if cal is not None and not cal.empty:
            dates = [str(d)[:10] for d in cal.columns]
            result = dates[0] if dates else None
            _save_cache(key, result)
            return result
    except Exception:
        pass

    _save_cache(key, "")
    return None


# ─── Risk window assessment ────────────────────────────────────────────────────

def is_high_risk_window(
    symbol: str,
    hours_ahead: int = 24,
) -> Dict:
    """
    Check if a trade on this symbol should be blocked or sized down.

    Returns:
        block_entry:     bool — True = no new positions at all
        reduce_size_pct: float — multiply position size by this (1.0 = no change)
        reason:          str
        events:          List[str] — events found
    """
    now = datetime.now()
    events = []
    block = False
    reduce = 1.0

    # Check macro events (affect all symbols)
    all_macro = [
        ("FOMC", get_fomc_dates()),
        ("CPI",  get_cpi_dates()),
        ("NFP",  get_nfp_dates()),
    ]

    for event_name, dates in all_macro:
        for date_str in dates:
            try:
                event_dt = datetime.strptime(date_str, "%Y-%m-%d")
                hours_until = (event_dt - now).total_seconds() / 3600

                if -2 <= hours_until <= 0:
                    # Event is happening today / just passed (within 2h)
                    block = True
                    reduce = min(reduce, 0.5)
                    events.append(f"{event_name} TODAY ({date_str})")
                elif 0 < hours_until <= hours_ahead:
                    # Within the warning window
                    reduce = min(reduce, 0.5)
                    events.append(f"{event_name} in {hours_until:.0f}h ({date_str})")
            except Exception:
                continue

    # Check earnings for this specific symbol (tighter window: 2 days)
    earnings = get_earnings_date(symbol)
    if earnings:
        try:
            earn_dt = datetime.strptime(earnings[:10], "%Y-%m-%d")
            hours_until = (earn_dt - now).total_seconds() / 3600
            days_until = hours_until / 24

            if -1 <= days_until <= 0:
                block = True
                events.append(f"{symbol} earnings TODAY ({earnings[:10]})")
            elif 0 < days_until <= 2:
                block = True
                events.append(f"{symbol} earnings in {days_until:.1f} days ({earnings[:10]})")
            elif 2 < days_until <= 5:
                reduce = min(reduce, 0.6)
                events.append(f"{symbol} earnings in {days_until:.1f} days ({earnings[:10]})")
        except Exception:
            pass

    reason = " | ".join(events) if events else "clear"

    return {
        "block_entry": block,
        "reduce_size_pct": reduce,
        "reason": reason,
        "events": events,
    }


# ─── Claude prompt block ───────────────────────────────────────────────────────

def get_calendar_summary_for_claude(symbols: List[str]) -> str:
    """
    Build a compact calendar block for Claude's trading prompt.
    Only includes upcoming events in the next 7 days.
    """
    now = datetime.now()
    lines = []

    # Macro events next 7 days
    macro_events = []
    for event_name, dates in [("FOMC", get_fomc_dates()), ("CPI", get_cpi_dates()), ("NFP", get_nfp_dates())]:
        for date_str in dates:
            try:
                event_dt = datetime.strptime(date_str, "%Y-%m-%d")
                days = (event_dt - now).days
                if -1 <= days <= 7:
                    macro_events.append((days, event_name, date_str))
            except Exception:
                continue

    if macro_events:
        macro_events.sort()
        for days, name, date in macro_events:
            if days <= 0:
                lines.append(f"  ⚠ {name} TODAY — NO new entries, existing positions at risk")
            elif days == 1:
                lines.append(f"  ⚠ {name} TOMORROW ({date}) — reduce position sizes 50%")
            else:
                lines.append(f"  · {name} in {days} days ({date})")

    # Earnings for watchlist symbols in next 5 days
    earnings_soon = []
    for sym in symbols[:15]:  # limit to avoid too many yfinance calls
        earn = get_earnings_date(sym)
        if earn:
            try:
                earn_dt = datetime.strptime(earn[:10], "%Y-%m-%d")
                days = (earn_dt - now).days
                if -1 <= days <= 5:
                    earnings_soon.append((days, sym, earn[:10]))
            except Exception:
                continue

    if earnings_soon:
        earnings_soon.sort()
        for days, sym, date in earnings_soon:
            if days <= 0:
                lines.append(f"  ⚠ {sym} earnings TODAY — DO NOT enter, consider exiting")
            elif days <= 2:
                lines.append(f"  ⚠ {sym} earnings in {days}d ({date}) — BLOCK new entry")
            else:
                lines.append(f"  · {sym} earnings in {days}d ({date}) — reduce size if entering")

    if not lines:
        return ""

    header = "=== ECONOMIC CALENDAR (next 7 days) ==="
    footer = "RULE: Block entries on event-day symbols. Reduce size 50% within 24h of macro events."
    return "\n".join([header] + lines + [footer])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    print("Economic Calendar Test\n")

    print("FOMC dates:", get_fomc_dates()[:4])
    print("CPI dates:", get_cpi_dates()[:4])
    print("NFP dates:", get_nfp_dates()[:4])

    print("\nRisk window for AAPL:")
    r = is_high_risk_window("AAPL")
    print(f"  block={r['block_entry']} reduce={r['reduce_size_pct']} reason={r['reason']}")

    print("\nCalendar summary for Claude:")
    summary = get_calendar_summary_for_claude(["AAPL", "MSFT", "NVDA", "GS", "CAT"])
    print(summary if summary else "  (no events in next 7 days)")
