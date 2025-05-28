import sys
import os
import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime
import json

# --- Start of Path Fix ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)
# --- End of Path Fix ---

# Import data_manager
try:
    from data_manager import data_manager
except ImportError:
    st.error("🚨 Failed to import 'data_manager'. Please ensure 'data_manager.py' exists in the parent directory.")
    st.stop()

# Page config
try:
    st.set_page_config(page_title="📋 Watchlist Manager", layout="wide")
except st.errors.StreamlitAPIException:
    pass

st.title("📋 Watchlist Manager")

# Initialize session state for temporary watchlist if not exists
if 'temp_watchlist' not in st.session_state:
    # Load saved watchlist from data_manager
    saved_watchlist = data_manager.get_watchlist()
    st.session_state.temp_watchlist = saved_watchlist.copy()

# Helper function to explain the difference
with st.expander("ℹ️ How the Watchlist Works", expanded=False):
    st.markdown("""
    **Add Ticker**: Adds a stock to your temporary working list (not saved yet)
    
    **Save Watchlist**: Permanently saves all your tickers to disk so they persist between sessions
    
    Think of it like editing a document:
    - Adding tickers = typing changes
    - Saving watchlist = clicking "Save" to keep your changes
    """)

# Sidebar for quick actions
with st.sidebar:
    st.header("⚡ Quick Actions")
    
    # Show save status
    if len(st.session_state.temp_watchlist) != len(data_manager.get_watchlist()):
        st.warning("⚠️ You have unsaved changes!")
    else:
        st.success("✅ Watchlist is saved")
    
    # Quick add from popular stocks
    st.subheader("🔥 Quick Add Popular Stocks")
    popular_stocks = {
        "Magnificent 7": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"],
        "High Momentum": ["SMCI", "ARM", "PLTR", "COIN", "MARA"],
        "Value Picks": ["BRK-B", "JPM", "JNJ", "PG", "KO"]
    }
    
    selected_group = st.selectbox("Select group:", list(popular_stocks.keys()))
    if st.button(f"Add all {selected_group}", key="add_group"):
        for ticker in popular_stocks[selected_group]:
            if ticker not in st.session_state.temp_watchlist:
                st.session_state.temp_watchlist.append(ticker)
        st.success(f"Added {len(popular_stocks[selected_group])} stocks!")
        st.rerun()

# Main content
col1, col2 = st.columns([2, 1])

with col1:
    st.header("📊 Current Watchlist")
    
    # Add new ticker form
    with st.form("add_ticker_form", clear_on_submit=True):
        col_input, col_add, col_save = st.columns([3, 1, 1])
        
        with col_input:
            new_ticker = st.text_input("Add ticker:", placeholder="AAPL", label_visibility="collapsed")
        
        with col_add:
            add_button = st.form_submit_button("➕ Add Ticker", use_container_width=True, type="secondary")
        
        with col_save:
            save_button = st.form_submit_button("💾 Save Watchlist", use_container_width=True, type="primary")
        
        # Handle Add Ticker
        if add_button and new_ticker:
            ticker_upper = new_ticker.strip().upper()
            if ticker_upper in st.session_state.temp_watchlist:
                st.warning(f"⚠️ {ticker_upper} is already in your watchlist")
            else:
                # Validate ticker exists
                try:
                    test = yf.Ticker(ticker_upper)
                    info = test.info
                    if info.get('regularMarketPrice'):
                        st.session_state.temp_watchlist.append(ticker_upper)
                        st.success(f"✅ Added {ticker_upper} to watchlist (not saved yet)")
                        st.rerun()
                    else:
                        st.error(f"❌ {ticker_upper} is not a valid ticker")
                except:
                    st.error(f"❌ Could not validate {ticker_upper}")
        
        # Handle Save Watchlist
        if save_button:
            if data_manager.save_watchlist(st.session_state.temp_watchlist):
                st.success("✅ Watchlist saved permanently!")
                st.balloons()
            else:
                st.error("❌ Failed to save watchlist")

