import streamlit as st
import pandas as pd
import yfinance as yf

# ─── Page Setup ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AISignals")

# ─── Pasteable Ticker Input ────────────────────────────────────────────────────
default = "KSS,QBTS,QSI,MARA,SNOW,HIMS,SMCI,FL,ENPH,BL"
user_input = st.text_input(
    "📋 Paste Tickers from Finviz (comma-separated):", 
    value=default
)
tickers = [t.strip().upper() for t in user_input.split(",") if t.strip()]

# ─── How This Screener Works ──────────────────────────────────────────────────
with st.expander("📘 How This Screener Works"):
    st.markdown("""
This tool scans the market for **momentum setups** using three metrics:

1. **% Change** – today’s price move  
2. **RVOL** – relative volume (= today’s volume / avg. volume)  
3. **Short %** – short interest as % of float  

🧠 **AI Score** = (% Change × 2) + (RVOL × 10) + (Short % × 2)

🏁 **Signals**  
- 🟢 **BUY** if Score ≥ 60  
- 🟡 **WATCH** if Score ≥ 45  
- 🔴 **AVOID** if Score < 45  

🔖 **Tags Logic**  
- 🏆 **Top Pick** – highest AI Score in the list  
- 🔁 **Momentum** – % Change ≥ 2% **and** RVOL ≥ 1.5  
- 📈 **Breakout** – price > 20‑day high  

Use the **signal dropdown** below to filter to BUY, WATCH, or AVOID.
    """)

# ─── Filter Dropdown ───────────────────────────────────────────────────────────
selected_signal = st.selectbox("📍 Filter by Signal", ["All", "BUY", "WATCH", "AVOID"])

# ─── Fetch Data & Compute Scores ───────────────────────────────────────────────
data = []
for ticker in tickers:
    try:
        tkr = yf.Ticker(ticker)
        info = tkr.info
        hist = tkr.history(period="1mo")

        price     = info.get("regularMarketPrice", 0) or 0
        change    = info.get("regularMarketChangePercent", 0) or 0
        rvol      = (info.get("regularMarketVolume", 1) or 1) / (info.get("averageVolume", 1) or 1)
        short_pct = (info.get("shortPercentOfFloat", 0) or 0) * 100

        ai_score = round((change * 2) + (rvol * 10) + (short_pct * 2), 2)

        # build tags
        tags = []
        if change >= 2 and rvol >= 1.5:
            tags.append("🔁 Momentum")
        if not hist.empty and price > hist["High"].rolling(20).max().iloc[-1]:
            tags.append("📈 Breakout")

        data.append({
            "Ticker": ticker,
            "Price":    f"${price:.2f}",
            "% Change": f"{change:.2f}%",
            "RVOL":      round(rvol, 3),
            "Short %":   f"{short_pct:.1f}%",
            "AI Score": ai_score,
            "Signal":   "",      # placeholder
            "Tags":     ", ".join(tags)
        })

    except Exception as e:
        # silently skip bad tickers
        print(f"Error fetching {ticker}: {e}")

# ─── Build DataFrame ───────────────────────────────────────────────────────────
df = pd.DataFrame(data)
if not df.empty:
    # assign Signal
    df["Signal"] = df["AI Score"].apply(
        lambda x: "BUY" if x >= 60 else "WATCH" if x >= 45 else "AVOID"
    )

    # tag the top AI Score
    top = df["AI Score"].idxmax()
    if pd.notna(top):
        df.at[top, "Tags"] = "🏆 Top Pick" + (", " + df.at[top, "Tags"] if df.at[top, "Tags"] else "")

    # filter by signal dropdown
    if selected_signal != "All":
        df = df[df["Signal"] == selected_signal]

    # sort: all BUY first by descending AI Score
    df["is_buy"] = df["Signal"] == "BUY"
    df = df.sort_values(
        by=["is_buy", "AI Score"], 
        ascending=[False, False]
    ).drop(columns="is_buy").reset_index(drop=True)

    # reorder columns: Ticker, Signal, Tags, then the rest
    cols = ["Ticker", "Signal", "Tags", "Price", "% Change", "RVOL", "Short %", "AI Score"]
    df = df[cols]

    # highlight colors for Signal
    def highlight_signal(val):
        if val == "BUY":
            return "background-color: #16a34a; color: white;"
        if val == "WATCH":
            return "background-color: #facc15; color: black;"
        if val == "AVOID":
            return "background-color: #dc2626; color: white;"
        return ""

    styled = df.style.applymap(highlight_signal, subset=["Signal"])
    st.dataframe(styled, use_container_width=True)

else:
    st.warning("No data available. Please check your tickers.")
