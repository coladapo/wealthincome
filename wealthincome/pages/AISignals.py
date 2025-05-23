import streamlit as st
import pandas as pd
import yfinance as yf

st.set_page_config(page_title="🧠 AI Stock Screener", layout="wide")

st.title("🧠 AI Stock Screener")
st.markdown("""
This tool scans top US tickers and highlights potential plays based on:

- **High Relative Volume (RVOL)**
- **Strong Price Momentum**
- **Short % interest**
- 📊 A-style rule logic: `% Change * 2 + RVOL * 10 + Shorts * 2`
""")

# Sample tickers
tickers = ["TSLA", "NVDA", "AAPL", "AMD", "MSFT", "META", "NFLX", "AMZN", "GME", "PLTR"]

data = []

for ticker in tickers:
    try:
        info = yf.Ticker(ticker).info
        price = info.get("currentPrice", 0)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("averageDailyVolume10Day", 0) / max(info.get("averageVolume", 1), 1)
        short_pct = info.get("shortPercentOfFloat", 0) * 100  # convert to %

        # AI score formula
        ai_score = (change * 2) + (rvol * 10) + (short_pct * 2)

        # Signal logic
        if ai_score >= 80:
            signal = "BUY"
        elif ai_score >= 50:
            signal = "WATCH"
        else:
            signal = "AVOID"

        data.append({
            "Ticker": ticker,
            "Price": f"${price:.2f}",
            "% Change": f"{change:.2f}%",
            "RVOL": round(rvol, 3),
            "Short %": f"{short_pct:.1f}%",
            "AI Score": round(ai_score, 2),
            "Signal": signal
        })

    except Exception as e:
        st.warning(f"Error loading data for {ticker}: {e}")

df = pd.DataFrame(data)
df["Signal"] = pd.Categorical(df["Signal"], categories=["BUY", "WATCH", "AVOID"], ordered=True)
df = df.sort_values("Signal")

# Filter by signal
signal_filter = st.selectbox("🎯 Filter by Signal", ["All", "BUY", "WATCH", "AVOID"])
if signal_filter != "All":
    df = df[df["Signal"] == signal_filter]

# Display table
st.dataframe(df, use_container_width=True)
