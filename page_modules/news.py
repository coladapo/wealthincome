"""
News & Sentiment Page — real news from yfinance for watchlist symbols.
Zero hardcoded or random sentiment scores.
"""

import streamlit as st
import pandas as pd
import requests
from datetime import datetime
import yfinance as yf

API_BASE = "http://localhost:8000"


def _api(path, params=None):
    try:
        r = requests.get(f"{API_BASE}{path}", params=params, timeout=8)
        r.raise_for_status()
        return r.json(), None
    except requests.exceptions.ConnectionError:
        return None, "Backend API not running"
    except Exception as e:
        return None, str(e)


@st.cache_data(ttl=300)
def _fetch_news(symbols: tuple) -> list:
    """Fetch real news headlines from yfinance for given symbols."""
    all_news = []
    for sym in symbols[:8]:
        try:
            ticker = yf.Ticker(sym)
            news_items = ticker.news or []
            for item in news_items[:3]:
                all_news.append({
                    "symbol": sym,
                    "title": item.get("title", ""),
                    "publisher": item.get("publisher", ""),
                    "link": item.get("link", ""),
                    "published": datetime.fromtimestamp(item.get("providerPublishTime", 0)).strftime("%Y-%m-%d %H:%M") if item.get("providerPublishTime") else "—",
                    "type": item.get("type", ""),
                })
        except Exception:
            continue
    # Sort by published desc
    all_news.sort(key=lambda x: x["published"], reverse=True)
    return all_news


def render_news():
    st.title("News & Sentiment")

    status_data, err = _api("/status")
    if err:
        st.error(err)
        return

    cfg = status_data.get("config") or {}
    watchlist_str = cfg.get("watchlist", "")
    symbols = [s.strip() for s in watchlist_str.split(",") if s.strip()]

    last_cycle = status_data.get("last_cycle") or {}
    positions = status_data.get("positions") or []
    held_symbols = [p.get("symbol") for p in positions]

    # ── Market regime from last cycle ────────────────────────────────────────
    st.subheader("Market Regime")
    import json
    raw_json = last_cycle.get("raw_json")
    if raw_json:
        try:
            output = json.loads(raw_json)
            summary = output.get("market_summary", "")
            notes = output.get("cycle_notes", "")
            cycle_time = last_cycle.get("started_at", "")[:19]
            st.info(f"**{cycle_time}** — {summary}")
            if notes:
                st.caption(notes)
        except Exception:
            pass
    else:
        st.info("No cycle data yet — regime context will appear after first trading cycle")

    st.markdown("---")

    # ── Real news feed ───────────────────────────────────────────────────────
    st.subheader(f"Latest News — {', '.join(symbols[:6]) if symbols else 'watchlist'}")

    if not symbols:
        st.info("Add symbols to your watchlist on the Autonomous Trader page to see news here")
        return

    with st.spinner("Fetching latest news..."):
        news_items = _fetch_news(tuple(symbols))

    if not news_items:
        st.info("No news available for current watchlist symbols")
        return

    # Symbol filter
    filter_sym = st.selectbox("Filter by symbol", ["All"] + symbols)
    if filter_sym != "All":
        news_items = [n for n in news_items if n["symbol"] == filter_sym]

    for item in news_items:
        with st.container():
            col1, col2 = st.columns([5, 1])
            with col1:
                held = "🟢 HOLDING" if item["symbol"] in held_symbols else ""
                st.markdown(f"**[{item['title']}]({item['link']})**")
                st.caption(f"{item['symbol']} · {item['publisher']} · {item['published']} {held}")
            with col2:
                st.markdown(f"`{item['symbol']}`")
            st.markdown("---")

    st.markdown("---")

    # ── Watchlist snapshot ───────────────────────────────────────────────────
    st.subheader("Watchlist Snapshot")
    rows = []
    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            info = t.fast_info
            rows.append({
                "Symbol": sym,
                "Price": f"${info.last_price:.2f}" if info.last_price else "—",
                "Day Change %": f"{((info.last_price - info.previous_close) / info.previous_close * 100):+.2f}%" if info.last_price and info.previous_close else "—",
                "52W High": f"${info.fifty_two_week_high:.2f}" if info.fifty_two_week_high else "—",
                "52W Low": f"${info.fifty_two_week_low:.2f}" if info.fifty_two_week_low else "—",
                "Held": "✓" if sym in held_symbols else "",
            })
        except Exception:
            rows.append({"Symbol": sym, "Price": "—", "Day Change %": "—", "52W High": "—", "52W Low": "—", "Held": ""})

    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
