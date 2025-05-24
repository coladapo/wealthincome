import streamlit as st  # <-- MUST BE AT THE VERY TOP!
import sys
import os
import pandas as pd
import yfinance as yf
import numpy as np
from datetime import datetime, timedelta
import ta                 # Technical analysis library
import requests
from concurrent.futures import ThreadPoolExecutor
import json

# --- Start of Path Fix & Debugging ---
# Get the absolute path of the directory containing the current script
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the absolute path of the parent directory (project root)
parent_dir = os.path.dirname(current_dir)

# Add the parent directory to the Python system path if it's not already there
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# --- Enhanced Debugging V2 ---
st.subheader("🛠️ Import Debugging Info (V2)")
st.write("**Current sys.path:**", sys.path)
st.write(f"**Checking for 'data_manager.py' in:** `{parent_dir}`")
try:
    dir_list = os.listdir(parent_dir)
    st.write("**Files/Folders in Root:**", dir_list)

    # --- New Detailed Check ---
    st.write("**Detailed File Check (using repr):**")
    found_it = False
    target_name = 'data_manager.py'
    st.write(f"Looking for: {repr(target_name)}") # Show target representation

    for filename in dir_list:
        st.write(f"  -> Checking: {repr(filename)}") # Show exact representation
        if filename == target_name:
            st.success(f"      -> Found a match!: {repr(filename)}")
            found_it = True
            break # No need to check further once found

    st.write(f"**Is 'data_manager.py' present?** {found_it}")
    # --- End Detailed Check ---

    if not found_it:
        st.warning("Warning: 'data_manager.py' *still* not found via direct comparison. Check filename and location AGAIN. Maybe hidden characters? Or Reboot app.")

except Exception as e:
    st.error(f"Could not list directory: {e}")
st.markdown("---")
# --- End Enhanced Debugging V2 ---

# --- Import data_manager ---
try:
    st.write("Attempting: `import data_manager`...")
    import data_manager as dm
    st.success("✅ Successfully imported `data_manager` as `dm`.")

    st.write("Attempting: `from data_manager import data_manager`...")
    from data_manager import data_manager
    st.success("✅ Successfully imported the `data_manager` object.")

except ImportError as e:
    st.error(f"🚨 **ImportError Caught!**")
    st.error(f"**Error Details:** `{e}`")
    st.warning("Please check the Streamlit logs for the full traceback.")
    st.stop()
except Exception as e:
    st.error(f"🚨 **An Unexpected Error Occurred During Import!**")
    st.error(f"**Error Details:** `{e}`")
    st.stop()
# --- End Imports & Debugging ---


# --- START OF YOUR ORIGINAL TRADING DASHBOARD CODE ---

# Page config
# NOTE: st.set_page_config can only be called once per app, and must be the first Streamlit command.
# If this is not the main page, this might cause issues or be ignored.
# Consider moving set_page_config to your main app file (e.g., Home.py or your primary script).
try:
    st.set_page_config(page_title="🧠 AI Multi-Strategy Screener", layout="wide")
except st.errors.StreamlitAPIException as e:
    if "can only be called once per app" in str(e):
        st.caption("Note: Page config was already set.")
    else:
        raise e # Reraise other set_page_config errors

st.title("🧠 AI Multi-Strategy Stock Screener")

# Initialize session state
if 'screener_results' not in st.session_state:
    st.session_state.screener_results = {}
if 'trade_signals' not in st.session_state:
    st.session_state.trade_signals = []

# Sidebar for configuration
with st.sidebar:
    st.header("⚙️ Screener Settings")

    trade_type = st.selectbox(
        "Trading Strategy",
        ["🎯 All Signals", "⚡ Day Trade", "📊 Swing Trade", "💎 Position Trade"]
    )

    data_source = st.selectbox(
        "Data Source",
        ["📊 Manual Tickers", "🔥 Top Movers", "📈 Trending Stocks"]
    )

    st.markdown("---")
    st.subheader("🎛️ Advanced Filters")

    min_price = st.number_input("Min Price ($)", value=1.0, step=0.5, min_value=0.0)
    max_price = st.number_input("Max Price ($)", value=500.0, step=10.0, min_value=0.0)
    min_volume = st.number_input("Min Volume (M)", value=1.0, step=0.5, min_value=0.0) * 1_000_000

    use_options_flow = st.checkbox("Include Options Flow", value=False)
    use_news_sentiment = st.checkbox("Include News Sentiment", value=False)

