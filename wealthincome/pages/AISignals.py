import streamlit as st
import pandas as pd
import yfinance as yf

# ---------- 3rd‑party FinViz API ----------
try:
    from analysis_engine.finviz.fetch_api import Overview
    finviz_available = True
except ImportError:
    finviz_available = False
# ------------------------------------------

# ---------- Page setup ----------
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")
# --------------------------------

# ---------- Session tickers ----------
if "tickers" not in st.session_state:
    st.session_state["tickers"] = (
        "TSLA,NVDA,AMD,AAPL,MSFT,AMZN,META,NFLX,GME,PLTR"
    )
# ------------------------------------

# ---------- Paste / edit tickers ----------
user_input = st.text_input(
    "📋 Paste Tickers from Finviz (comma‑separated):",
    value=st.session_state["tickers"],
)
if user_input:
    st.session_state["tickers"] = ",".join(
        [t.strip().upper() for t in user_input.split(",") if t.strip()]
    )
# -----------------------------------------

# ---------- FinViz Top‑Gainers button ----------
def get_top_gainers() -> list[str]:
    """
    Pull top gainers via analysis_engine.finviz.fetch_api.
    Returns a list of tickers (upper‑case) or [] on failure.
    """
    try:
        overview = Overview()
        # Equivalent to FinViz preset: Average Vol > 500 K & RelVol > 1.5, ordered by % change
        filters = {
            "Average Volume": "Over 500K",
            "Relative Volume": "Over 1.5",
        }
        overview.set_filter(filters_dict=filters)
        overview.set_order("change")          # sort by % change descending
        df = overview.screener_view()         # pull into DataFrame
        return df["Ticker"].str.upper().tolist()
    except Exception as e:                    # graceful fallback
        st.error(f"⚠️ Failed to fetch FinViz data: {e}")
        return []

if finviz_available:
    if st.button("🔄 Add Top Gainers from Finviz"):
        new = get_top_gainers()
        if new:
            merged = list(
                dict.fromkeys(       # keeps order, removes dups
                    st.session_state["tickers"].split(",") + new
                )
            )
            st.session_state["tickers"] = ",".join(merged)
        st.experimental_rerun()
else:
    st.info(
        "FinViz API unavailable. Run `pip install analysis_engine` "
        "and restart, or ignore this button."
    )
# ------------------------------------------------

# ---------- Main screener logic ----------
tickers = [t for t in st.session_state["tickers"].split(",") if t]

data = []
for t in tickers:
    try:
        yft = yf.Ticker(t)
        info = yft.info
        hist = yft.history(period="1mo")

        price      = info.get("regularMarketPrice", 0)
        change_pct = info.get("regularMarketChangePercent", 0)
        rvol       = info.get("regularMarketVolume", 1) / info.get("averageVolume", 1)
        short_pct  = info.get("shortPercentOfFloat", 0) * 100

        ai_score = round((change_pct * 2) + (rvol * 10) + (short_pct * 2), 2)

        # ---- tags ----
        tags = []
        # placeholder for Top Pick – set later
        tags.append("")
        if change_pct >= 2 and rvol >= 1.5:
            tags.append("🔁 Momentum")
        if not hist.empty and price > hist["High"].rolling(20).max().iloc[-1]:
            tags.append("📈 Breakout")

        data.append(
            {
                "Ticker": t,
                "Price": f"${price:.2f}",
                "% Change": f"{change_pct:.2f}%",
                "RVOL": round(rvol, 3),
                "Short %": f"{short_pct:.1f}%",
                "AI Score": ai_score,
                "Signal": "",     # set after DataFrame creation
                "Tags": ", ".join(tags[1:]),
            }
        )
    except Exception:
        pass  # silently skip bad ticker
df = pd.DataFrame(data)

if df.empty:
    st.warning("No data available. Please check the tickers.")
    st.stop()

# set signals
df["Signal"] = df["AI Score"].apply(
    lambda x: "BUY" if x >= 60 else "WATCH" if x >= 45 else "AVOID"
)

# mark Top Pick
idx = df["AI Score"].idxmax()
df.at[idx, "Tags"] = "🏆 Top Pick" + (", " + df.at[idx, "Tags"] if df.at[idx, "Tags"] else "")

# filter dropdown
signal_filter = st.selectbox("📍 Filter by Signal", ["All", "BUY", "WATCH", "AVOID"])
if signal_filter != "All":
    df = df[df["Signal"] == signal_filter]

    # ➍ Sort so rows with 🏆 (or multiple tags) stay on top
    df = (df
          .assign(has_top=df["Tags"].str.contains("🏆"))
          .sort_values(
              by=["has_top", "AI Score"],  # first true/false, then score
              ascending=[False, False]
          )
          .drop(columns="has_top")
          .reset_index(drop=True)
    )

# colour‑coding
def highlight(row):
    color = {
        "BUY":   "background-color:#16a34a;color:white;",
        "WATCH": "background-color:#facc15;color:black;",
        "AVOID": "background-color:#dc2626;color:white;",
    }.get(row["Signal"], "")
    return [""] * (len(row) - 2) + [color] + [""]  # only apply to Signal col

styled = df.style.apply(highlight, axis=1)
st.dataframe(styled, use_container_width=True)
# --------------------------------------------