with col2:
    st.header("📈 Quick Stats")
    
    # Calculate portfolio metrics
    total_tickers = len(st.session_state.temp_watchlist)
    st.metric("Total Tickers", total_tickers)
    
    # Show save status prominently
    saved_list = data_manager.get_watchlist()
    if st.session_state.temp_watchlist != saved_list:
        unsaved_changes = len(st.session_state.temp_watchlist) - len(saved_list)
        st.metric("Unsaved Changes", abs(unsaved_changes), delta=f"{unsaved_changes:+d} tickers")
    else:
        st.metric("Status", "All Saved", delta="✓")

# Display watchlist with live data
if st.session_state.temp_watchlist:
    st.markdown("---")
    
    # Fetch data for all tickers
    with st.spinner("Fetching latest data..."):
        watchlist_data = []
        
        for ticker in st.session_state.temp_watchlist:
            try:
                # Get comprehensive data from data_manager
                stock_data = data_manager.get_stock_data([ticker], period="1d")
                
                if stock_data and ticker in stock_data:
                    info = stock_data[ticker]['info']
                    hist = stock_data[ticker]['history']
                    
                    # Calculate signals
                    signals = data_manager.calculate_signals(stock_data[ticker])
                    
                    # Get news sentiment
                    news = data_manager.get_latest_news_sentiment(ticker)
                    
                    # Calculate price metrics
                    current_price = info.get('regularMarketPrice', 0)
                    prev_close = info.get('previousClose', current_price)
                    change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close > 0 else 0
                    
                    # Calculate 52-week position
                    fifty_two_high = info.get('fiftyTwoWeekHigh', current_price)
                    fifty_two_low = info.get('fiftyTwoWeekLow', current_price)
                    position_52w = ((current_price - fifty_two_low) / (fifty_two_high - fifty_two_low) * 100) if (fifty_two_high - fifty_two_low) > 0 else 50
                    
                    watchlist_data.append({
                        'Ticker': ticker,
                        'Price': current_price,
                        'Change %': change_pct,
                        'Volume': info.get('regularMarketVolume', 0),
                        'Avg Volume': info.get('averageVolume', 0),
                        'Day Score': signals.get('day_score', 0),
                        'Swing Score': signals.get('swing_score', 0),
                        'News': news['label'] if news else 'N/A',
                        'Market Cap': info.get('marketCap', 0),
                        '52W Position': position_52w,
                        'PE Ratio': info.get('trailingPE', 0),
                        'Remove': False
                    })
            except Exception as e:
                st.error(f"Error fetching {ticker}: {str(e)}")
        
        if watchlist_data:
            # Create DataFrame
            df = pd.DataFrame(watchlist_data)
            
            # Calculate additional metrics
            df['RVOL'] = df.apply(lambda row: row['Volume'] / row['Avg Volume'] if row['Avg Volume'] > 0 else 0, axis=1)
            
            # Format for display
            display_df = df.copy()
            display_df['Price'] = display_df['Price'].apply(lambda x: f"${x:.2f}")
            display_df['Change %'] = display_df['Change %'].apply(lambda x: f"{x:.1f}%")  # Fixed: Clean format
            display_df['RVOL'] = display_df['RVOL'].apply(lambda x: f"{x:.1f}")  # Fixed: Clean format
            display_df['Day Score'] = display_df['Day Score'].apply(lambda x: f"{x:.0f}")  # Fixed: No decimals
            display_df['Swing Score'] = display_df['Swing Score'].apply(lambda x: f"{x:.0f}")  # Fixed: No decimals
            display_df['52W Position'] = display_df['52W Position'].apply(lambda x: f"{x:.0f}%")
            display_df['PE Ratio'] = display_df['PE Ratio'].apply(lambda x: f"{x:.1f}" if x > 0 else "N/A")
            display_df['Market Cap'] = display_df['Market Cap'].apply(lambda x: f"${x/1e9:.1f}B" if x > 1e9 else f"${x/1e6:.0f}M" if x > 0 else "N/A")
            
            # Remove technical columns
            display_df = display_df[['Ticker', 'Price', 'Change %', 'RVOL', 'Day Score', 'Swing Score', 'News', 'Market Cap', '52W Position', 'PE Ratio']]
            
            # Display controls
            st.subheader("📊 Watchlist Overview")
            
            col_sort1, col_sort2, col_filter = st.columns([2, 2, 2])
            with col_sort1:
                sort_by = st.selectbox("Sort by:", 
                    ['Change %', 'Day Score', 'Swing Score', 'RVOL', '52W Position'],
                    index=0
                )
            with col_sort2:
                sort_order = st.radio("Order:", ["High to Low", "Low to High"], horizontal=True)
            with col_filter:
                min_score = st.slider("Min Score Filter:", 0, 100, 0)
            
            # Apply sorting (need to work with original numeric values)
            if sort_by == 'Change %':
                df = df.sort_values('Change %', ascending=(sort_order == "Low to High"))
            elif sort_by in ['Day Score', 'Swing Score']:
                df = df.sort_values(sort_by, ascending=(sort_order == "Low to High"))
            elif sort_by == 'RVOL':
                df = df.sort_values('RVOL', ascending=(sort_order == "Low to High"))
            elif sort_by == '52W Position':
                df = df.sort_values('52W Position', ascending=(sort_order == "Low to High"))
            
            # Apply filter
            if min_score > 0:
                mask = (df['Day Score'] >= min_score) | (df['Swing Score'] >= min_score)
                filtered_df = df[mask]
                if len(filtered_df) > 0:
                    df = filtered_df
                else:
                    st.warning(f"No stocks meet the minimum score of {min_score}")
            
            # Re-create display dataframe after sorting/filtering
            display_df = df.copy()
            display_df['Price'] = display_df['Price'].apply(lambda x: f"${x:.2f}")
            display_df['Change %'] = display_df['Change %'].apply(lambda x: f"{x:.1f}%")
            display_df['RVOL'] = display_df['RVOL'].apply(lambda x: f"{x:.1f}")
            display_df['Day Score'] = display_df['Day Score'].apply(lambda x: f"{x:.0f}")
            display_df['Swing Score'] = display_df['Swing Score'].apply(lambda x: f"{x:.0f}")
            display_df['52W Position'] = display_df['52W Position'].apply(lambda x: f"{x:.0f}%")
            display_df['PE Ratio'] = display_df['PE Ratio'].apply(lambda x: f"{x:.1f}" if x > 0 else "N/A")
            display_df['Market Cap'] = display_df['Market Cap'].apply(lambda x: f"${x/1e9:.1f}B" if x > 1e9 else f"${x/1e6:.0f}M" if x > 0 else "N/A")
            display_df = display_df[['Ticker', 'Price', 'Change %', 'RVOL', 'Day Score', 'Swing Score', 'News', 'Market Cap', '52W Position', 'PE Ratio']]
            
            # Display the dataframe
            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Change %": st.column_config.TextColumn(
                        "Change %",
                        help="Daily price change percentage"
                    ),
                    "RVOL": st.column_config.TextColumn(
                        "RVOL",
                        help="Relative Volume - today's volume vs average"
                    ),
                    "Day Score": st.column_config.TextColumn(
                        "Day Score",
                        help="0-100 score for day trading potential"
                    ),
                    "Swing Score": st.column_config.TextColumn(
                        "Swing Score", 
                        help="0-100 score for swing trading potential"
                    ),
                    "52W Position": st.column_config.TextColumn(
                        "52W Position",
                        help="Where price sits in 52-week range (0% = low, 100% = high)"
                    )
                }
            )
            
            # Remove ticker functionality
            st.markdown("---")
            col_rem1, col_rem2, col_rem3 = st.columns([2, 1, 1])
            
            with col_rem1:
                ticker_to_remove = st.selectbox("Remove ticker:", [""] + st.session_state.temp_watchlist)
            
            with col_rem2:
                if st.button("🗑️ Remove Selected", use_container_width=True) and ticker_to_remove:
                    st.session_state.temp_watchlist.remove(ticker_to_remove)
                    st.success(f"Removed {ticker_to_remove} (remember to save!)")
                    st.rerun()
            
            with col_rem3:
                if st.button("🔄 Refresh Data", use_container_width=True):
                    st.cache_data.clear()
                    st.rerun()
            
            # Quick Analysis Section
            st.markdown("---")
            st.subheader("🎯 Quick Analysis")
            
            selected_ticker = st.selectbox("Select ticker for detailed view:", 
                                         st.session_state.temp_watchlist)
            
            if selected_ticker:
                col_act1, col_act2, col_act3, col_act4 = st.columns(4)
                
                with col_act1:
                    if st.button("📊 View Patterns", use_container_width=True):
                        st.session_state['analyze_ticker'] = selected_ticker
                        st.switch_page("pages/4_📊_Patterns.py")
                
                with col_act2:
                    if st.button("📰 Check News", use_container_width=True):
                        st.session_state['news_ticker_filter'] = selected_ticker
                        st.switch_page("pages/3_📰_News.py")
                
                with col_act3:
                    if st.button("🤖 AI Analysis", use_container_width=True):
                        st.session_state['analyze_ticker'] = selected_ticker
                        st.switch_page("pages/2_🧠_AI_Signals.py")
                
                with col_act4:
                    if st.button("📓 Add to Journal", use_container_width=True):
                        st.session_state['journal_ticker'] = selected_ticker
                        st.switch_page("pages/5_📓_Journal.py")

