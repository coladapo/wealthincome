import streamlit as st
import pandas as pd
import yfinance as yf

try:
    from finvizfinance.screener.overview import Overview
    finviz_available = True
except ImportError:
    finviz_available = False

# Page Setup
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# Session Setup
if "tickers" not in st.session_state:
    st.session_state["tickers"] = "TSLA,NVDA,AMD,AAPL,MSFT,AMZN,META,NFLX,GME,PLTR"

# Pasteable Ticker Input (Finviz style)
user_input = st.text_input("📋 Paste Tickers from Finviz (comma-separated):", value=st.session_state["tickers"])
tickers = [t.strip().upper() for t in user_input.split(",") if t.strip()]
st.session_state["tickers"] = ",".join(tickers)

# 📈 Add Top Gainers Button
if finviz_available:
    if st.button("🔄 Add Top Gainers from Finviz"):
        try:
            overview = Overview()
            overview.set_filter(filters_dict={"top_gainers": True})
            top_df = overview.screener_view()
            top_tickers = top_df["Ticker"].tolist()
            new_tickers = [t.strip().upper() for t in top_tickers if t.strip()]
            updated_tickers = list(set(tickers + new_tickers))
            st.session_state["tickers"] = ",".join(updated_tickers)
            tickers = updated_tickers
            st.success(f"✅ Added {len(new_tickers)} top gainers from Finviz!")
        except Exception as e:
            st.error(f"❌ Failed to fetch Finviz data: {e}")

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

        tag_list = []

        # Top Pick placeholder
        tag_list.append("")

        # Momentum
        if change >= 2 and rvol >= 1.5:
            tag_list.append("🔁 Momentum")

        # Breakout
        if not hist.empty and price > hist["High"].rolling(20).max().iloc[-1]:
            tag_list.append("📈 Breakout")

        data.append({
            "Ticker": ticker,
            "Price": f"${price:.2f}",
            "% Change": f"{change:.2f}%",
            "RVOL": round(rvol, 3),
            "Short %": f"{short_pct:.1f}%",
            "AI Score": ai_score,
            "Signal": "",
            "Tags": ", ".join(tag_list[1:])
        })
    except Exception as e:
        print(f"Error fetching data for {ticker}: {e}")

df = pd.DataFrame(data)

if not df.empty:
    df["Signal"] = df["AI Score"].apply(lambda x: "BUY" if x >= 60 else "WATCH" if x >= 45 else "AVOID")

    # Set Top Pick
    top_idx = df["AI Score"].idxmax()
    if pd.notna(top_idx):
        current_tags = df.at[top_idx, "Tags"]
        df.at[top_idx, "Tags"] = "🏆 Top Pick" + (", " + current_tags if current_tags else "")

    if selected_signal != "All":
        df = df[df["Signal"] == selected_signal]

    def highlight_signal(val):
        if val == "BUY":
            return "background-color: #16a34a; color: white;"
        elif val == "WATCH":
            return "background-color: #facc15; color: black;"
        elif val == "AVOID":
            return "background-color: #dc2626; color: white;"
        return ""

    styled_df = df.style.applymap(highlight_signal, subset=["Signal"])
    st.dataframe(styled_df, use_container_width=True)
else:
    st.warning("No data available. Please check the tickers.")
