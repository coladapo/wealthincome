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

# ─── Finviz Screener Presets ──────────────────────────────────────────────────
FINVIZ_PRESETS = {
    "🚀 High Volume Movers": "https://finviz.com/screener.ashx?v=111&f=sh_avgvol_o500,sh_price_o5,ta_change_u5,ta_volatility_o3&ft=4",
    "📈 Momentum Stocks": "https://finviz.com/screener.ashx?v=111&f=sh_avgvol_o500,sh_price_o10,ta_perf_d5o,ta_rsi_os50&ft=4",
    "🔥 Short Squeeze Candidates": "https://finviz.com/screener.ashx?v=111&f=sh_avgvol_o500,sh_price_o5,sh_short_o20&ft=4",
    "💎 Breakout Patterns": "https://finviz.com/screener.ashx?v=111&f=sh_avgvol_o500,sh_price_o5,ta_pattern_channelup,ta_perf_dup&ft=4",
    "📊 High Relative Volume": "https://finviz.com/screener.ashx?v=111&f=sh_avgvol_o500,sh_price_o5,sh_relvol_o2&ft=4",
    "🎯 Custom URL": "custom"
}

# ─── Finviz Scraper Function ──────────────────────────────────────────────────
@st.cache_data(ttl=600)  # Cache for 10 minutes
def scrape_finviz_tickers(url):
    """Scrape tickers from Finviz screener URL"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find ticker links
        tickers = []
        ticker_links = soup.find_all('a', class_='screener-link-primary')
        
        for link in ticker_links:
            ticker = link.text.strip()
            if ticker and len(ticker) <= 5:  # Valid ticker length
                tickers.append(ticker)
        
        return list(set(tickers))  # Remove duplicates
    except Exception as e:
        st.error(f"Error fetching from Finviz: {str(e)}")
        return []

# ─── Main Interface Tabs ──────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["🔍 Finviz Scanner", "📋 My Watchlist", "📘 How It Works"])

with tab1:
    st.markdown("### 🔍 Scan Finviz for Stocks")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        preset = st.selectbox(
            "Choose a preset screener or paste custom URL:",
            options=list(FINVIZ_PRESETS.keys()),
            help="Select a pre-configured screener or choose 'Custom URL' to paste your own"
        )
    
    with col2:
        scan_button = st.button("🔄 Scan Finviz", type="primary", use_container_width=True)
    
    # Custom URL input - This appears when you select "Custom URL"
    if preset == "🎯 Custom URL":
        st.info("👇 Paste your Finviz URL below:")
        custom_url = st.text_input(
            "Paste your Finviz screener URL:",
            placeholder="https://finviz.com/screener.ashx?v=111&f=...",
            value="https://finviz.com/screener.ashx?v=111&s=ta_newhigh&f=geo_usa,sh_curvol_o1000,sh_price_u5,sh_relvol_o1.5",
            help="Example: Your URL for new highs with high volume"
        )
        finviz_url = custom_url
    else:
        finviz_url = FINVIZ_PRESETS[preset]
    
    # Scan button action
    if scan_button and finviz_url and finviz_url != "custom":
        with st.spinner("🔍 Scanning Finviz..."):
            tickers = scrape_finviz_tickers(finviz_url)
            if tickers:
                st.session_state.finviz_tickers = tickers
                st.success(f"✅ Found {len(tickers)} stocks from Finviz!")
            else:
                st.error("No tickers found. Check the URL or try a different screener.")
    
    # Display Finviz results and analyze
    if st.session_state.finviz_tickers:
        st.markdown("---")
        st.markdown(f"### 📊 Analyzing {len(st.session_state.finviz_tickers)} Stocks from Finviz")
        
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
                if st.button("🎯 Add to Watchlist", type="primary"):
                    if st.session_state.selected_tickers:
                        new_tickers = [t for t in st.session_state.selected_tickers 
                                     if t not in st.session_state.watchlist]
                        st.session_state.watchlist.extend(new_tickers)
                        save_watchlist(st.session_state.watchlist)
                        st.success(f"Added {len(new_tickers)} stocks to watchlist!")
                        st.session_state.selected_tickers.clear()
                        time.sleep(1)
                        st.rerun()
            
            # Display table with checkboxes
            for idx, row in df.iterrows():
                cols = st.columns([0.5, 1, 1, 1, 1, 1, 1, 1, 1, 2])
                
                with cols[0]:
                    if st.checkbox("", key=f"check_{row['Ticker']}", 
                                 value=row['Ticker'] in st.session_state.selected_tickers):
                        st.session_state.selected_tickers.add(row['Ticker'])
                    else:
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
        # Manual ticker input
        col1, col2 = st.columns([4, 1])
        with col1:
            manual_ticker = st.text_input("Add ticker manually:", key="manual_add")
        with col2:
            if st.button("➕ Add", key="manual_add_btn"):
                if manual_ticker and manual_ticker.upper() not in st.session_state.watchlist:
                    st.session_state.watchlist.append(manual_ticker.upper())
                    save_watchlist(st.session_state.watchlist)
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
