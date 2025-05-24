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
            
    st.write(f"**Is 'data_manager.py' present?** {found_it}")
    # --- End Detailed Check ---

    if not found_it:
        st.warning("Warning: 'data_manager.py' *still* not found via direct comparison. Check for hidden characters/typos or try rebooting the app.")

except Exception as e:
    st.error(f"Could not list directory: {e}")
st.markdown("---")
# --- End Enhanced Debugging V2 ---

# --- Rest of your import code ---
try:
    st.write("Attempting: `import data_manager`...")
    import data_manager as dm # Keep this line
    st.success("✅ Successfully imported `data_manager` as `dm`.")

    st.write("Attempting: `from data_manager import data_manager`...")
    from data_manager import data_manager # Keep this line
    st.success("✅ Successfully imported the `data_manager` object.")

except ImportError as e:
    st.error(f"🚨 **ImportError Caught!**")
    st.error(f"**Error Details:** `{e}`") # This will show the actual error.
    st.stop()
except Exception as e:
    st.error(f"🚨 **An Unexpected Error Occurred During Import!**")
    st.error(f"**Error Details:** `{e}`")
    st.stop()

# --- Rest of your page code ---
# ...



import streamlit as st
import pandas as pd
import yfinance as yf
import numpy as np
from datetime import datetime, timedelta
import ta  # Technical analysis library
import requests
from concurrent.futures import ThreadPoolExecutor
import json

# Page config
st.set_page_config(page_title="🧠 AI Multi-Strategy Screener", layout="wide")
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
    
    min_price = st.number_input("Min Price ($)", value=1.0, step=0.5)
    max_price = st.number_input("Max Price ($)", value=500.0, step=10.0)
    min_volume = st.number_input("Min Volume (M)", value=1.0, step=0.5) * 1_000_000
    
    use_options_flow = st.checkbox("Include Options Flow", value=False)
    use_news_sentiment = st.checkbox("Include News Sentiment", value=False)

# Main content area
tab1, tab2, tab3, tab4 = st.tabs(["📊 Scanner", "📈 Signals", "💼 Portfolio", "📚 Education"])

# Helper functions
def calculate_technical_indicators(ticker_symbol, period="1mo"):
    """Calculate comprehensive technical indicators"""
    try:
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period=period)
        
        if hist.empty:
            return None
            
        # Price action
        current_price = hist['Close'].iloc[-1]
        
        # Moving averages
        hist['SMA_20'] = ta.trend.sma_indicator(hist['Close'], window=20)
        hist['SMA_50'] = ta.trend.sma_indicator(hist['Close'], window=50)
        hist['EMA_12'] = ta.trend.ema_indicator(hist['Close'], window=12)
        hist['EMA_26'] = ta.trend.ema_indicator(hist['Close'], window=26)
        
        # RSI
        hist['RSI'] = ta.momentum.RSIIndicator(hist['Close']).rsi()
        
        # MACD
        macd = ta.trend.MACD(hist['Close'])
        hist['MACD'] = macd.macd()
        hist['MACD_signal'] = macd.macd_signal()
        hist['MACD_diff'] = macd.macd_diff()
        
        # Bollinger Bands
        bb = ta.volatility.BollingerBands(hist['Close'])
        hist['BB_upper'] = bb.bollinger_hband()
        hist['BB_lower'] = bb.bollinger_lband()
        
        # Volume indicators
        hist['OBV'] = ta.volume.OnBalanceVolumeIndicator(hist['Close'], hist['Volume']).on_balance_volume()
        hist['Volume_SMA'] = hist['Volume'].rolling(window=20).mean()
        
        # Support/Resistance (simplified)
        support = hist['Low'].rolling(window=20).min().iloc[-1]
        resistance = hist['High'].rolling(window=20).max().iloc[-1]
        
        return {
            'price': current_price,
            'sma_20': hist['SMA_20'].iloc[-1],
            'sma_50': hist['SMA_50'].iloc[-1],
            'rsi': hist['RSI'].iloc[-1],
            'macd': hist['MACD'].iloc[-1],
            'macd_signal': hist['MACD_signal'].iloc[-1],
            'bb_upper': hist['BB_upper'].iloc[-1],
            'bb_lower': hist['BB_lower'].iloc[-1],
            'support': support,
            'resistance': resistance,
            'volume_trend': hist['Volume'].iloc[-1] / hist['Volume_SMA'].iloc[-1]
        }
    except:
        return None

