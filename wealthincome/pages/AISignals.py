
import streamlit as st
import pandas as pd
import yfinance as yf

# --- Page Setup ---
st.set_page_config(page_title="🧠 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# --- Pasteable Ticker Input (Finviz style) ---
default_tickers = "TSLA,NVDA,AMD,AAPL,MSFT,AMZN,META,NFLX,GME,PLTR"
if "tickers" not in st.session_state:
    st.session_state["tickers"] = default_tickers

user_input = st.text_input("📋 Paste Tickers from Finviz (comma-separated):", st.session_state["tickers"])
if user_input:
    st.session_state["tickers"] = user_input

tickers = [t.strip().upper() for t in st.session_state["tickers"].split(",") if t.strip()]

# --- How This Screener Works ---
with st.expander("📘 How This Screener Works"):
    st.markdown("""
This tool scans the market for **momentum setups** using the logic below:

### 🧠 Key Metrics:
- **% Change** – today's movement
- **RVOL** – relative volume
- **Short %** – short interest float

### 🧮 AI Score Formula:
```text
AI Score = (% Change × 2) + (RVOL × 10) + (Short % × 2)
```
This composite score helps rank stocks by **momentum + volume + sentiment**.

### 🎯 Signals:
- 🟢 **BUY** if Score ≥ 60
- 🟡 **WATCH** if Score ≥ 45
- 🔴 **AVOID** if Score < 45

💡 Tip: Use this screener before market open or during **power hour** (last hour of trading).

🕰️ **Market Hours Reference:**

| Location       | Market Open | Power Hour       |
|----------------|-------------|------------------|
| Los Angeles 🇺🇸 | 6:30 AM PST | 12:00 – 1:00 PM PST |
| London 🇬🇧      | 8:00 AM GMT | 3:00 – 4:00 PM GMT |
    """)

# --- Filter by Signal ---
signal_filter = st.selectbox("📍 Filter by Signal", options=["All", "BUY", "WATCH", "AVOID"])

# --- Screener Logic ---
data = []
for ticker in tickers:
    try:
        info = yf.Ticker(ticker).info
        price = info.get("regularMarketPrice", 0)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("averageDailyVolume10Day", 1) / info.get("averageVolume", 1)
        short_pct = info.get("shortPercentOfFloat", 0) * 100
        ai_score = (change * 2) + (rvol * 10) + (short_pct * 2)

        if ai_score >= 60:
            signal = "BUY"
        elif ai_score >= 45:
            signal = "WATCH"
        else:
            signal = "AVOID"

        tags = []
        # Top Pick
        tags.append("🏆 Top Pick") if ai_score == max([ai_score] + [x[-1] for x in data]) else None
        # Momentum
        if change >= 3 and rvol >= 2:
            tags.append("🔁 Momentum")
        # Breakout logic placeholder
        # if price > 20-day high (not yet implemented): tags.append("📈 Breakout")

        data.append([ticker, f"${price:.2f}", f"{change:.2f}%", round(rvol, 3), f"{short_pct:.1f}%", round(ai_score, 2), signal, ", ".join(tags)])
    except Exception:
        pass

df = pd.DataFrame(data, columns=["Ticker", "Price", "% Change", "RVOL", "Short %", "AI Score", "Signal", "Tags"])

if signal_filter != "All":
    df = df[df["Signal"] == signal_filter]

# --- Style Function ---
def highlight_signals(row):
    signal = row["Signal"]
    if signal == "BUY":
        return [""]*6 + ["background-color: #16a34a; color: white;", ""]
    elif signal == "WATCH":
        return [""]*6 + ["background-color: #facc15; color: black;", ""]
    elif signal == "AVOID":
        return [""]*6 + ["background-color: #dc2626; color: white;", ""]
    else:
        return [""] * len(row)

if not df.empty:
    styled_df = df.style.apply(highlight_signals, axis=1)
    st.dataframe(styled_df, use_container_width=True)
