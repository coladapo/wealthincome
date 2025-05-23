import streamlit as st
import pandas as pd
import yfinance as yf

# FinViz integration via stock-analysis-engine
try:
    from analysis_engine.finviz.fetch_api import fetch_tickers_from_screener
    finviz_available = True
except ImportError:
    finviz_available = False

# Page setup
st.set_page_config(page_title="🧠 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# Initialize tickers list
if "tickers" not in st.session_state:
    st.session_state["tickers"] = ["TSLA", "NVDA", "AMD", "AAPL", "MSFT", "AMZN", "META", "NFLX", "GME", "PLTR"]

# Pasteable ticker input
user_input = st.text_input("📋 Paste Tickers from Finviz (comma-separated):", value=",".join(st.session_state["tickers"]))
if user_input:
    st.session_state["tickers"] = [t.strip().upper() for t in user_input.split(",") if t.strip()]

# Fetch top gainers from FinViz
def get_top_gainers():
    try:
        url = (
            "https://finviz.com/screener.ashx?"
            "v=111&f=sh_avgvol_o500,fa_perf_1wup&ft=4"
        )
        df = fetch_tickers_from_screener(
            url=url,
            columns=["ticker", "change"],
            soup_selector="td.screener-body-table-nw",
            as_json=False
        )
        return df["ticker"].tolist()
    except Exception as e:
        st.error(f"⚠️ FinViz error: {e}")
        return []

if finviz_available:
    if st.button("🪙 Add Top Gainers from Finviz"):
        new = get_top_gainers()
        combined = st.session_state["tickers"] + [t.upper() for t in new]
        # de-duplicate while preserving order
        updated = list(dict.fromkeys(combined))
        st.session_state["tickers"] = updated

# Gather data
data = []
for t in st.session_state["tickers"]:
    try:
        stock = yf.Ticker(t)
        info = stock.info
        data.append({
            "Ticker": t,
            "Price": info.get("regularMarketPrice"),
            "% Change": info.get("regularMarketChangePercent"),
            "RVOL": None,
            "Short %": None,
            "AI Score": None,
            "Signal": None,
            "Tags": None
        })
    except Exception:
        continue

df = pd.DataFrame(data)

# Default sort: keep 🏆 Top Pick rows at top
df = (
    df.sort_values(
        by=["Tags"],
        key=lambda s: s.fillna("").str.contains("🏆"),
        ascending=False
    )
    .reset_index(drop=True)
)

# Display
st.dataframe(df, use_container_width=True)
