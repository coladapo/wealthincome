import streamlit as st
import pandas as pd
import yfinance as yf

# ─── Page Setup ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# ─── Pasteable Tickers Input ───────────────────────────────────────────────────
default = (
    "KSS,QBTS,QSI,MARA,SNOW,HIMS,SMCI,FL,ENPH,BL"
)
user_input = st.text_input(
    "📋 Paste Tickers from Finviz (comma-separated):",
    value=default,
)
tickers = [t.strip().upper() for t in user_input.split(",") if t.strip()]

# ─── Explainer ─────────────────────────────────────────────────────────────────
with st.expander("📘 How This Screener Works"):
    st.markdown("""
This tool scans the market for **momentum setups** using:

- **% Change** ×2  
- **RVOL** (rel. vol) ×10  
- **Short %** ×2  

**BUY** ≥ 60, **WATCH** ≥ 45, **AVOID** < 45  
""")

# ─── Filter Control ────────────────────────────────────────────────────────────
selected_signal = st.selectbox(
    "📍 Filter by Signal",
    ["All", "BUY", "WATCH", "AVOID"],
)

# ─── Fetch & Score ─────────────────────────────────────────────────────────────
records = []
for sym in tickers:
    try:
        tk   = yf.Ticker(sym)
        info = tk.info
        hist = tk.history(period="1mo")

        price    = info.get("regularMarketPrice", 0) or 0
        change   = info.get("regularMarketChangePercent", 0) or 0
        rvol     = (info.get("regularMarketVolume", 1) or 1) / (info.get("averageVolume", 1) or 1)
        short_pct= (info.get("shortPercentOfFloat", 0) or 0) * 100
        ai_score = round((change*2) + (rvol*10) + (short_pct*2), 2)

        # build tags
        tags = []
        if change >= 2 and rvol >= 1.5:
            tags.append("🔁 Momentum")
        if not hist.empty and price > hist["High"].rolling(20).max().iloc[-1]:
            tags.append("📈 Breakout")

        records.append({
            "Ticker":    sym,
            "Price":     f"${price:.2f}",
            "% Change":  f"{change:.2f}%",
            "RVOL":      round(rvol, 3),
            "Short %":   f"{short_pct:.1f}%",
            "AI Score":  ai_score,
            "Signal":    "",      # to set below
            "Tags":      ", ".join(tags),
        })
    except Exception:
        pass

df = pd.DataFrame(records)

# ─── Post‑Processing & Display ─────────────────────────────────────────────────
if not df.empty:
    # assign signals
    df["Signal"] = df["AI Score"].apply(
        lambda x: "BUY" if x >= 60 else "WATCH" if x >= 45 else "AVOID"
    )

    # tag top pick
    top = df["AI Score"].idxmax()
    if pd.notna(top):
        base = df.at[top, "Tags"]
        df.at[top, "Tags"] = "🏆 Top Pick" + (", " + base if base else "")

    # filter by dropdown
    if selected_signal != "All":
        df = df[df["Signal"] == selected_signal]

    # sort: BUY→WATCH→AVOID, then by AI Score desc
    order_map = {"BUY": 0, "WATCH": 1, "AVOID": 2}
    df["__order"] = df["Signal"].map(order_map)
    df = df.sort_values(["__order","AI Score"], ascending=[True, False]).drop("__order", axis=1)

    # reorder columns: Signal & Tags come right after Ticker
    cols = ["Ticker", "Signal", "Tags", "Price", "% Change", "RVOL", "Short %", "AI Score"]
    df = df[cols]

    # styling
    def color_sig(v):
        if v=="BUY":   return "background-color: #16a34a; color:white;"
        if v=="WATCH": return "background-color: #facc15; color:black;"
        if v=="AVOID": return "background-color: #dc2626; color:white;"
        return ""
    styled = df.style.applymap(color_sig, subset=["Signal"])
    st.dataframe(styled, use_container_width=True)

else:
    st.warning("No data available. Check your tickers and try again.")
