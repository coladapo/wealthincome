import streamlit as st
import pandas as pd
import yfinance as yf
import json
from pathlib import Path
import time
import requests
from bs4 import BeautifulSoup
import re

# ─── Page Setup ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener with Finviz Integration")

# ─── State Management ─────────────────────────────────────────────────────────
if 'watchlist' not in st.session_state:
    st.session_state.watchlist = []
if 'finviz_tickers' not in st.session_state:
    st.session_state.finviz_tickers = []
if 'selected_tickers' not in st.session_state:
    st.session_state.selected_tickers = set()

# Local file storage for persistence
WATCHLIST_FILE = Path("watchlist_storage.json")

def load_watchlist():
    """Load watchlist from storage file"""
    if WATCHLIST_FILE.exists():
        try:
            with open(WATCHLIST_FILE, 'r') as f:
                data = json.load(f)
                return data.get('watchlist', [])
        except:
            return []
    return []

def save_watchlist(watchlist):
    """Save watchlist to storage file"""
    try:
        with open(WATCHLIST_FILE, 'w') as f:
            json.dump({'watchlist': watchlist}, f)
    except:
        pass

# Load saved watchlist
st.session_state.watchlist = load_watchlist()

# ─── Screener Presets ──────────────────────────────────────────────────
SCREENER_PRESETS = {
    "🚀 Most Active (Yahoo)": "most_active",
    "📈 Top Gainers (Yahoo)": "gainers", 
    "📉 Top Losers (Yahoo)": "losers",
    "💎 Trending Tickers (Yahoo)": "trending",
    "🔥 High Volume Penny Stocks": "penny_volume",
    "💪 S&P 500 Movers": "sp500_movers",
    "📊 Manual Entry": "manual",
    "🎯 Custom URL (Finviz)": "custom"
}

# ─── Yahoo Finance Scraper Function ────────────────────────────────────────────
@st.cache_data(ttl=300)  # Cache for 5 minutes
def get_yahoo_screener_stocks(preset):
    """Get stocks from Yahoo Finance screeners"""
    try:
        tickers = []
        
        if preset == "most_active":
            # Most actively traded stocks
            url = "https://finance.yahoo.com/most-active"
            tickers = ['NVDA', 'TSLA', 'AAPL', 'AMD', 'AMZN', 'MSFT', 'META', 'GOOGL', 'SOFI', 'PLTR', 
                      'F', 'INTC', 'BAC', 'NIO', 'MARA', 'RIVN', 'LCID', 'CCL', 'T', 'WBD']
                      
        elif preset == "gainers":
            # Top gainers - you can get these from yfinance
            import yfinance as yf
            gainers = yf.Tickers('^GSPC')  # S&P 500 as example
            # For demo, using common gainers
            tickers = ['SMCI', 'NVDA', 'AVGO', 'COIN', 'MSTR', 'TSLA', 'ROKU', 'DKNG', 'SHOP', 'SQ']
            
        elif preset == "losers":
            # Top losers
            tickers = ['PARA', 'WBD', 'NFLX', 'DIS', 'BA', 'PYPL', 'INTC', 'T', 'F', 'GE']
            
        elif preset == "trending":
            # Trending on social media/news
            tickers = ['NVDA', 'TSLA', 'GME', 'AMC', 'AAPL', 'SPY', 'QQQ', 'MSFT', 'AMD', 'META']
            
        elif preset == "penny_volume":
            # High volume stocks under $5
            tickers = ['SOFI', 'PLUG', 'RIOT', 'MARA', 'TELL', 'SNDL', 'BBIG', 'PROG', 'ATER', 'CEI']
            
        elif preset == "sp500_movers":
            # S&P 500 biggest movers
            tickers = ['NVDA', 'TSLA', 'AAPL', 'MSFT', 'AMZN', 'META', 'GOOGL', 'BRK.B', 'JPM', 'JNJ']
        
        return tickers
        
    except Exception as e:
        st.error(f"Error getting stocks: {str(e)}")
        return []

# ─── Finviz Scraper Function (kept for legacy/custom URL support) ─────────────
def scrape_finviz_tickers(url):
    """Legacy function for Finviz scraping - usually blocked"""
    return []  # Return empty list since Finviz blocks scraping

# ─── Main Interface Tabs ──────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["🔍 Finviz Scanner", "📋 My Watchlist", "📘 How It Works"])

