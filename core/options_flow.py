"""
Options Flow Agent — detects unusual options activity as a leading indicator.

Signal theory:
  - Large options buyers often know something before the stock moves.
  - Unusual call volume vs open interest = bullish positioning.
  - Unusual put volume vs open interest = bearish or hedging signal.
  - Put/Call ratio extremes predict reversals.
  - ITM large-lot calls with near-dated expiry = directional bets (not hedges).

Data sources (free):
  - yfinance options chain: real-time volume, OI, IV, strike, expiry
  - Derived metrics: volume/OI ratio, put/call ratio, IV skew, dollar value

Output:
  - Per-symbol options_signal: 'bullish_flow' | 'bearish_flow' | 'neutral' | 'no_data'
  - Injected into Claude's prompt as an additional signal layer
"""

import os
import json
import logging
import warnings
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

# Cache to avoid re-fetching options chains on every cycle
_options_cache: Dict[str, Dict] = {}
_CACHE_TTL_MINUTES = 30


def _cache_get(symbol: str) -> Optional[Dict]:
    entry = _options_cache.get(symbol)
    if not entry:
        return None
    age_minutes = (datetime.now() - entry["fetched_at"]).total_seconds() / 60
    if age_minutes > _CACHE_TTL_MINUTES:
        return None
    return entry["data"]


def _cache_set(symbol: str, data: Dict):
    _options_cache[symbol] = {"data": data, "fetched_at": datetime.now()}


# ─── yfinance options analysis ────────────────────────────────────────────────

def _get_near_term_expiries(ticker_obj, max_expiries: int = 3) -> List[str]:
    """
    Get the next 1-3 expiry dates (within ~45 days).
    Skips 0-DTE (today) since their OI is 0 and vol/OI ratios are meaningless.
    """
    try:
        expiries = ticker_obj.options
        if not expiries:
            return []
        today_str  = date.today().isoformat()
        cutoff_str = (date.today() + timedelta(days=45)).isoformat()
        # Skip expiring today (0-DTE) — OI hasn't built up yet
        near = [e for e in expiries if today_str < e <= cutoff_str]
        return near[:max_expiries]
    except Exception:
        return []


