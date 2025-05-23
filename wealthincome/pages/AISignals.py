
import streamlit as st
import pandas as pd
import yfinance as yf

# Page Setup
st.set_page_config(page_title="🧠 AI Stock Screener")

# Collapsible How-It-Works Section
with st.expander("📘 How This Screener Works"):
    st.markdown("""
This tool scans the market for **momentum setups** using the logic below:

### 📊 Key Metrics:
- **% Change** = price movement today. Positive = bullish.
- **RVOL (Relative Volume)** = volume compared to average. >1 = unusual activity.
- **Short % Interest** = how many people are betting against the stock.

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

🧠 *Tip: Use this screener before market open or during power hour (last hour of trading).*
""")

# List of tickers
tickers = ["TSLA", "NVDA", "AMD", "AAPL", "MSFT", "AMZN", "META", "NFLX", "GME", "PLTR"]

data = []

# Pull and compute metrics
for ticker in tickers:
    try:
        info = yf.Ticker(ticker).info
        price = info.get("regularMarketPrice", 0)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("averageDailyVolume10Day", 0) / max(info.get("averageVolume", 1), 1)
        short_pct = info.get("shortPercentOfFloat", 0) * 100

        # Score formula
        ai_score = (change * 2) + (rvol * 10) + (short_pct * 2)

        # Signal logic
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
    except Exception as e:
        print(f"Error loading {ticker}: {e}")

# Create DataFrame
df = pd.DataFrame(data)

# Filter by signal
signal_filter = st.selectbox("📍 Filter by Signal", ["All", "BUY", "WATCH", "AVOID"])
if signal_filter != "All":
    df = df[df["Signal"] == signal_filter]

# Apply color styling
def highlight_signal(row):
    color = ""
    if row["Signal"] == "BUY":
        color = "background-color: #28a745; color: white"
    elif row["Signal"] == "WATCH":
        color = "background-color: #ffc107; color: black"
    elif row["Signal"] == "AVOID":
        color = "background-color: #dc3545; color: white"
    return [color if col == "Signal" else "" for col in row.index]

# Display styled table
st.dataframe(df.style.apply(highlight_signal, axis=1), use_container_width=True)
