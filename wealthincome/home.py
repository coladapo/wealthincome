# Home.py
import streamlit as st
import pandas as pd
from datetime import datetime
import plotly.graph_objects as go
import plotly.express as px
from data_manager import data_manager

# Page configuration
st.set_page_config(page_title="Trading Hub", layout="wide")
st.title("🏠 Trading Dashboard Hub")
st.markdown(f"Welcome! Choose a module from the sidebar to begin. *{datetime.now().strftime('%A, %B %d, %Y')}*")

# Main dashboard hub content
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("Today's P&L", "$+342.50", delta="+1.8%")
    st.markdown("📈 Check out your watchlist in the sidebar.")

with col2:
    st.metric("Win Rate", "73%", delta="+5%")
    st.markdown("⚡ 2 Buy signals active")

with col3:
    st.metric("Pattern Alerts", "3 Active")
    st.markdown("🎯 View chart setups via sidebar.")

with col4:
    st.metric("AI Signals", "2 Buys, 1 Sell")
    st.markdown("🧠 Screen fresh tickers with AI logic.")

st.markdown("---")

# Quick Market Overview
st.header("📊 Market Overview")

# Fetch major indices
indices = ['SPY', 'QQQ', 'DIA', 'IWM', 'VIX']
market_data = data_manager.get_stock_data(indices, period="1d")

if market_data:
    cols = st.columns(len(indices))
    for i, ticker in enumerate(indices):
        if ticker in market_data:
            info = market_data[ticker]['info']
            price = info.get('regularMarketPrice', 0)
            change = info.get('regularMarketChangePercent', 0)
            
            with cols[i]:
                delta_color = "normal" if change >= 0 else "inverse"
                st.metric(
                    ticker,
                    f"${price:.2f}",
                    f"{change:.2f}%",
                    delta_color=delta_color
                )

st.markdown("---")

# Portfolio Performance
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("📈 Portfolio Performance")
    
    # Get performance metrics
    performance = data_manager.analyze_portfolio_performance()
    
    if performance and performance['total_trades'] > 0:
        # Create performance chart
        dates = pd.date_range(end=datetime.now(), periods=30, freq='D')
        cumulative_pnl = pd.Series(
            data=[i * performance.get('total_pnl', 0) / 30 for i in range(30)],
            index=dates
        )
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=dates,
            y=cumulative_pnl,
            mode='lines',
            name='P&L',
            line=dict(color='green' if performance.get('total_pnl', 0) > 0 else 'red', width=2)
        ))
        
        fig.update_layout(
            title="30-Day P&L Curve",
            xaxis_title="Date",
            yaxis_title="Cumulative P&L ($)",
            height=300,
            showlegend=False
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
        # Performance metrics
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.metric("Total Trades", performance['total_trades'])
        with col_b:
            st.metric("Win Rate", f"{performance['win_rate']*100:.1f}%")
        with col_c:
            st.metric("Profit Factor", f"{performance.get('profit_factor', 0):.2f}")
    else:
        st.info("No trades recorded yet. Start using the Trade Journal to track performance!")

with col2:
    st.subheader("🔥 Hot Stocks")
    
    # Get watchlist
    watchlist = data_manager.get_watchlist()[:5]  # Top 5
    
    if watchlist:
        watchlist_data = data_manager.get_stock_data(watchlist, period="1d")
        
        hot_stocks = []
        for ticker in watchlist:
            if ticker in watchlist_data:
                info = watchlist_data[ticker]['info']
                signals = data_manager.calculate_signals(watchlist_data[ticker])
                
                hot_stocks.append({
                    'Ticker': ticker,
                    'Price': f"${info.get('regularMarketPrice', 0):.2f}",
                    'Change': f"{info.get('regularMarketChangePercent', 0):.2f}%",
                    'Signal': '🔥' if signals['day_score'] > 70 else '👀'
                })
        
        df = pd.DataFrame(hot_stocks)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("Add stocks to your watchlist to see them here!")

st.markdown("---")

# Quick Actions
st.header("⚡ Quick Actions")

col1, col2, col3, col4 = st.columns(4)

with col1:
    if st.button("🔍 Run AI Scanner", use_container_width=True):
        st.switch_page("pages/AISignals.py")

with col2:
    if st.button("📊 View Patterns", use_container_width=True):
        st.switch_page("pages/patterns.py")

with col3:
    if st.button("📰 Check News", use_container_width=True):
        st.switch_page("pages/news.py")

with col4:
    if st.button("📓 Trade Journal", use_container_width=True):
        st.switch_page("pages/journal.py")

# Trading Tips
with st.expander("💡 Today's Trading Tips"):
    market_open = data_manager.is_market_open()
    
    if market_open:
        st.success("🟢 Market is OPEN")
        st.markdown("""
        - **First 30 minutes**: Watch for opening range breakouts
        - **10:30 AM**: Look for reversals after initial moves
        - **Power Hour (3-4 PM)**: Best time for momentum trades
        """)
    else:
        st.warning("🔴 Market is CLOSED")
        st.markdown("""
        - Review today's trades in your journal
        - Set alerts for tomorrow's watchlist
        - Study patterns that worked today
        """)

# Footer
st.markdown("---")
st.markdown("*Remember: Risk management is key. Never risk more than 2% per trade.*")