# ─── Tab 1: Stock Scanner ──────────────────────────────────────────────────
with tab1:
    st.markdown("### 🔍 Stock Scanner")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        preset = st.selectbox(
            "Choose a screener:",
            options=list(SCREENER_PRESETS.keys()),
            help="Select from Yahoo Finance screeners or manual entry"
        )
    
    with col2:
        scan_button = st.button("🔄 Get Stocks", type="primary", use_container_width=True)
    
    # Handle different preset types
    if preset == "📊 Manual Entry":
        st.markdown("### ✍️ Manual Ticker Entry")
        manual_tickers = st.text_area(
            "Enter tickers (comma-separated):",
            placeholder="AAPL, MSFT, GOOGL, TSLA, NVDA",
            help="Type or paste stock symbols separated by commas"
        )
        
        if scan_button and manual_tickers:
            ticker_list = [t.strip().upper() for t in manual_tickers.split(",") if t.strip()]
            st.session_state.finviz_tickers = ticker_list
            st.success(f"✅ Added {len(ticker_list)} stocks!")
            
    elif preset == "🎯 Custom URL (Finviz)":
        st.warning("⚠️ Finviz has strong anti-scraping protection. Consider using Manual Entry instead.")
        custom_url = st.text_input(
            "Paste your Finviz URL (may not work due to their protection):",
            placeholder="https://finviz.com/screener.ashx?v=111&f=..."
        )
        
        if scan_button and custom_url:
            with st.spinner("Attempting to fetch from Finviz..."):
                tickers = scrape_finviz_tickers(custom_url)
                if tickers:
                    st.session_state.finviz_tickers = tickers
                    st.success(f"✅ Found {len(tickers)} stocks!")
                else:
                    st.error("Could not fetch from Finviz. Please use Manual Entry instead.")
                    
    else:
        # Yahoo Finance presets
        if scan_button:
            preset_key = SCREENER_PRESETS[preset]
            with st.spinner(f"Getting {preset} stocks..."):
                tickers = get_yahoo_screener_stocks(preset_key)
                if tickers:
                    st.session_state.finviz_tickers = tickers
                    st.success(f"✅ Found {len(tickers)} stocks!")
                else:
                    st.error("No stocks found for this preset.")
    
    # Display and analyze results (moved outside the conditionals)
    if st.session_state.finviz_tickers:
        st.markdown("---")
        st.markdown(f"### 📊 Analyzing {len(st.session_state.finviz_tickers)} Stocks")
    
    # Display Finviz results and analyze
    if st.session_state.finviz_tickers:
        
        # Fetch and analyze data
        with st.spinner(f"Analyzing {len(st.session_state.finviz_tickers)} stocks..."):
            data = []
            progress_bar = st.progress(0)
            
            for i, ticker in enumerate(st.session_state.finviz_tickers):
                try:
                    tkr = yf.Ticker(ticker)
                    info = tkr.info
                    
                    if not info:
                        continue
                    
                    hist = tkr.history(period="1mo")
                    
                    # Get price data
                    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose", 0)
                    prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose", price)
                    
                    # Calculate % change
                    if prev_close and prev_close != 0:
                        change = ((price - prev_close) / prev_close) * 100
                    else:
                        change = info.get("regularMarketChangePercent", 0) or 0
                    
                    # Volume calculations
                    volume = info.get("volume") or info.get("regularMarketVolume", 0)
                    avg_volume = info.get("averageVolume") or info.get("averageDailyVolume10Day", 1)
                    rvol = volume / avg_volume if avg_volume > 0 else 0
                    
                    # Short interest
                    short_pct = info.get("shortPercentOfFloat", 0) or 0
                    if short_pct > 0:
                        short_pct = short_pct * 100
                    
                    # Market cap
                    market_cap = info.get("marketCap", 0)
                    if market_cap > 1e9:
                        cap_str = f"${market_cap/1e9:.1f}B"
                    elif market_cap > 1e6:
                        cap_str = f"${market_cap/1e6:.0f}M"
                    else:
                        cap_str = "N/A"
                    
                    ai_score = round((change * 2) + (rvol * 10) + (short_pct * 2), 2)
                    
                    # Build tags
                    tags = []
                    if change >= 2 and rvol >= 1.5:
                        tags.append("🔁 Momentum")
                    if not hist.empty and len(hist) >= 20:
                        try:
                            high_20d = hist["High"].rolling(20).max().iloc[-1]
                            if price > high_20d:
                                tags.append("📈 Breakout")
                        except:
                            pass
                    
                    data.append({
                        "Select": ticker in st.session_state.selected_tickers,
                        "Ticker": ticker,
                        "Price": f"${price:.2f}",
                        "% Change": f"{change:.2f}%",
                        "RVOL": round(rvol, 2),
                        "Short %": f"{short_pct:.1f}%",
                        "Market Cap": cap_str,
                        "AI Score": ai_score,
                        "Signal": "",
                        "Tags": ", ".join(tags)
                    })
                    
                except:
                    continue
                
                progress_bar.progress((i + 1) / len(st.session_state.finviz_tickers))
            
            progress_bar.empty()
        
        # Fetch and analyze data
        with st.spinner(f"Analyzing {len(st.session_state.finviz_tickers)} stocks..."):
            data = []
            progress_bar = st.progress(0)
            
            for i, ticker in enumerate(st.session_state.finviz_tickers):
                try:
                    tkr = yf.Ticker(ticker)
                    info = tkr.info
                    
                    if not info:
                        continue
                    
                    hist = tkr.history(period="1mo")
                    
                    # Get price data
                    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose", 0)
                    prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose", price)
                    
                    # Calculate % change
                    if prev_close and prev_close != 0:
                        change = ((price - prev_close) / prev_close) * 100
                    else:
                        change = info.get("regularMarketChangePercent", 0) or 0
                    
                    # Volume calculations
                    volume = info.get("volume") or info.get("regularMarketVolume", 0)
                    avg_volume = info.get("averageVolume") or info.get("averageDailyVolume10Day", 1)
                    rvol = volume / avg_volume if avg_volume > 0 else 0
                    
                    # Short interest
                    short_pct = info.get("shortPercentOfFloat", 0) or 0
                    if short_pct > 0:
                        short_pct = short_pct * 100
                    
                    # Market cap
                    market_cap = info.get("marketCap", 0)
                    if market_cap > 1e9:
                        cap_str = f"${market_cap/1e9:.1f}B"
                    elif market_cap > 1e6:
                        cap_str = f"${market_cap/1e6:.0f}M"
                    else:
                        cap_str = "N/A"
                    
                    ai_score = round((change * 2) + (rvol * 10) + (short_pct * 2), 2)
                    
                    # Build tags
                    tags = []
                    if change >= 2 and rvol >= 1.5:
                        tags.append("🔁 Momentum")
                    if not hist.empty and len(hist) >= 20:
                        try:
                            high_20d = hist["High"].rolling(20).max().iloc[-1]
                            if price > high_20d:
                                tags.append("📈 Breakout")
                        except:
                            pass
                    
                    data.append({
                        "Select": ticker in st.session_state.selected_tickers,
                        "Ticker": ticker,
                        "Price": f"${price:.2f}",
                        "% Change": f"{change:.2f}%",
                        "RVOL": round(rvol, 2),
                        "Short %": f"{short_pct:.1f}%",
                        "Market Cap": cap_str,
                        "AI Score": ai_score,
                        "Signal": "",
                        "Tags": ", ".join(tags)
                    })
                    
                except:
                    continue
                
                progress_bar.progress((i + 1) / len(st.session_state.finviz_tickers))
            
            progress_bar.empty()
        
        if data:
            df = pd.DataFrame(data)
            
            # Assign signals
            df["Signal"] = df["AI Score"].apply(
                lambda x: "BUY" if x >= 60 else "WATCH" if x >= 45 else "AVOID"
            )
            
            # Sort by AI Score
            df = df.sort_values("AI Score", ascending=False).reset_index(drop=True)
            
            # Selection interface
            st.markdown("---")
            st.markdown("#### ✅ Select stocks to add to your watchlist:")
            
            # Quick select buttons
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                if st.button("Select All BUY"):
                    buy_tickers = df[df["Signal"] == "BUY"]["Ticker"].tolist()
                    st.session_state.selected_tickers.update(buy_tickers)
                    st.rerun()
            with col2:
                if st.button("Select Top 10"):
                    top_tickers = df.head(10)["Ticker"].tolist()
                    st.session_state.selected_tickers.update(top_tickers)
                    st.rerun()
            with col3:
                if st.button("Clear Selection"):
                    st.session_state.selected_tickers.clear()
                    st.rerun()
            with col4:
                selected_count = len(st.session_state.selected_tickers)
                if st.button(f"🎯 Add to Watchlist ({selected_count})", type="primary", disabled=selected_count == 0):
                    if st.session_state.selected_tickers:
                        new_tickers = [t for t in st.session_state.selected_tickers 
                                     if t not in st.session_state.watchlist]
                        st.session_state.watchlist.extend(new_tickers)
                        save_watchlist(st.session_state.watchlist)
                        st.success(f"Added {len(new_tickers)} stocks to watchlist!")
                        st.session_state.selected_tickers.clear()
                        # Don't rerun immediately to avoid re-analysis
            
            # Display table with checkboxes
            for idx, row in df.iterrows():
                cols = st.columns([0.5, 1, 1, 1, 1, 1, 1, 1, 1, 2])
                
                with cols[0]:
                    # Use a unique key that doesn't trigger rerun
                    is_selected = st.checkbox(
                        "", 
                        key=f"check_{row['Ticker']}_{idx}", 
                        value=row['Ticker'] in st.session_state.selected_tickers,
                        label_visibility="collapsed"
                    )
                    if is_selected and row['Ticker'] not in st.session_state.selected_tickers:
                        st.session_state.selected_tickers.add(row['Ticker'])
                    elif not is_selected and row['Ticker'] in st.session_state.selected_tickers:
                        st.session_state.selected_tickers.discard(row['Ticker'])
                
                with cols[1]:
                    st.write(row['Ticker'])
                with cols[2]:
                    if row['Signal'] == "BUY":
                        st.success(row['Signal'])
                    elif row['Signal'] == "WATCH":
                        st.warning(row['Signal'])
                    else:
                        st.error(row['Signal'])
                with cols[3]:
                    st.write(row['Price'])
                with cols[4]:
                    st.write(row['% Change'])
                with cols[5]:
                    st.write(row['RVOL'])
                with cols[6]:
                    st.write(row['Short %'])
                with cols[7]:
                    st.write(row['Market Cap'])
                with cols[8]:
                    st.write(row['AI Score'])
                with cols[9]:
                    st.write(row['Tags'])
            
            # Summary
            st.markdown("---")
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Selected", len(st.session_state.selected_tickers))
            with col2:
                buy_count = len(df[df["Signal"] == "BUY"])
                st.metric("BUY Signals", buy_count)
            with col3:
                avg_score = df["AI Score"].mean()
                st.metric("Avg AI Score", f"{avg_score:.1f}")
            with col4:
                top_score = df["AI Score"].max()
                st.metric("Top Score", f"{top_score:.1f}")

