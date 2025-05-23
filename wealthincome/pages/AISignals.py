
import streamlit as st
import pandas as pd
import yfinance as yf

# Optional FinViz top gainers fetch
try:
    from finvizfinance.quote import finvizfinance
    finviz_available = True
except ImportError:
    finviz_available = False

# ---------- Page Setup ----------
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# ---------- Session ----------
if "tickers" not in st.session_state:
    st.session_state["tickers"] = "TSLA,NVDA,AMD,AAPL,MSFT,AMZN,META,NFLX,GME,PLTR"

# ---------- Ticker Input ----------
user_input = st.text_input(
    "📋 Paste Tickers from Finviz (comma‑separated):",
    value=st.session_state["tickers"],
)
if user_input:
    st.session_state["tickers"] = user_input

tickers = [t.strip().upper() for t in st.session_state["tickers"].split(",") if t.strip()]

# ---------- FinViz Top Gainers ----------
if finviz_available:
    if st.button("🔄 Add Top Gainers from Finviz"):
        try:
            soup = finvizfinance("Top Gainers")
            top = soup.ticker
            new_tickers = [t for t in top if t not in tickers]
            tickers.extend(new_tickers)
            st.session_state["tickers"] = ",".join(tickers)
            st.success(f"Added {len(new_tickers)} top gainers.")
        except Exception as e:
            st.error(f"❌ finvizfinance error: {e}")
else:
    st.info(
        "FinViz API unavailable. Run `pip install finvizfinance` locally "
        "or ignore this button."
    )

# ---------- How It Works ----------
with st.expander("📘 How This Screener Works"):
    st.markdown(
        """
This tool scans the market for **momentum setups** using the logic below:

### 📊 Key Metrics
* **% Change** – today's movement  
* **RVOL** – relative volume  
* **Short %** – short interest float  

### 🧠 AI Score Formula
```text
AI Score = (% Change × 2) + (RVOL × 10) + (Short % × 2)
```

### 🏁 Signals
* 🟢 **BUY** if Score ≥ 60  
* 🟡 **WATCH** if Score ≥ 45  
* 🔴 **AVOID** if Score < 45  

Use the **signal dropdown** below to focus on actionable opportunities.

🕐 **Tip:** Use this screener before market open or during **power hour** (last hour of trading).
"""
    )

# ---------- Filter dropdown ----------
selected_signal = st.selectbox("📍 Filter by Signal", options=["All", "BUY", "WATCH", "AVOID"])

# ---------- Fetch + Compute ----------
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
        tags = []

        # Top Pick placeholder
        tags.append("")

        # Momentum Tag
        if change >= 2 and rvol >= 1.5:
            tags.append("🔁 Momentum")

        # Breakout Tag
        if not hist.empty and price > hist["High"].rolling(20).max().iloc[-1]:
            tags.append("📈 Breakout")

        data.append(
            {
                "Ticker": ticker,
                "Price": f"${price:.2f}",
                "% Change": f"{change:.2f}%",
                "RVOL": round(rvol, 6),
                "Short %": f"{short_pct:.1f}%",
                "AI Score": ai_score,
                "Signal": "",  # to fill later
                "Tags": ", ".join(tags[1:]),
            }
        )
    except Exception:
        pass

df = pd.DataFrame(data)

if not df.empty:
    # Signals
    df["Signal"] = df["AI Score"].apply(
        lambda x: "BUY" if x >= 60 else ("WATCH" if x >= 45 else "AVOID")
    )

    # Top Pick Tag
    top_idx = df["AI Score"].idxmax()
    if pd.notna(top_idx):
        current = df.at[top_idx, "Tags"]
        df.at[top_idx, "Tags"] = "🏆 Top Pick" + (", " + current if current else "")

    # Default sort: Top Pick / multiple tags on top
    df = (
        df.sort_values(by="Tags", key=lambda s: s.str.count(",") + s.str.contains("🏆"))
        .reset_index(drop=True)
    )

    # Apply filter
    if selected_signal != "All":
        df = df[df["Signal"] == selected_signal]

    # Color formatting
    def highlight_signal(val):
        if val == "BUY":
            return "background-color:#16a34a;color:white"
        if val == "WATCH":
            return "background-color:#facc15;color:black"
        return "background-color:#dc2626;color:white"

    st.dataframe(
        df.style.applymap(highlight_signal, subset=["Signal"]),
        use_container_width=True,
    )
else:
    st.warning("No tickers added.")
