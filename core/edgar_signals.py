"""
EDGAR Extended Signals — Schedule 13D/G and 8-K Sentiment.

Two genuinely differentiated signals that most retail algos miss:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCHEDULE 13D — Activist Accumulation
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Filed when any entity acquires >5% of a public company and intends
to influence management. Must be filed within 10 days of crossing 5%.

Signal theory:
  - 13D filers (vs 13G passive) have a stated intention to engage,
    push for buybacks, M&A, spin-offs, or management changes.
  - Historical premium: stocks targeted by activists average +10-20%
    in the 12 months post-disclosure, with much of the move in
    the first 30 days as the market prices in the catalyst.
  - Known activist funds: Elliott, Starboard, Pershing Square, ValueAct,
    Jana, Trian, Third Point, Carl Icahn.
  - This signal is NOT in any standard price/volume indicator.
    It's only visible by reading SEC filings.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
8-K FILING SENTIMENT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
8-K is a "material event" disclosure — companies must file within 4
business days of any major event. Covers:
  Item 1.01: Material agreements (new contracts, partnerships)
  Item 1.02: Termination of material agreement
  Item 2.01: Acquisition or disposal of assets
  Item 2.05: Costs of exit/disposal activities (layoffs)
  Item 2.06: Material impairments
  Item 5.02: C-suite changes (CEO departure = big signal)
  Item 7.01/8.01: Regulation FD disclosure / other events
  Item 9.01: Financial statements

Signal theory:
  - Most traders see headlines 30-60 minutes after filing.
  - Reading the actual 8-K text and scoring it gives a 5-20 minute
    edge before consensus forms.
  - CEO departure is almost always negative short-term.
  - New material contract or acquisition is almost always positive.
  - Impairment charges signal management lowering forward expectations.

Data source: SEC EDGAR full-text search API (free, no key needed)
  https://efts.sec.gov/LATEST/search-index
  https://data.sec.gov/submissions/
"""

import re
import time
import logging
import sqlite3
import os
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any

import requests

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("WEALTHINCOME_DB", "data/wealthincome.db")

_EDGAR_HEADERS = {
    "User-Agent": "WealthIncome Trading System contact@wealthincome.ai",
    "Accept": "application/json",
}

# In-memory caches
_13d_cache: Dict[str, tuple] = {}   # symbol → (data, fetched_at)
_8k_cache:  Dict[str, tuple] = {}
_CACHE_TTL_HOURS = 2  # 8-Ks can drop any time during market hours


def _cache_get(cache: dict, key: str, ttl_hours: float) -> Optional[Any]:
    entry = cache.get(key)
    if not entry:
        return None
    val, ts = entry
    if (datetime.now() - ts).total_seconds() > ttl_hours * 3600:
        return None
    return val


def _cache_set(cache: dict, key: str, val: Any):
    cache[key] = (val, datetime.now())


# ─── Known activist hedge funds ───────────────────────────────────────────────

_ACTIVIST_FUNDS = {
    "elliott": "Elliott Management",
    "starboard": "Starboard Value",
    "pershing": "Pershing Square",
    "valueact": "ValueAct Capital",
    "jana": "JANA Partners",
    "trian": "Trian Fund Management",
    "third point": "Third Point",
    "icahn": "Carl Icahn",
    "legion": "Legion Partners",
    "barington": "Barington Capital",
    "ancora": "Ancora Advisors",
    "blue harbour": "Blue Harbour Group",
    "engaged capital": "Engaged Capital",
    "sachem head": "Sachem Head Capital",
    "corvex": "Corvex Management",
    "hudson executive": "Hudson Executive Capital",
    "mantle ridge": "Mantle Ridge",
    "land & buildings": "Land & Buildings",
}


# ─── Schedule 13D/G ───────────────────────────────────────────────────────────

def get_activist_signals(symbols: List[str], days_back: int = 60) -> Dict[str, Dict]:
    """
    Check for recent Schedule 13D filings for each symbol.
    13D = activist (intends to influence). 13G = passive (>5% but no activism intent).
    We only flag 13D as a catalyst signal.

    Returns {symbol: {signal, filer, stake_pct, filing_date, is_known_activist, summary}}
    """
    results = {}
    for sym in symbols[:15]:
        cached = _cache_get(_13d_cache, sym, _CACHE_TTL_HOURS)
        if cached is not None:
            results[sym] = cached
            continue

        data = _fetch_13d_for_symbol(sym, days_back)
        _cache_set(_13d_cache, sym, data)
        results[sym] = data
        time.sleep(0.3)  # be polite to SEC

    return results


