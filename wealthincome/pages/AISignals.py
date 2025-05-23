import streamlit as st
import pandas as pd
import yfinance as yf

# ─── Page Setup ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# ─── Pasteable Tickers Input ───────────────────────────────────────────────────
default = (
    "AVGO,MRVL,CRWD,BJ,FL,S,Z,PANW,NET,DG,LMT,FDX,CVX,XOM,EQT,"
    "HOOD,COIN,DDOG,VIX,MARA,MDB,XLV,BA,CMG,VRT,TXN,LRCX,GOOGL,SNOW,CRM,DELL,"
    "X,XLK,XLY,WM,BBY,R,M,KSS,TGT,TJX,WMT,AMZN,GFI,MSTR,TSM,NFLX,PLTR,CVS,"
    "UBER,HUBS,HIMS,AMD,SMCI,ANET,VST,BABA,BL,JNJ,MS,ASML,IBM,ALLY,AXP,PG,"
    "META,LLY,ENPH,TMQ,MCO,MSFT,V,BTC,ETH,IBIT,QQQ,NVDA,LOW,COST,VOO,CRVW,"
    "QSI,QBTS,SPY,TSLA,ULTA,EQIX,INTC,BSX,SMH"
)
user_input = st.text_input(
    "📋 Paste Tickers from Finviz (comma-separated):",
    value=default,
)
tickers = [t.strip().upper() for t in user_input.split(",") if t.strip()]

# ─── Screener Logic Explainer ──────────────────────────────────────────────────
with st.expander("📘 How This Screener Works"):
    st.markdown("""
This tool scans the market for **momentum setups** using:

- **% Change** ×2  
- **RVOL** (rel. vol) ×10  
- **Short %** ×2  

**BUY** ≥ 60, **WATCH** ≥ 45, **AVOID** < 45  
""")

# ─── Signal Filter ─────────────────────────────────────────────────────────────
selected_signal = st.selectbox(
    "📍 Filter by Signal",
    options=["All", "BUY", "WATCH", "AVOID"],
)

# ─── Fetch Data & Compute Scores ───────────────────────────────────────────────
records = []
for sym in tickers:
    try:
        tk = yf.Ticker(sym)
        info = tk.info
        hist = tk.history(period="1mo")

        price = info.get("regularMarketPrice", 0) or 0
        change = info.get("regularMarketChangePercent", 0) or 0
        rvol = (info.get("regularMarketVolume", 1) or 1) / (info.get("averageVolume", 1) or 1)
        short_pct = (info.get("shortPercentOfFloat", 0) or 0) * 100

        ai_score = round((change * 2) + (rvol * 10) + (short_pct * 2), 2)

        # build tags
        tags = []
        if change >= 2 and rvol >= 1.5:
            tags.append("🔁 Momentum")
        if not hist.empty and price > hist["High"].rolling(20).max().iloc[-1]:
            tags.append("📈 Breakout")

        records.append({
            "Ticker": sym,
            "Price": f"${price:.2f}",
            "% Change": f"{change:.2f}%",
            "RVOL": round(rvol, 3),
            "Short %": f"{short_pct:.1f}%",
            "AI Score": ai_score,
            "Signal": "",  # filled next
            "Tags": ", ".join(tags),
        })

    except Exception:
        # skip missing or invalid tickers
        pass

df = pd.DataFrame(records)

# ─── Post‑process & Display ────────────────────────────────────────────────────
if not df.empty:
    # 1) assign signals
    df["Signal"] = df["AI Score"].apply(
        lambda x: "BUY" if x >= 60 else "WATCH" if x >= 45 else "AVOID"
    )

    # 2) tag top pick
    top = df["AI Score"].idxmax()
    if pd.notna(top):
        df.at[top, "Tags"] = "🏆 Top Pick" + (", " + df.at[top, "Tags"] if df.at[top, "Tags"] else "")

    # 3) filter by signal dropdown
    if selected_signal != "All":
        df = df[df["Signal"] == selected_signal]

    # 4) sort: all BUY first, then by AI Score descending
    df["__sort_key"] = df["Signal"].map({"BUY": 0, "WATCH": 1, "AVOID": 2})
    df = df.sort_values(["__sort_key", "AI Score"], ascending=[True, False]).drop(columns="__sort_key")

    # 5) style colors
    def _color_sig(val):
        if val == "BUY":   return "background-color: #16a34a; color: white;"
        if val == "WATCH": return "background-color: #facc15; color: black;"
        if val == "AVOID": return "background-color: #dc2626; color: white;"
        return ""

    styled = df.style.applymap(_color_sig, subset=["Signal"])
    st.dataframe(styled, use_container_width=True)

else:
    st.warning("No data available. Check your tickers and try again.")
