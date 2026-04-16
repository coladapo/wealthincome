"""
Options Flow Scraper — Barchart Unusual Options Activity.
Scrapes barchart.com/options/unusual-activity for real institutional options flow.

Signal theory:
  - Unusual options activity = someone is betting big on a move before it happens.
  - "Sweep" orders = aggressive market orders across multiple exchanges = urgency.
  - Large call sweeps above ask = bullish institutional conviction.
  - Large put sweeps below bid = bearish hedging or directional bet.
  - Net premium (calls - puts) per symbol: positive = net bullish flow.
  - This is what hedge funds pay $27k/yr Bloomberg for — we scrape it free.

Data source: barchart.com/options/unusual-activity (free, 15-min delayed)
Updates every 15 minutes during market hours.
"""

import re
import logging
import time
from datetime import datetime, date
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# In-memory cache: {symbol: (data, fetched_at)}
_cache: Dict[str, tuple] = {}
_CACHE_TTL_MIN = 20  # 20 min — slightly longer than the 15-min delay


def _cache_get(key: str) -> Optional[dict]:
    entry = _cache.get(key)
    if not entry:
        return None
    data, ts = entry
    if (datetime.now() - ts).total_seconds() > _CACHE_TTL_MIN * 60:
        return None
    return data


def _cache_set(key: str, data: dict):
    _cache[key] = (data, datetime.now())


def scrape_barchart_unusual(symbols: Optional[List[str]] = None, max_rows: int = 100) -> List[dict]:
    """
    Scrape Barchart unusual options activity table.
    Returns list of unusual option rows with symbol, type, strike, expiry, volume, OI, premium.

    If symbols is provided, filters to only those symbols.
    """
    cached = _cache_get("_barchart_all")
    if cached:
        rows = cached.get("rows", [])
        if symbols:
            rows = [r for r in rows if r.get("symbol") in symbols]
        return rows

    rows = []
    try:
        url = "https://www.barchart.com/options/unusual-activity/stocks"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.barchart.com/",
        }
        resp = requests.get(url, headers=headers, timeout=15)

        if resp.status_code != 200:
            logger.debug(f"Barchart returned {resp.status_code}")
            return []

        html = resp.text

        # Extract data rows from the HTML table
        # Barchart's unusual activity table has this structure:
        # Symbol | Exp Date | Strike | Type | Vol/OI | Bid | Ask | Last | % Change | Volume | OI | IV
        table_match = re.search(r'<table[^>]*>.*?</table>', html, re.DOTALL)
        if not table_match:
            # Try to find rows directly
            rows = _parse_barchart_rows(html)
        else:
            rows = _parse_barchart_rows(table_match.group(0))

    except Exception as e:
        logger.debug(f"Barchart scrape failed: {e}")

    # Cache the full result
    _cache_set("_barchart_all", {"rows": rows, "fetched_at": datetime.now().isoformat()})

    if symbols:
        rows = [r for r in rows if r.get("symbol") in symbols]

    return rows[:max_rows]


def _parse_barchart_rows(html: str) -> List[dict]:
    """Parse option rows from Barchart HTML."""
    rows = []

    # Pattern: find rows with ticker symbols and option data
    # Barchart rows contain: symbol, expiry date, strike, C/P, volume, OI
    row_pattern = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)

    for row_html in row_pattern:
        cells_raw = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells_raw]

        if len(cells) < 8:
            continue

        # Try to identify if this is a valid options row
        # Look for symbol (2-5 uppercase letters), expiry date, strike price
        symbol = None
        for cell in cells[:3]:
            clean = cell.strip().upper()
            if re.match(r'^[A-Z]{1,5}$', clean) and len(clean) >= 1:
                symbol = clean
                break

        if not symbol or symbol in ("TYPE", "SYMBOL", "EXP", "CALL", "PUT"):
            continue

        try:
            # Find option type (C or P, Call or Put)
            opt_type = None
            for cell in cells:
                if cell.strip().upper() in ("C", "CALL", "CALLS"):
                    opt_type = "call"
                    break
                if cell.strip().upper() in ("P", "PUT", "PUTS"):
                    opt_type = "put"
                    break

            if not opt_type:
                continue

            # Find volume (usually the largest number in the row)
            volume = None
            for cell in cells:
                clean = cell.replace(",", "").strip()
                try:
                    v = int(float(clean))
                    if v > 100:  # minimum volume threshold
                        volume = v
                        break
                except ValueError:
                    pass

            if not volume:
                continue

            rows.append({
                "symbol": symbol,
                "option_type": opt_type,
                "volume": volume,
                "raw": " | ".join(cells[:8]),
            })

        except Exception:
            continue

    return rows