def get_intraday_momentum(ticker_symbol):
    """Get intraday price action for day trading signals"""
    try:
        ticker = yf.Ticker(ticker_symbol)
        # Get 1-day 1-minute data
        intraday = ticker.history(period="1d", interval="1m")
        
        if intraday.empty:
            return None
            
        # Calculate intraday metrics
        open_price = intraday['Open'].iloc[0]
        current_price = intraday['Close'].iloc[-1]
        high = intraday['High'].max()
        low = intraday['Low'].min()
        
        # Price position in range
        price_position = (current_price - low) / (high - low) if high != low else 0.5
        
        # Volume profile
        avg_volume = intraday['Volume'].mean()
        current_volume = intraday['Volume'].iloc[-5:].mean()  # Last 5 minutes
        volume_surge = current_volume / avg_volume if avg_volume > 0 else 1
        
        # Momentum
        price_change = ((current_price - open_price) / open_price) * 100
        
        return {
            'intraday_change': price_change,
            'price_position': price_position,
            'volume_surge': volume_surge,
            'day_high': high,
            'day_low': low,
            'range': high - low
        }
    except:
        return None

def calculate_ai_scores(ticker_data):
    """Calculate AI scores for different trading strategies"""
    scores = {}
    
    # Day Trade Score (focus on momentum and volume)
    day_score = 0
    if ticker_data.get('intraday'):
        intra = ticker_data['intraday']
        day_score += intra['intraday_change'] * 3  # Weight price change heavily
        day_score += intra['volume_surge'] * 15    # Volume surge is key
        day_score += intra['price_position'] * 20  # Breaking high of day
        day_score += ticker_data['short_pct'] * 2  # Short squeeze potential
    
    # Swing Trade Score (technical patterns)
    swing_score = 0
    if ticker_data.get('technicals'):
        tech = ticker_data['technicals']
        # RSI conditions
        if 30 < tech['rsi'] < 70:
            swing_score += 10
        if tech['rsi'] < 30:  # Oversold bounce
            swing_score += 20
            
        # Moving average alignment
        if tech['price'] > tech['sma_20'] > tech['sma_50']:
            swing_score += 25
            
        # MACD momentum
        if tech['macd'] > tech['macd_signal']:
            swing_score += 15
            
        # Price near support
        if abs(tech['price'] - tech['support']) / tech['price'] < 0.02:
            swing_score += 20
    
    # Position Trade Score (fundamentals + technicals)
    position_score = 0
    if ticker_data.get('fundamentals'):
        fund = ticker_data['fundamentals']
        # Value metrics
        if 0 < fund.get('pe_ratio', 100) < 25:
            position_score += 20
        if fund.get('peg_ratio', 100) < 1.5:
            position_score += 25
        # Growth metrics  
        if fund.get('revenue_growth', 0) > 0.15:
            position_score += 20
        # Technical confirmation
        if ticker_data.get('technicals'):
            if ticker_data['technicals']['price'] > ticker_data['technicals']['sma_50']:
                position_score += 15
    
    scores['day_trade'] = round(day_score, 2)
    scores['swing_trade'] = round(swing_score, 2)
    scores['position_trade'] = round(position_score, 2)
    scores['overall'] = round((day_score + swing_score + position_score) / 3, 2)
    
    return scores

