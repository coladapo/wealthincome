import streamlit as st
import pandas as pd
import yfinance as yf
import json
from pathlib import Path

# ─── Page Setup ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# ─── State Management ─────────────────────────────────────────────────────────
# Option 1: Use session state (persists during session, not across browser refresh)
if 'tickers' not in st.session_state:
    st.session_state.tickers = "KSS,QBTS,QSI,MARA,SNOW,HIMS,SMCI,FL,ENPH,BL"

# Option 2: Use local file storage (persists across browser refresh)
STORAGE_FILE = Path("ticker_storage.json")

def load_tickers():
    """Load tickers from storage file or return default"""
    if STORAGE_FILE.exists():
        try:
            with open(STORAGE_FILE, 'r') as f:
                data = json.load(f)
                return data.get('tickers', st.session_state.tickers)
        except:
            return st.session_state.tickers
    return st.session_state.tickers

def save_tickers(tickers):
    """Save tickers to storage file"""
    try:
        with open(STORAGE_FILE, 'w') as f:
            json.dump({'tickers': tickers}, f)
    except:
        pass

# Load saved tickers
saved_tickers = load_tickers()

# ─── Pasteable Ticker Input ────────────────────────────────────────────────────
user_input = st.text_input(
    "📋 Paste Tickers from Finviz (comma-separated):", 
    value=saved_tickers,
    key="ticker_input"
)

# Update session state and save to file when input changes
if user_input != st.session_state.tickers:
    st.session_state.tickers = user_input
    save_tickers(user_input)

tickers = [t.strip().upper() for t in user_input.split(",") if t.strip()]

# Add/Remove ticker functionality
col1, col2, col3 = st.columns([2, 2, 8])
with col1:
    new_ticker = st.text_input("Add ticker:", key="add_ticker")
    if st.button("➕ Add") and new_ticker:
        new_ticker = new_ticker.strip().upper()
        if new_ticker not in tickers:
            tickers.append(new_ticker)
            updated_input = ",".join(tickers)
            st.session_state.tickers = updated_input
            save_tickers(updated_input)
            st.rerun()

with col2:
    if tickers:
        remove_ticker = st.selectbox("Remove ticker:", [""] + tickers)
        if st.button("➖ Remove") and remove_ticker:
            tickers.remove(remove_ticker)
            updated_input = ",".join(tickers)
            st.session_state.tickers = updated_input
            save_tickers(updated_input)
            st.rerun()

# ─── How This Screener Works ──────────────────────────────────────────────────
with st.expander("📘 How This Screener Works"):
    st.markdown("""
This tool scans the market for **momentum setups** using three metrics:

1. **% Change** – today's price move  
2. **RVOL** – relative volume (= today's volume / avg. volume)  
3. **Short %** – short interest as % of float  

🧠 **AI Score** = (% Change × 2) + (RVOL × 10) + (Short % × 2)

🏁 **Signals**  
- 🟢 **BUY** if Score ≥ 60  
- 🟡 **WATCH** if Score ≥ 45  
- 🔴 **AVOID** if Score < 45  

🔖 **Tags Logic**  
- 🏆 **Top Pick** – highest AI Score in the list  
- 🔁 **Momentum** – % Change ≥ 2% **and** RVOL ≥ 1.5  
- 📈 **Breakout** – price > 20‑day high  

Use the **signal dropdown** below to filter to BUY, WATCH, or AVOID.
    """)

# ─── Filter Dropdown ───────────────────────────────────────────────────────────
selected_signal = st.selectbox("📍 Filter by Signal", ["All", "BUY", "WATCH", "AVOID"])

