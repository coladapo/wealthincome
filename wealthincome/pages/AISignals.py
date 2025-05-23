import streamlit as st
import yfinance as yf
import pandas as pd

st.set_page_config(page_title="🧠 AI Screener", layout="wide")

st.title("🧠 AI Stock Screener")
st.markdown("""
This tool scans top US tickers and highlights potential plays based on:

- **High Relative Volume (RVOL)**
- **Strong Price Momentum**
- **Short % interest**
- A-style rule logic: `% Change * 2 + RVOL * 10 + Short% * 2`
""")

# Sample tickers — swap in your watchlist or use dynamic scanning later
tickers = ["TSLA", "NVDA", "AMD", "AAPL", "MSFT", "AMZN", "META", "NFLX", "GME", "PLTR"]

data = []
for ticker in tickers:
    try:
        t = yf.Ticker(ticker)
        info = t.info

        price = info.get("currentPrice", None)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("averageDailyVolume10Day", 1) / info.get("averageVolume", 1)
        short_pct = info.get("shortPercentOfFloat", 0) * 100 if info.get("shortPercentOfFloat") else 0

        # AI Scoring logic
        score = round((change * 2) + (rvol * 10) + (short_pct * 2), 2)

        if score >= 80:
            signal = "BUY"
        elif score >= 50:
            signal = "WATCH"
        else:
            signal = "AVOID"

        data.append({
            "Ticker": ticker,
            "Price": f"${price:.2f}" if price else "N/A",
            "% Change": f"{change:.2f}%",
            "RVOL": round(rvol, 2),
            "Short %": f"{short_pct:.1f}%",
            "AI Score": score,
            "Signal": signal
        })

    except Exception as e:
        st.warning(f"{ticker}: {e}")

df = pd.DataFrame(data)

# Sort by top score
df = df.sort_values("AI Score", ascending=False)

# Color formatter
def highlight_signal(val):
    color = {
        "BUY": "background-color: #16c784; color: white;",
        "WATCH": "background-color: #facc15; color: black;",
        "AVOID": "background-color: #ef4444; color: white;"
    }
    return color.get(val, "")

# Display
st.dataframe(
    df.style.applymap(highlight_signal, subset=["Signal"]),
    use_container_width=True,
    hide_index=True
)

# Optional filter
st.divider()
tag_filter = st.selectbox("🔎 Filter by Signal", ["All"] + df["Signal"].unique().tolist())
if tag_filter != "All":
    st.dataframe(
        df[df["Signal"] == tag_filter].style.applymap(highlight_signal, subset=["Signal"]),
        use_container_width=True,
        hide_index=True
    )
