import streamlit as st
import pandas as pd
import yfinance as yf

# ── Pull any previously‐saved tickers from the URL ────────────────────
q = st.experimental_get_query_params()
initial = q.get("tickers", [""])[0]

# ── The text_input is now “bound” to that query param ───────────────
user_input = st.text_input(
    "📋 Paste Tickers (comma‑separated):",
    value=initial,
    key="ticker_box"
)

# ── Whenever they change it, push it back into the URL ─────────────
if user_input != initial:
    st.experimental_set_query_params(tickers=user_input)

# ── Parse into a list ────────────────────────────────────────────────
tickers = [t.strip().upper() for t in user_input.split(",") if t.strip()]

# ── Now your normal page logic ───────────────────────────────────────
st.set_page_config(page_title="🧠 AI Stock Screener", layout="wide")
st.title("📊 AI Stock Screener")

# (… your “How This Screener Works” expander, signal filter, etc …)

data = []
for ticker in tickers:
    try:
        info = yf.Ticker(ticker).info
        hist = yf.Ticker(ticker).history(period="1mo")

        price = info.get("regularMarketPrice", 0)
        change = info.get("regularMarketChangePercent", 0)
        rvol = info.get("regularMarketVolume", 1) / info.get("averageVolume", 1)
        short_pct = info.get("shortPercentOfFloat", 0) * 100

        ai_score = round((change * 2) + (rvol * 10) + (short_pct * 2), 2)

        # build tags & signal (your existing logic)
        tags = []
        # … etc …

        data.append({
            "Ticker": ticker,
            "Price": f"${price:.2f}",
            "% Change": f"{change:.2f}%",
            "RVOL": round(rvol,3),
            "Short %": f"{short_pct:.1f}%",
            "AI Score": ai_score,
            "Signal": "BUY" if ai_score>=60 else "WATCH" if ai_score>=45 else "AVOID",
            "Tags": ", ".join(tags),
        })

    except Exception:
        continue

df = pd.DataFrame(data)

if not df.empty:
    # (optional) sort so Top‑Pick / BUY always floats to the top:
    df["is_top"] = df["Tags"].str.contains("Top Pick")
    df = df.sort_values(["is_top","Signal"], ascending=[False,False]).reset_index(drop=True)
    df.drop(columns="is_top", inplace=True)

    def fmt_signal(v):
        if v=="BUY":    return "background-color:#16a34a;color:white"
        if v=="WATCH":  return "background-color:#facc15;color:black"
        if v=="AVOID":  return "background-color:#dc2626;color:white"
        return ""

    styled = df.style.applymap(fmt_signal, subset=["Signal"])
    st.dataframe(styled, use_container_width=True)
else:
    st.warning("No data available — check your tickers.")
