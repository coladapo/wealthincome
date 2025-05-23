
import streamlit as st
import pandas as pd
import yfinance as yf

st.set_page_config(page_title="🧠 AI Stock Screener", layout="wide")

st.title("🧠 AI Stock Screener")
st.markdown("""
### 🧠 How This Screener Works

This tool scans the market for **momentum setups** using the logic below:

#### 📊 Key Metrics:
- **% Change** — price movement today. Positive = bullish.
- **RVOL (Relative Volume)** — volume compared to average. >1 = unusual activity.
- **Short % Interest** — how many people are betting against the stock.

#### 🧪 AI Signal Score Formula:
```
AI Score = (% Change × 2) + (RVOL × 10) + (Short % × 2)
```

This composite score helps rank stocks by momentum + volume + sentiment.

#### 🏁 Signals:
- 🟩 **BUY** if AI Score ≥ 60
- 🟨 **WATCH** if AI Score ≥ 45
- 🟥 **AVOID** if AI Score < 45

Use the **signal dropdown below** to focus on actionable opportunities.
""")

st.caption("📘 Tip: Use this screener before market open or during power hour (last hour of trading).")

tickers = ["TSLA", "NVDA", "AMD", "AAPL", "MSFT", "AMZN", "META", "NFLX", "GME", "PLTR"]

data = []
for ticker in tickers:
    try:
        info = yf.Ticker(ticker).info
        price = info.get("currentPrice", 0)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("threeMonthAverageTradingVolume", 0) / info.get("averageDailyVolume10Day", 1)
        short_pct = info.get("shortPercentOfFloat", 0) * 100
        ai_score = (change * 2) + (rvol * 10) + (short_pct * 2)

        if ai_score >= 60:
            signal = "BUY"
        elif ai_score >= 45:
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
    except:
        continue

df = pd.DataFrame(data)
signal_filter = st.selectbox("📍 Filter by Signal", ["All"] + sorted(df["Signal"].unique()))
if signal_filter != "All":
    df = df[df["Signal"] == signal_filter]

st.dataframe(df, use_container_width=True)
