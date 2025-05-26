import sys
import os
import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime

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
    st.error("🚨 Failed to import 'data_manager'. Please ensure 'data_manager.py' exists in the root directory.")
    st.stop()

# Page config
try:
    st.set_page_config(page_title="📋 Watchlist Manager", layout="wide")
except st.errors.StreamlitAPIException:
    pass

st.title("📋 Watchlist Manager")

# Load watchlist from data_manager
watchlist = data_manager.get_watchlist()

# Initialize session state
if 'temp_watchlist' not in st.session_state:
    st.session_state.temp_watchlist = watchlist.copy()

# Sidebar for quick actions
with st.sidebar:
    st.header("⚡ Quick Actions")
    
    # Add from AI Signals
    if 'trade_signals' in st.session_state and st.session_state.trade_signals:
        st.subheader("Add from AI Signals")
        ai_tickers = [s['Ticker'] for s in st.session_state.trade_signals if s['Ticker'] not in st.session_state.temp_watchlist]
        if ai_tickers:
            selected_ai = st.multiselect("Select tickers:", ai_tickers)
            if st.button("Add Selected", key="add_ai"):
                st.session_state.temp_watchlist.extend(selected_ai)
                st.success(f"Added {len(selected_ai)} tickers from AI signals")
                st.rerun()
    
    # Add from News
    if 'news_articles' in st.session_state and st.session_state.news_articles:
        st.subheader("Add from News")
        # Get positive sentiment tickers
        positive_tickers = list(set([
            article['Ticker'] for article in st.session_state.news_articles 
            if article.get('Cached_Sentiment') == 'Positive' and article['Ticker'] not in st.session_state.temp_watchlist
        ]))
        if positive_tickers:
            selected_news = st.multiselect("Positive sentiment:", positive_tickers)
            if st.button("Add Selected", key="add_news"):
                st.session_state.temp_watchlist.extend(selected_news)
                st.success(f"Added {len(selected_news)} positive sentiment tickers")
                st.rerun()

# Main content
col1, col2 = st.columns([2, 1])

with col1:
    st.header("📊 Current Watchlist")
    
    # Add new ticker manually
    with st.form("add_ticker_form"):
        new_ticker = st.text_input("Add ticker manually:", placeholder="AAPL")
        col_a, col_b = st.columns(2)
        with col_a:
            submitted = st.form_submit_button("➕ Add Ticker", use_container_width=True)
        with col_b:
            save_all = st.form_submit_button("💾 Save Watchlist", type="primary", use_container_width=True)
        
        if submitted and new_ticker:
            ticker_upper = new_ticker.strip().upper()
            if ticker_upper not in st.session_state.temp_watchlist:
                st.session_state.temp_watchlist.append(ticker_upper)
                st.success(f"Added {ticker_upper} to watchlist")
                st.rerun()
            else:
                st.warning(f"{ticker_upper} already in watchlist")
        
        if save_all:
            if data_manager.save_watchlist(st.session_state.temp_watchlist):
                st.success("✅ Watchlist saved successfully!")
                st.balloons()
            else:
                st.error("Failed to save watchlist")

with col2:
    st.header("📈 Quick Stats")
    st.metric("Total Tickers", len(st.session_state.temp_watchlist))
    
    # Calculate some stats
    if st.session_state.temp_watchlist:
        gaining = 0
        losing = 0
        for ticker in st.session_state.temp_watchlist[:10]:  # Check first 10 for performance
            try:
                info = yf.Ticker(ticker).info
                change = info.get('regularMarketChangePercent', 0)
                if change > 0:
                    gaining += 1
                elif change < 0:
                    losing += 1
            except:
                pass
        
        col_stat1, col_stat2 = st.columns(2)
        with col_stat1:
            st.metric("Gaining", gaining, f"{gaining/(gaining+losing)*100:.0f}%" if (gaining+losing) > 0 else "0%")
        with col_stat2:
            st.metric("Losing", losing, f"{losing/(gaining+losing)*100:.0f}%" if (gaining+losing) > 0 else "0%")