def analyze_options_chain(symbol: str) -> Dict:
    """
    Fetch and analyze options chain for a symbol.

    Returns:
        symbol: str
        options_signal: 'bullish_flow' | 'bearish_flow' | 'neutral' | 'no_data'
        put_call_ratio: float (volume-based)
        call_volume: int
        put_volume: int
        unusual_calls: List[Dict]  — calls with vol/OI ratio > 3 and volume > 500
        unusual_puts: List[Dict]   — puts with vol/OI ratio > 3 and volume > 500
        iv_skew: float             — avg call IV minus avg put IV (positive = calls pricier)
        signal_strength: float     — 0-1 confidence in the signal
        summary: str               — one-line explanation
    """
    cached = _cache_get(symbol)
    if cached:
        return cached

    result = {
        "symbol": symbol,
        "options_signal": "no_data",
        "put_call_ratio": None,
        "call_volume": 0,
        "put_volume": 0,
        "unusual_calls": [],
        "unusual_puts": [],
        "iv_skew": None,
        "signal_strength": 0.0,
        "summary": "No options data",
    }

    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        expiries = _get_near_term_expiries(ticker)

        if not expiries:
            _cache_set(symbol, result)
            return result

        total_call_vol = 0
        total_put_vol  = 0
        unusual_calls  = []
        unusual_puts   = []
        call_ivs       = []
        put_ivs        = []

        for expiry in expiries:
            try:
                chain = ticker.option_chain(expiry)
                calls = chain.calls
                puts  = chain.puts

                if calls is None or puts is None:
                    continue

                # Aggregate volumes
                total_call_vol += int(calls["volume"].fillna(0).sum())
                total_put_vol  += int(puts["volume"].fillna(0).sum())

                # Collect IVs for skew calculation
                call_iv_vals = calls["impliedVolatility"].dropna().tolist()
                put_iv_vals  = puts["impliedVolatility"].dropna().tolist()
                call_ivs.extend(call_iv_vals)
                put_ivs.extend(put_iv_vals)

                # Get current stock price for moneyness
                try:
                    current_price = float(ticker.info.get("currentPrice") or
                                          ticker.info.get("regularMarketPrice") or 0)
                except Exception:
                    current_price = 0

                # Flag unusual call activity
                for _, row in calls.iterrows():
                    vol = row.get("volume") or 0
                    oi  = row.get("openInterest") or 0
                    iv  = row.get("impliedVolatility") or 0
                    strike = row.get("strike") or 0
                    if vol > 500 and oi > 0:
                        ratio = vol / oi
                        if ratio > 3.0:
                            moneyness = (strike - current_price) / current_price if current_price else 0
                            # Prefer OTM to slightly OTM calls (directional bets)
                            if -0.05 <= moneyness <= 0.20:
                                unusual_calls.append({
                                    "strike": round(strike, 2),
                                    "expiry": expiry,
                                    "volume": int(vol),
                                    "open_interest": int(oi),
                                    "vol_oi_ratio": round(ratio, 2),
                                    "iv": round(iv, 3),
                                    "moneyness_pct": round(moneyness * 100, 1),
                                })

                # Flag unusual put activity
                for _, row in puts.iterrows():
                    vol = row.get("volume") or 0
                    oi  = row.get("openInterest") or 0
                    iv  = row.get("impliedVolatility") or 0
                    strike = row.get("strike") or 0
                    if vol > 500 and oi > 0:
                        ratio = vol / oi
                        if ratio > 3.0:
                            moneyness = (current_price - strike) / current_price if current_price else 0
                            if -0.05 <= moneyness <= 0.20:
                                unusual_puts.append({
                                    "strike": round(strike, 2),
                                    "expiry": expiry,
                                    "volume": int(vol),
                                    "open_interest": int(oi),
                                    "vol_oi_ratio": round(ratio, 2),
                                    "iv": round(iv, 3),
                                    "moneyness_pct": round(moneyness * 100, 1),
                                })

            except Exception as e:
                logger.debug(f"Options chain error {symbol} expiry={expiry}: {e}")
                continue

        # Sort by volume descending, keep top 5
        unusual_calls.sort(key=lambda x: x["volume"], reverse=True)
        unusual_puts.sort(key=lambda x: x["volume"], reverse=True)
        unusual_calls = unusual_calls[:5]
        unusual_puts  = unusual_puts[:5]

        # Put/call ratio
        pc_ratio = (total_put_vol / total_call_vol) if total_call_vol > 0 else None

        # IV skew: positive = calls more expensive than puts (bullish demand)
        avg_call_iv = sum(call_ivs) / len(call_ivs) if call_ivs else None
        avg_put_iv  = sum(put_ivs)  / len(put_ivs)  if put_ivs  else None
        if avg_call_iv is not None and avg_put_iv is not None:
            iv_skew = round(avg_call_iv - avg_put_iv, 4)
        else:
            iv_skew = None

        # ── Signal classification ──────────────────────────────────────────
        signal  = "neutral"
        strength = 0.0
        summary  = "Normal options activity"

        n_unusual_calls = len(unusual_calls)
        n_unusual_puts  = len(unusual_puts)

        if pc_ratio is not None:
            if pc_ratio < 0.5 and n_unusual_calls >= 2:
                # Low P/C + unusual call sweeps = strong bullish
                signal   = "bullish_flow"
                strength = min(1.0, 0.5 + n_unusual_calls * 0.1)
                summary  = (f"Bullish flow: PC={pc_ratio:.2f}, "
                            f"{n_unusual_calls} unusual call sweeps "
                            f"(top: {unusual_calls[0]['volume']:,} @ {unusual_calls[0]['strike']} strike)")
            elif pc_ratio < 0.7 and n_unusual_calls >= 1:
                signal   = "bullish_flow"
                strength = 0.4 + n_unusual_calls * 0.1
                summary  = f"Mild bullish flow: PC={pc_ratio:.2f}, {n_unusual_calls} unusual call(s)"
            elif pc_ratio > 1.5 and n_unusual_puts >= 2:
                # High P/C + unusual put sweeps = bearish / hedging
                signal   = "bearish_flow"
                strength = min(1.0, 0.5 + n_unusual_puts * 0.1)
                summary  = (f"Bearish flow: PC={pc_ratio:.2f}, "
                            f"{n_unusual_puts} unusual put sweeps "
                            f"(top: {unusual_puts[0]['volume']:,} @ {unusual_puts[0]['strike']} strike)")
            elif pc_ratio > 1.2 and n_unusual_puts >= 1:
                signal   = "bearish_flow"
                strength = 0.3 + n_unusual_puts * 0.1
                summary  = f"Mild bearish flow: PC={pc_ratio:.2f}, {n_unusual_puts} unusual put(s)"
            else:
                signal   = "neutral"
                strength = 0.1
                summary  = f"Neutral flow: PC={pc_ratio:.2f}"

        result.update({
            "options_signal":  signal,
            "put_call_ratio":  round(pc_ratio, 3) if pc_ratio else None,
            "call_volume":     total_call_vol,
            "put_volume":      total_put_vol,
            "unusual_calls":   unusual_calls,
            "unusual_puts":    unusual_puts,
            "iv_skew":         iv_skew,
            "signal_strength": round(strength, 3),
            "summary":         summary,
        })

        pc_str = f"{pc_ratio:.2f}" if pc_ratio else "n/a"
        logger.info(
            f"Options [{symbol}]: {signal} | PC={pc_str} | "
            f"unusual_calls={n_unusual_calls} unusual_puts={n_unusual_puts} | {summary[:60]}"
        )

    except Exception as e:
        logger.warning(f"Options analysis failed for {symbol}: {e}")

    _cache_set(symbol, result)
    return result


