import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go

st.set_page_config(page_title="📋 Watchlist V2", layout="wide")
st.title("📋 Smart Watchlist with AI Signals")

# Session state for tickers
if "watchlist" not in st.session_state:
    st.session_state.watchlist = ["QSI", "CRWV"]

# Form to add ticker
with st.form("add_ticker"):
    new_ticker = st.text_input("Add Ticker Symbol", "").upper()
    submit = st.form_submit_button("Add")
    if submit and new_ticker and new_ticker not in st.session_state.watchlist:
        try:
            _ = yf.Ticker(new_ticker).info
            st.session_state.watchlist.append(new_ticker)
            st.success(f"Added {new_ticker}")
        except:
            st.error("Ticker not found or data not available.")

# Function to get stats
def get_stock_data(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        hist = stock.history(period="5d")
        current_price = info.get("regularMarketPrice", None)
        previous_close = info.get("previousClose", None)
        percent_change = ((current_price - previous_close) / previous_close * 100) if current_price and previous_close else None
        rsi = "48"  # Placeholder
        short_percent = info.get("shortPercentOfFloat", None)
        volume = info.get("volume", None)
        avg_volume = info.get("averageVolume", None)
        rvol = round(volume / avg_volume, 2) if volume and avg_volume else None
        ai_signal = "✅ BUY" if percent_change and percent_change > 2 else "❌ WAIT"
        return {
            "Ticker": ticker,
            "Price": f"${current_price:.2f}" if current_price else "N/A",
            "% Change": f"{percent_change:.2f}%" if percent_change else "N/A",
            "RSI": rsi,
            "Short %": f"{short_percent*100:.1f}%" if short_percent else "N/A",
            "RVOL": rvol,
            "AI Signal": ai_signal
        }
    except:
        return {
            "Ticker": ticker,
            "Price": "Error",
            "% Change": "Error",
            "RSI": "Error",
            "Short %": "Error",
            "RVOL": "Error",
            "AI Signal": "Error"
        }

# Build table
data = [get_stock_data(t) for t in st.session_state.watchlist]
df = pd.DataFrame(data)

# Render smart table
st.dataframe(df, use_container_width=True)

# Chart preview
selected = st.selectbox("📊 View Chart for Ticker", st.session_state.watchlist)
if selected:
    hist = yf.Ticker(selected).history(period="7d", interval="30m")
    fig = go.Figure(data=[go.Candlestick(
        x=hist.index,
        open=hist['Open'],
        high=hist['High'],
        low=hist['Low'],
        close=hist['Close']
    )])
    fig.update_layout(title=f"{selected} - Last 7 Days", xaxis_title="Date", yaxis_title="Price")
    st.plotly_chart(fig, use_container_width=True)
