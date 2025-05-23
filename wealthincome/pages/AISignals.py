import streamlit as st
import pandas as pd
import yfinance as yf

# Page Setup
st.set_page_config(page_title="🧠 AI Stock Screener")

# Collapsible How-It-Works Section
with st.expander("📘 How This Screener Works"):
    st.markdown("""
This tool scans the market for **momentum setups** using the logic below:

### 🧠 Key Metrics:
- **% Change** — price movement today. Positive = bullish.
- **RVOL (Relative Volume)** — volume compared to average. >1 = unusual activity.
- **Short % Interest** — how many people are betting against the stock.

### 🧮 AI Signal Score Formula:
```text
AI Score = (% Change × 2) + (RVOL × 10) + (Short % × 2)
```
This composite score helps rank stocks by **momentum + volume + sentiment**.

### 🏁 Signals:
- 🟢 **BUY** if AI Score ≥ 60
- 🟡 **WATCH** if AI Score ≥ 45
- 🔴 **AVOID** if AI Score < 45

Use the **signal dropdown below** to focus on actionable opportunities.

🕒 **Tip**: Use this screener before market open or during **power hour** (last hour of trading).

---

### ⏰ Market Hours Reference:
| Location       | Market Open      | Power Hour           |
|----------------|------------------|-----------------------|
| Los Angeles 🇺🇸 | 6:30 AM PST      | 12:00 – 1:00 PM PST   |
| London 🇬🇧     | 8:00 AM GMT      | 3:00 – 4:00 PM GMT    |
""")

# Sample tickers
tickers = ["TSLA", "NVDA", "AMD", "AAPL", "MSFT", "AMZN", "META", "NFLX", "GME", "PLTR"]

data = []

for ticker in tickers:
    try:
        info = yf.Ticker(ticker).info
        price = info.get("currentPrice", 0)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("averageDailyVolume10Day", 0) / max(info.get("averageVolume", 1), 1)
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

    except Exception as e:
        print(f"Error with {ticker}: {e}")

# Convert to DataFrame
df = pd.DataFrame(data)

# Dropdown filter
signal_filter = st.selectbox("🎯 Filter by Signal", ["All"] + df["Signal"].unique().tolist())

if signal_filter != "All":
    df = df[df["Signal"] == signal_filter]

# Display Table
st.dataframe(df.reset_index(drop=True), use_container_width=True)
