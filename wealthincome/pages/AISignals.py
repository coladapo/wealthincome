
import streamlit as st
import pandas as pd
import yfinance as yf

# Page Setup
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# Session State for Tickers
if "tickers" not in st.session_state:
    st.session_state["tickers"] = "TSLA,NVDA,AMD,AAPL,MSFT,AMZN,META,NFLX,GME,PLTR"

# Pasteable Input
user_input = st.text_input("📋 Paste Tickers from Finviz (comma-separated):", value=st.session_state["tickers"])
if user_input:
    st.session_state["tickers"] = user_input

tickers = [t.strip().upper() for t in st.session_state["tickers"].split(",") if t.strip()]

# Add Top Gainers Button (Mock Placeholder)
if st.button("🔄 Add Top Gainers from Finviz"):
    try:
        from finvizfinance.screener.overview import Overview
        overview = Overview()
        overview.set_filter("Top Gainers")
        top_df = overview.screener_view()
        new_tickers = top_df["Ticker"].tolist()
        tickers = list(set(tickers + new_tickers))
        st.session_state["tickers"] = ",".join(tickers)
    except Exception as e:
        st.error(f"❌ Failed to fetch Finviz data: {e}")

# Screener Explanation
with st.expander("📘 How This Screener Works"):
    st.markdown("""
This tool scans the market for **momentum setups** using the logic below:

### 📊 Key Metrics:
- **% Change** – today's movement
- **RVOL** – relative volume
- **Short %** – short interest float

### 🧠 AI Score Formula:
```text
AI Score = (% Change × 2) + (RVOL × 10) + (Short % × 2)
```
This composite score ranks stocks by **momentum + volume + sentiment**.

### 🏁 Signals:
- 🟢 **BUY** if Score ≥ 60
- 🟡 **WATCH** if Score ≥ 45
- 🔴 **AVOID** if Score < 45

Use the **signal dropdown below** to focus on actionable opportunities.

🕐 **Tip:** Use this screener before market open or during **power hour** (last hour of trading).

⏰ **Market Hours Reference:**

| Location      | Market Open | Power Hour      |
|---------------|-------------|-----------------|
| Los Angeles 🇺🇸 | 6:30 AM PST | 12:00 – 1:00 PM PST |
| London 🇬🇧     | 8:00 AM GMT | 3:00 – 4:00 PM GMT |
    """)

# Signal Filter
selected_signal = st.selectbox("📍 Filter by Signal", options=["All", "BUY", "WATCH", "AVOID"])

# Process Tickers
data = []
for ticker in tickers:
    try:
        info = yf.Ticker(ticker).info
        hist = yf.Ticker(ticker).history(period="1mo")

        price = info.get("regularMarketPrice", 0)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("regularMarketVolume", 1) / info.get("averageVolume", 1)
        short_pct = info.get("shortPercentOfFloat", 0) * 100

        ai_score = round((change * 2) + (rvol * 10) + (short_pct * 2), 2)

        tags = []

        # Momentum
        if change >= 2 and rvol >= 1.5:
            tags.append("🔁 Momentum")

        # Breakout
        if not hist.empty and price > hist["High"].rolling(20).max().iloc[-1]:
            tags.append("📈 Breakout")

        data.append({
            "Ticker": ticker,
            "Price": f"${price:.2f}",
            "% Change": f"{change:.2f}%",
            "RVOL": round(rvol, 3),
            "Short %": f"{short_pct:.1f}%",
            "AI Score": ai_score,
            "Signal": "",  # to be filled
            "Tags": ", ".join(tags)
        })
    except Exception as e:
        print(f"Error fetching {ticker}: {e}")

df = pd.DataFrame(data)

if not df.empty:
    df["Signal"] = df["AI Score"].apply(lambda x: "BUY" if x >= 60 else "WATCH" if x >= 45 else "AVOID")
    top_idx = df["AI Score"].idxmax()
    if pd.notna(top_idx):
        current_tags = df.at[top_idx, "Tags"]
        df.at[top_idx, "Tags"] = ("🏆 Top Pick" + (", " + current_tags if current_tags else ""))

    if selected_signal != "All":
        df = df[df["Signal"] == selected_signal]

    def highlight(val):
        if val == "BUY":
            return "background-color: #16a34a; color: white"
        elif val == "WATCH":
            return "background-color: #facc15; color: black"
        elif val == "AVOID":
            return "background-color: #dc2626; color: white"
        return ""

    styled = df.style.applymap(highlight, subset=["Signal"])
    st.dataframe(styled, use_container_width=True)
else:
    st.warning("No data available. Please check the tickers.")
