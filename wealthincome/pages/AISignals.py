import streamlit as st
import pandas as pd
import yfinance as yf

# Page Setup
st.set_page_config(page_title="🧠 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# Pasteable Ticker Input (Finviz style)
default = "TSLA,NVDA,AMD,AAPL,MSFT,AMZN,META,NFLX,GME,PLTR"
user_input = st.text_input("📋 Paste Tickers from Finviz (comma-separated):", value=default)
tickers = [t.strip().upper() for t in user_input.split(",") if t.strip()]

# How This Screener Works
with st.expander("📘 How This Screener Works"):
    st.markdown("""
This tool scans the market for **momentum setups** using the logic below:

### 🧠 Key Metrics:
- **% Change** — today's movement
- **RVOL** = relative volume
- **Short %** = short interest float

### 🧮 AI Score Formula:
```text
AI Score = (% Change × 2) + (RVOL × 10) + (Short % × 2)
```
This composite score helps rank stocks by momentum + volume + sentiment.

### 🚦 Signals:
- 🟢 **BUY** if Score ≥ 60
- 🟡 **WATCH** if Score ≥ 45
- 🔴 **AVOID** if Score < 45

Use the **signal dropdown below** to focus on actionable opportunities.

🧠 *Tip: Use this screener before market open or during power hour (last hour of trading).*

### 🕰 Market Hours Reference:

| Location      | Market Open | Power Hour      |
|---------------|-------------|-----------------|
| Los Angeles 🇺🇸 | 6:30 AM PST | 12:00 – 1:00 PM PST |
| London 🇬🇧      | 8:00 AM GMT | 3:00 – 4:00 PM GMT  |
""")

# Filter dropdown
signal_filter = st.selectbox("📍 Filter by Signal", options=["All", "BUY", "WATCH", "AVOID"])

# Fetch and compute data
data = []
for ticker in tickers:
    try:
        info = yf.Ticker(ticker).info
        hist = yf.Ticker(ticker).history(period="30d")
        price = info.get("currentPrice", None)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("averageDailyVolume10Day", 1) / max(info.get("averageVolume", 1), 1)
        short_pct = info.get("shortPercentOfFloat", 0) * 100
        ai_score = (change * 2) + (rvol * 10) + (short_pct * 2)

        # Signal
        if ai_score >= 60:
            signal = "BUY"
        elif ai_score >= 45:
            signal = "WATCH"
        else:
            signal = "AVOID"

        # Tags
        tags = []
        if change > 2 and rvol > 1:
            tags.append("🔁 Momentum")
        if len(hist) >= 20 and price > hist['High'][-20:].max():
            tags.append("📈 BREAKOUT")

        data.append({
            "Ticker": ticker,
            "Price": f"${price:.2f}" if price else "-",
            "% Change": f"{change:.2f}%",
            "RVOL": round(rvol, 3),
            "Short %": f"{short_pct:.1f}%",
            "AI Score": round(ai_score, 2),
            "Signal": signal,
            "Tags": ", ".join(tags)
        })
    except:
        pass

df = pd.DataFrame(data)

# Filter logic
if signal_filter != "All":
    df = df[df["Signal"] == signal_filter]

# Color formatting
def color_signal(val):
    if val == "BUY":
        return 'background-color: #27ae60; color: white'
    elif val == "WATCH":
        return 'background-color: #f1c40f; color: black'
    elif val == "AVOID":
        return 'background-color: #e74c3c; color: white'
    return ""

styled_df = df.style.applymap(color_signal, subset=["Signal"])
st.dataframe(styled_df, use_container_width=True)