# Display watchlist with live data
if st.session_state.temp_watchlist:
    st.markdown("---")
    
    # Fetch data for all tickers
    with st.spinner("Fetching latest data..."):
        watchlist_data = []
        
        for ticker in st.session_state.temp_watchlist:
            try:
                # Get data from data_manager (cached)
                stock_data = data_manager.get_stock_data([ticker], period="1d")
                
                if stock_data and ticker in stock_data:
                    info = stock_data[ticker]['info']
                    
                    # Calculate signals
                    signals = data_manager.calculate_signals(stock_data[ticker])
                    
                    # Get news sentiment
                    news = data_manager.get_latest_news_sentiment(ticker)
                    
                    watchlist_data.append({
                        'Ticker': ticker,
                        'Price': info.get('regularMarketPrice', 0),
                        'Change %': info.get('regularMarketChangePercent', 0),
                        'Volume': info.get('regularMarketVolume', 0),
                        'Avg Volume': info.get('averageVolume', 0),
                        'Day Score': signals.get('day_score', 0),
                        'Swing Score': signals.get('swing_score', 0),
                        'News': news['label'] if news else 'N/A',
                        'Remove': False
                    })
            except Exception as e:
                st.error(f"Error fetching {ticker}: {str(e)}")
    
    if watchlist_data:
        # Create DataFrame
        df = pd.DataFrame(watchlist_data)
        
        # Format columns
        df['Price'] = df['Price'].apply(lambda x: f"${x:.2f}")
        df['Change %'] = df['Change %'].apply(lambda x: f"{x:.2f}%")
        df['Volume'] = df['Volume'].apply(lambda x: f"{x/1e6:.1f}M" if x > 0 else "0")
        df['RVOL'] = (watchlist_data[i]['Volume'] / watchlist_data[i]['Avg Volume'] 
                      if watchlist_data[i]['Avg Volume'] > 0 else 0 
                      for i in range(len(watchlist_data)))
        df['RVOL'] = [f"{rvol:.2f}" for rvol in df['RVOL']]
        df['Day Score'] = df['Day Score'].apply(lambda x: f"{x:.0f}")
        df['Swing Score'] = df['Swing Score'].apply(lambda x: f"{x:.0f}")
        
        # Remove Avg Volume column (used for RVOL calculation)
        df = df.drop(columns=['Avg Volume'])
        
        # Display with sorting
        st.subheader("📊 Watchlist Details")
        
        # Sorting options
        sort_col1, sort_col2 = st.columns([3, 1])
        with sort_col1:
            sort_by = st.selectbox("Sort by:", 
                                   ["Ticker", "Change %", "Day Score", "Swing Score", "RVOL"],
                                   index=1)
        with sort_col2:
            sort_order = st.radio("Order:", ["Desc", "Asc"])
        
        # Sort dataframe
        ascending = sort_order == "Asc"
        if sort_by == "Change %":
            df['_sort_change'] = df['Change %'].str.rstrip('%').astype(float)
            df = df.sort_values('_sort_change', ascending=ascending).drop(columns=['_sort_change'])
        elif sort_by in ["Day Score", "Swing Score"]:
            df[f'_sort_{sort_by}'] = df[sort_by].astype(float)
            df = df.sort_values(f'_sort_{sort_by}', ascending=ascending).drop(columns=[f'_sort_{sort_by}'])
        elif sort_by == "RVOL":
            df['_sort_rvol'] = df['RVOL'].astype(float)
            df = df.sort_values('_sort_rvol', ascending=ascending).drop(columns=['_sort_rvol'])
        else:
            df = df.sort_values(sort_by, ascending=ascending)
        
        # Display DataFrame
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Remove": st.column_config.CheckboxColumn(
                    "Remove",
                    help="Select to remove from watchlist",
                    default=False,
                )
            }
        )
        
        # Remove selected
        col_rem1, col_rem2, col_rem3 = st.columns([1, 1, 2])
        with col_rem1:
            if st.button("🗑️ Remove Selected", use_container_width=True):
                # Note: In real implementation, you'd need to track which rows were checked
                st.info("Select tickers using checkboxes first")
        
        with col_rem2:
            if st.button("🔄 Refresh Data", use_container_width=True):
                st.cache_data.clear()
                st.rerun()
        
        # Quick actions for selected ticker
        st.markdown("---")
        st.subheader("🎯 Quick Analysis")
        
        selected_ticker = st.selectbox("Select ticker for detailed view:", 
                                       st.session_state.temp_watchlist)
        
        if selected_ticker:
            col_act1, col_act2, col_act3, col_act4 = st.columns(4)
            
            with col_act1:
                if st.button("📊 View Patterns", use_container_width=True):
                    st.session_state['analyze_ticker'] = selected_ticker
                    st.switch_page("pages/patterns.py")
            
            with col_act2:
                if st.button("📰 Check News", use_container_width=True):
                    st.session_state['news_ticker_filter'] = selected_ticker
                    st.switch_page("pages/news.py")
            
            with col_act3:
                if st.button("🤖 AI Analysis", use_container_width=True):
                    st.session_state['analyze_ticker'] = selected_ticker
                    st.switch_page("pages/AISignals.py")
            
            with col_act4:
                if st.button("📓 Add to Journal", use_container_width=True):
                    st.session_state['journal_ticker'] = selected_ticker
                    st.switch_page("pages/journal.py")

else:
    st.info("👆 Your watchlist is empty. Add some tickers to get started!")
    
    # Suggestions
    st.markdown("### 💡 Suggestions")
    st.markdown("""
    - Run the **AI Scanner** to find high-scoring stocks
    - Check **Market News** for stocks with positive sentiment
    - Add major indices: SPY, QQQ, DIA, IWM
    - Popular stocks: AAPL, MSFT, GOOGL, AMZN, TSLA, NVDA
    """)

# Export functionality
st.markdown("---")
with st.expander("📤 Export/Import Watchlist"):
    col_exp1, col_exp2 = st.columns(2)
    
    with col_exp1:
        st.subheader("Export")
        if st.session_state.temp_watchlist:
            export_data = ",".join(st.session_state.temp_watchlist)
            st.text_area("Copy this list:", value=export_data, height=100)
            
            # Download as file
            st.download_button(
                label="📥 Download as CSV",
                data=export_data,
                file_name=f"watchlist_{datetime.now().strftime('%Y%m%d')}.txt",
                mime="text/plain"
            )
    
    with col_exp2:
        st.subheader("Import")
        import_data = st.text_area("Paste comma-separated tickers:", height=100)
        if st.button("📤 Import Tickers"):
            if import_data:
                new_tickers = [t.strip().upper() for t in import_data.split(",") if t.strip()]
                added = 0
                for ticker in new_tickers:
                    if ticker not in st.session_state.temp_watchlist:
                        st.session_state.temp_watchlist.append(ticker)
                        added += 1
                st.success(f"Added {added} new tickers to watchlist")
                st.rerun()