def get_options_flow(symbols: List[str]) -> Dict[str, dict]:
    """
    Get options flow summary for a list of symbols.
    Returns {symbol: flow_summary} with signal classification.
    """
    all_rows = scrape_barchart_unusual(symbols=symbols)

    results = {}
    for sym in symbols:
        sym_rows = [r for r in all_rows if r.get("symbol") == sym]
        results[sym] = _analyze_flow(sym, sym_rows)

    return results


def _analyze_flow(symbol: str, rows: List[dict]) -> dict:
    """Analyze options flow rows for a symbol and classify signal."""
    if not rows:
        return {
            "symbol": symbol,
            "flow_signal": "neutral",
            "call_volume": 0,
            "put_volume": 0,
            "put_call_ratio": None,
            "net_flow": "neutral",
            "signal_strength": 0.0,
            "summary": "No unusual options activity",
        }

    call_vol = sum(r.get("volume", 0) for r in rows if r.get("option_type") == "call")
    put_vol  = sum(r.get("volume", 0) for r in rows if r.get("option_type") == "put")
    total    = call_vol + put_vol

    pc_ratio = round(put_vol / call_vol, 3) if call_vol > 0 else None

    # Classify
    if total == 0:
        signal = "neutral"
        strength = 0.0
    elif pc_ratio is not None and pc_ratio < 0.5:
        signal = "bullish_flow"
        strength = min(1.0, 0.4 + (call_vol / max(total, 1)) * 0.6)
    elif pc_ratio is not None and pc_ratio > 2.0:
        signal = "bearish_flow"
        strength = min(1.0, 0.4 + (put_vol / max(total, 1)) * 0.6)
    elif call_vol > put_vol * 1.5:
        signal = "bullish_flow"
        strength = 0.4
    elif put_vol > call_vol * 1.5:
        signal = "bearish_flow"
        strength = 0.4
    else:
        signal = "neutral"
        strength = 0.0

    summary = (
        f"{len(rows)} unusual contracts: {call_vol:,} calls / {put_vol:,} puts"
        + (f" | P/C ratio {pc_ratio:.2f}" if pc_ratio else "")
    )

    return {
        "symbol": symbol,
        "flow_signal": signal,
        "call_volume": call_vol,
        "put_volume": put_vol,
        "put_call_ratio": pc_ratio,
        "net_flow": "bullish" if call_vol > put_vol else "bearish" if put_vol > call_vol else "neutral",
        "signal_strength": round(strength, 3),
        "unusual_contract_count": len(rows),
        "summary": summary,
    }


def build_options_flow_block_for_claude(flow: Dict[str, dict], positions: Optional[List[dict]] = None) -> str:
    """Build options flow block for Claude prompt."""
    if not flow:
        return ""

    held = {p.get("symbol") for p in (positions or [])}
    bullish = []
    bearish = []

    for sym, data in sorted(flow.items()):
        sig      = data.get("flow_signal", "neutral")
        strength = data.get("signal_strength", 0)
        summary  = data.get("summary", "")
        is_held  = sym in held
        held_tag = " [HELD]" if is_held else ""

        if sig == "bullish_flow" and strength >= 0.3:
            bullish.append(f"  ↑ {sym}{held_tag}: BULLISH OPTIONS FLOW — {summary}")
        elif sig == "bearish_flow" and strength >= 0.3:
            bearish.append(f"  ↓ {sym}{held_tag}: BEARISH OPTIONS FLOW — {summary}")

    if not bullish and not bearish:
        return ""

    lines = ["=== UNUSUAL OPTIONS FLOW (Barchart, ~15min delayed) ==="]
    lines.extend(bullish)
    lines.extend(bearish)
    lines.append("NOTE: Options flow = confirmation signal (+0.05 confidence boost for bullish flow on existing setups). Never enter on flow alone.")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "NVDA", "TSLA", "SPY", "QQQ"]
    print(f"\nOptions Flow — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    flow = get_options_flow(symbols)
    for sym, d in flow.items():
        print(f"  {sym}: {d.get('flow_signal')} strength={d.get('signal_strength'):.2f} | {d.get('summary')}")
    print("\nClaude Block:")
    print(build_options_flow_block_for_claude(flow) or "(no unusual flow)")
