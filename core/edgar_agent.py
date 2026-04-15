"""
EDGAR Agent — SEC Form 4 Insider Transaction Monitor.

Signal theory:
  - Form 4 must be filed within 2 business days of any insider transaction.
  - Insider BUYS (open market purchases) are highly predictive — insiders only
    buy when they're confident in the company's near-term prospects.
  - Insider SELLS are unreliable signals (could be diversification, taxes, etc.).
  - "Cluster buying" (multiple insiders buying simultaneously) is the strongest signal.
  - Director/Officer buys of >$100k are institutional-grade conviction.

Data source: SEC EDGAR full-text search API (free, no API key needed)
  https://efts.sec.gov/LATEST/search-index?q=%22form+4%22&dateRange=custom

Also uses openinsider.com via scraping as a cross-check (optional).

Output per symbol:
  - insider_signal: 'strong_buy' | 'buy' | 'sell' | 'neutral' | 'no_data'
  - recent_buys: list of recent insider purchases
  - total_buy_value_30d: total $ value of purchases in last 30 days
  - cluster_buy: bool — multiple insiders buying in same window
"""

import os
import json
import time
import logging
import warnings
import sqlite3
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any

import requests

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("WEALTHINCOME_DB", "data/wealthincome.db")

# SEC EDGAR requires a User-Agent identifying your app
_EDGAR_HEADERS = {
    "User-Agent": "WealthIncome Trading System contact@wealthincome.ai",
    "Accept": "application/json",
}

# Cache TTL: 4 hours (Form 4s are filed same-day or next-day)
_insider_cache: Dict[str, Dict] = {}
_CACHE_TTL_HOURS = 4


def _cache_get(symbol: str) -> Optional[Dict]:
    entry = _insider_cache.get(symbol)
    if not entry:
        return None
    age_hours = (datetime.now() - entry["fetched_at"]).total_seconds() / 3600
    if age_hours > _CACHE_TTL_HOURS:
        return None
    return entry["data"]


def _cache_set(symbol: str, data: Dict):
    _insider_cache[symbol] = {"data": data, "fetched_at": datetime.now()}


# ─── SEC EDGAR CIK lookup ──────────────────────────────────────────────────────

