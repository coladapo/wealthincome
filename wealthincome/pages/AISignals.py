import streamlit as st
import pandas as pd
import yfinance as yf

# ──────────────────────────────────────────────
# Page Setup
st.set_page_config(page_title="🧠 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# ──────────────────────────────────────────────
# Pasteable Ticker Input (Finviz style)
default = "TSLA,NVDA,AMD,AAPL,MSFT,AMZN,META,NFLX,GME,PLTR"
user_input = st.text_input("📥 Paste Tickers from Finviz (comma-separated):", default)
tickers = [t.strip().upper() for t in user_input.split(",") if t.strip()]

# ──────────────────────────────────────────────
# How This Screener Works
with st.expander("🧠 How This Screener Works"):
    st.markdown("""
This tool scans any tickers you paste using **momentum logic**:

### 📊 Key Metrics:
- **% Change** — today’s movement
- **RVOL** — relative volume
- **Short %** — short interest float

### 🤖 AI Score Formula:
```text
AI Score = (% Change × 2) + (RVOL × 10) + (Short % × 2)
```

### 🚦 Signals:
- 🟢 **BUY** = Score ≥ 60  
- 🟡 **WATCH** = Score ≥ 45  
- 🔴 **AVOID** = Score < 45

🧠 *Use this during market open or power hour for best results.*

| Location      | Market Open | Power Hour         |
|---------------|-------------|--------------------|
| Los Angeles 🇺🇸 | 6:30 AM PST | 12:00 – 1:00 PM PST |
| London 🇬🇧      | 8:00 AM GMT | 3:00 – 4:00 PM GMT  |
    """)

# ──────────────────────────────────────────────
# Run AI Screener
data = []
for ticker in tickers:
    try:
        info = yf.Ticker(ticker).info
        price = info.get("regularMarketPrice", 0)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("averageDailyVolume10Day", 0) / info.get("averageVolume", 1)
        short_pct = info.get("shortPercentOfFloat", 0) * 100

        score = (change * 2) + (rvol * 10) + (short_pct * 2)

        if score >= 60:
            signal = "BUY"
        elif score >= 45:
            signal = "WATCH"
        else:
            signal = "AVOID"

        data.append({
            "Ticker": ticker,
            "Price": f"${price:.2f}",
            "% Change": f"{change:.2f}%",
            "RVOL": round(rvol, 2),
            "Short %": f"{short_pct:.1f}%",
            "AI Score": round(score, 2),
            "Signal": signal
        })
    except Exception as e:
        st.warning(f"{ticker} failed: {e}")

df = pd.DataFrame(data)

# ──────────────────────────────────────────────
# Filter and Style
signal_filter = st.selectbox("📍 Filter by Signal", ["All", "BUY", "WATCH", "AVOID"])
if signal_filter != "All":
    df = df[df["Signal"] == signal_filter]

def highlight_signal(val):
    color = {"BUY": "#22c55e", "WATCH": "#facc15", "AVOID": "#ef4444"}.get(val, "#ffffff")
    return f"background-color: {color}; color: black"

st.dataframe(df.style.applymap(highlight_signal, subset=["Signal"]), use_container_width=True)
