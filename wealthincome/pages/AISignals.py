import streamlit as st
import pandas as pd
import yfinance as yf

try:
    from finvizfinance.screener.overview import Overview
    finviz_available = True
except ImportError:
    finviz_available = False

# Finviz Top Gainers Scrape Function
def get_top_gainers():
    try:
        overview = Overview()
        overview.set_filter(filters=['sh_avgvol_0500', 'sh_relvol_o1.5'])
        overview.set_order('change')
        df = overview.screener_view()
        return df['Ticker'].tolist()
    except Exception as e:
        st.error(f"⚠️ Failed to fetch Finviz data: {e}")
        return []

# Page Setup
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# Session Setup
if "tickers" not in st.session_state:
    st.session_state["tickers"] = "TSLA,NVDA,AMD,AAPL,MSFT,AMZN,META,NFLX,GME,PLTR"

# Pasteable Ticker Input (Finviz style)
user_input = st.text_input("📋 Paste Tickers from Finviz (comma-separated):", value=st.session_state["tickers"])
if user_input:
    st.session_state["tickers"] = user_input

# Add Top Gainers Button
if finviz_available:
    if st.button("🔄 Add Top Gainers from Finviz"):
        top_gainers = get_top_gainers()
        if top_gainers:
            combined = list(set(st.session_state["tickers"].split(",") + top_gainers))
            st.session_state["tickers"] = ",".join(sorted(set(t.strip().upper() for t in combined)))

tickers = [t.strip().upper() for t in st.session_state["tickers"].split(",") if t.strip()]

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
        tag_list.append("")  # placeholder

        if change >= 2 and rvol >= 1.5:
            tag_list.append("🔁 Momentum")

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
    top_idx = df["AI Score"].idxmax()
    if pd.notna(top_idx):
        current_tags = df.at[top_idx, "Tags"]
        df.at[top_idx, "Tags"] = ("🏆 Top Pick" + (", " + current_tags if current_tags else ""))

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
