import streamlit as st

# Set up page configuration
st.set_page_config(page_title="Trading Hub", layout="wide")

# ✅ Sidebar with working navigation links
with st.sidebar:
    st.header("📊 Navigation")
    st.page_link("Home.py", label="🏠 Dashboard Hub")
    st.page_link("Watchlist.py", label="📋 Watchlist")
    st.page_link("AISignals.py", label="🧠 AI Screener")
    st.page_link("Journal.py", label="📓 Journal")
    st.page_link("Patterns.py", label="📈 Patterns")
    st.page_link("News.py", label="🗞️ News Feed")

# Main dashboard hub content
st.title("📈 Trading Dashboard Hub")
st.markdown("Welcome! Choose a module from the sidebar to begin:")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Today's P&L", "$+342.55", delta="+1.8%")
    st.link_button("Go to Watchlist", "Watchlist.py")

with col2:
    st.metric("Pattern Alerts", "3 Active")
    st.link_button("View Patterns", "Patterns.py")

with col3:
    st.metric("AI Signal", "2 Buys, 1 Sell")
    st.link_button("AI Screener", "AISignals.py")