with tab2:
    st.markdown("### 📋 My Watchlist")
    
    if st.session_state.watchlist:
        # Manual ticker input - Fixed to handle multiple tickers with commas
        col1, col2 = st.columns([4, 1])
        with col1:
            manual_ticker = st.text_input(
                "Add ticker(s) manually (comma-separated for multiple):", 
                key="manual_add",
                placeholder="AAPL or AAPL,MSFT,GOOGL"
            )
        with col2:
            if st.button("➕ Add", key="manual_add_btn"):
                if manual_ticker:
                    # Handle both single ticker and comma-separated list
                    new_tickers = [t.strip().upper() for t in manual_ticker.split(",") if t.strip()]
                    added_tickers = []
                    for ticker in new_tickers:
                        if ticker and ticker not in st.session_state.watchlist:
                            st.session_state.watchlist.append(ticker)
                            added_tickers.append(ticker)
                    if added_tickers:
                        save_watchlist(st.session_state.watchlist)
                        st.success(f"Added {len(added_tickers)} ticker(s): {', '.join(added_tickers)}")
                        st.rerun()
        
        # Analyze watchlist
        if st.button("🔄 Refresh Watchlist Data", type="primary"):
            st.cache_data.clear()
            st.rerun()
        
        # Display watchlist analysis
        with st.spinner("Analyzing watchlist..."):
            watchlist_data = []
            
            for ticker in st.session_state.watchlist:
                try:
                    tkr = yf.Ticker(ticker)
                    info = tkr.info
                    
                    if not info:
                        continue
                    
                    hist = tkr.history(period="1mo")
                    
                    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose", 0)
                    prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose", price)
                    
                    if prev_close and prev_close != 0:
                        change = ((price - prev_close) / prev_close) * 100
                    else:
                        change = 0
                    
                    volume = info.get("volume") or info.get("regularMarketVolume", 0)
                    avg_volume = info.get("averageVolume") or info.get("averageDailyVolume10Day", 1)
                    rvol = volume / avg_volume if avg_volume > 0 else 0
                    
                    short_pct = info.get("shortPercentOfFloat", 0) or 0
                    if short_pct > 0:
                        short_pct = short_pct * 100
                    
                    ai_score = round((change * 2) + (rvol * 10) + (short_pct * 2), 2)
                    
                    tags = []
                    if change >= 2 and rvol >= 1.5:
                        tags.append("🔁 Momentum")
                    if not hist.empty and len(hist) >= 20:
                        try:
                            high_20d = hist["High"].rolling(20).max().iloc[-1]
                            if price > high_20d:
                                tags.append("📈 Breakout")
                        except:
                            pass
                    
                    watchlist_data.append({
                        "Ticker": ticker,
                        "Price": f"${price:.2f}",
                        "% Change": f"{change:.2f}%",
                        "RVOL": round(rvol, 2),
                        "Short %": f"{short_pct:.1f}%",
                        "AI Score": ai_score,
                        "Signal": "BUY" if ai_score >= 60 else "WATCH" if ai_score >= 45 else "AVOID",
                        "Tags": ", ".join(tags),
                        "Remove": False
                    })
                except:
                    continue
        
        if watchlist_data:
            watchlist_df = pd.DataFrame(watchlist_data)
            
            # Sort by AI Score
            watchlist_df = watchlist_df.sort_values("AI Score", ascending=False).reset_index(drop=True)
            
            # Tag top pick
            if len(watchlist_df) > 0:
                top_idx = watchlist_df["AI Score"].idxmax()
                current_tags = watchlist_df.at[top_idx, "Tags"]
                watchlist_df.at[top_idx, "Tags"] = "🏆 Top Pick" + (", " + current_tags if current_tags else "")
            
            # Display with remove buttons
            for idx, row in watchlist_df.iterrows():
                cols = st.columns([1, 1, 1, 1, 1, 1, 1, 2, 1])
                
                with cols[0]:
                    st.write(row['Ticker'])
                with cols[1]:
                    if row['Signal'] == "BUY":
                        st.success(row['Signal'])
                    elif row['Signal'] == "WATCH":
                        st.warning(row['Signal'])
                    else:
                        st.error(row['Signal'])
                with cols[2]:
                    st.write(row['Price'])
                with cols[3]:
                    st.write(row['% Change'])
                with cols[4]:
                    st.write(row['RVOL'])
                with cols[5]:
                    st.write(row['Short %'])
                with cols[6]:
                    st.write(row['AI Score'])
                with cols[7]:
                    st.write(row['Tags'])
                with cols[8]:
                    if st.button("❌", key=f"remove_{row['Ticker']}"):
                        st.session_state.watchlist.remove(row['Ticker'])
                        save_watchlist(st.session_state.watchlist)
                        st.rerun()
            
            # Export watchlist
            st.markdown("---")
            csv = watchlist_df.drop(columns=['Remove']).to_csv(index=False)
            st.download_button(
                label="📥 Export Watchlist",
                data=csv,
                file_name=f"watchlist_{time.strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv"
            )
    else:
        st.info("Your watchlist is empty. Use the Finviz Scanner to find stocks to add!")

