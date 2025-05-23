
import streamlit as st
import pandas as pd
import yfinance as yf

# Page Setup
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# Pasteable Ticker Input (Finviz style)
default = "TSLA,NVDA,AMD,AAPL,MSFT,AMZN,META,NFLX,GME,PLTR"
user_input = st.text_input("📋 Paste Tickers from Finviz (comma-separated):", default)
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

This composite score ranks stocks by **momentum + volume + sentiment**.

### 🏁 Signals:
- 🟢**BUY** if Score ≥ 60
- 🟡**WATCH** if Score ≥ 45
- 🔴**AVOID** if Score < 45

📌 Tip: Use this screener before market open or during **power hour** (last hour of trading).

### 🕒 Market Hours Reference:

| Location       | Market Open | Power Hour        |
|----------------|-------------|-------------------|
| Los Angeles 🇺🇸 | 6:30 AM PST | 12:00 – 1:00 PM PST |
| London 🇬🇧       | 8:00 AM GMT | 3:00 – 4:00 PM GMT |
""")

# Screener Logic
data = []
max_score = -1
top_pick = None

for ticker in tickers:
    try:
        info = yf.Ticker(ticker).info
        hist = yf.Ticker(ticker).history(period="1mo")

        price = info.get("regularMarketPrice", 0)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("regularMarketVolume", 0) / info.get("averageVolume", 1)
        short_pct = info.get("shortPercentOfFloat", 0) * 100

        # Compute score
        ai_score = round((change * 2) + (rvol * 10) + (short_pct * 2), 2)

        # Track top scorer
        if ai_score > max_score:
            max_score = ai_score
            top_pick = ticker

        # Signal logic
        if ai_score >= 60:
            signal = "BUY"
        elif ai_score >= 45:
            signal = "WATCH"
        else:
            signal = "AVOID"

        # Tags
        tags = []
        if change >= 2 and rvol >= 1.0:
            tags.append("🔁 Momentum")
        if hist is not None and not hist.empty and price > hist["High"].max():
            tags.append("📈 Breakout")

        data.append({
            "Ticker": ticker,
            "Price": f"${price:.2f}",
            "% Change": f"{change:.2f}%",
            "RVOL": round(rvol, 3),
            "Short %": f"{short_pct:.1f}%",
            "AI Score": ai_score,
            "Signal": signal,
            "Tags": ", ".join(tags)
        })
    except Exception as e:
        data.append({
            "Ticker": ticker,
            "Price": "N/A",
            "% Change": "N/A",
            "RVOL": "N/A",
            "Short %": "N/A",
            "AI Score": "N/A",
            "Signal": "Error",
            "Tags": str(e)
        })

df = pd.DataFrame(data)

# 🏆 Highlight Top Pick
if not df.empty and top_pick:
    df.loc[df["Ticker"] == top_pick, "Tags"] += " 🏆 Top Pick"

# Filter
selected = st.selectbox("📍 Filter by Signal", ["All"] + df["Signal"].unique().tolist())
if selected != "All":
    df = df[df["Signal"] == selected]

st.dataframe(df, use_container_width=True)

