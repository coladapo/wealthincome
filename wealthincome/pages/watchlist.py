# At the top of AISignals.py, watchlist.py, etc.
from data_manager import data_manager

# Replace direct yfinance calls with:
stock_data = data_manager.get_stock_data(tickers)
signals = data_manager.calculate_signals(stock_data[ticker])

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
