import streamlit as st
import yfinance as yf
import pandas as pd

st.set_page_config(page_title="🧠 AI Screener", layout="wide")
st.title("🧠 AI Stock Screener")

st.markdown("""
This tool scans **top US tickers** and highlights potential plays based on:
- High Relative Volume (RVOL)
- Strong price momentum
- AI-style rule logic: % Change + RVOL + Short %
""")

# Define sample tickers to scan
tickers = ["TSLA", "NVDA", "AMD", "AAPL", "MSFT", "AMZN", "META", "NFLX", "GME", "PLTR", "QSI", "RUN", "CRVW", "ENPH"]

def get_screener_data(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        hist = stock.history(period="5d")
        current_price = info.get("regularMarketPrice", None)
        previous_close = info.get("previousClose", None)
        percent_change = ((current_price - previous_close) / previous_close * 100) if current_price and previous_close else None
        volume = info.get("volume", None)
        avg_volume = info.get("averageVolume", None)
        rvol = round(volume / avg_volume, 2) if volume and avg_volume else None
        short_percent = info.get("shortPercentOfFloat", None)
        signal = ""

        if rvol and percent_change:
            if rvol > 2 and percent_change > 3:
                signal = "🔥 Momentum"
            elif short_percent and short_percent > 0.2:
                signal = "🧨 Squeeze Risk"
            elif percent_change < -3:
                signal = "📉 Pullback"

        return {
            "Ticker": ticker,
            "Price": f"${current_price:.2f}" if current_price else "N/A",
            "% Change": f"{percent_change:.2f}%" if percent_change else "N/A",
            "RVOL": rvol,
            "Short %": f"{short_percent*100:.1f}%" if short_percent else "N/A",
            "AI Tag": signal or "➖ Neutral"
        }
    except:
        return {
            "Ticker": ticker,
            "Price": "Error",
            "% Change": "Error",
            "RVOL": "Error",
            "Short %": "Error",
            "AI Tag": "Error"
        }

# Process data
results = [get_screener_data(t) for t in tickers]
df = pd.DataFrame(results)

# Display table
st.dataframe(df, use_container_width=True)

# Filter option
tag = st.selectbox("🔍 Filter by Tag", ["All", "🔥 Momentum", "🧨 Squeeze Risk", "📉 Pullback"])
if tag != "All":
    st.dataframe(df[df["AI Tag"] == tag], use_container_width=True)