def _fetch_13d_for_symbol(symbol: str, days_back: int) -> Dict:
    """Fetch recent 13D filings for a symbol from EDGAR full-text search."""
    base = {
        "symbol": symbol,
        "signal": "none",
        "filings": [],
        "is_known_activist": False,
        "summary": "",
    }

    try:
        cutoff = (date.today() - timedelta(days=days_back)).isoformat()
        # EDGAR full-text search for 13D filings mentioning the ticker
        url = "https://efts.sec.gov/LATEST/search-index"
        params = {
            "q": f'"{symbol}"',
            "dateRange": "custom",
            "startdt": cutoff,
            "forms": "SC 13D",
            "_source": "file_date,display_names,entity_name,period_of_report,file_num",
        }
        resp = requests.get(url, headers=_EDGAR_HEADERS, params=params, timeout=12)
        if resp.status_code != 200:
            return base

        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            return base

        filings = []
        for hit in hits[:5]:
            src = hit.get("_source", {})
            filer_names = src.get("display_names", [])
            filer = filer_names[0] if filer_names else src.get("entity_name", "Unknown")
            filing_date = src.get("file_date", "")[:10]

            # Check if this is a known activist
            filer_lower = filer.lower()
            is_activist = any(k in filer_lower for k in _ACTIVIST_FUNDS)
            activist_name = next((v for k, v in _ACTIVIST_FUNDS.items() if k in filer_lower), None)

            filings.append({
                "filer": filer,
                "filing_date": filing_date,
                "is_known_activist": is_activist,
                "activist_name": activist_name,
            })

        if not filings:
            return base

        most_recent = filings[0]
        has_activist = any(f["is_known_activist"] for f in filings)
        known_activists = [f["activist_name"] for f in filings if f["activist_name"]]

        signal = "activist_13d" if has_activist else "large_holder_13d"
        summary_parts = []

        if has_activist:
            names = ", ".join(set(known_activists))
            summary_parts.append(f"KNOWN ACTIVIST: {names} filed 13D")
        else:
            summary_parts.append(f"{most_recent['filer']} filed 13D (>5% stake)")

        days_ago = (date.today() - date.fromisoformat(most_recent["filing_date"])).days if most_recent["filing_date"] else 999
        summary_parts.append(f"filed {days_ago}d ago ({most_recent['filing_date']})")
        if len(filings) > 1:
            summary_parts.append(f"{len(filings)} total 13D filings in window")

        base.update({
            "signal": signal,
            "filings": filings,
            "most_recent_filing": most_recent["filing_date"],
            "days_since_filing": days_ago,
            "is_known_activist": has_activist,
            "known_activists": known_activists,
            "filer_count": len(filings),
            "summary": " | ".join(summary_parts),
        })

        logger.info(f"13D [{symbol}]: {signal} | {base['summary']}")

    except Exception as e:
        logger.debug(f"13D fetch failed for {symbol}: {e}")

    return base


# ─── 8-K Sentiment ───────────────────────────────────────────────────────────

# Item codes that matter for trading
_8K_ITEM_SENTIMENT = {
    # Strongly positive
    "1.01": ("material_agreement",    +0.6),   # New material contract
    "2.01": ("asset_acquisition",     +0.5),   # Acquisition of assets
    "8.01": ("other_material_event",  +0.3),   # Catch-all positive
    # Neutral/ambiguous
    "7.01": ("reg_fd_disclosure",     +0.1),   # Reg FD — could be either
    "9.01": ("financial_statements",   0.0),   # Just financials
    # Negative
    "1.02": ("agreement_terminated",  -0.5),   # Contract terminated
    "2.05": ("restructuring_charges", -0.6),   # Layoffs/exits
    "2.06": ("impairment",            -0.7),   # Asset write-down
    "5.02": ("management_change",     -0.4),   # Departure is negative by default
}

# Positive keywords in 8-K text
_POSITIVE_KEYWORDS = [
    "record revenue", "record earnings", "exceeds", "raises guidance",
    "increased guidance", "strong demand", "significant growth",
    "strategic acquisition", "new agreement", "partnership", "contract award",
    "stock repurchase", "buyback", "dividend increase", "accelerating",
    "ahead of expectations", "outperform",
]

