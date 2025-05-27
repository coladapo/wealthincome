# Home.py
import streamlit as st
import sys
import os
from datetime import datetime
import pandas as pd

# Page config MUST be first
st.set_page_config(page_title="Trading Dashboard Hub", page_icon="🏠", layout="wide")

# Path setup
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Import data_manager
try:
    from data_manager import data_manager
except ImportError as e:
    st.error(f"Failed to import data_manager: {e}")
    st.error(f"Current directory: {current_dir}")
    st.stop()

st.title("🏠 Trading Dashboard Hub")
st.caption(f"Welcome! Choose a module from the sidebar to begin. {datetime.now().strftime('%A, %B %d, %Y')}")

# Dashboard Metrics
col1, col2, col3, col4 = st.columns(4)

# Get portfolio performance
try:
    portfolio_stats = data_manager.analyze_portfolio_performance()
    if portfolio_stats and portfolio_stats.get('total_trades', 0) > 0:
        with col1:
            total_pnl = portfolio_stats.get('total_pnl', 0)
            pnl_pct = (total_pnl / 10000 * 100) if total_pnl != 0 else 0
            st.metric("Total P&L", f"${total_pnl:,.2f}", f"{pnl_pct:.1f}%")
        with col2:
            win_rate = portfolio_stats.get('win_rate', 0)
            st.metric("Win Rate", f"{win_rate*100:.0f}%", "+5%" if win_rate > 0.5 else "-5%")
    else:
        with col1:
            st.metric("Total P&L", "$0.00", "0%")
        with col2:
            st.metric("Win Rate", "0%", "0%")
except Exception as e:
    with col1:
        st.metric("Total P&L", "$0.00", "Error")
    with col2:
        st.metric("Win Rate", "0%", "Error")

# Get watchlist
try:
    watchlist = data_manager.get_watchlist()
    with col3:
        st.metric("Watchlist Size", f"{len(watchlist)} stocks", "📈 View in Watchlist page")
except Exception as e:
    watchlist = []
    with col3:
        st.metric("Watchlist Size", "0 stocks", "Error")

# Get recent signals from news
with col4:
    if 'news_articles' in st.session_state:
        try:
            positive_articles = [a for a in st.session_state.get('news_articles', [])
                               if a.get('Cached_Sentiment') == 'Positive']
            recent_count = len(positive_articles)
            st.metric("Positive News", f"{recent_count} articles", "📰 Check News page")
        except:
            st.metric("News Signals", "0 articles", "📰 Fetch news first")
    else:
        st.metric("News Signals", "0 articles", "📰 Fetch news first")

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

indices = ['SPY', 'QQQ', 'DIA', 'IWM']
market_data = []

with st.spinner("Loading market data..."):
    for symbol in indices:
        try:
            stock_data = data_manager.get_stock_data([symbol], period="1d")
            if stock_data and symbol in stock_data:
                info = stock_data[symbol].get('info', {})
                price = info.get('regularMarketPrice', 0)
                change = info.get('regularMarketChangePercent', 0)
                volume = info.get('regularMarketVolume', 0)
                
                market_data.append({
                    'Index': symbol,
                    'Price': f"${price:.2f}" if price > 0 else "N/A",
                    'Change': f"{change:.2f}%" if price > 0 else "N/A",
                    'Volume': f"{volume/1e6:.1f}M" if volume > 0 else "N/A"
                })
        except:
            pass

if market_data:
    df = pd.DataFrame(market_data)
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("Market data unavailable. Check your internet connection.")

# Watchlist Overview
if watchlist and len(watchlist) > 0:
    st.markdown("---")
    st.header("👀 Watchlist Overview")
    
    display_watchlist = watchlist[:5]
    if len(watchlist) > 5:
        st.caption(f"Showing top 5 of {len(watchlist)} stocks")
    
    watchlist_data = []
    
    for ticker in display_watchlist:
        try:
            stock_data = data_manager.get_stock_data([ticker], period="1d")
            if stock_data and ticker in stock_data:
                info = stock_data[ticker].get('info', {})
                price = info.get('regularMarketPrice', 0)
                change = info.get('regularMarketChangePercent', 0)
                
                # Get signals safely
                signals = {'day_score': 0, 'swing_score': 0}
                try:
                    signals = data_manager.calculate_signals(stock_data[ticker])
                except:
                    pass
                
                # Get news safely
                news_label = 'N/A'
                try:
                    news = data_manager.get_latest_news_sentiment(ticker)
                    if news:
                        news_label = news.get('label', 'N/A')
                except:
                    pass
                
                watchlist_data.append({
                    'Ticker': ticker,
                    'Price': f"${price:.2f}" if price > 0 else "N/A",
                    'Change': f"{change:.2f}%" if price > 0 else "N/A",
                    'Day Score': f"{signals.get('day_score', 0):.0f}",
                    'Swing Score': f"{signals.get('swing_score', 0):.0f}",
                    'News': news_label
                })
        except:
            pass
    
    if watchlist_data:
        df = pd.DataFrame(watchlist_data)
        st.dataframe(df, use_container_width=True, hide_index=True)

# Trading Tips
with st.expander("💡 Today's Trading Tips"):
    st.markdown("""
    ### Risk Management First! 
    - 🎯 **Pre-Market Prep**: Check news sentiment before market open
    - 📊 **Volume Matters**: Look for stocks with RVOL > 2.0
    - 🛡️ **Risk Management**: Never risk more than 2% per trade
    - 📈 **Trend is Friend**: Trade with the overall market direction
    - 🔍 **AI Advantage**: Use sentiment + technicals for better entries
    """)

st.markdown("---")
st.caption("⚠️ **Disclaimer**: This is for educational purposes only. Always do your own research.")