# Main content area
tab1, tab2, tab3, tab4 = st.tabs(["📊 Scanner", "📈 Signals", "💼 Portfolio", "📚 Education"])

# Helper functions (Using your original functions - consider updating to use data_manager later)
def calculate_technical_indicators(ticker_symbol, period="1mo"):
    """Calculate comprehensive technical indicators"""
    try:
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period=period)

        if hist.empty:
            # st.caption(f"No history data for {ticker_symbol} for period {period}")
            return None

        current_price = hist['Close'].iloc[-1] if not hist['Close'].empty else None
        if current_price is None: return None


        hist['SMA_20'] = ta.trend.sma_indicator(hist['Close'], window=20)
        hist['SMA_50'] = ta.trend.sma_indicator(hist['Close'], window=50)
        hist['EMA_12'] = ta.trend.ema_indicator(hist['Close'], window=12)
        hist['EMA_26'] = ta.trend.ema_indicator(hist['Close'], window=26)
        hist['RSI'] = ta.momentum.RSIIndicator(hist['Close']).rsi()
        macd_obj = ta.trend.MACD(hist['Close'])
        hist['MACD'] = macd_obj.macd()
        hist['MACD_signal'] = macd_obj.macd_signal()
        hist['MACD_diff'] = macd_obj.macd_diff()
        bb_obj = ta.volatility.BollingerBands(hist['Close'])
        hist['BB_upper'] = bb_obj.bollinger_hband()
        hist['BB_lower'] = bb_obj.bollinger_lband()
        hist['OBV'] = ta.volume.OnBalanceVolumeIndicator(hist['Close'], hist['Volume']).on_balance_volume()
        hist['Volume_SMA'] = hist['Volume'].rolling(window=20).mean()
        
        support = hist['Low'].rolling(window=20).min().iloc[-1] if not hist['Low'].rolling(window=20).min().empty else current_price * 0.95 # Fallback
        resistance = hist['High'].rolling(window=20).max().iloc[-1] if not hist['High'].rolling(window=20).max().empty else current_price * 1.05 # Fallback

        volume_sma_last = hist['Volume_SMA'].iloc[-1] if not hist['Volume_SMA'].empty else 0
        volume_trend = (hist['Volume'].iloc[-1] / volume_sma_last) if volume_sma_last and volume_sma_last > 0 else 0

        # Ensure all keys exist, providing defaults for missing data
        return {
            'price': current_price,
            'sma_20': hist['SMA_20'].iloc[-1] if not hist['SMA_20'].empty else None,
            'sma_50': hist['SMA_50'].iloc[-1] if not hist['SMA_50'].empty else None,
            'rsi': hist['RSI'].iloc[-1] if not hist['RSI'].empty else None,
            'macd': hist['MACD'].iloc[-1] if not hist['MACD'].empty else None,
            'macd_signal': hist['MACD_signal'].iloc[-1] if not hist['MACD_signal'].empty else None,
            'bb_upper': hist['BB_upper'].iloc[-1] if not hist['BB_upper'].empty else None,
            'bb_lower': hist['BB_lower'].iloc[-1] if not hist['BB_lower'].empty else None,
            'support': support,
            'resistance': resistance,
            'volume_trend': volume_trend
        }
    except Exception as e:
        # st.warning(f"TA calc error for {ticker_symbol}: {e}") # Optional: show non-critical errors
        return None