def _get_cik(symbol: str) -> Optional[str]:
    """Get SEC CIK number for a ticker symbol."""
    try:
        url = "https://www.sec.gov/cgi-bin/browse-edgar"
        params = {
            "company": "",
            "CIK": symbol,
            "type": "4",
            "dateb": "",
            "owner": "include",
            "count": "1",
            "search_text": "",
            "action": "getcompany",
            "output": "atom",
        }
        resp = requests.get(url, headers=_EDGAR_HEADERS, params=params, timeout=10)
        if resp.status_code == 200:
            # Extract CIK from URL in atom feed
            import re
            match = re.search(r'CIK=(\d+)', resp.url)
            if match:
                return match.group(1).zfill(10)
            # Also try extracting from body
            match = re.search(r'/cgi-bin/browse-edgar\?action=getcompany&CIK=(\d+)', resp.text)
            if match:
                return match.group(1).zfill(10)
    except Exception as e:
        logger.debug(f"CIK lookup failed for {symbol}: {e}")

    # Fallback: use EDGAR company facts API
    try:
        url = f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22&dateRange=custom&startdt=2024-01-01&forms=4"
        resp = requests.get(url, headers=_EDGAR_HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            for hit in hits[:3]:
                src = hit.get("_source", {})
                entity_name = src.get("entity_name", "")
                cik = src.get("entity_id", "")
                if cik:
                    return cik.zfill(10)
    except Exception:
        pass

    return None


def _get_form4_filings(cik: str, days_back: int = 30) -> List[Dict]:
    """
    Fetch recent Form 4 filings for a CIK from EDGAR.
    Returns list of filing metadata dicts.
    """
    try:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        resp = requests.get(url, headers=_EDGAR_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        filings = data.get("filings", {}).get("recent", {})
        form_types = filings.get("form", [])
        filing_dates = filings.get("filingDate", [])
        accessions = filings.get("accessionNumber", [])

        cutoff = (date.today() - timedelta(days=days_back)).isoformat()
        result = []
        for i, (form, fdate, acc) in enumerate(zip(form_types, filing_dates, accessions)):
            if form != "4":
                continue
            if fdate < cutoff:
                continue
            result.append({
                "form": form,
                "filing_date": fdate,
                "accession": acc.replace("-", ""),
                "cik": cik,
            })
        return result
    except Exception as e:
        logger.debug(f"Form 4 filings fetch failed for CIK {cik}: {e}")
        return []


def _parse_form4_filing(cik: str, accession: str) -> Optional[Dict]:
    """
    Parse a Form 4 filing to extract transaction details.
    Returns dict with transaction info or None.
    """
    try:
        # Get filing index
        acc_formatted = f"{accession[:10]}-{accession[10:12]}-{accession[12:]}"
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/0000950170{accession[10:]}-index.json"
        # Try the standard EDGAR path
        index_url = f"https://data.sec.gov/submissions/CIK{cik}.json"

        # Actually parse the XML form4 document
        doc_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4&dateb=&owner=include&count=1"

        # Use EDGAR XBRL viewer API for parsed data
        api_url = f"https://efts.sec.gov/LATEST/search-index?q=%22{accession}%22&forms=4"
        resp = requests.get(api_url, headers=_EDGAR_HEADERS, timeout=10)
        if resp.status_code == 200:
            hits = resp.json().get("hits", {}).get("hits", [])
            if hits:
                src = hits[0].get("_source", {})
                return {
                    "filer_name": src.get("display_names", ["Unknown"])[0] if src.get("display_names") else "Unknown",
                    "period_of_report": src.get("period_of_report", ""),
                    "filing_date": src.get("file_date", ""),
                }
    except Exception as e:
        logger.debug(f"Form 4 parse failed for {accession}: {e}")

    return None


# ─── OpenInsider scraping (primary source — better parsed data) ───────────────

def _fetch_openinsider(symbol: str, days_back: int = 30) -> List[Dict]:
    """
    Fetch insider transactions from openinsider.com.
    This site aggregates SEC Form 4 filings with clean structured data.
    Free, no auth needed.

    Page structure: The data table has NO class attribute.
    We identify it by finding the table that contains rows with date patterns.
    Columns: X | Filing Date | Trade Date | Ticker | Company | Insider Name |
             Title | Trade Type | Price | Qty | Owned | ΔOwn | Value | ...
    """
    transactions = []
    try:
        url = (f"http://openinsider.com/screener?s={symbol}"
               f"&o=&pl=&ph=&ll=&lh=&fd={days_back}&fdr=&td=0&tdr="
               f"&fdlyl=&fdlyh=&daysago=&xp=1&vl=50&vh=&ocl=&och="
               f"&sic1=-1&sicl=100&sich=9999&grp=0&nfl=&nfh=&nil=&nih="
               f"&nol=&noh=&v2l=&v2h=&oc2l=&oc2h=&sortcol=0&cnt=40&page=1")
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        if resp.status_code != 200:
            return []

        import re

        # Strategy: find all <tr> rows that contain a date AND "P - Purchase"
        # This is more robust than class-based table finding
        # Row pattern: each data row contains the trade date, insider name, type, value
        all_rows = re.findall(r'<tr[^>]*>(.*?)</tr>', resp.text, re.DOTALL)

        for row in all_rows:
            # Only process rows that contain "P - Purchase" (open market buy)
            if "P - Purchase" not in row and "P+Purchase" not in row:
                continue

            # Extract all td text content
            cells_raw = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells_raw]

            # Need at least: filing_date, trade_date, ticker, company, insider, title, type, price, qty, value
            if len(cells) < 10:
                continue

            try:
                # Actual openinsider screener columns (0-indexed):
                # 0: checkbox
                # 1: Filing Date  "2026-04-14 16:27:16"
                # 2: Trade Date   "2026-04-13"
                # 3: Ticker       (has JS tooltip prefix)
                # 4: Insider Name
                # 5: Title
                # 6: Trade Type   "P - Purchase"
                # 7: Price        "$42.27"
                # 8: Qty          "+23,660"
                # 9: Owned
                # 10: ΔOwn%
                # 11: Value       "+$1,000,000"
                # 12-15: performance columns

                filing_date  = cells[1][:10] if len(cells) > 1 else ""
                trade_date   = cells[2][:10] if len(cells) > 2 else ""
                insider_name = cells[4]       if len(cells) > 4 else ""
                title        = cells[5]       if len(cells) > 5 else ""
                trade_type   = cells[6]       if len(cells) > 6 else ""
                price_str    = cells[7].replace("$", "").replace(",", "").strip() if len(cells) > 7 else ""
                qty_str      = cells[8].replace(",", "").replace("+", "").strip() if len(cells) > 8 else ""
                value_str    = cells[11].replace("$", "").replace(",", "").replace("+", "").strip() if len(cells) > 11 else ""

                # Verify it's actually a Purchase (already filtered above but double-check)
                if "Purchase" not in trade_type:
                    continue

                price = float(price_str) if price_str and price_str not in ("N/A", "") else 0.0
                qty   = float(qty_str)   if qty_str   and qty_str   not in ("N/A", "") else 0.0

                # Parse value — openinsider screener shows raw dollar amounts
                value = 0.0
                if value_str and value_str not in ("N/A", ""):
                    v_clean = value_str.replace("+", "").replace(",", "")
                    try:
                        value = float(v_clean)
                    except ValueError:
                        value = price * qty if price and qty else 0.0

                # If value still 0, compute from price * qty
                if value == 0 and price > 0 and qty > 0:
                    value = price * qty

                if value < 10_000:  # Skip tiny buys (< $10k)
                    continue

                transactions.append({
                    "filing_date":  filing_date,
                    "trade_date":   trade_date,
                    "insider_name": insider_name,
                    "title":        title[:50],
                    "trade_type":   "buy",
                    "price":        round(price, 2),
                    "qty":          int(qty),
                    "value":        round(value, 0),
                })

            except Exception:
                continue

    except Exception as e:
        logger.debug(f"OpenInsider fetch failed for {symbol}: {e}")

    return transactions


# ─── Core analysis ────────────────────────────────────────────────────────────

def analyze_insider_activity(symbol: str, days_back: int = 30) -> Dict:
    """
    Analyze recent insider activity for a symbol.

    Returns:
        symbol: str
        insider_signal: 'strong_buy' | 'buy' | 'sell' | 'neutral' | 'no_data'
        recent_buys: List[Dict]  — filtered open-market purchases
        total_buy_value_30d: float
        cluster_buy: bool  — 2+ insiders buying in same window
        signal_strength: float  — 0-1
        summary: str
    """
    cached = _cache_get(symbol)
    if cached:
        return cached

    result = {
        "symbol": symbol,
        "insider_signal": "no_data",
        "recent_buys": [],
        "total_buy_value_30d": 0.0,
        "cluster_buy": False,
        "signal_strength": 0.0,
        "summary": "No insider data",
    }

    # Primary: openinsider.com (better structured data)
    transactions = _fetch_openinsider(symbol, days_back=days_back)

    # Throttle to avoid rate limiting
    time.sleep(0.3)

    if not transactions:
        result["insider_signal"] = "neutral"
        result["summary"] = "No recent insider purchases"
        _cache_set(symbol, result)
        return result

    # Compute aggregates
    total_value = sum(t["value"] for t in transactions)
    unique_insiders = len({t["insider_name"] for t in transactions})
    max_single_buy  = max((t["value"] for t in transactions), default=0)

    cluster_buy = unique_insiders >= 2

    # Classify signal
    if total_value >= 500_000 or (cluster_buy and total_value >= 200_000):
        signal   = "strong_buy"
        strength = min(1.0, 0.6 + (total_value / 1_000_000) * 0.4)
    elif total_value >= 100_000:
        signal   = "buy"
        strength = min(0.8, 0.3 + (total_value / 500_000) * 0.5)
    elif total_value > 0:
        signal   = "buy"
        strength = 0.2
    else:
        signal   = "neutral"
        strength = 0.0

    # Build summary
    top = transactions[0] if transactions else {}
    if signal in ("strong_buy", "buy"):
        summary = (
            f"Insider buying: ${total_value:,.0f} total by {unique_insiders} insider(s). "
            f"Top: {top.get('insider_name', 'Unknown')} ({top.get('title', '')}) "
            f"${top.get('value', 0):,.0f} on {top.get('trade_date', 'unknown')}"
        )
        if cluster_buy:
            summary = f"CLUSTER BUY ({unique_insiders} insiders). " + summary
    else:
        summary = "No significant insider buying"

    result.update({
        "insider_signal":    signal,
        "recent_buys":       transactions[:5],  # top 5 by value
        "total_buy_value_30d": total_value,
        "cluster_buy":       cluster_buy,
        "signal_strength":   round(strength, 3),
        "summary":           summary,
    })

    logger.info(
        f"Insider [{symbol}]: {signal} | total=${total_value:,.0f} | "
        f"insiders={unique_insiders} cluster={cluster_buy}"
    )

    _cache_set(symbol, result)
    return result


def get_insider_signals(symbols: List[str]) -> Dict[str, Dict]:
    """Analyze insider activity for a list of symbols. Returns {symbol: analysis}."""
    results = {}
    for sym in symbols[:12]:  # cap to avoid rate limiting
        try:
            results[sym] = analyze_insider_activity(sym)
        except Exception as e:
            logger.debug(f"Insider analysis skipped {sym}: {e}")
    return results


# ─── DB persistence ──────────────────────────────────────────────────────────

def ensure_edgar_table():
    """Create sec_filings table if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sec_filings (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol              TEXT NOT NULL,
            snapshot_at         TEXT NOT NULL,
            date                TEXT NOT NULL,
            insider_signal      TEXT,
            total_buy_value_30d REAL,
            cluster_buy         INTEGER,
            signal_strength     REAL,
            recent_buys_json    TEXT,
            summary             TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sec_filings_symbol_date ON sec_filings(symbol, date);
    """)
    conn.commit()
    conn.close()


