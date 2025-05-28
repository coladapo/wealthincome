import streamlit as st
import pandas as pd
import datetime
import os
import sys

# --- Start of Path Fix ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)
# --- End of Path Fix ---

# ---- Page Config ----
try:
    st.set_page_config(page_title="🧾 Paper Trading Agent", layout="wide")
except st.errors.StreamlitAPIException:
    pass

# ---- Setup ----
# Use the data/persistent directory structure
TRADE_LOG_DIR = "data/persistent"
TRADE_LOG = os.path.join(TRADE_LOG_DIR, "paper_trades.csv")

# Ensure directory exists
os.makedirs(TRADE_LOG_DIR, exist_ok=True)

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

# Prefill using session state (from AI Signals)
prefill_ticker = st.session_state.get("prefill_ticker", "")
prefill_entry = st.session_state.get("prefill_entry", 100.0)
prefill_exit = st.session_state.get("prefill_exit", prefill_entry * 1.05)
prefill_type = st.session_state.get("prefill_type", "Day Trade")
prefill_notes = st.session_state.get("prefill_notes", "Triggered by dashboard signal")

# Display alert if coming from AI Signals
if prefill_ticker:
    st.success(f"📊 Pre-filled with {prefill_ticker} analysis from AI Signals!")

# Trade entry form
col1, col2 = st.columns(2)

with col1:
    ticker = st.text_input("Ticker (e.g. AAPL)", value=prefill_ticker)
    entry = st.number_input("Entry Price", value=float(prefill_entry), format="%.2f")
    exit_ = st.number_input("Target Exit Price", value=float(prefill_exit), format="%.2f")
    trade_type = st.selectbox("Trade Type", ["Day Trade", "Swing Trade", "Position Trade"], 
                             index=["Day Trade", "Swing Trade", "Position Trade"].index(prefill_type) if prefill_type in ["Day Trade", "Swing Trade", "Position Trade"] else 0)

with col2:
    # Calculate potential profit/loss
    if entry > 0:
        potential_pnl = exit_ - entry
        potential_pnl_pct = (potential_pnl / entry) * 100
        
        st.metric("Potential P/L", f"${potential_pnl:.2f}", f"{potential_pnl_pct:.2f}%")
        
        # Risk/Reward if we assume stop loss at 2% below entry
        stop_loss = entry * 0.98
        risk = entry - stop_loss
        reward = exit_ - entry
        rr_ratio = reward / risk if risk > 0 else 0
        
        st.metric("Risk/Reward (2% stop)", f"1:{rr_ratio:.1f}")

notes = st.text_area("Notes", value=prefill_notes, height=100)

# Add trade button
if st.button("💾 Add Trade", type="primary", use_container_width=True):
    if ticker and entry > 0:
        new_trade = pd.DataFrame([{
            "Date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Ticker": ticker.upper(),
            "Entry Price": entry,
            "Exit Price": exit_,
            "Type": trade_type,
            "Result": "Pending",
            "Notes": notes
        }])
        
        # Use concat instead of append (pandas 2.0+ compatible)
        trades = pd.concat([trades, new_trade], ignore_index=True)
        trades.to_csv(TRADE_LOG, index=False)
        
        st.success("✅ Trade added successfully!")
        st.balloons()
        
        # Clear prefill data after successful save
        for key in ["prefill_ticker", "prefill_entry", "prefill_exit", "prefill_type", "prefill_notes"]:
            if key in st.session_state:
                del st.session_state[key]
        
        # Rerun to show updated data
        st.rerun()
    else:
        st.error("Please enter a valid ticker and entry price")

# ---- Show Trade Log ----
st.markdown("---")
st.subheader("📘 Trade Log")

if not trades.empty:
    # Add some basic stats
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Trades", len(trades))
    
    with col2:
        pending_trades = len(trades[trades['Result'] == 'Pending'])
        st.metric("Pending Trades", pending_trades)
    
    with col3:
        # Count by trade type
        day_trades = len(trades[trades['Type'] == 'Day Trade'])
        st.metric("Day Trades", day_trades)
    
    with col4:
        swing_trades = len(trades[trades['Type'] == 'Swing Trade'])
        st.metric("Swing Trades", swing_trades)
    
    # Display the trades
    st.dataframe(trades.sort_values('Date', ascending=False), use_container_width=True, hide_index=True)
    
    # Export functionality
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 1, 2])
    
    with col1:
        csv = trades.to_csv(index=False)
        st.download_button(
            label="📥 Download CSV",
            data=csv,
            file_name=f"paper_trades_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )
    
    with col2:
        if st.button("🗑️ Clear All Trades", type="secondary"):
            if st.checkbox("Confirm deletion"):
                trades = pd.DataFrame(columns=[
                    "Date", "Ticker", "Entry Price", "Exit Price", "Type", "Result", "Notes"
                ])
                trades.to_csv(TRADE_LOG, index=False)
                st.success("All trades cleared!")
                st.rerun()
    
else:
    st.info("No trades recorded yet. Add your first simulated trade above!")

# ---- Paper Trading Tips ----
with st.expander("💡 Paper Trading Best Practices"):
    st.markdown("""
    ### Why Paper Trade?
    - **Risk-Free Learning**: Test strategies without real money
    - **Build Confidence**: Develop your trading skills
    - **Track Performance**: Analyze what works and what doesn't
    
    ### Tips for Realistic Paper Trading:
    1. **Trade with realistic position sizes** - Use the same account size you plan to trade with
    2. **Include commissions** - Factor in trading costs
    3. **Honor your stops** - Don't move stop losses just because it's paper money
    4. **Track everything** - The more data, the better your analysis
    5. **Review weekly** - Identify patterns in your wins and losses
    
    ### Coming Soon:
    - Automated P&L tracking
    - Win rate statistics
    - AI mentor feedback on your trades
    - Pattern recognition in your trading behavior
    """)

# Footer
st.markdown("---")
st.caption("🤖 Paper Trading Agent v1.0 - Part of WealthIncome Trading Dashboard")