def get_intraday_momentum(ticker_symbol):
    """Get intraday price action for day trading signals"""
    try:
        ticker = yf.Ticker(ticker_symbol)
        intraday = ticker.history(period="1d", interval="1m") # yfinance might use 1m or 2m depending on availability

        if intraday.empty:
            # st.caption(f"No intraday data for {ticker_symbol}")
            return None

        open_price = intraday['Open'].iloc[0] if not intraday['Open'].empty else None
        current_price = intraday['Close'].iloc[-1] if not intraday['Close'].empty else None
        if open_price is None or current_price is None: return None

        high = intraday['High'].max()
        low = intraday['Low'].min()
        price_position = (current_price - low) / (high - low) if (high - low) != 0 else 0.5 # Avoid division by zero
        
        avg_volume = intraday['Volume'].mean()
        # Ensure there are at least 5 data points for .iloc[-5:]
        current_volume_series = intraday['Volume'].iloc[-5:] if len(intraday['Volume']) >= 5 else intraday['Volume']
        current_volume = current_volume_series.mean() if not current_volume_series.empty else 0
        
        volume_surge = current_volume / avg_volume if avg_volume and avg_volume > 0 else 1
        price_change = ((current_price - open_price) / open_price) * 100 if open_price != 0 else 0

        return {
            'intraday_change': price_change, 'price_position': price_position,
            'volume_surge': volume_surge, 'day_high': high, 'day_low': low,
            'range': high - low
        }
    except Exception as e:
        # st.warning(f"Intraday error for {ticker_symbol}: {e}") # Optional
        return None

def calculate_ai_scores(ticker_data):
    """Calculate AI scores for different trading strategies"""
    scores = {'day_trade': 0, 'swing_trade': 0, 'position_trade': 0, 'overall': 0}
    
    # Ensure ticker_data and its nested dictionaries are not None
    if not ticker_data: return scores

    tech = ticker_data.get('technicals')
    intra = ticker_data.get('intraday')
    fund = ticker_data.get('fundamentals')

    # Day Trade Score
    if intra and tech and all(k in intra for k in ['intraday_change', 'volume_surge', 'price_position']):
        day_score = 0
        day_score += intra.get('intraday_change', 0) * 3
        day_score += intra.get('volume_surge', 0) * 15
        day_score += intra.get('price_position', 0) * 20
        day_score += ticker_data.get('short_pct', 0) * 2
        scores['day_trade'] = round(day_score, 2)

    # Swing Trade Score
    if tech and all(k in tech for k in ['rsi', 'price', 'sma_20', 'sma_50', 'macd', 'macd_signal', 'support']):
        swing_score = 0
        if 30 < tech.get('rsi', 50) < 70: swing_score += 10 # Default to neutral RSI if missing
        if tech.get('rsi', 50) < 30: swing_score += 20
        if tech.get('price', 0) > tech.get('sma_20', float('inf')) > tech.get('sma_50', float('inf')): swing_score += 25
        if tech.get('macd', 0) > tech.get('macd_signal', float('inf')): swing_score += 15
        if tech.get('price') and tech.get('support') and tech['price'] > 0 and abs(tech['price'] - tech['support']) / tech['price'] < 0.02:
             swing_score += 20
        scores['swing_trade'] = round(swing_score, 2)

    # Position Trade Score
    if fund and tech and tech.get('price') and tech.get('sma_50'):
        position_score = 0
        if 0 < fund.get('pe_ratio', 100) < 25: position_score += 20
        if fund.get('peg_ratio', 100) < 1.5: position_score += 25
        if fund.get('revenue_growth', 0) > 0.15: position_score += 20
        if tech.get('price', 0) > tech.get('sma_50', float('inf')): position_score += 15
        scores['position_trade'] = round(position_score, 2)

    scores['overall'] = round((scores.get('day_trade',0) + scores.get('swing_trade',0) + scores.get('position_trade',0)) / 3, 2)
    return scores

