import streamlit as st

# Set up page configuration
st.set_page_config(page_title="Trading Hub", layout="wide")

# Main dashboard hub content
st.title("📈 Trading Dashboard Hub")
st.markdown("Welcome! Choose a module from the sidebar to begin:")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Today's P&L", "$+342.55", delta="+1.8%")
    st.markdown("📋 Check out your watchlist in the sidebar.")

with col2:
    st.metric("Pattern Alerts", "3 Active")
    st.markdown("📈 View chart setups via sidebar.")

with col3:
    st.metric("AI Signal", "2 Buys, 1 Sell")
    st.markdown("🧠 Screen fresh tickers with AI logic.")