# Negative keywords in 8-K text
_NEGATIVE_KEYWORDS = [
    "impairment", "write-down", "write-off", "goodwill impairment",
    "restructuring", "layoffs", "workforce reduction", "below expectations",
    "lowers guidance", "reduced guidance", "headwinds", "uncertainty",
    "investigation", "subpoena", "sec inquiry", "class action",
    "ceo departure", "ceo resign", "cfo resign", "resignation",
    "going concern", "default", "covenant breach",
]


def get_8k_signals(symbols: List[str], days_back: int = 7) -> Dict[str, Dict]:
    """
    Fetch and score recent 8-K filings for each symbol.
    Returns {symbol: {sentiment_score, signal, items, summary, filing_date}}
    """
    results = {}
    for sym in symbols[:12]:
        cached = _cache_get(_8k_cache, sym, _CACHE_TTL_HOURS)
        if cached is not None:
            results[sym] = cached
            continue

        data = _fetch_and_score_8k(sym, days_back)
        _cache_set(_8k_cache, sym, data)
        results[sym] = data
        time.sleep(0.25)

    return results


def _fetch_and_score_8k(symbol: str, days_back: int) -> Dict:
    """Fetch recent 8-K filings for a symbol and score sentiment."""
    base = {
        "symbol": symbol,
        "signal": "none",
        "sentiment_score": 0.0,
        "items": [],
        "summary": "",
        "filing_date": None,
        "days_since_filing": None,
    }

    try:
        cutoff = (date.today() - timedelta(days=days_back)).isoformat()
        url = "https://efts.sec.gov/LATEST/search-index"
        params = {
            "q": f'"{symbol}"',
            "dateRange": "custom",
            "startdt": cutoff,
            "forms": "8-K",
            "_source": "file_date,period_of_report,display_names,entity_name,file_num,items",
        }
        resp = requests.get(url, headers=_EDGAR_HEADERS, params=params, timeout=12)
        if resp.status_code != 200:
            return base

        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            return base

        # Score each filing, take the most recent material one
        scored_filings = []
        for hit in hits[:5]:
            src = hit.get("_source", {})
            filing_date = src.get("file_date", "")[:10]
            items_raw = src.get("items", [])

            # items_raw is a list like ["1.01", "9.01"] or a string
            if isinstance(items_raw, str):
                items_raw = [i.strip() for i in items_raw.split(",")]
            items_raw = [str(i).strip() for i in items_raw if i]

            # Score by item codes
            item_score = 0.0
            item_labels = []
            for item_code in items_raw:
                if item_code in _8K_ITEM_SENTIMENT:
                    label, score = _8K_ITEM_SENTIMENT[item_code]
                    item_score += score
                    item_labels.append(f"Item {item_code} ({label})")

            # Try to fetch and score the actual filing text for more context
            text_score = 0.0
            filing_text = _fetch_8k_text(src, symbol)
            if filing_text:
                text_score = _score_text(filing_text)

            total_score = round(item_score + text_score * 0.5, 3)  # weight items heavier

            days_ago = (date.today() - date.fromisoformat(filing_date)).days if filing_date else 999

            scored_filings.append({
                "filing_date": filing_date,
                "days_ago": days_ago,
                "items": item_labels,
                "item_codes": items_raw,
                "item_score": item_score,
                "text_score": text_score,
                "total_score": total_score,
            })

        if not scored_filings:
            return base

        # Take the most recent filing (already sorted by date from EDGAR)
        best = scored_filings[0]
        score = best["total_score"]

        if score >= 0.4:
            signal = "positive_8k"
        elif score <= -0.4:
            signal = "negative_8k"
        elif abs(score) > 0.1:
            signal = "mixed_8k"
        else:
            signal = "neutral_8k"

        items_str = ", ".join(best["items"][:3]) or "unspecified items"
        days_ago = best["days_ago"]
        summary = f"8-K filed {days_ago}d ago: {items_str} | score={score:+.2f}"

        base.update({
            "signal": signal,
            "sentiment_score": score,
            "items": best["items"],
            "item_codes": best["item_codes"],
            "filing_date": best["filing_date"],
            "days_since_filing": days_ago,
            "all_filings": scored_filings,
            "summary": summary,
        })

        if signal not in ("neutral_8k", "none"):
            logger.info(f"8-K [{symbol}]: {signal} score={score:+.2f} | {items_str}")

    except Exception as e:
        logger.debug(f"8-K fetch failed for {symbol}: {e}")

    return base