def analyze_stock(ticker_symbol):
    """Comprehensive stock analysis"""
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
        if not info or info.get('regularMarketPrice') is None and info.get('currentPrice') is None:
            # st.caption(f"Insufficient data from yfinance for {ticker_symbol}")
            return None


        data = {
            'ticker': ticker_symbol,
            'price': info.get('regularMarketPrice', info.get('currentPrice', 0)), # Fallback for currentPrice
            'change': info.get('regularMarketChangePercent', 0) * 100, # Show as %
            'volume': info.get('regularMarketVolume', 0),
            'avg_volume': info.get('averageDailyVolume10Day', info.get('averageVolume', 1)), # Prefer 10-day avg
            'rvol': 0, # Will calculate below
            'market_cap': info.get('marketCap', 0),
            'short_pct': info.get('shortPercentOfFloat', 0) * 100 if info.get('shortPercentOfFloat') else 0,
        }
        if data['avg_volume'] and data['avg_volume'] > 0: # Ensure avg_volume is not None and > 0
            data['rvol'] = data['volume'] / data['avg_volume']
        
        data['fundamentals'] = {
            'pe_ratio': info.get('trailingPE'), 'peg_ratio': info.get('pegRatio'),
            'revenue_growth': info.get('revenueGrowth'), 'profit_margins': info.get('profitMargins'),
            'debt_to_equity': info.get('debtToEquity')
        }
        data['technicals'] = calculate_technical_indicators(ticker_symbol)
        data['intraday'] = get_intraday_momentum(ticker_symbol)
        
        # Ensure technicals and intraday are not None before calculating scores
        if data['technicals'] is None or data['intraday'] is None:
             # st.caption(f"Skipping AI scores for {ticker_symbol} due to missing TA/Intraday data.")
             data['scores'] = {'day_trade': 0, 'swing_trade': 0, 'position_trade': 0, 'overall': 0}
        else:
            data['scores'] = calculate_ai_scores(data)


        signals = []
        if data['scores'].get('day_trade', 0) > 60: signals.append("⚡ DAY")
        if data['scores'].get('swing_trade', 0) > 70: signals.append("📊 SWING")
        if data['scores'].get('position_trade', 0) > 75: signals.append("💎 POSITION")
        data['signals'] = signals

        return data
    except Exception as e:
        st.error(f"Error analyzing {ticker_symbol}: {str(e)}")
        return None