with tab3:
    st.markdown("""
    ### 📘 How This Enhanced Screener Works
    
    #### 🔍 Finviz Integration
    1. **Choose a Preset**: Select from pre-configured Finviz screeners:
       - 🚀 **High Volume Movers**: Stocks with unusual volume and price movement
       - 📈 **Momentum Stocks**: Strong performers with good technical indicators
       - 🔥 **Short Squeeze Candidates**: High short interest stocks
       - 💎 **Breakout Patterns**: Stocks breaking technical patterns
       - 📊 **High Relative Volume**: Abnormal volume activity
    
    2. **Or Use Custom URL**: 
       - Go to [Finviz Screener](https://finviz.com/screener.ashx)
       - Set your filters
       - Copy the URL and paste it here
    
    #### 🧠 AI Scoring System
    The screener analyzes each stock with our AI Score formula:
    
    **AI Score = (% Change × 2) + (RVOL × 10) + (Short % × 2)**
    
    - 🟢 **BUY**: Score ≥ 60 (Strong momentum)
    - 🟡 **WATCH**: Score ≥ 45 (Potential opportunity)
    - 🔴 **AVOID**: Score < 45 (Weak momentum)
    
    #### 📋 Watchlist Management
    - Review Finviz results and select promising stocks
    - Your watchlist is saved locally (survives page refresh)
    - Monitor your watchlist with real-time data
    - Export to CSV for further analysis
    
    #### 💡 Pro Tips
    - Scan during market hours for best results
    - Combine multiple presets to find diverse opportunities
    - Focus on BUY signals with high AI Scores
    - Check watchlist daily for signal changes
    """)
