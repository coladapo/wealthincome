import streamlit as st
import pandas as pd
import yfinance as yf

# Attempt FinViz imports
try:
    from analysis_engine.finviz.fetch_api import fetch_tickers_from_screener
    finviz_api_available = True
except ImportError:
    finviz_api_available = False
    try:
        from finvizfinance.quote import finvizfinance
        finvizfinance_available = True
    except ImportError:
        finvizfinance_available = False

# ------------------ Page setup ------------------
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# ------------------ Session state ------------------
if "tickers" not in st.session_state:
    st.session_state["tickers"] = "TSLA,NVDA,AMD,AAPL,MSFT,AMZN,META,NFLX,GME,PLTR"

# ------------------ Ticker input ------------------
user_input = st.text_input("📋 Paste Tickers from Finviz (comma‑separated):",
                           value=st.session_state["tickers"])
if user_input:
    st.session_state["tickers"] = user_input

tickers = [t.strip().upper() for t in st.session_state["tickers"].split(",") if t.strip()]

# ------------------ FinViz top‑gainers helper ------------------
def add_top_gainers():
    """Append today's top gainers from FinViz to the ticker list."""
    new = []
    if finviz_api_available:
        try:
            url = "https://finviz.com/screener.ashx?v=111&f=sh_avgvol_o50,ta_perf_jump&ft=4"
            res = fetch_tickers_from_screener(url, as_json=False)
            new = res["ticker"].tolist() if "ticker" in res else []
        except Exception as e:
            st.error(f"⚠️ FinViz API error: {e}")
    elif finvizfinance_available:
        try:
            q = finvizfinance("Top Gainers")
            df = q.tickers_chart()
            new = df["Ticker"].tolist()
        except Exception as e:
            st.error(f"⚠️ finvizfinance error: {e}")
    else:
        st.warning("FinViz API unavailable. Run `pip install analysis_engine` (preferred) "
                   "or `pip install finvizfinance` then restart.")
    if new:
        combined = list(dict.fromkeys(tickers + new))  # preserve order / deduplicate
        st.session_state["tickers"] = ",".join(combined)
        st.success(f"Added {len(new)} tickers from FinViz.")
    else:
        st.info("No tickers added.")

st.button("🔄 Add Top Gainers from FinViz", on_click=add_top_gainers)

# ------------------ Metrics computation ------------------
rows = []
for tkr in tickers:
    try:
        info = yf.Ticker(tkr).info
        price = info.get("regularMarketPrice", 0)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("regularMarketVolume", 1) / info.get("averageVolume", 1)
        short_pct = info.get("shortPercentOfFloat", 0) * 100

        ai_score = round((change * 2) + (rvol * 10) + (short_pct * 2), 2)

        tags = []

        # Momentum tag
        if change >= 2 and rvol >= 1.5:
            tags.append("🔁 Momentum")

        # Breakout tag (20‑day high)
        hist = yf.Ticker(tkr).history(period="1mo")
        if not hist.empty and price > hist["High"].rolling(20).max().iloc[-1]:
            tags.append("📈 Breakout")

        rows.append(
            {"Ticker": tkr,
             "Price": f"${price:.2f}",
             "% Change": f"{change:.2f}%",
             "RVOL": round(rvol, 3),
             "Short %": f"{short_pct:.1f}%",
             "AI Score": ai_score,
             "Signal": "",   # fill later
             "Tags": ", ".join(tags)}
        )
    except Exception:
        continue

df = pd.DataFrame(rows)
if df.empty:
    st.warning("No data available. Please check the tickers.")
    st.stop()

# Signal classification
df["Signal"] = df["AI Score"].apply(lambda s: "BUY" if s >= 60 else "WATCH" if s >= 45 else "AVOID")

# Add 🏆 Top Pick tag
if not df.empty:
    best_idx = df["AI Score"].idxmax()
    df.at[best_idx, "Tags"] = ("🏆 Top Pick" + (", " + df.at[best_idx,"Tags"] if df.at[best_idx,"Tags"] else ""))

# Sort so rows with 🏆 or multiple tags float to top
df = (df.sort_values(by=["Tags"], key=lambda col: col.apply(lambda x: "🏆" in x or ("," in x)))
        .reset_index(drop=True))

# Optional filter
sig_choice = st.selectbox("📍 Filter by Signal", options=["All", "BUY", "WATCH", "AVOID"], index=0)
if sig_choice != "All":
    df = df[df["Signal"] == sig_choice]

# ------------------ Styling ------------------
def color_signal(val):
    if val == "BUY":
        return "background-color:#16a34a;color:white"
    if val == "WATCH":
        return "background-color:#facc15;color:black"
    return "background-color:#dc2626;color:white"

styled = df.style.applymap(color_signal, subset=["Signal"])
st.dataframe(styled, use_container_width=True)