def analyze_stock(ticker_symbol):
    """Comprehensive stock analysis"""
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
        
        # Basic data
        data = {
            'ticker': ticker_symbol,
            'price': info.get('regularMarketPrice', 0),
            'change': info.get('regularMarketChangePercent', 0),
            'volume': info.get('regularMarketVolume', 0),
            'avg_volume': info.get('averageVolume', 1),
            'rvol': info.get('regularMarketVolume', 0) / info.get('averageVolume', 1),
            'market_cap': info.get('marketCap', 0),
            'short_pct': info.get('shortPercentOfFloat', 0) * 100,
        }
        
        # Add fundamental data
        data['fundamentals'] = {
            'pe_ratio': info.get('trailingPE', 0),
            'peg_ratio': info.get('pegRatio', 0),
            'revenue_growth': info.get('revenueGrowth', 0),
            'profit_margin': info.get('profitMargins', 0),
            'debt_to_equity': info.get('debtToEquity', 0)
        }
        
        # Get technical indicators
        data['technicals'] = calculate_technical_indicators(ticker_symbol)
        
        # Get intraday data for day trading
        data['intraday'] = get_intraday_momentum(ticker_symbol)
        
        # Calculate AI scores
        data['scores'] = calculate_ai_scores(data)
        
        # Generate signals
        signals = []
        if data['scores']['day_trade'] > 60:
            signals.append("⚡ DAY")
        if data['scores']['swing_trade'] > 70:
            signals.append("📊 SWING")
        if data['scores']['position_trade'] > 75:
            signals.append("💎 POSITION")
            
        data['signals'] = signals
        
        return data
    except Exception as e:
        st.error(f"Error analyzing {ticker_symbol}: {str(e)}")
        return None

# Tab 1: Scanner
with tab1:
    st.header("🔍 Stock Scanner")
    
    # Get tickers based on source
    if data_source == "📊 Manual Tickers":
        default_tickers = "NVDA,TSLA,AAPL,AMD,MSFT,META,GOOGL,AMZN,NFLX,PLTR"
        ticker_input = st.text_area(
            "Enter tickers (comma-separated):",
            value=default_tickers,
            height=100
        )
        tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
        
    elif data_source == "🔥 Top Movers":
        # In real implementation, fetch from API
        tickers = ["NVDA", "TSLA", "AMD", "SOFI", "PLTR", "RIVN", "LCID", "NIO", "MARA", "RIOT"]
        st.info(f"Scanning top {len(tickers)} movers...")
        
    else:  # Trending
        tickers = ["NVDA", "TSLA", "AAPL", "SPY", "QQQ", "AMD", "MSFT", "META", "GOOGL", "AMZN"]
        st.info(f"Scanning {len(tickers)} trending stocks...")
    
    if st.button("🚀 Run Analysis", type="primary"):
        progress_bar = st.progress(0)
        results = []
        
        # Analyze stocks in parallel
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(analyze_stock, ticker): ticker for ticker in tickers}
            
            for i, future in enumerate(futures):
                result = future.result()
                if result:
                    results.append(result)
                progress_bar.progress((i + 1) / len(tickers))
        
        progress_bar.empty()
        
        # Store results
        st.session_state.screener_results = results
        
        # Display results
        if results:
            # Create summary DataFrame
            summary_data = []
            for r in results:
                # Filter by price and volume
                if not (min_price <= r['price'] <= max_price and r['volume'] >= min_volume):
                    continue
                    
                # Filter by trade type
                if trade_type != "🎯 All Signals":
                    if trade_type == "⚡ Day Trade" and r['scores']['day_trade'] < 60:
                        continue
                    elif trade_type == "📊 Swing Trade" and r['scores']['swing_trade'] < 70:
                        continue
                    elif trade_type == "💎 Position Trade" and r['scores']['position_trade'] < 75:
                        continue
                
                summary_data.append({
                    'Ticker': r['ticker'],
                    'Price': f"${r['price']:.2f}",
                    '% Change': f"{r['change']:.2f}%",
                    'RVOL': f"{r['rvol']:.2f}",
                    'Signals': ' '.join(r['signals']),
                    'Day Score': r['scores']['day_trade'],
                    'Swing Score': r['scores']['swing_trade'],
                    'Position Score': r['scores']['position_trade'],
                    'AI Score': r['scores']['overall']
                })
            
            if summary_data:
                df = pd.DataFrame(summary_data)
                df = df.sort_values('AI Score', ascending=False)
                
                # Display metrics
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Stocks Analyzed", len(results))
                with col2:
                    st.metric("Buy Signals", len([d for d in summary_data if d['Signals']]))
                with col3:
                    avg_score = df['AI Score'].mean()
                    st.metric("Avg AI Score", f"{avg_score:.1f}")
                with col4:
                    top_pick = df.iloc[0]['Ticker'] if len(df) > 0 else "N/A"
                    st.metric("Top Pick", top_pick)
                
                # Display table
                st.dataframe(
                    df.style.background_gradient(subset=['AI Score'], cmap='RdYlGn'),
                    use_container_width=True
                )
                
                # Save signals to session state
                st.session_state.trade_signals = summary_data