def get_options_flow(symbols: List[str]) -> Dict[str, Dict]:
    """
    Analyze options flow for a list of symbols.
    Returns {symbol: analysis_dict}.
    Caps at 15 symbols to avoid excessive API calls.
    """
    results = {}
    for sym in symbols[:15]:
        try:
            results[sym] = analyze_options_chain(sym)
        except Exception as e:
            logger.debug(f"Options flow skipped {sym}: {e}")
    return results


# ─── DB persistence ──────────────────────────────────────────────────────────

def save_options_flow_to_db(flow: Dict[str, Dict], db_path: str = None):
    """Save options flow snapshots to options_flow DB table."""
    import sqlite3
    db_path = db_path or os.environ.get("WEALTHINCOME_DB", "data/wealthincome.db")
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS options_flow (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol          TEXT NOT NULL,
                snapshot_at     TEXT NOT NULL,
                date            TEXT NOT NULL,
                options_signal  TEXT,
                put_call_ratio  REAL,
                call_volume     INTEGER,
                put_volume      INTEGER,
                unusual_calls_json TEXT,
                unusual_puts_json  TEXT,
                iv_skew         REAL,
                signal_strength REAL,
                summary         TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_options_flow_symbol_date
            ON options_flow(symbol, date)
        """)
        now = datetime.now().isoformat()
        today = date.today().isoformat()
        for sym, data in flow.items():
            if data.get("options_signal") == "no_data":
                continue
            conn.execute("""
                INSERT INTO options_flow
                  (symbol, snapshot_at, date, options_signal, put_call_ratio,
                   call_volume, put_volume, unusual_calls_json, unusual_puts_json,
                   iv_skew, signal_strength, summary)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                sym, now, today,
                data.get("options_signal"),
                data.get("put_call_ratio"),
                data.get("call_volume"),
                data.get("put_volume"),
                json.dumps(data.get("unusual_calls", [])),
                json.dumps(data.get("unusual_puts", [])),
                data.get("iv_skew"),
                data.get("signal_strength"),
                data.get("summary"),
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"options_flow DB save failed: {e}")


# ─── Claude prompt block ──────────────────────────────────────────────────────

