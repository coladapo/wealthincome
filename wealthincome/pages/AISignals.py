import streamlit as st
import pandas as pd
import yfinance as yf
import json
from pathlib import Path
import time

# ─── Page Setup ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="📊 AI Stock Screener", layout="wide")
st.title("🧠 AI Stock Screener")

# ─── State Management ─────────────────────────────────────────────────────────
# Initialize session state
if 'tickers' not in st.session_state:
    st.session_state.tickers = "KSS,QBTS,QSI,MARA,SNOW,HIMS,SMCI,FL,ENPH,BL"

# Local file storage for persistence
STORAGE_FILE = Path("ticker_storage.json")

def load_tickers():
    """Load tickers from storage file or return default"""
    if STORAGE_FILE.exists():
        try:
            with open(STORAGE_FILE, 'r') as f:
                data = json.load(f)
                return data.get('tickers', st.session_state.tickers)
        except Exception as e:
            st.error(f"Error loading saved tickers: {str(e)}")
            return st.session_state.tickers
    return st.session_state.tickers

def save_tickers(tickers):
    """Save tickers to storage file"""
    try:
        with open(STORAGE_FILE, 'w') as f:
            json.dump({'tickers': tickers}, f)
    except Exception as e:
        st.error(f"Error saving tickers: {str(e)}")

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
        if new_ticker and new_ticker not in tickers:
            tickers.append(new_ticker)
            updated_input = ",".join(tickers)
            st.session_state.tickers = updated_input
            save_tickers(updated_input)
            st.rerun()

with col2:
    if tickers:
        remove_ticker = st.selectbox("Remove ticker:", [""] + tickers, key="remove_ticker")
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

# ─── Fetch Data Function ───────────────────────────────────────────────────────
@st.cache_data(ttl=300)  # Cache for 5 minutes
def fetch_all_stock_data(ticker_list):
    """Fetch data for all tickers with caching"""
    data = []
    for ticker in ticker_list:
        try:
            tkr = yf.Ticker(ticker)
            info = tkr.info
            
            # Sometimes yfinance returns None for info
            if not info:
                continue
                
            hist = tkr.history(period="1mo")

            # Get values with better error handling
            price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose", 0)
            prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose", price)
            
            # Calculate % change manually if not provided
            if prev_close and prev_close != 0:
                change = ((price - prev_close) / prev_close) * 100
            else:
                change = info.get("regularMarketChangePercent", 0) or 0
            
            # Volume calculations
            volume = info.get("volume") or info.get("regularMarketVolume", 0)
            avg_volume = info.get("averageVolume") or info.get("averageDailyVolume10Day", 1)
            rvol = volume / avg_volume if avg_volume > 0 else 0
            
            # Short interest
            short_pct = info.get("shortPercentOfFloat", 0) or 0
            if short_pct > 0:
                short_pct = short_pct * 100

            ai_score = round((change * 2) + (rvol * 10) + (short_pct * 2), 2)

            # Build tags
            tags = []
            if change >= 2 and rvol >= 1.5:
                tags.append("🔁 Momentum")
            if not hist.empty and len(hist) >= 20:
                try:
                    high_20d = hist["High"].rolling(20).max().iloc[-1]
                    if price > high_20d:
                        tags.append("📈 Breakout")
                except:
                    pass

            data.append({
                "Ticker": ticker,
                "Price": f"${price:.2f}",
                "% Change": f"{change:.2f}%",
                "RVOL": round(rvol, 3),
                "Short %": f"{short_pct:.1f}%",
                "AI Score": ai_score,
                "Signal": "",  # placeholder
                "Tags": ", ".join(tags),
                "_change_raw": change,  # for sorting
                "_price_raw": price  # for export
            })

        except Exception as e:
            st.warning(f"Failed to fetch data for {ticker}: {str(e)}")
            continue
    
    return data

# ─── Fetch and Display Data ────────────────────────────────────────────────────
if tickers:
    # Show progress
    with st.spinner(f"Fetching data for {len(tickers)} tickers..."):
        data = fetch_all_stock_data(tickers)
    
    if data:
        df = pd.DataFrame(data)
        
        # Assign Signal
        df["Signal"] = df["AI Score"].apply(
            lambda x: "BUY" if x >= 60 else "WATCH" if x >= 45 else "AVOID"
        )

        # Tag the top AI Score
        if len(df) > 0:
            top_idx = df["AI Score"].idxmax()
            current_tags = df.at[top_idx, "Tags"]
            df.at[top_idx, "Tags"] = "🏆 Top Pick" + (", " + current_tags if current_tags else "")

        # Filter by signal
        if selected_signal != "All":
            df = df[df["Signal"] == selected_signal]

        # Sort: BUY first, then by AI Score
        df["_signal_sort"] = df["Signal"].map({"BUY": 0, "WATCH": 1, "AVOID": 2})
        df = df.sort_values(
            by=["_signal_sort", "AI Score"], 
            ascending=[True, False]
        ).reset_index(drop=True)

        # Select columns to display
        display_cols = ["Ticker", "Signal", "Tags", "Price", "% Change", "RVOL", "Short %", "AI Score"]
        display_df = df[display_cols]

        # Style the dataframe
        def highlight_signal(val):
            if val == "BUY":
                return "background-color: #16a34a; color: white; font-weight: bold;"
            elif val == "WATCH":
                return "background-color: #facc15; color: black; font-weight: bold;"
            elif val == "AVOID":
                return "background-color: #dc2626; color: white; font-weight: bold;"
            return ""

        styled = display_df.style.applymap(highlight_signal, subset=["Signal"])
        
        # Display the dataframe
        st.dataframe(
            styled, 
            use_container_width=True, 
            height=400,
            hide_index=True
        )

        # ─── Export and Stats Row ─────────────────────────────────────────────
        st.markdown("---")
        
        col1, col2, col3, col4, col5 = st.columns([1, 1, 1, 1, 2])
        
        with col1:
            buy_count = len(df[df["Signal"] == "BUY"])
            st.metric("🟢 BUY", buy_count)
        
        with col2:
            watch_count = len(df[df["Signal"] == "WATCH"])
            st.metric("🟡 WATCH", watch_count)
        
        with col3:
            avoid_count = len(df[df["Signal"] == "AVOID"])
            st.metric("🔴 AVOID", avoid_count)
        
        with col4:
            avg_score = df["AI Score"].mean()
            st.metric("📈 Avg Score", f"{avg_score:.1f}")
        
        with col5:
            # Prepare CSV for download
            export_df = df[display_cols].copy()
            csv = export_df.to_csv(index=False)
            
            st.download_button(
                label="📥 Export to CSV",
                data=csv,
                file_name=f"ai_screener_{time.strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
                help="Download the screener results as CSV"
            )
            
            # Optional: Add refresh button
            if st.button("🔄 Refresh Data"):
                st.cache_data.clear()
                st.rerun()

    else:
        st.warning("No data could be fetched. Please check your ticker symbols.")
else:
    st.info("Please enter some ticker symbols to begin screening.")

# ─── Footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption("💡 Tip: Data is cached for 5 minutes. Click 'Refresh Data' to force update.")
