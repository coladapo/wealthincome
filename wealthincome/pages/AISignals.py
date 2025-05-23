import streamlit as st
import pandas as pd
import yfinance as yf

# Page Setup
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# Pasteable Ticker Input (Finviz style)
default = "BTM,BSGM,TSLA,NVDA,AMD,AAPL,MSFT,AMZN,META,NFLX,GME,PLTR"
user_input = st.text_input("📋 Paste Tickers from Finviz (comma-separated):", value=default)
tickers = [t.strip().upper() for t in user_input.split(",") if t.strip()]

# How This Screener Works
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

# Filter dropdown
selected_signal = st.selectbox("📍 Filter by Signal", options=["All", "BUY", "WATCH", "AVOID"])

# Fetch and compute
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

        # Tags
        tag_list = []

        # Top Pick
        tag_list.append("")  # Will assign Top Pick later

        # Momentum: high % change + high rvol
        if change >= 2 and rvol >= 1.5:
            tag_list.append("🔁 Momentum")

        # Breakout Alert: today’s price > 20-day high
        if not hist.empty and price > hist["High"].rolling(20).max().iloc[-1]:
            tag_list.append("📈 Breakout")

        data.append({
            "Ticker": ticker,
            "Price": f"${price:.2f}",
            "% Change": f"{change:.2f}%",
            "RVOL": round(rvol, 3),
            "Short %": f"{short_pct:.1f}%",
            "AI Score": ai_score,
            "Signal": "",  # Set later
            "Tags": ", ".join(tag_list[1:])  # omit placeholder
        })

    except Exception as e:
        print(f"Error fetching data for {ticker}: {e}")

# Create DataFrame
df = pd.DataFrame(data)

if not df.empty:
    # Set Signal
    df["Signal"] = df["AI Score"].apply(lambda x: "BUY" if x >= 60 else "WATCH" if x >= 45 else "AVOID")

    # Set Top Pick tag
    top_idx = df["AI Score"].idxmax()
    if pd.notna(top_idx):
        current_tags = df.at[top_idx, "Tags"]
        df.at[top_idx, "Tags"] = ("🏆 Top Pick" + (", " + current_tags if current_tags else ""))

    # Apply signal filter
    if selected_signal != "All":
        df = df[df["Signal"] == selected_signal]

    # Custom color formatting
    def highlight_signal(val):
        color = ""
        if val == "BUY":
            color = "background-color: #16a34a; color: white;"
        elif val == "WATCH":
            color = "background-color: #facc15; color: black;"
        elif val == "AVOID":
            color = "background-color: #dc2626; color: white;"
        return color

    styled_df = df.style.applymap(highlight_signal, subset=["Signal"])
    st.dataframe(styled_df, use_container_width=True)
else:
    st.warning("No data available. Please check the tickers.")
