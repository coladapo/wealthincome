# Home.py
import streamlit as st
import pandas as pd
from datetime import datetime

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
    st.markdown("""
    - **First 30 minutes**: Watch for opening range breakouts
    - **10:30 AM**: Look for reversals after initial moves
    - **Power Hour (3-4 PM)**: Best time for momentum trades
    - **Risk Management**: Never risk more than 2% per trade
    """)

# Footer
st.markdown("---")
st.markdown("*Remember: Risk management is key. Never risk more than 2% per trade.*")