# Tab 1: Scanner
with tab1:
    st.header("🔍 Stock Scanner")

    if data_source == "📊 Manual Tickers":
        default_tickers = "NVDA,TSLA,AAPL,AMD,MSFT,META,GOOGL,AMZN,NFLX,PLTR"
        ticker_input = st.text_area("Enter tickers (comma-separated):", value=default_tickers, height=100)
        tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
    elif data_source == "🔥 Top Movers":
        # In a real app, fetch this from an API
        tickers = ["NVDA", "TSLA", "AMD", "SOFI", "PLTR", "RIVN", "LCID", "NIO", "MARA", "RIOT"]
        st.info(f"Scanning top {len(tickers)} movers (sample data)...")
    else: # Trending
        # In a real app, fetch this from an API
        tickers = ["NVDA", "TSLA", "AAPL", "SPY", "QQQ", "AMD", "MSFT", "META", "GOOGL", "AMZN"]
        st.info(f"Scanning {len(tickers)} trending stocks (sample data)...")

    if st.button("🚀 Run Analysis", type="primary", key="run_analysis_button"):
        if not tickers:
            st.warning("Please enter some tickers or select a dynamic data source.")
        else:
            progress_bar = st.progress(0, text="Initializing Analysis...")
            results = []
            total_tickers = len(tickers)

            with ThreadPoolExecutor(max_workers=10) as executor:
                future_to_ticker = {executor.submit(analyze_stock, ticker): ticker for ticker in tickers}
                for i, future in enumerate(future_to_ticker):
                    ticker_name = future_to_ticker[future]
                    progress_text = f"Analyzing {ticker_name} ({i+1}/{total_tickers})..."
                    progress_bar.progress((i + 1) / total_tickers, text=progress_text)
                    try:
                        result = future.result()
                        if result:
                            results.append(result)
                    except Exception as e:
                        st.error(f"Analysis failed for {ticker_name}: {e}")
            
            progress_bar.empty() # Remove progress bar after completion
            st.session_state.screener_results = results

            if results:
                summary_data = []
                for r_idx, r_val in enumerate(results):
                    if not (r_val and isinstance(r_val.get('price'), (int, float)) and
                            isinstance(r_val.get('volume'), (int, float)) and
                            r_val.get('technicals') and r_val.get('scores')):
                        # st.caption(f"Skipping result for {r_val.get('ticker', 'Unknown')} due to missing core data.")
                        continue

                    price = r_val['price']
                    volume = r_val['volume']
                    scores = r_val['scores']

                    if not (min_price <= price <= max_price and volume >= min_volume):
                        continue

                    passes_trade_type_filter = True
                    if trade_type != "🎯 All Signals":
                        if trade_type == "⚡ Day Trade" and scores.get('day_trade', 0) < 60: passes_trade_type_filter = False
                        elif trade_type == "📊 Swing Trade" and scores.get('swing_trade', 0) < 70: passes_trade_type_filter = False
                        elif trade_type == "💎 Position Trade" and scores.get('position_trade', 0) < 75: passes_trade_type_filter = False
                    
                    if not passes_trade_type_filter:
                        continue

                    summary_data.append({
                        'Ticker': r_val.get('ticker', 'N/A'),
                        'Price': f"${price:.2f}",
                        '% Change': f"{r_val.get('change', 0):.2f}%",
                        'RVOL': f"{r_val.get('rvol', 0):.2f}",
                        'Signals': ' '.join(r_val.get('signals', [])),
                        'Day Score': scores.get('day_trade', 0),
                        'Swing Score': scores.get('swing_trade', 0),
                        'Position Score': scores.get('position_trade', 0),
                        'AI Score': scores.get('overall', 0)
                    })

                if summary_data:
                    df = pd.DataFrame(summary_data).sort_values('AI Score', ascending=False)
                    
                    # Metrics
                    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
                    col_m1.metric("Stocks Matched Filters", len(df))
                    col_m2.metric("Total Buy Signals", len([d for d in summary_data if d['Signals']]))
                    avg_ai_score = df['AI Score'].mean() if not df.empty else 0
                    col_m3.metric("Avg AI Score", f"{avg_ai_score:.1f}")
                    top_pick_ticker = df.iloc[0]['Ticker'] if not df.empty else "N/A"
                    col_m4.metric("Top Pick", top_pick_ticker)
                    
                    st.dataframe(df.style.background_gradient(subset=['AI Score'], cmap='RdYlGn'), use_container_width=True)
                    st.session_state.trade_signals = summary_data # Save filtered signals
                else:
                    st.warning("No stocks matched your filter criteria after analysis.")
            else:
                st.info("No analysis results. Check if tickers were provided and if analysis functions ran correctly.")

