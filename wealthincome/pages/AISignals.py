# AISignals.py
import streamlit as st
import pandas as pd
import yfinance as yf

# ─── 1) Must be first Streamlit command ────────────────────────────────────────
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")

# ─── 2) Title & Instructions ───────────────────────────────────────────────────
st.title("🧠 AI Stock Screener")

# ─── 3) Persistent Ticker Input via session_state ──────────────────────────────
if "tickers" not in st.session_state:
    # initialize with empty or any default you like
    st.session_state.tickers = []

# show the text_input with current list joined by commas
ticker_str = st.text_input(
    "📋 Paste Tickers (comma-separated):",
    value=",".join(st.session_state.tickers),
    help="Enter any tickers you want to scan. They’ll be remembered until you close this tab."
)

# whenever user changes the input, update session_state.tickers
if ticker_str is not None:
    st.session_state.tickers = [
        t.strip().upper() for t in ticker_str.split(",") if t.strip()
    ]

tickers = st.session_state.tickers

# ─── 4) How It Works (optional) ───────────────────────────────────────────────
with st.expander("📘 How This Screener Works"):
    st.markdown("""
- **AI Score** = ( % Change × 2 ) + ( RVOL × 10 ) + ( Short% × 2 )  
- **Signals**:  
  - 🟢 **BUY** if Score ≥ 60  
  - 🟡 **WATCH** if Score ≥ 45  
  - 🔴 **AVOID** if Score < 45  
- **Tags**:  
  - 🏆 Top Pick → highest score  
  - 🔁 Momentum → % Change ≥ 2% & RVOL ≥ 1.5  
  - 📈 Breakout → price > 20‑day high  
    """)

# ─── 5) Signal Filter ─────────────────────────────────────────────────────────
selected_signal = st.selectbox(
    "📍 Filter by Signal", 
    options=["All", "BUY", "WATCH", "AVOID"]
)

# ─── 6) Fetch & Compute ───────────────────────────────────────────────────────
data = []
for t in tickers:
    try:
        tk = yf.Ticker(t)
        info = tk.info
        hist = tk.history(period="1mo")

        price     = info.get("regularMarketPrice", 0) or 0
        change    = info.get("regularMarketChangePercent", 0) or 0
        avg_vol   = info.get("averageVolume", 1) or 1
        rvol      = (info.get("regularMarketVolume", 1) or 1) / avg_vol
        short_pct = (info.get("shortPercentOfFloat", 0) or 0) * 100

        ai_score = round((change*2) + (rvol*10) + (short_pct*2), 2)

        tags = []
        # Momentum
        if change >= 2 and rvol >= 1.5:
            tags.append("🔁 Momentum")
        # Breakout
        if not hist.empty:
            twenty_high = hist["High"].rolling(20).max().iloc[-1]
            if price > twenty_high:
                tags.append("📈 Breakout")

        data.append({
            "Ticker":    t,
            "Price":     f"${price:.2f}",
            "% Change":  f"{change:.2f}%",
            "RVOL":      round(rvol, 3),
            "Short %":   f"{short_pct:.1f}%",
            "AI Score":  ai_score,
            "Tags":      ", ".join(tags)  # Top Pick will be added later
        })

    except Exception as e:
        st.error(f"Error fetching {t}: {e}")

df = pd.DataFrame(data)

if not df.empty:
    # ─── 7) Signal column ──────────────────────────────────────────────────────
    df["Signal"] = df["AI Score"].apply(
        lambda x: "BUY" if x >= 60 else "WATCH" if x >= 45 else "AVOID"
    )

    # ─── 8) Tag the single Top Pick ────────────────────────────────────────────
    top_idx = df["AI Score"].idxmax()
    df.at[top_idx, "Tags"] = (
        "🏆 Top Pick" 
        + (", " + df.at[top_idx, "Tags"] if df.at[top_idx, "Tags"] else "")
    )

    # ─── 9) Apply filter ───────────────────────────────────────────────────────
    if selected_signal != "All":
        df = df[df["Signal"] == selected_signal]

    # ─── 🔽 10) Sort so Top Picks (🏆) & BUYs float up ─────────────────────────
    # Rows containing “🏆” first, then BUY > WATCH > AVOID
    def sort_key(row):
        # Top Pick?
        if "🏆" in row["Tags"]:
            return (0,)
        # BUY vs WATCH vs AVOID
        order = {"BUY": 1, "WATCH": 2, "AVOID": 3}
        return (order.get(row["Signal"], 4), -row["AI Score"])

    df = df.sort_values(by=df.columns, key=lambda col: df.apply(sort_key, axis=1), ascending=True)

    # ─── 11) Styling function ──────────────────────────────────────────────────
    def style_signal(val):
        if val == "BUY":
            return "background-color:#16a34a;color:white"
        if val == "WATCH":
            return "background-color:#facc15;color:black"
        if val == "AVOID":
            return "background-color:#dc2626;color:white"
        return ""

    styled = df.style.applymap(style_signal, subset=["Signal"])
    st.dataframe(styled, use_container_width=True)

else:
    st.warning("No data available. Paste some tickers above and hit Enter.")