# ─── Fetch Data & Compute Scores ───────────────────────────────────────────────
@st.cache_data(ttl=300)  # Cache for 5 minutes
def fetch_stock_data(ticker):
    """Fetch data for a single ticker with caching"""
    try:
        tkr = yf.Ticker(ticker)
        info = tkr.info
        hist = tkr.history(period="1mo")

        price = info.get("regularMarketPrice", 0) or 0
        change = info.get("regularMarketChangePercent", 0) or 0
        rvol = (info.get("regularMarketVolume", 1) or 1) / (info.get("averageVolume", 1) or 1)
        short_pct = (info.get("shortPercentOfFloat", 0) or 0) * 100

        ai_score = round((change * 2) + (rvol * 10) + (short_pct * 2), 2)

        # build tags
        tags = []
        if change >= 2 and rvol >= 1.5:
            tags.append("🔁 Momentum")
        if not hist.empty and price > hist["High"].rolling(20).max().iloc[-1]:
            tags.append("📈 Breakout")

        return {
            "Ticker": ticker,
            "Price": f"${price:.2f}",
            "% Change": f"{change:.2f}%",
            "RVOL": round(rvol, 3),
            "Short %": f"{short_pct:.1f}%",
            "AI Score": ai_score,
            "Signal": "",  # placeholder
            "Tags": ", ".join(tags)
        }
    except Exception as e:
        return None

# Fetch data with progress bar
data = []
progress_bar = st.progress(0)
status_text = st.empty()

for i, ticker in enumerate(tickers):
    status_text.text(f"Fetching {ticker}...")
    result = fetch_stock_data(ticker)
    if result:
        data.append(result)
    progress_bar.progress((i + 1) / len(tickers))

progress_bar.empty()
status_text.empty()

# ─── Build DataFrame ───────────────────────────────────────────────────────────
df = pd.DataFrame(data)
if not df.empty:
    # assign Signal
    df["Signal"] = df["AI Score"].apply(
        lambda x: "BUY" if x >= 60 else "WATCH" if x >= 45 else "AVOID"
    )

    # tag the top AI Score
    top = df["AI Score"].idxmax()
    if pd.notna(top):
        df.at[top, "Tags"] = "🏆 Top Pick" + (", " + df.at[top, "Tags"] if df.at[top, "Tags"] else "")

    # filter by signal dropdown
    if selected_signal != "All":
        df = df[df["Signal"] == selected_signal]

    # sort: all BUY first by descending AI Score
    df["is_buy"] = df["Signal"] == "BUY"
    df = df.sort_values(
        by=["is_buy", "AI Score"], 
        ascending=[False, False]
    ).drop(columns="is_buy").reset_index(drop=True)

    # reorder columns: Ticker, Signal, Tags, then the rest
    cols = ["Ticker", "Signal", "Tags", "Price", "% Change", "RVOL", "Short %", "AI Score"]
    df = df[cols]

    # highlight colors for Signal
    def highlight_signal(val):
        if val == "BUY":
            return "background-color: #16a34a; color: white;"
        if val == "WATCH":
            return "background-color: #facc15; color: black;"
        if val == "AVOID":
            return "background-color: #dc2626; color: white;"
        return ""

    styled = df.style.applymap(highlight_signal, subset=["Signal"])
    st.dataframe(styled, use_container_width=True, height=600)

    # Export functionality
    col1, col2 = st.columns([1, 5])
    with col1:
        csv = df.to_csv(index=False)
        st.download_button(
            label="📥 Export CSV",
            data=csv,
            file_name="ai_stock_screener.csv",
            mime="text/csv"
        )

else:
    st.warning("No data available. Please check your tickers.")

# ─── Quick Stats ───────────────────────────────────────────────────────────────
if not df.empty:
    st.markdown("### 📊 Quick Stats")
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        buy_count = len(df[df["Signal"] == "BUY"])
        st.metric("🟢 BUY Signals", buy_count)
    
    with col2:
        watch_count = len(df[df["Signal"] == "WATCH"])
        st.metric("🟡 WATCH Signals", watch_count)
    
    with col3:
        avoid_count = len(df[df["Signal"] == "AVOID"])
        st.metric("🔴 AVOID Signals", avoid_count)
    
    with col4:
        avg_score = df["AI Score"].mean()
        st.metric("📈 Avg AI Score", f"{avg_score:.1f}")
