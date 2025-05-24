# At the top of AISignals.py, watchlist.py, etc.
from data_manager import data_manager

# Replace direct yfinance calls with:
stock_data = data_manager.get_stock_data(tickers)
signals = data_manager.calculate_signals(stock_data[ticker])


import streamlit as st

st.title('📓 Trade Journal')
