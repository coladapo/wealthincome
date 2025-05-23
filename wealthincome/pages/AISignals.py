import streamlit as st
import pandas as pd
import yfinance as yf

# ------------------ Page Setup ------------------
st.set_page_config(page_title="🧠 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# ------------------ Ticker Input Persistence ------------------
default_tickers = "TSLA,NVDA,AMD,AAPL,MSFT,AMZN,META,NFLX,GME,PLTR"

if "tickers" not in st.session_state:
    st.session_state.tickers = default_tickers

user_input = st.text_input("📋 Paste Tickers from Finviz (comma-separated):", st.session_state.tickers)
if user_input:
    st.session_state.tickers = user_input

tickers = [t.strip().upper() for t in st.session_state.tickers.split(",")]

# ------------------ How This Screener Works ------------------
with st.expander("📘 How This Screener Works"):
    st.markdown("""
This tool scans the market for **momentum setups** using the logic below:

### 📊 Key Metrics:
- **% Change** — today's movement
- **RVOL** (Relative Volume) — volume vs average
- **Short %** — short interest float

### 🧠 AI Score Formula:
```text
AI Score = (% Change × 2) + (RVOL × 10) + (Short % × 2)
```

This composite score helps rank stocks by **momentum + volume + sentiment**.

### 🚦 Signals:
- 🟢**BUY** if Score ≥ 60
- 🟡**WATCH** if Score ≥ 45
- 🔴**AVOID** if Score < 45

📌 Use the dropdown to focus on actionable opportunities.

⏰ **Market Hours Reference:**

| Location       | Market Open | Power Hour       |
|----------------|--------------|------------------|
| Los Angeles 🇺🇸 | 6:30 AM PST  | 12:00–1:00 PM PST |
| London 🇬🇧      | 8:00 AM GMT  | 3:00–4:00 PM GMT  |
    """)

# ------------------ Screener Logic ------------------
data = []
top_score = 0
top_ticker = ""

for ticker in tickers:
    try:
        info = yf.Ticker(ticker).info
        price = info.get("regularMarketPrice", 0)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("averageDailyVolume10Day", 1) / info.get("averageVolume", 1)
        short_pct = info.get("shortPercentOfFloat", 0) * 100

        ai_score = round((change * 2) + (rvol * 10) + (short_pct * 2), 2)

        signal = "BUY" if ai_score >= 60 else "WATCH" if ai_score >= 45 else "AVOID"

        tags = []
        if ai_score > top_score:
            top_score = ai_score
            top_ticker = ticker

        if change > 5 and rvol > 2:
            tags.append("🔁 Momentum")

        hist = yf.Ticker(ticker).history(period="1mo")
        if not hist.empty:
            recent_close = hist["Close"][-1]
            last_20_high = hist["High"][-20:].max()
            if recent_close > last_20_high:
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
    except Exception:
        continue

# Mark top pick
for row in data:
    if row["Ticker"] == top_ticker:
        row["Tags"] = "🏆 Top Pick" + (", " + row["Tags"] if row["Tags"] else "")

df = pd.DataFrame(data)

# ------------------ UI Controls ------------------
signal_filter = st.selectbox("📍 Filter by Signal", ["All", "BUY", "WATCH", "AVOID"])
if signal_filter != "All":
    df = df[df["Signal"] == signal_filter]

st.dataframe(df, use_container_width=True)
