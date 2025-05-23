import streamlit as st
import pandas as pd
import yfinance as yf

from analysis_engine.finviz.fetch_api import fetch_tickers_from_screener

# -------- Helper to get FinViz top gainers ----------------
def get_top_gainers():
    try:
        # example URL for top gainers
        url = (
            "https://finviz.com/screener.ashx?"
            "v=111&f=sh_avgvol_o500,ta_perf_1wup&ft=4"
        )
        data = fetch_tickers_from_screener(
            url,
            columns=['ticker','company','sector','industry','country','market_cap','pe','price','change','volume'],
            as_json=False
        )
        df = data['df']  # pandas DataFrame
        return df['ticker'].tolist()
    except Exception as e:
        st.error(f"⚠️ Finviz error: {e}")
        return []

# -------- Main App ----------------
def main():
    st.set_page_config(page_title="🧠 AI Stock Screener", layout="wide")
    st.title("🧠 AI Stock Screener")

    # Session state for tickers
    if 'tickers' not in st.session_state:
        st.session_state['tickers'] = "TSLA,NVDA,AMD,AAPL,MSFT,AMZN,META,NFLX,GME,PLTR"

    user_input = st.text_input(
        "📋 Paste Tickers from Finviz (comma-separated):",
        value=st.session_state['tickers']
    )
    if user_input:
        st.session_state['tickers'] = user_input

    tickers = [t.strip().upper() for t in st.session_state['tickers'].split(",") if t.strip()]

    if st.button("🔄 Add Top Gainers from Finviz"):
        new = get_top_gainers()
        updated = list(dict.fromkeys(tickers + new))
        st.session_state['tickers'] = ",".join(updated)
        tickers = updated

    st.expander("📘 How This Screener Works")

    # Mock scoring/data retrieval
    # In real version, fetch price/% change/RVOL/short%/AI score etc.
    df = pd.DataFrame({
        "Ticker": tickers,
        "Price": ["$340.00"] * len(tickers),
        "% Change": ["1.23%"] * len(tickers),
        "RVOL": [0.8] * len(tickers),
        "Short %": ["2.5%"] * len(tickers),
        "AI Score": [7.5] * len(tickers),
        "Signal": ["BUY" if i % 2 == 0 else "AVOID" for i in range(len(tickers))],
        "Tags": ["🏆 Top Pick" if i==0 else "Momentum" for i in range(len(tickers))]
    })

    # ── Default sort: Top Pick & BUY at top ──
    df["__sort_key"] = df["Tags"].apply(lambda t: 0 if "🏆" in t or ", " in t else 1)
    df["__signal_key"] = df["Signal"].map({"BUY": 1, "AVOID": 0})
    df = (
        df
        .sort_values(by=["__sort_key", "__signal_key"], ascending=[True, False])
        .reset_index(drop=True)
        .drop(columns=["__sort_key", "__signal_key"])
    )

    st.dataframe(df, use_container_width=True)

if __name__ == "__main__":
    main()