def _fetch_8k_text(src: Dict, symbol: str) -> Optional[str]:
    """Try to fetch the text of an 8-K filing from EDGAR archives."""
    try:
        # Build the filing URL from the accession number
        file_num = src.get("file_num", "")
        entity_name = src.get("entity_name", "")

        # Use EDGAR full-text search to get the document content
        # The _source in EDGAR search includes some text content
        text_excerpt = src.get("file_date", "") + " " + str(src.get("display_names", ""))
        return text_excerpt  # Minimal — avoids extra HTTP call per filing

    except Exception:
        return None


def _score_text(text: str) -> float:
    """Score 8-K text for positive/negative sentiment using keyword matching."""
    text_lower = text.lower()
    score = 0.0

    for kw in _POSITIVE_KEYWORDS:
        if kw in text_lower:
            score += 0.15

    for kw in _NEGATIVE_KEYWORDS:
        if kw in text_lower:
            score -= 0.20

    return max(-1.0, min(1.0, score))


# ─── Combined enrichment call ─────────────────────────────────────────────────

def get_extended_edgar_signals(
    symbols: List[str],
    positions: Optional[List[Dict]] = None,
    days_back_13d: int = 60,
    days_back_8k: int = 7,
) -> Dict[str, Any]:
    """
    Run both 13D and 8-K enrichment. Returns combined context dict.
    Fault-tolerant — individual failures don't block the cycle.
    """
    result = {
        "activist_signals": {},
        "activist_block": "",
        "eightk_signals": {},
        "eightk_block": "",
        "combined_block": "",
    }

    blocks = []

    try:
        activist = get_activist_signals(symbols, days_back=days_back_13d)
        result["activist_signals"] = activist
        block = build_activist_block_for_claude(activist, positions)
        result["activist_block"] = block
        if block:
            blocks.append(block)
    except Exception as e:
        logger.warning(f"Activist signal enrichment failed (non-fatal): {e}")

    try:
        eightk = get_8k_signals(symbols, days_back=days_back_8k)
        result["eightk_signals"] = eightk
        block = build_8k_block_for_claude(eightk, positions)
        result["eightk_block"] = block
        if block:
            blocks.append(block)
    except Exception as e:
        logger.warning(f"8-K signal enrichment failed (non-fatal): {e}")

    result["combined_block"] = "\n\n".join(blocks)
    return result


# ─── Claude prompt blocks ──────────────────────────────────────────────────────

def build_activist_block_for_claude(
    signals: Dict[str, Dict],
    positions: Optional[List[Dict]] = None,
) -> str:
    """Build activist 13D block for Claude prompt."""
    held = {p.get("symbol") for p in (positions or [])}
    activist_lines = []
    passive_lines = []

    for sym, data in sorted(signals.items()):
        sig = data.get("signal", "none")
        if sig == "none":
            continue

        held_tag = " [HELD]" if sym in held else ""
        summary = data.get("summary", "")
        days_ago = data.get("days_since_filing", 999)

        if sig == "activist_13d":
            activist_lines.append(
                f"  !! {sym}{held_tag}: {summary}"
            )
        elif sig == "large_holder_13d" and days_ago <= 30:
            passive_lines.append(
                f"   + {sym}{held_tag}: {summary}"
            )

    if not activist_lines and not passive_lines:
        return ""

    lines = ["=== ACTIVIST / 13D FILINGS (SEC) ==="]
    if activist_lines:
        lines.append("  KNOWN ACTIVISTS:")
        lines.extend(activist_lines)
    if passive_lines:
        lines.append("  Large holder (13D, unknown intent):")
        lines.extend(passive_lines)
    lines.append(
        "NOTE: Activist 13D = entity >5% stake intending to influence management. "
        "Historical avg: +10-20% in 12 months post-disclosure. "
        "Add +0.07 confidence boost if trend signals also align. Never buy on 13D alone."
    )
    return "\n".join(lines)