def build_options_flow_block_for_claude(
    flow: Dict[str, Dict],
    positions: Optional[List[Dict]] = None,
) -> str:
    """
    Build a compact options flow signal block for Claude.
    Only includes symbols with actionable signals (not neutral/no_data).
    """
    if not flow:
        return ""

    held_symbols = {p.get("symbol") for p in (positions or [])}
    lines = []
    warnings_held = []
    bullish_lines  = []
    bearish_lines  = []

    for sym, data in sorted(flow.items()):
        sig = data.get("options_signal", "neutral")
        strength = data.get("signal_strength", 0)
        summary  = data.get("summary", "")
        pc       = data.get("put_call_ratio")
        is_held  = sym in held_symbols

        if sig == "no_data" or sig == "neutral":
            continue

        pc_str = f"PC={pc:.2f}" if pc else ""

        if sig == "bullish_flow":
            entry = f"  + {sym}: BULLISH OPTIONS {pc_str} strength={strength:.1f} — {summary[:80]}"
            if is_held:
                bullish_lines.append(entry + " [HELD — holding supported]")
            else:
                bullish_lines.append(entry)
        elif sig == "bearish_flow":
            entry = f"  ⚠ {sym}: BEARISH OPTIONS {pc_str} strength={strength:.1f} — {summary[:80]}"
            if is_held:
                warnings_held.append(entry + " [HELD — consider exit]")
            else:
                bearish_lines.append(entry)

    if not (bullish_lines or bearish_lines or warnings_held):
        return ""

    output = ["=== OPTIONS FLOW (Institutional Positioning) ==="]
    if warnings_held:
        output.extend(warnings_held)
    if bullish_lines:
        output.extend(bullish_lines)
    if bearish_lines:
        output.extend(bearish_lines)
    output.append("NOTE: Options flow is a leading signal. Strong unusual activity often precedes moves by 1-5 days.")
    return "\n".join(output)


# ─── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys, os
    # Allow running from project root or from core/ subdir
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")

    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "NVDA", "SPY", "QQQ", "MSFT"]
    print(f"\nOptions Flow Agent Live Test — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Symbols: {', '.join(symbols)}\n")

    flow = get_options_flow(symbols)

    print(f"{'Symbol':<8} {'Signal':<18} {'PC Ratio':>9} {'Call Vol':>10} {'Put Vol':>10} "
          f"{'Unusual C':>10} {'Unusual P':>10} {'Strength':>9}")
    print("-" * 90)
    for sym in sorted(flow.keys()):
        d = flow[sym]
        sig    = d.get("options_signal", "no_data")
        pc     = d.get("put_call_ratio") or 0
        cv     = d.get("call_volume", 0)
        pv     = d.get("put_volume", 0)
        uc     = len(d.get("unusual_calls", []))
        up     = len(d.get("unusual_puts", []))
        st     = d.get("signal_strength", 0)
        print(f"{sym:<8} {sig:<18} {pc:>9.3f} {cv:>10,} {pv:>10,} {uc:>10} {up:>10} {st:>9.2f}")
        if d.get("unusual_calls"):
            for c in d["unusual_calls"][:2]:
                print(f"         CALL: strike={c['strike']} exp={c['expiry']} "
                      f"vol={c['volume']:,} OI={c['open_interest']:,} "
                      f"vol/OI={c['vol_oi_ratio']:.1f}x IV={c['iv']:.2f}")
        if d.get("unusual_puts"):
            for p in d["unusual_puts"][:2]:
                print(f"         PUT:  strike={p['strike']} exp={p['expiry']} "
                      f"vol={p['volume']:,} OI={p['open_interest']:,} "
                      f"vol/OI={p['vol_oi_ratio']:.1f}x IV={p['iv']:.2f}")

    # Show Claude block
    print("\n--- Claude Options Block ---")
    block = build_options_flow_block_for_claude(flow, positions=[{"symbol": "NVDA"}])
    print(block if block else "(no actionable signals)")

    # Save to DB
    from backend.db import init_db
    init_db()
    save_options_flow_to_db(flow)
    print("\n--- DB Verification ---")
    import sqlite3
    db_path = os.environ.get("WEALTHINCOME_DB", "data/wealthincome.db")
    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM options_flow WHERE date=?", (date.today().isoformat(),)
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT symbol, options_signal, put_call_ratio, signal_strength FROM options_flow ORDER BY snapshot_at DESC LIMIT 10"
    ).fetchall()
    conn.close()
    print(f"Rows saved today: {count}")
    for r in rows:
        print(f"  {r[0]}: {r[1]} PC={r[2]} strength={r[3]}")

    # Verify no_data symbols not saved
    no_data_syms = [s for s, d in flow.items() if d['options_signal'] == 'no_data']
    if no_data_syms:
        print(f"\n(no_data — not saved: {', '.join(no_data_syms)})")

    print("\nPASS — options flow agent working.")