else:
    st.info("👆 Your watchlist is empty. Add some tickers to get started!")
    
    # Show suggestions
    st.markdown("### 💡 Quick Start Suggestions")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown("**📈 Trending Now**")
        if st.button("Add NVDA"):
            st.session_state.temp_watchlist.append("NVDA")
            st.rerun()
        if st.button("Add TSLA"):
            st.session_state.temp_watchlist.append("TSLA")
            st.rerun()
            
    with col2:
        st.markdown("**💎 Blue Chips**")
        if st.button("Add AAPL"):
            st.session_state.temp_watchlist.append("AAPL")
            st.rerun()
        if st.button("Add MSFT"):
            st.session_state.temp_watchlist.append("MSFT")
            st.rerun()
            
    with col3:
        st.markdown("**🔥 High Beta**")
        if st.button("Add PLTR"):
            st.session_state.temp_watchlist.append("PLTR")
            st.rerun()
        if st.button("Add COIN"):
            st.session_state.temp_watchlist.append("COIN")
            st.rerun()

# Export/Import functionality
st.markdown("---")
with st.expander("📤 Export/Import Watchlist"):
    col_exp1, col_exp2 = st.columns(2)
    
    with col_exp1:
        st.subheader("Export")
        if st.session_state.temp_watchlist:
            export_data = {
                "watchlist": st.session_state.temp_watchlist,
                "exported_at": datetime.now().isoformat(),
                "total_tickers": len(st.session_state.temp_watchlist)
            }
            export_json = json.dumps(export_data, indent=2)
            
            st.download_button(
                label="📥 Download Watchlist (JSON)",
                data=export_json,
                file_name=f"watchlist_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json"
            )
            
            # Simple text format
            export_text = ",".join(st.session_state.temp_watchlist)
            st.text_area("Or copy as text:", value=export_text, height=100)
    
    with col_exp2:
        st.subheader("Import")
        import_method = st.radio("Import method:", ["Text (comma-separated)", "JSON file"])
        
        if import_method == "Text (comma-separated)":
            import_text = st.text_area("Paste tickers (comma-separated):", height=100)
            if st.button("📤 Import from Text"):
                if import_text:
                    new_tickers = [t.strip().upper() for t in import_text.split(",") if t.strip()]
                    added = 0
                    for ticker in new_tickers:
                        if ticker not in st.session_state.temp_watchlist:
                            st.session_state.temp_watchlist.append(ticker)
                            added += 1
                    st.success(f"Added {added} new tickers!")
                    st.rerun()
        else:
            uploaded_file = st.file_uploader("Choose a JSON file", type=['json'])
            if uploaded_file is not None:
                try:
                    import_data = json.load(uploaded_file)
                    if 'watchlist' in import_data:
                        new_tickers = import_data['watchlist']
                        added = 0
                        for ticker in new_tickers:
                            if ticker not in st.session_state.temp_watchlist:
                                st.session_state.temp_watchlist.append(ticker)
                                added += 1
                        st.success(f"Imported {added} new tickers!")
                        st.rerun()
                except Exception as e:
                    st.error(f"Error importing file: {e}")
