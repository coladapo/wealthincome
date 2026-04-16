"""
Earnings Calendar Scraper — EarningsWhispers.com via krawlr.
Provides the "whisper number" (what the street actually expects vs. official consensus)
and upcoming earnings dates so Claude avoids entering positions right before earnings.

Signal theory:
  - Official EPS consensus is published. The "whisper number" is the unofficial,
    higher bar the stock is actually measured against.
  - If whisper > consensus: stock needs to beat whisper to rally, not just consensus.
  - Earnings within 3 days: avoid new entries — gap risk is binary and unhedgeable.
  - Earnings surprise history: companies that consistently beat tend to continue doing so.

Data source: earningswhispers.com (free public data, scraped via krawlr)
Fallback: yfinance earnings calendar (less detail but always available)
"""

import logging
import time
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# In-memory cache
_cache: Dict[str, dict] = {}
_CACHE_TTL_HOURS = 6  # earnings dates change rarely intraday


def _cache_get(key: str) -> Optional[dict]:
    entry = _cache.get(key)
    if not entry:
        return None
    if (datetime.now() - entry["ts"]).total_seconds() > _CACHE_TTL_HOURS * 3600:
        return None
    return entry["data"]


def _cache_set(key: str, data: dict):
    _cache[key] = {"data": data, "ts": datetime.now()}


def get_earnings_via_yfinance(symbols: List[str]) -> Dict[str, dict]:
    """
    Fallback: use yfinance to get next earnings date for each symbol.
    Less detail than EarningsWhispers but always available.
    """
    results = {}
    try:
        import yfinance as yf
    except ImportError:
        return results

    for sym in symbols:
        cached = _cache_get(f"yf_{sym}")
        if cached:
            results[sym] = cached
            continue
        try:
            ticker = yf.Ticker(sym)
            cal = ticker.calendar  # returns dict in modern yfinance
            next_date = None
            if isinstance(cal, dict):
                earnings_dates = cal.get("Earnings Date", [])
                if not isinstance(earnings_dates, list):
                    earnings_dates = [earnings_dates] if earnings_dates else []
                future = [d for d in earnings_dates if d and d >= date.today()]
                if future:
                    next_date = min(future)
            elif cal is not None and hasattr(cal, 'empty') and not cal.empty:
                # Legacy DataFrame format
                for key in ["Earnings Date", "earningsDate"]:
                    if key in cal.index:
                        raw = cal.loc[key]
                        for v in (raw.values if hasattr(raw, 'values') else [raw]):
                            try:
                                d = v if isinstance(v, date) else v.date() if hasattr(v, 'date') else None
                                if d and d >= date.today():
                                    next_date = d
                                    break
                            except Exception:
                                pass

            if next_date:
                days_away = (next_date - date.today()).days
                data = {
                    "symbol": sym,
                    "next_earnings_date": next_date.isoformat(),
                    "days_until_earnings": days_away,
                    "earnings_risk": _classify_earnings_risk(days_away),
                    "source": "yfinance",
                }
                results[sym] = data
                _cache_set(f"yf_{sym}", data)
                continue

            results[sym] = {"symbol": sym, "next_earnings_date": None, "days_until_earnings": None,
                            "earnings_risk": "unknown", "source": "yfinance"}
        except Exception as e:
            logger.debug(f"yfinance earnings failed for {sym}: {e}")
            results[sym] = {"symbol": sym, "next_earnings_date": None, "days_until_earnings": None,
                            "earnings_risk": "unknown", "source": "yfinance"}
        time.sleep(0.1)

    return results


def get_earnings_calendar(symbols: List[str], use_krawlr: bool = False) -> Dict[str, dict]:
    """
    Get upcoming earnings dates for a list of symbols.
    Primary: yfinance (always available, no scraping needed)
    Optional: krawlr scrape of EarningsWhispers for whisper numbers (set use_krawlr=True)
    """
    # yfinance is reliable enough for the core need (knowing dates)
    results = get_earnings_via_yfinance(symbols)

    if use_krawlr:
        # Attempt to enrich with whisper numbers via krawlr
        # This is optional — if krawlr isn't available in this context, skip
        try:
            results = _enrich_with_whispers(symbols, results)
        except Exception as e:
            logger.debug(f"EarningsWhispers enrichment skipped: {e}")

    return results


