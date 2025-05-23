# AISignals.py
import streamlit as st
import pandas as pd
import yfinance as yf

# ─── 1) This must be your very first Streamlit command ────────────────────────
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")

# ─── 2) App Title ─────────────────────────────────────────────────────────────
st.title("🧠 AI Stock Screener")

# ─── 3) Persisted Ticker Input ────────────────────────────────────────────────
if "tickers" not in st.session_state:
    st.session_state.tickers = []

# Show text_input with current list
ticker_str = st.text_input(
    "📋 Paste Tickers (comma-separated):",
    value=",".join(st.session_state.tickers),
    help="Any tickers you paste here will stay in this session until you close the tab."
)

# Whenever it changes, update the session list
if ticker_str is not None:
    st.session_state.tickers = [
        t.strip().upper() for t in ticker_str.split(",") if t.strip()
    ]

tickers = st.session_state.tickers

# ─── 4) How it Works (optional) ──────────────────────────────────────────────
with st.expander("📘 How This Screener Works"):
    st.markdown("""
- **AI Score** = (% Change × 2) + (RVOL × 10) + (Short% × 2)  
- **Signals**  
  - 🟢 BUY if Score ≥ 60  
  - 🟡 WATCH if Score ≥ 45  
  - 🔴 AVOID if Score < 45  
- **Tags**  
  - 🏆 Top Pick → highest score each refresh  
  - 🔁 Momentum → % Change ≥ 2% & RVOL ≥ 1.5  
  - 📈 Breakout → price > 20‑day high  
    """)

# ─── 5) Signal Filter ───────────────────────────────────────────────────────
selected_signal = st.selectbox(
    "📍 Filter by Signal", ["All", "BUY", "WATCH", "AVOID"]
)

# ─── 6) Fetch, Score & Tag ────────────────────────────────────────────────────
records = []
for sym in tickers:
    try:
        tk   = yf.Ticker(sym)
        info = tk.info
        hist = tk.history(period="1mo")

        price   = info.get("regularMarketPrice", 0) or 0
        change  = info.get("regularMarketChangePercent", 0) or 0
        avg_vol = info.get("averageVolume", 1) or 1
        rvol    = (info.get("regularMarketVolume", 1) or 1) / avg_vol
        shortp  = (info.get("shortPercentOfFloat", 0) or 0) * 100

        score = round((change * 2) + (rvol * 10) + (shortp * 2), 2)

        tags = []
        if change >= 2 and rvol >= 1.5:
            tags.append("🔁 Momentum")
        if not hist.empty:
            high20 = hist["High"].rolling(20).max().iloc[-1]
            if price > high20:
                tags.append("📈 Breakout")

        records.append({
            "Ticker":    sym,
            "Price":     f"${price:.2f}",
            "% Change":  f"{change:.2f}%",
            "RVOL":      round(rvol,3),
            "Short %":   f"{shortp:.1f}%",
            "AI Score":  score,
            "Tags":      ", ".join(tags)  # Top Pick added below
        })

    except Exception as e:
        st.error(f"Error fetching {sym}: {e}")

df = pd.DataFrame(records)

if not df.empty:
    # ─── 7) Derive Signal ──────────────────────────────────────────────────────
    df["Signal"] = df["AI Score"].apply(
        lambda x: "BUY" if x >= 60 else "WATCH" if x >= 45 else "AVOID"
    )

    # ─── 8) Mark Top Pick ──────────────────────────────────────────────────────
    top_i = df["AI Score"].idxmax()
    df.at[top_i, "Tags"] = "🏆 Top Pick" + (
        (", " + df.at[top_i, "Tags"]) if df.at[top_i, "Tags"] else ""
    )

    # ─── 9) Apply User Filter ─────────────────────────────────────────────────
    if selected_signal != "All":
        df = df[df["Signal"] == selected_signal]

    # ─── 🔽 10) Custom Sort ───────────────────────────────────────────────────
    def sort_key(row):
        # Top Pick first
        if "🏆" in row["Tags"]:
            return (0, -row["AI Score"])
        # Then BUY, WATCH, AVOID
        order = {"BUY":1, "WATCH":2, "AVOID":3}
        return (order.get(row["Signal"], 4), -row["AI Score"])

    # build a temporary sort-key column, sort, then drop it
    df["_sort"] = df.apply(sort_key, axis=1)
    df = df.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)

    # ─── 11) Styling ───────────────────────────────────────────────────────────
    def style_sig(v):
        if v=="BUY":   return "background-color:#16a34a;color:white"
        if v=="WATCH": return "background-color:#facc15;color:black"
        if v=="AVOID": return "background-color:#dc2626;color:white"
        return ""

    st.dataframe(df.style.applymap(style_sig, subset=["Signal"]),
                 use_container_width=True)

else:
    st.warning("No data available. Paste some tickers above and press Enter.")
