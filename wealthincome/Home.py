# Home.py
import streamlit as st
import sys
import os
from datetime import datetime
import pandas as pd

# Path setup
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# Import data_manager
try:
    from data_manager import data_manager
except ImportError:
    st.error("Failed to import data_manager. Make sure data_manager.py is in the wealthincome directory.")
    st.stop()

# Page config
st.set_page_config(page_title="Trading Dashboard Hub", page_icon="🏠", layout="wide")

st.title("🏠 Trading Dashboard Hub")
st.caption(f"Welcome! Choose a module from the sidebar to begin. {datetime.now().strftime('%A, %B %d, %Y')}")

# Dashboard Metrics
col1, col2, col3, col4 = st.columns(4)

# Get portfolio performance
portfolio_stats = data_manager.analyze_portfolio_performance()
if portfolio_stats and portfolio_stats.get('total_trades', 0) > 0:
    with col1:
        st.metric("Total P&L", 
                  f"${portfolio_stats.get('total_pnl', 0):,.2f}", 
                  f"{portfolio_stats.get('total_pnl', 0)/10000*100:.1f}%" if portfolio_stats.get('total_pnl', 0) != 0 else "0%")
    with col2:
        st.metric("Win Rate", 
                  f"{portfolio_stats.get('win_rate', 0)*100:.0f}%",
                  "+5%" if portfolio_stats.get('win_rate', 0) > 0.5 else "-5%")
else:
    with col1:
        st.metric("Total P&L", "$0.00", "0%")
    with col2:
        st.metric("Win Rate", "0%", "0%")

# Get watchlist
watchlist = data_manager.get_watchlist()
with col3:
    st.metric("Watchlist Size", f"{len(watchlist)} stocks", 
              "📈 View in Watchlist page")

# Get recent signals from news
with col4:
    if 'news_articles' in st.session_state:
        recent_count = len([a for a in st.session_state.get('news_articles', [])
                           if 'Cached_Sentiment' in a and a['Cached_Sentiment'] == 'Positive'])
        st.metric("Positive News", f"{recent_count} articles", 
                  "🧠 Check News page")
    else:
        st.metric("News Signals", "0 articles", "🧠 Fetch news first")

st.markdown("---")

# Quick Actions
st.header("⚡ Quick Actions")
col1, col2, col3, col4 = st.columns(4)

with col1:
    if st.button("🔍 Run AI Scanner", use_container_width=True):
        st.switch_page("pages/2_🧠_AI_Signals.py")
        
with col2:
    if st.button("📊 View Patterns", use_container_width=True):
        st.switch_page("pages/4_📊_Patterns.py")
        
with col3:
    if st.button("📰 Check News", use_container_width=True):
        st.switch_page("pages/3_📰_News.py")
        
with col4:
    if st.button("📓 Trade Journal", use_container_width=True):
        st.switch_page("pages/5_📓_Journal.py")

# Market Overview
st.markdown("---")
st.header("📊 Market Overview")

# Get market data for major indices
indices = ['SPY', 'QQQ', 'DIA', 'IWM']
market_data = []

with st.spinner("Loading market data..."):
    for symbol in indices:
        stock_data = data_manager.get_stock_data([symbol], period="1d")
        if stock_data and symbol in stock_data:
            info = stock_data[symbol].get('info', {})
            market_data.append({
                'Index': symbol,
                'Price': f"${info.get('regularMarketPrice', 0):.2f}",
                'Change': f"{info.get('regularMarketChangePercent', 0):.2f}%",
                'Volume': f"{info.get('regularMarketVolume', 0)/1e6:.1f}M"
            })

if market_data:
    df = pd.DataFrame(market_data)
    st.dataframe(df, use_container_width=True, hide_index=True)

# Recent Activity
if watchlist:
    st.markdown("---")
    st.header("👀 Watchlist Overview")
    
    watchlist_data = []
    for ticker in watchlist[:5]:  # Show top 5
        # Get latest news sentiment
        news = data_manager.get_latest_news_sentiment(ticker)
        
        stock_data = data_manager.get_stock_data([ticker], period="1d")
        if stock_data and ticker in stock_data:
            info = stock_data[ticker].get('info', {})
            
            # Calculate signals
            signals = data_manager.calculate_signals(stock_data[ticker])
            
            watchlist_data.append({
                'Ticker': ticker,
                'Price': f"${info.get('regularMarketPrice', 0):.2f}",
                'Change': f"{info.get('regularMarketChangePercent', 0):.2f}%",
                'Day Score': f"{signals.get('day_score', 0):.0f}",
                'Swing Score': f"{signals.get('swing_score', 0):.0f}",
                'News': news['label'] if news else 'N/A'
            })
    
    if watchlist_data:
        df = pd.DataFrame(watchlist_data)
        st.dataframe(df, use_container_width=True, hide_index=True)

# Trading Tips
with st.expander("💡 Today's Trading Tips"):
    st.markdown("""
    - 🎯 **Pre-Market Prep**: Check news sentiment before market open
    - 📊 **Volume Matters**: Look for stocks with RVOL > 2.0
    - 🛡️ **Risk Management**: Never risk more than 2% per trade
    - 📈 **Trend is Friend**: Trade with the overall market direction
    - 🔍 **AI Advantage**: Use sentiment + technicals for better entries
    """)

st.markdown("---")
st.caption("Remember: Risk management is key. Never risk more than 2% per trade.")
