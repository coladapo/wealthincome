import sys
import os
import streamlit as st # Keep st import if needed early

# --- Start of Fix ---
# Get the absolute path of the directory containing the current script (e.g., .../pages)
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the absolute path of the parent directory (e.g., .../wealthincome)
parent_dir = os.path.dirname(current_dir)

# Add the parent directory to the Python system path if it's not already there
if parent_dir not in sys.path:
    sys.path.append(parent_dir)
# --- End of Fix ---

# Now you can import data_manager
try:
    from data_manager import data_manager
except ImportError:
    st.error("🚨 Failed to import 'data_manager'. Please ensure 'data_manager.py' exists in the root directory and the path is correct.")
    st.stop()

import streamlit as st
import yfinance as yf

st.title("📋 My Watchlist")

# Initialize session state
if "watchlist" not in st.session_state:
    st.session_state.watchlist = ["TSLA", "GME"]

# Add new ticker
with st.form("add_ticker"):
    new_ticker = st.text_input("Add Ticker")
    submitted = st.form_submit_button("Add")
    if submitted and new_ticker:
        st.session_state.watchlist.append(new_ticker.upper())

# Remove tickers
if st.session_state.watchlist:
    remove_ticker = st.selectbox("Remove Ticker", st.session_state.watchlist)
    if st.button("Remove"):
        st.session_state.watchlist.remove(remove_ticker)

# Display current data
data = []
for symbol in st.session_state.watchlist:
    info = yf.Ticker(symbol).info
    data.append({
        "Ticker": symbol,
        "Price": info.get("currentPrice", "N/A"),
        "% Change": round(info.get("regularMarketChangePercent", 0), 2),
        "52W High": info.get("fiftyTwoWeekHigh", "N/A"),
        "52W Low": info.get("fiftyTwoWeekLow", "N/A"),
    })

st.dataframe(data, use_container_width=True)