# Tab 2: Detailed Signals
with tab2:
    st.header("📈 Trading Signals")
    
    if st.session_state.trade_signals:
        for signal in st.session_state.trade_signals[:5]:  # Top 5
            with st.expander(f"{signal['Ticker']} - {signal['Signals']}"):
                # Find full data
                full_data = next((r for r in st.session_state.screener_results 
                                if r['ticker'] == signal['Ticker']), None)
                
                if full_data:
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.subheader("📊 Technical Analysis")
                        if full_data.get('technicals'):
                            tech = full_data['technicals']
                            st.write(f"**Price:** ${tech['price']:.2f}")
                            st.write(f"**RSI:** {tech['rsi']:.2f}")
                            st.write(f"**Support:** ${tech['support']:.2f}")
                            st.write(f"**Resistance:** ${tech['resistance']:.2f}")
                            
                            # Trade setup
                            st.markdown("### 🎯 Trade Setup")
                            if "⚡ DAY" in signal['Signals']:
                                entry = tech['price']
                                stop = tech['support']
                                target = tech['resistance']
                                risk_reward = (target - entry) / (entry - stop) if entry > stop else 0
                                
                                st.write(f"**Entry:** ${entry:.2f}")
                                st.write(f"**Stop Loss:** ${stop:.2f} (-{((entry-stop)/entry*100):.1f}%)")
                                st.write(f"**Target:** ${target:.2f} (+{((target-entry)/entry*100):.1f}%)")
                                st.write(f"**Risk/Reward:** 1:{risk_reward:.1f}")
                    
                    with col2:
                        st.subheader("💰 Position Sizing")
                        account_size = st.number_input(
                            "Account Size ($)", 
                            value=10000, 
                            step=1000,
                            key=f"acc_{signal['Ticker']}"
                        )
                        risk_pct = st.slider(
                            "Risk per trade (%)", 
                            1, 5, 2,
                            key=f"risk_{signal['Ticker']}"
                        )
                        
                        if full_data.get('technicals'):
                            tech = full_data['technicals']
                            entry = tech['price']
                            stop = tech['support']
                            
                            risk_amount = account_size * (risk_pct / 100)
                            stop_distance = entry - stop
                            shares = int(risk_amount / stop_distance) if stop_distance > 0 else 0
                            position_size = shares * entry
                            
                            st.write(f"**Risk Amount:** ${risk_amount:.2f}")
                            st.write(f"**Shares to Buy:** {shares}")
                            st.write(f"**Position Size:** ${position_size:.2f}")
                            st.write(f"**% of Account:** {(position_size/account_size*100):.1f}%")
    else:
        st.info("Run the scanner first to see detailed signals")

# Tab 3: Portfolio Tracker
with tab3:
    st.header("💼 Portfolio Tracker")
    st.info("Portfolio tracking coming soon! This will track your positions, P&L, and performance metrics.")

# Tab 4: Education
with tab4:
    st.header("📚 Trading Education")
    
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
