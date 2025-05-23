import streamlit as st
import pandas as pd
import yfinance as yf

# --- Page Setup ---
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# --- Pasteable Ticker Input (Finviz style) ---
default_tickers = "TSLA,NVDA,AMD,AAPL,MSFT,AMZN,META,NFLX,GME,PLTR"
if "tickers" not in st.session_state:
    st.session_state["tickers"] = default_tickers

user_input = st.text_input("📋 Paste Tickers from Finviz (comma-separated):", value=st.session_state["tickers"])
if user_input:
    st.session_state["tickers"] = user_input

tickers = [t.strip().upper() for t in st.session_state["tickers"].split(",") if t.strip()]

# --- How This Screener Works ---
with st.expander("📘 How This Screener Works"):
    st.markdown("""
This tool scans the market for **momentum setups** using the logic below:

### 📊 Key Metrics:
- **% Change** – today's movement
- **RVOL** – relative volume
- **Short %** – short interest float

### 🧮 AI Score Formula:
```text
AI Score = (% Change × 2) + (RVOL × 10) + (Short % × 2)
```

This composite score helps rank stocks by **momentum + volume + sentiment**.

### 🏁 Signals:
- 🟢 **BUY** if Score ≥ 60
- 🟡 **WATCH** if Score ≥ 45
- 🔴 **AVOID** if Score < 45

Use the **signal dropdown below** to focus on actionable opportunities.

🧠 Tip: Use this screener before market open or during power hour (last hour of trading).

### 🕰️ Market Hours Reference:

| Location       | Market Open | Power Hour     |
|----------------|-------------|----------------|
| Los Angeles 🇺🇸 | 6:30 AM PST | 12:00 – 1:00 PM PST |
| London 🇬🇧      | 8:00 AM GMT | 3:00 – 4:00 PM GMT |
    """)

# --- Filter UI ---
filter_option = st.selectbox("📍 Filter by Signal", options=["All", "BUY", "WATCH", "AVOID"])

# --- Data Fetching ---
data = []
for ticker in tickers:
    try:
        info = yf.Ticker(ticker).info
        price = info.get("regularMarketPrice", 0)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("averageDailyVolume10Day", 0) / info.get("averageVolume", 1)
        short_pct = info.get("shortPercentOfFloat", 0) * 100 if info.get("shortPercentOfFloat") else 0
        ai_score = (change * 2) + (rvol * 10) + (short_pct * 2)

        data.append({
            "Ticker": ticker,
            "Price": f"${price:.2f}",
            "% Change": f"{change:.2f}%",
            "RVOL": round(rvol, 3),
            "Short %": f"{short_pct:.1f}%",
            "AI Score": round(ai_score, 2),
        })
    except Exception as e:
        print(f"Error fetching data for {ticker}: {e}")

df = pd.DataFrame(data)

# --- Apply Signals ---
def classify_signal(score):
    if score >= 60:
        return "BUY"
    elif score >= 45:
        return "WATCH"
    else:
        return "AVOID"

df["Signal"] = df["AI Score"].apply(classify_signal)

# --- Top Pick Tag ---
if not df.empty:
    top_score = df["AI Score"].max()
    df["Tags"] = df.apply(
        lambda row: "🏆 Top Pick" if row["AI Score"] == top_score and row["Signal"] == "BUY" else "", axis=1
    )

# --- Filter View ---
if filter_option != "All":
    df = df[df["Signal"] == filter_option]

# --- Display Table ---
st.dataframe(df, use_container_width=True, hide_index=True)