def _enrich_with_whispers(symbols: List[str], base: Dict[str, dict]) -> Dict[str, dict]:
    """
    Scrape EarningsWhispers for whisper numbers.
    Uses requests directly (simpler than krawlr for this structured page).
    """
    for sym in symbols[:10]:
        cached = _cache_get(f"ew_{sym}")
        if cached:
            if sym in base:
                base[sym].update(cached)
            continue

        try:
            url = f"https://www.earningswhispers.com/stocks/{sym.lower()}"
            resp = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Accept": "text/html",
                },
                timeout=10,
            )
            if resp.status_code != 200:
                continue

            html = resp.text
            whisper_data = _parse_earningswhispers(html, sym)
            if whisper_data:
                _cache_set(f"ew_{sym}", whisper_data)
                if sym in base:
                    base[sym].update(whisper_data)
                else:
                    base[sym] = whisper_data

            time.sleep(0.5)  # be polite

        except Exception as e:
            logger.debug(f"EarningsWhispers scrape failed for {sym}: {e}")

    return base


def _parse_earningswhispers(html: str, symbol: str) -> Optional[dict]:
    """Extract whisper number and earnings date from EarningsWhispers HTML."""
    import re

    result = {}

    # Whisper EPS estimate
    whisper_match = re.search(r'id="consensus"[^>]*>\s*\$?([-\d.]+)', html)
    if not whisper_match:
        whisper_match = re.search(r'whisper[^>]*>\s*\$?([-\d.]+)', html, re.IGNORECASE)
    if whisper_match:
        try:
            result["whisper_eps"] = float(whisper_match.group(1))
        except ValueError:
            pass

    # Consensus EPS
    consensus_match = re.search(r'id="estimate"[^>]*>\s*\$?([-\d.]+)', html)
    if consensus_match:
        try:
            result["consensus_eps"] = float(consensus_match.group(1))
        except ValueError:
            pass

    # Whisper vs consensus delta
    if "whisper_eps" in result and "consensus_eps" in result:
        result["whisper_vs_consensus"] = round(result["whisper_eps"] - result["consensus_eps"], 4)
        result["bar_is_higher"] = result["whisper_vs_consensus"] > 0

    result["source"] = "earningswhispers"
    return result if result else None


def _classify_earnings_risk(days_away: Optional[int]) -> str:
    if days_away is None:
        return "unknown"
    if days_away <= 0:
        return "today"         # earnings today — do not enter
    if days_away <= 3:
        return "imminent"      # within 3 days — avoid new entries
    if days_away <= 7:
        return "this_week"     # this week — reduce position sizing
    if days_away <= 14:
        return "next_week"     # monitor
    return "safe"              # >2 weeks out — no near-term gap risk


def build_earnings_block_for_claude(earnings: Dict[str, dict], positions: Optional[List[dict]] = None) -> str:
    """Build earnings calendar block for Claude prompt."""
    if not earnings:
        return ""

    held = {p.get("symbol") for p in (positions or [])}
    warnings = []
    safe = []

    for sym, data in sorted(earnings.items()):
        days = data.get("days_until_earnings")
        risk = data.get("earnings_risk", "unknown")
        date_str = data.get("next_earnings_date", "unknown")
        is_held = sym in held
        held_tag = " [HELD]" if is_held else ""

        whisper = data.get("whisper_eps")
        consensus = data.get("consensus_eps")
        whisper_str = ""
        if whisper is not None and consensus is not None:
            delta = whisper - consensus
            whisper_str = f" | whisper ${whisper:.2f} vs consensus ${consensus:.2f} ({delta:+.2f})"

        if risk in ("today", "imminent"):
            warnings.append(
                f"  ⚠ {sym}{held_tag}: earnings in {days}d ({date_str}) — AVOID new entries, gap risk{whisper_str}"
            )
        elif risk == "this_week":
            warnings.append(
                f"  ! {sym}{held_tag}: earnings in {days}d ({date_str}) — reduce sizing{whisper_str}"
            )
        elif days is not None and days <= 14:
            safe.append(
                f"    {sym}{held_tag}: earnings in {days}d ({date_str}){whisper_str}"
            )

    if not warnings and not safe:
        return ""

    lines = ["=== EARNINGS CALENDAR ==="]
    if warnings:
        lines.extend(warnings)
    if safe:
        lines.append("  Upcoming (>7 days):")
        lines.extend(safe)
    lines.append("RULE: Do not enter new positions within 3 days of earnings. Binary gap risk.")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "NVDA", "MSFT", "AMZN", "GOOGL"]
    print(f"\nEarnings Calendar — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    data = get_earnings_calendar(symbols)
    for sym, d in data.items():
        print(f"  {sym}: {d.get('next_earnings_date')} ({d.get('days_until_earnings')}d) — {d.get('earnings_risk')}")
    print("\nClaude Block:")
    print(build_earnings_block_for_claude(data))