# Tab 2: Detailed Signals
with tab2:
    st.header("📈 Trading Signals")
    if st.session_state.get('trade_signals'): # Use .get for safety
        for signal_idx, signal_data in enumerate(st.session_state.trade_signals[:5]): # Top 5
            ticker_key = signal_data.get('Ticker', f"unknown_ticker_{signal_idx}")
            with st.expander(f"{signal_data.get('Ticker','N/A')} - {signal_data.get('Signals','No Signals')}"):
                # Find full data from screener_results (which is unfiltered)
                full_data = next((r for r in st.session_state.get('screener_results', []) 
                                  if r and r.get('ticker') == signal_data.get('Ticker')), None)
                
                if full_data and full_data.get('technicals'):
                    tech = full_data['technicals']
                    col_d1, col_d2 = st.columns(2)
                    with col_d1:
                        st.subheader("📊 Technical Analysis")
                        st.write(f"**Price:** ${tech.get('price', 0):.2f}")
                        st.write(f"**RSI:** {tech.get('rsi', 'N/A')}") # RSI can be float
                        st.write(f"**Support:** ${tech.get('support', 0):.2f}")
                        st.write(f"**Resistance:** ${tech.get('resistance', 0):.2f}")
                        
                        st.markdown("### 🎯 Trade Setup")
                        entry = tech.get('price')
                        stop = tech.get('support')
                        target = tech.get('resistance')

                        if all(isinstance(val, (int, float)) for val in [entry, stop, target]) and entry > stop and target > entry : # Check all are numbers
                            risk_reward = (target - entry) / (entry - stop) if (entry - stop) != 0 else 0
                            st.write(f"**Entry:** ${entry:.2f}")
                            st.write(f"**Stop Loss:** ${stop:.2f} (-{((entry-stop)/entry*100):.1f}%)" if entry != 0 else "N/A")
                            st.write(f"**Target:** ${target:.2f} (+{((target-entry)/entry*100):.1f}%)" if entry != 0 else "N/A")
                            st.write(f"**Risk/Reward:** 1:{risk_reward:.1f}")
                        else:
                            st.warning("Cannot calculate trade setup (invalid or missing S/R/Price levels).")
                            
                    with col_d2:
                        st.subheader("💰 Position Sizing")
                        account_size = st.number_input("Account Size ($)", value=10000, step=1000, min_value=0, key=f"acc_{ticker_key}_{signal_idx}")
                        risk_pct = st.slider("Risk per trade (%)", 1, 5, 2, key=f"risk_{ticker_key}_{signal_idx}")
                        
                        if all(isinstance(val, (int, float)) for val in [entry, stop]) and entry > stop:
                            risk_amount = account_size * (risk_pct / 100)
                            stop_distance = entry - stop
                            shares = int(risk_amount / stop_distance) if stop_distance > 0 else 0
                            position_size = shares * entry
                            
                            st.write(f"**Risk Amount:** ${risk_amount:.2f}")
                            st.write(f"**Shares to Buy:** {shares}")
                            st.write(f"**Position Size:** ${position_size:.2f}")
                            if account_size > 0:
                                st.write(f"**% of Account:** {(position_size/account_size*100):.1f}%")
                            else:
                                st.write(f"**% of Account:** N/A (Account size is 0)")
                        else:
                            st.warning("Cannot calculate position size (invalid entry/stop).")
                else:
                    st.warning(f"Could not retrieve full technical data for {signal_data.get('Ticker','N/A')}.")
    else:
        st.info("Run the scanner first to see detailed signals. No signals found in session state.")

# Tab 3: Portfolio Tracker
with tab3:
    st.header("💼 Portfolio Tracker")
    st.info("Portfolio tracking coming soon! This will track your positions, P&L, and performance metrics.")

# Tab 4: Education
with tab4:
    st.header("📚 Trading Education")
    # Keeping your markdown content concise for this example
    with st.expander("⚡ Day Trading Strategy"):
        st.markdown("""
        ### Entry Criteria:
        1. **AI Day Score > 60**
        2. **RVOL > 2.0** (High relative volume)
        3. **Price breaking VWAP or key level**
        4. **Strong market (SPY green)**
        ### Risk Management:
        - Max 1-2% risk per trade
        - Stop loss at previous candle low
        - Take profits at resistance or 2:1 R/R
        - No overnight positions
        ### Best Times:
        - First 30 minutes (9:30-10:00 AM ET)
        - Power hour (3:00-4:00 PM ET)
        """)
    with st.expander("📊 Swing Trading Strategy"):
        st.markdown("""
        ### Entry Criteria:
        1. **AI Swing Score > 70**
        2. **RSI oversold bounce or momentum**
        3. **Price above 20 SMA**
        4. **Volume confirmation**
        ### Position Management:
        - Hold 2-10 days
        - Trail stop at 20 SMA
        - Scale out at targets
        - Position size: 5-10% of account
        ### Best Setups:
        - Bull flag breakouts
        - Oversold bounces at support
        - Moving average reclaims
        """)
    with st.expander("💎 Position Trading Strategy"):
        st.markdown("""
        ### Entry Criteria:
        1. **AI Position Score > 75**
        2. **Strong fundamentals (PE < 25, Growth > 15%)**
        3. **Technical uptrend (above 50 SMA)**
        4. **Sector leadership**
        ### Long-term Approach:
        - Hold weeks to months
        - Add on dips to support
        - Rebalance quarterly
        - Focus on quality over quantity
        """)

# Footer
st.markdown("---")
st.markdown("🚨 **Disclaimer:** This tool is for educational purposes. Always do your own research and manage risk appropriately.")
