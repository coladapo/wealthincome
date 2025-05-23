
import streamlit as st
import pandas as pd
import yfinance as yf

# ──────────────────────────────────────────────
# Page Setup
st.set_page_config(page_title="🧠 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# ──────────────────────────────────────────────
# Collapsible How-It-Works Section
with st.expander("📘 How This Screener Works", expanded=False):
    st.markdown("""
This tool scans the market for **momentum setups** using the logic below:

### 📊 Key Metrics:
- **% Change** — price movement today. Positive = bullish.
- **RVOL (Relative Volume)** — volume compared to average. >1 = unusual activity.
- **Short % Interest** — how many people are betting against the stock.

### 🧠 AI Signal Score Formula:
```text
AI Score = (% Change × 2) + (RVOL × 10) + (Short % × 2)
```
This composite score helps rank stocks by **momentum + volume + sentiment**.

### 🏁 Signals:
- 🟢 **BUY** if AI Score ≥ 60
- 🟡 **WATCH** if AI Score ≥ 45
- 🔴 **AVOID** if AI Score < 45

Use the **signal dropdown below** to focus on actionable opportunities.

💡 *Tip: Use this screener before market open or during power hour (last hour of trading).*
    """)

# ──────────────────────────────────────────────
# Ticker List
tickers = ["TSLA", "NVDA", "AMD", "AAPL", "MSFT", "AMZN", "META", "NFLX", "GME", "PLTR"]
data = []

# ──────────────────────────────────────────────
# AI Score Calculation Loop
for ticker in tickers:
    try:
        info = yf.Ticker(ticker).info
        price = info.get("currentPrice", 0)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("averageVolume", 1) / max(info.get("volume", 1), 1)
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

    except Exception:
        pass  # Skip ticker if data fails

# ──────────────────────────────────────────────
# Build DataFrame
df = pd.DataFrame(data)

# Filter by Signal
selected_signal = st.selectbox("📍 Filter by Signal", ["All", "BUY", "WATCH", "AVOID"])
if selected_signal != "All":
    df = df[df["Signal"] == selected_signal]

# Display Table
st.dataframe(df, use_container_width=True)
