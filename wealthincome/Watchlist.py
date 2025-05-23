import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Watchlist Builder", layout="wide")
st.title("📋 Watchlist Builder")

# Initialize watchlist session state
if 'watchlist' not in st.session_state:
    st.session_state.watchlist = []

# Input: Add ticker
with st.form("add_ticker_form"):
    new_ticker = st.text_input("Enter Ticker Symbol (e.g. AAPL, TSLA)").upper()
    submitted = st.form_submit_button("Add to Watchlist")
    
    if submitted:
        if new_ticker and new_ticker not in st.session_state.watchlist:
            try:
                yf.Ticker(new_ticker).info  # Try fetching data to validate
                st.session_state.watchlist.append(new_ticker)
                st.success(f"Added {new_ticker}")
            except:
                st.error("Invalid ticker or data not available.")
        elif new_ticker in st.session_state.watchlist:
            st.warning(f"{new_ticker} is already in your watchlist.")

# Display current watchlist
st.subheader("📈 Your Watchlist")
if st.session_state.watchlist:
    for ticker in st.session_state.watchlist:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.write(f"**{ticker}**")
        with col2:
            if st.button(f"Remove {ticker}", key=f"remove_{ticker}"):
                st.session_state.watchlist.remove(ticker)
                st.experimental_rerun()

    # Show live price data (optional)
    if st.checkbox("Show live data preview"):
        for symbol in st.session_state.watchlist:
            try:
                data = yf.Ticker(symbol).history(period="1d")
                current_price = data["Close"].iloc[-1]
                st.metric(label=symbol, value=f"${current_price:.2f}")
            except:
                st.warning(f"Could not fetch price for {symbol}")
else:
    st.info("Your watchlist is empty. Add tickers above to get started.")