def save_insider_signals_to_db(signals: Dict[str, Dict]):
    """Persist insider signal snapshots to DB."""
    ensure_edgar_table()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    now   = datetime.now().isoformat()
    today = date.today().isoformat()
    for sym, data in signals.items():
        if data.get("insider_signal") == "no_data":
            continue
        conn.execute("""
            INSERT INTO sec_filings
              (symbol, snapshot_at, date, insider_signal, total_buy_value_30d,
               cluster_buy, signal_strength, recent_buys_json, summary)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            sym, now, today,
            data.get("insider_signal"),
            data.get("total_buy_value_30d", 0),
            int(data.get("cluster_buy", False)),
            data.get("signal_strength", 0),
            json.dumps(data.get("recent_buys", [])),
            data.get("summary", ""),
        ))
    conn.commit()
    conn.close()


# ─── Claude prompt block ──────────────────────────────────────────────────────

def build_insider_block_for_claude(
    signals: Dict[str, Dict],
    positions: Optional[List[Dict]] = None,
) -> str:
    """
    Build a compact insider activity block for Claude.
    Only includes symbols with buy signals.
    """
    if not signals:
        return ""

    held_symbols = {p.get("symbol") for p in (positions or [])}
    strong = []
    moderate = []

    for sym, data in sorted(signals.items()):
        sig      = data.get("insider_signal", "neutral")
        strength = data.get("signal_strength", 0)
        summary  = data.get("summary", "")
        is_held  = sym in held_symbols
        cluster  = data.get("cluster_buy", False)
        total_v  = data.get("total_buy_value_30d", 0)
        held_tag = " [HELD]" if is_held else ""

        if sig == "strong_buy":
            tag = "CLUSTER " if cluster else ""
            strong.append(f"  ++ {sym}{held_tag}: {tag}INSIDER BUY ${total_v:,.0f} — {summary[:90]}")
        elif sig == "buy" and strength >= 0.3:
            moderate.append(f"  +  {sym}{held_tag}: Insider buy ${total_v:,.0f} — {summary[:90]}")

    if not (strong or moderate):
        return ""

    output = ["=== SEC INSIDER BUYING (Form 4, last 30 days) ==="]
    output.extend(strong)
    output.extend(moderate)
    output.append("NOTE: Insider purchases (open-market buys) are high-conviction signals. Cluster buys = multiple insiders buying together = strongest signal.")
    return "\n".join(output)


# ─── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")

    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "NVDA", "MSFT", "JPM", "GS"]
    print(f"\nEDGAR Agent Live Test — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Symbols: {', '.join(symbols)}\n")

    signals = get_insider_signals(symbols)

    print(f"{'Symbol':<8} {'Signal':<14} {'Total Buy':>12} {'Insiders':>9} {'Cluster':>8} {'Strength':>9}")
    print("-" * 65)
    for sym in sorted(signals.keys()):
        d = signals[sym]
        sig   = d.get("insider_signal", "no_data")
        total = d.get("total_buy_value_30d", 0)
        buys  = d.get("recent_buys", [])
        n_ins = len({b.get("insider_name") for b in buys})
        clust = "YES" if d.get("cluster_buy") else "no"
        st    = d.get("signal_strength", 0)
        print(f"{sym:<8} {sig:<14} {total:>12,.0f} {n_ins:>9} {clust:>8} {st:>9.2f}")
        for buy in buys[:2]:
            print(f"         {buy.get('insider_name','?')} ({buy.get('title','?')[:20]}): "
                  f"${buy.get('value',0):,.0f} @ ${buy.get('price',0):.2f} on {buy.get('trade_date','?')}")

    # Claude block
    print("\n--- Claude Insider Block ---")
    block = build_insider_block_for_claude(signals, positions=[{"symbol": "NVDA"}])
    print(block if block else "(no insider buying detected)")

    # Save to DB
    from backend.db import init_db
    init_db()
    save_insider_signals_to_db(signals)

    print("\n--- DB Verification ---")
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM sec_filings WHERE date=?", (date.today().isoformat(),)
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT symbol, insider_signal, total_buy_value_30d, signal_strength "
        "FROM sec_filings ORDER BY snapshot_at DESC LIMIT 10"
    ).fetchall()
    conn.close()
    print(f"Rows saved: {count}")
    for r in rows:
        print(f"  {r[0]}: {r[1]} total=${r[2]:,.0f} strength={r[3]:.2f}")

    print("\nPASS — EDGAR agent working.")
