import streamlit as st
import pandas as pd
import datetime
import os

# ---- Setup ----
TRADE_LOG = "paper_trades.csv"  # Save at root for Streamlit Cloud compatibility

# ---- Load or Create Trade Log ----
if os.path.exists(TRADE_LOG):
    trades = pd.read_csv(TRADE_LOG)
else:
    trades = pd.DataFrame(columns=[
        "Date", "Ticker", "Entry Price", "Exit Price", "Type", "Result", "Notes"
    ])

# ---- Streamlit UI ----
st.title("🧾 Paper Trading Agent")

st.subheader("➕ Add a Simulated Trade")
ticker = st.text_input("Ticker (e.g. AAPL)", "AAPL")
entry = st.number_input("Entry Price", value=100.0)
exit_ = st.number_input("Target Exit Price", value=105.0)
trade_type = st.selectbox("Trade Type", ["Day Trade", "Swing Trade"])
notes = st.text_area("Notes", "Triggered by dashboard signal")

if st.button("💾 Add Trade"):
    new_trade = {
        "Date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Ticker": ticker.upper(),
        "Entry Price": entry,
        "Exit Price": exit_,
        "Type": trade_type,
        "Result": "Pending",
        "Notes": notes
    }
    trades = trades.append(new_trade, ignore_index=True)
    trades.to_csv(TRADE_LOG, index=False)
    st.success("✅ Trade added!")

# ---- Show Trade Log ----
st.subheader("📘 Trade Log")
st.dataframe(trades)