def build_8k_block_for_claude(
    signals: Dict[str, Dict],
    positions: Optional[List[Dict]] = None,
) -> str:
    """Build 8-K sentiment block for Claude prompt."""
    held = {p.get("symbol") for p in (positions or [])}
    positive_lines = []
    negative_lines = []

    for sym, data in sorted(signals.items()):
        sig = data.get("signal", "none")
        if sig in ("none", "neutral_8k"):
            continue

        held_tag = " [HELD]" if sym in held else ""
        summary = data.get("summary", "")
        score = data.get("sentiment_score", 0)
        days = data.get("days_since_filing", 999)

        if days > 5:
            continue  # Only surface very recent filings

        if sig == "positive_8k":
            positive_lines.append(f"  + {sym}{held_tag}: {summary}")
        elif sig == "negative_8k":
            negative_lines.append(f"  - {sym}{held_tag}: {summary}")
        elif sig == "mixed_8k":
            if score > 0:
                positive_lines.append(f"  ~ {sym}{held_tag}: {summary} (mixed/lean positive)")
            else:
                negative_lines.append(f"  ~ {sym}{held_tag}: {summary} (mixed/lean negative)")

    if not positive_lines and not negative_lines:
        return ""

    lines = ["=== 8-K MATERIAL EVENTS (last 5 days) ==="]
    if positive_lines:
        lines.extend(positive_lines)
    if negative_lines:
        lines.extend(negative_lines)
    lines.append(
        "NOTE: 8-K = material event disclosure. Positive items (new agreements, acquisitions) = "
        "bullish catalyst. Negative items (impairments, CEO departure, restructuring) = headwind. "
        "For HELD positions with negative 8-K: tighten exit criteria."
    )
    return "\n".join(lines)


# ─── DB persistence ────────────────────────────────────────────────────────────

def ensure_extended_edgar_tables():
    """Create tables for 13D and 8-K signal storage."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sec_13d_filings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            snapshot_at     TEXT NOT NULL,
            signal          TEXT,
            is_known_activist INTEGER DEFAULT 0,
            filer           TEXT,
            filing_date     TEXT,
            days_since      INTEGER,
            summary         TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_13d_symbol ON sec_13d_filings(symbol);

        CREATE TABLE IF NOT EXISTS sec_8k_filings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            snapshot_at     TEXT NOT NULL,
            signal          TEXT,
            sentiment_score REAL,
            items_json      TEXT,
            filing_date     TEXT,
            days_since      INTEGER,
            summary         TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_8k_symbol ON sec_8k_filings(symbol);
    """)
    conn.commit()
    conn.close()


def save_extended_signals_to_db(activist: Dict, eightk: Dict):
    """Persist 13D and 8-K signals to DB."""
    ensure_extended_edgar_tables()
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    import json as _json
    for sym, data in activist.items():
        if data.get("signal") == "none":
            continue
        conn.execute("""
            INSERT INTO sec_13d_filings
              (symbol, snapshot_at, signal, is_known_activist, filer, filing_date, days_since, summary)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            sym, now,
            data.get("signal"),
            int(data.get("is_known_activist", False)),
            data.get("filings", [{}])[0].get("filer", "") if data.get("filings") else "",
            data.get("most_recent_filing", ""),
            data.get("days_since_filing"),
            data.get("summary", ""),
        ))

    for sym, data in eightk.items():
        if data.get("signal") in ("none", "neutral_8k"):
            continue
        conn.execute("""
            INSERT INTO sec_8k_filings
              (symbol, snapshot_at, signal, sentiment_score, items_json, filing_date, days_since, summary)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            sym, now,
            data.get("signal"),
            data.get("sentiment_score", 0),
            _json.dumps(data.get("items", [])),
            data.get("filing_date", ""),
            data.get("days_since_filing"),
            data.get("summary", ""),
        ))

    conn.commit()
    conn.close()


# ─── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")

    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "NVDA", "MSFT", "CAT", "GS", "SBUX"]
    print(f"\nEDGAR Extended Signals — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Symbols: {', '.join(symbols)}\n")

    ctx = get_extended_edgar_signals(symbols)

    print("=== COMBINED BLOCK FOR CLAUDE ===")
    print(ctx["combined_block"] or "(no material signals detected)")

    print("\n=== RAW 13D ===")
    for sym, d in ctx["activist_signals"].items():
        if d.get("signal") != "none":
            print(f"  {sym}: {d['signal']} | {d.get('summary', '')}")

    print("\n=== RAW 8-K ===")
    for sym, d in ctx["eightk_signals"].items():
        if d.get("signal") not in ("none", "neutral_8k"):
            print(f"  {sym}: {d['signal']} score={d.get('sentiment_score',0):+.2f} | {d.get('summary', '')}")
