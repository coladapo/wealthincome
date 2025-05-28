import streamlit as st
import sys
import os
import pandas as pd
import yfinance as yf
import numpy as np
from datetime import datetime, timedelta
import ta
import requests
from concurrent.futures import ThreadPoolExecutor
import json

# --- Start of Path Fix ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)
# --- End of Path Fix ---

# --- Import data_manager ---
data_manager_instance = None 
try:
    from data_manager import data_manager as dm_instance 
    data_manager_instance = dm_instance 
except ImportError as e:
    st.error(f"Could not import data_manager: {e}. Some features might be limited.")
except Exception as e:
    st.error(f"An unexpected error occurred during data_manager import: {e}")
# --- End Imports ---

# Page config
try:
    st.set_page_config(page_title="🧠 AI Multi-Strategy Screener", layout="wide")
except st.errors.StreamlitAPIException:
    pass

st.title("🧠 AI Multi-Strategy Stock Screener")

# Initialize session state
if 'screener_results' not in st.session_state:
    st.session_state.screener_results = [] 
if 'trade_signals' not in st.session_state:
    st.session_state.trade_signals = []

# Sidebar for configuration
with st.sidebar:
    st.header("⚙️ Screener Settings")
    trade_type = st.selectbox(
        "Trading Strategy",
        ["🎯 All Signals", "⚡ Day Trade", "📊 Swing Trade", "💎 Position Trade"],
        key="trade_type_selector"
    )
    data_source = st.selectbox(
        "Data Source",
        ["📊 Manual Tickers", "🔥 Top Movers", "📈 Trending Stocks", "📋 My Watchlist"],
        key="data_source_selector"
    )
    st.markdown("---")
    st.subheader("🎛️ Advanced Filters")
    min_price = st.number_input("Min Price ($)", value=1.0, step=0.5, min_value=0.0, key="min_price_input")
    max_price = st.number_input("Max Price ($)", value=500.0, step=10.0, min_value=0.0, key="max_price_input")
    min_volume_m = st.number_input("Min Volume (M)", value=1.0, step=0.5, min_value=0.0, key="min_volume_input")
    min_volume = min_volume_m * 1_000_000
    use_news_sentiment = st.checkbox("Include News Sentiment", value=True, key="news_sentiment_checkbox")
    debug_mode = st.checkbox("Debug Mode", value=False, help="Show detailed analysis info")

# Main content area
tab1, tab2, tab3, tab4 = st.tabs(["📊 Scanner", "📈 Signals", "💼 Portfolio", "📚 Education"])

# Enhanced analyze_stock function using data_manager
def analyze_stock(ticker_symbol):
    """Analyze a stock using data_manager for consistency with other modules"""
    try:
        # Use data_manager for stock data
        if data_manager_instance:
            stock_data = data_manager_instance.get_stock_data([ticker_symbol], period="1mo")
            if not stock_data or ticker_symbol not in stock_data:
                return None
            
            ticker_data = stock_data[ticker_symbol]
            info = ticker_data.get('info', {})
            hist = ticker_data.get('history')
            
            # Basic stock data
            data = {
                'ticker': ticker_symbol,
                'price': info.get('regularMarketPrice', info.get('currentPrice', 0)),
                'change': info.get('regularMarketChangePercent', 0),
                'volume': info.get('regularMarketVolume', 0),
                'avg_volume': info.get('averageVolume', 1),
                'market_cap': info.get('marketCap', 0),
                'short_pct': info.get('shortPercentOfFloat', 0) * 100 if info.get('shortPercentOfFloat') else 0
            }
            
            # Calculate RVOL
            data['rvol'] = (data['volume'] / data['avg_volume']) if data['avg_volume'] > 0 else 0
            
            # Fundamentals
            data['fundamentals'] = {
                'pe_ratio': info.get('trailingPE'),
                'peg_ratio': info.get('pegRatio'),
                'revenue_growth': info.get('revenueGrowth'),
                'profit_margins': info.get('profitMargins'),
                'debt_to_equity': info.get('debtToEquity')
            }
            
            # Technical indicators
            if hist is not None and not hist.empty and len(hist) >= 20:
                data['technicals'] = {
                    'price': data['price'],
                    'sma_20': hist['Close'].rolling(20).mean().iloc[-1] if len(hist) >= 20 else None,
                    'sma_50': hist['Close'].rolling(50).mean().iloc[-1] if len(hist) >= 50 else None,
                    'rsi': data_manager_instance._calculate_rsi(hist['Close']) if data_manager_instance else None,
                    'support': hist['Low'].rolling(20).min().iloc[-1],
                    'resistance': hist['High'].rolling(20).max().iloc[-1],
                    'volume_trend': hist['Volume'].iloc[-1] / hist['Volume'].rolling(20).mean().iloc[-1] if hist['Volume'].rolling(20).mean().iloc[-1] > 0 else 0
                }
                
                # MACD
                if len(hist) >= 26:
                    exp1 = hist['Close'].ewm(span=12, adjust=False).mean()
                    exp2 = hist['Close'].ewm(span=26, adjust=False).mean()
                    data['technicals']['macd'] = (exp1 - exp2).iloc[-1]
                    data['technicals']['macd_signal'] = (exp1 - exp2).ewm(span=9, adjust=False).mean().iloc[-1]
                else:
                    data['technicals']['macd'] = None
                    data['technicals']['macd_signal'] = None
                    
                # Bollinger Bands
                sma = hist['Close'].rolling(20).mean()
                std = hist['Close'].rolling(20).std()
                data['technicals']['bb_upper'] = (sma + (std * 2)).iloc[-1]
                data['technicals']['bb_lower'] = (sma - (std * 2)).iloc[-1]
            else:
                data['technicals'] = None
                
            # Intraday momentum
            intraday = ticker_data.get('intraday')
            if intraday is not None and not intraday.empty:
                open_price = intraday['Open'].iloc[0]
                current_price = intraday['Close'].iloc[-1]
                high = intraday['High'].max()
                low = intraday['Low'].min()
                
                data['intraday'] = {
                    'intraday_change': ((current_price - open_price) / open_price * 100) if open_price > 0 else 0,
                    'price_position': (current_price - low) / (high - low) if (high - low) > 0 else 0.5,
                    'volume_surge': intraday['Volume'].iloc[-1] / intraday['Volume'].mean() if intraday['Volume'].mean() > 0 else 1,
                    'day_high': high,
                    'day_low': low,
                    'range': high - low
                }
            else:
                # Fallback for intraday data from info
                data['intraday'] = {
                    'intraday_change': data['change'],
                    'price_position': 0.5,
                    'volume_surge': data['rvol'],
                    'day_high': info.get('dayHigh', data['price']),
                    'day_low': info.get('dayLow', data['price']),
                    'range': info.get('dayHigh', data['price']) - info.get('dayLow', data['price'])
                }
            
            # Get news sentiment if enabled
            if use_news_sentiment and data_manager_instance:
                news_sentiment = data_manager_instance.get_latest_news_sentiment(ticker_symbol, debug_mode=debug_mode)
                data['news_sentiment'] = news_sentiment
            else:
                data['news_sentiment'] = None
                
            # Calculate AI scores using the enhanced function
            data['scores'] = calculate_ai_scores_enhanced(data)
            
            # Generate signals
            signals = []
            if data['scores'].get('day_trade', 0) > 60:
                signals.append("⚡ DAY")
            if data['scores'].get('swing_trade', 0) > 70:
                signals.append("📊 SWING")
            if data['scores'].get('position_trade', 0) > 75:
                signals.append("💎 POSITION")
            data['signals'] = signals
            
            return data
            
        else:
            # Fallback to basic analysis if data_manager not available
            st.warning(f"DataManager not available for {ticker_symbol}, using basic analysis")
            return None
            
    except Exception as e:
        if debug_mode:
            st.error(f"Error analyzing {ticker_symbol}: {str(e)}")
        return None

def calculate_ai_scores_enhanced(ticker_data):
    """Enhanced AI scoring that includes news sentiment"""
    scores = {'day_trade': 0, 'swing_trade': 0, 'position_trade': 0, 'overall': 0}
    if not ticker_data:
        return scores

    tech = ticker_data.get('technicals')
    intra = ticker_data.get('intraday')
    fund = ticker_data.get('fundamentals')
    news = ticker_data.get('news_sentiment')

    # Day Trading Score (focus on momentum and intraday metrics)
    if intra:
        # Intraday momentum (0-30 points)
        if intra.get('intraday_change', 0) > 2:
            scores['day_trade'] += min(intra['intraday_change'] * 3, 15)
        elif intra.get('intraday_change', 0) < -2:
            scores['day_trade'] -= 10
            
        # Price position in range (0-15 points)
        price_pos = intra.get('price_position', 0.5)
        if price_pos > 0.8:  # Near high of day
            scores['day_trade'] += 15
        elif price_pos > 0.6:
            scores['day_trade'] += 10
        elif price_pos < 0.2:  # Near low of day
            scores['day_trade'] -= 5
            
        # Volume surge (0-20 points)
        vol_surge = intra.get('volume_surge', 1)
        if vol_surge > 3:
            scores['day_trade'] += 20
        elif vol_surge > 2:
            scores['day_trade'] += 15
        elif vol_surge > 1.5:
            scores['day_trade'] += 10
            
    # Technical indicators scoring (applies to all strategies)
    if tech:
        rsi = tech.get('rsi')
        price = tech.get('price', 0)
        sma_20 = tech.get('sma_20')
        sma_50 = tech.get('sma_50')
        macd = tech.get('macd')
        macd_signal = tech.get('macd_signal')
        bb_upper = tech.get('bb_upper')
        bb_lower = tech.get('bb_lower')
        volume_trend = tech.get('volume_trend', 0)
        
        # RSI scoring
        if rsi is not None:
            # Day trade: extreme levels for quick reversals
            if 30 < rsi < 70:
                scores['day_trade'] += 10
            if rsi < 30:  # Oversold bounce potential
                scores['day_trade'] += 15
                scores['swing_trade'] += 20
            elif rsi > 70:  # Overbought but could continue
                scores['day_trade'] += 5
                scores['swing_trade'] -= 5
                
            # Swing/Position: prefer moderate RSI
            if 40 < rsi < 60:
                scores['swing_trade'] += 15
                scores['position_trade'] += 15
                
        # Moving average alignment
        if price and sma_20 and sma_50:
            # Bullish alignment
            if price > sma_20 > sma_50:
                scores['day_trade'] += 10
                scores['swing_trade'] += 20
                scores['position_trade'] += 25
            # Price above 20 SMA
            elif price > sma_20:
                scores['day_trade'] += 5
                scores['swing_trade'] += 10
                scores['position_trade'] += 10
            # Bearish alignment
            elif price < sma_20 < sma_50:
                scores['day_trade'] -= 10
                scores['swing_trade'] -= 15
                scores['position_trade'] -= 20
                
        # MACD scoring
        if macd is not None and macd_signal is not None:
            # Bullish crossover
            if macd > macd_signal:
                scores['day_trade'] += 10
                scores['swing_trade'] += 15
                scores['position_trade'] += 10
            # Strong momentum
            if macd > 0 and macd > macd_signal:
                scores['swing_trade'] += 10
                scores['position_trade'] += 15
                
        # Bollinger Bands
        if price and bb_upper and bb_lower:
            # Near lower band (oversold)
            if price < bb_lower * 1.02:
                scores['day_trade'] += 15
                scores['swing_trade'] += 20
            # Near upper band (overbought)
            elif price > bb_upper * 0.98:
                scores['day_trade'] += 5  # Could continue
                scores['swing_trade'] -= 5
                
        # Volume trend
        if volume_trend > 2:
            scores['day_trade'] += 15
            scores['swing_trade'] += 10
        elif volume_trend > 1.5:
            scores['day_trade'] += 10
            scores['swing_trade'] += 5
            
    # Fundamental scoring (mainly for position trading)
    if fund:
        pe_ratio = fund.get('pe_ratio')
        peg_ratio = fund.get('peg_ratio')
        revenue_growth = fund.get('revenue_growth')
        profit_margins = fund.get('profit_margins')
        debt_to_equity = fund.get('debt_to_equity')
        
        # PE Ratio
        if pe_ratio and 0 < pe_ratio < 25:
            scores['position_trade'] += 15
            scores['swing_trade'] += 5
        elif pe_ratio and pe_ratio > 50:
            scores['position_trade'] -= 10
            
        # PEG Ratio
        if peg_ratio and 0 < peg_ratio < 1.5:
            scores['position_trade'] += 15
            scores['swing_trade'] += 10
            
        # Revenue Growth
        if revenue_growth and revenue_growth > 0.2:
            scores['position_trade'] += 20
            scores['swing_trade'] += 10
        elif revenue_growth and revenue_growth > 0.1:
            scores['position_trade'] += 10
            scores['swing_trade'] += 5
            
        # Profit Margins
        if profit_margins and profit_margins > 0.2:
            scores['position_trade'] += 15
        elif profit_margins and profit_margins > 0.1:
            scores['position_trade'] += 10
            
        # Debt to Equity
        if debt_to_equity is not None:
            if debt_to_equity < 0.5:
                scores['position_trade'] += 10
            elif debt_to_equity > 2:
                scores['position_trade'] -= 10
    
    # Add relative volume boost for all strategies
    rvol = ticker_data.get('rvol', 0)
    if rvol > 3:
        scores['day_trade'] += 10
        scores['swing_trade'] += 5
    elif rvol > 2:
        scores['day_trade'] += 5
        scores['swing_trade'] += 3
        
    # Add market cap consideration
    market_cap = ticker_data.get('market_cap', 0)
    if market_cap > 10_000_000_000:  # Large cap
        scores['position_trade'] += 10
    elif market_cap > 2_000_000_000:  # Mid cap
        scores['swing_trade'] += 5
    elif 300_000_000 < market_cap < 2_000_000_000:  # Small cap
        scores['day_trade'] += 5
        
    # News sentiment boost/penalty
    if news:
        news_boost = 0
        if news['label'] == 'Positive':
            news_boost = news['score'] * 10  # Max +10 points
        elif news['label'] == 'Negative':
            news_boost = -abs(news['score']) * 10  # Max -10 points
            
        # Apply news boost to all scores
        scores['day_trade'] = scores['day_trade'] + news_boost * 1.5  # Day traders care more about news
        scores['swing_trade'] = scores['swing_trade'] + news_boost
        scores['position_trade'] = scores['position_trade'] + news_boost * 0.5  # Position traders care less about short-term news
    
    # Ensure scores are within 0-100 range and round
    for key in ['day_trade', 'swing_trade', 'position_trade']:
        scores[key] = round(max(0, min(100, scores[key])), 2)
    
    # Calculate overall score
    scores['overall'] = round((scores['day_trade'] + scores['swing_trade'] + scores['position_trade']) / 3, 2)
    
    return scores

with tab1:
    st.header("🔍 Stock Scanner")
    
    # Get tickers based on data source
    if data_source == "📊 Manual Tickers":
        default_tickers = "NVDA,TSLA,AAPL,AMD,MSFT,META,GOOGL,AMZN,NFLX,PLTR"
        ticker_input = st.text_area("Enter tickers (comma-separated):", value=default_tickers, height=100, key="ticker_text_area")
        tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
    elif data_source == "📋 My Watchlist":
        if data_manager_instance:
            watchlist = data_manager_instance.get_watchlist()
            if watchlist:
                tickers = watchlist
                st.info(f"Scanning {len(tickers)} stocks from your watchlist: {', '.join(tickers)}")
            else:
                st.warning("Your watchlist is empty. Add stocks in the Watchlist page.")
                tickers = []
        else:
            st.warning("DataManager not available. Cannot access watchlist.")
            tickers = []
    elif data_source == "🔥 Top Movers":
        tickers = ["NVDA", "TSLA", "AMD", "SOFI", "PLTR", "RIVN", "LCID", "NIO", "MARA", "RIOT"] 
        st.info(f"Scanning top {len(tickers)} movers...")
    else: 
        tickers = ["NVDA", "TSLA", "AAPL", "SPY", "QQQ", "AMD", "MSFT", "META", "GOOGL", "AMZN"] 
        st.info(f"Scanning {len(tickers)} trending stocks...")

    if st.button("🚀 Run Analysis", type="primary", key="run_analysis_button_main"):
        if not tickers:
            st.warning("Please enter some tickers or select a valid data source.")
        else:
            progress_bar = st.progress(0, text="Initializing Analysis...")
            results_list = [] 
            total_tickers = len(tickers)

            with ThreadPoolExecutor(max_workers=min(10, total_tickers)) as executor:
                future_to_ticker = {executor.submit(analyze_stock, ticker): ticker for ticker in tickers}
                for i, future in enumerate(future_to_ticker):
                    ticker_name = future_to_ticker[future]
                    progress_text = f"Analyzing {ticker_name} ({i+1}/{total_tickers})..."
                    progress_bar.progress((i + 1) / total_tickers, text=progress_text)
                    try:
                        result = future.result()
                        if result:
                            results_list.append(result)
                    except Exception as e:
                        if debug_mode:
                            st.error(f"Analysis failed for {ticker_name}: {e}")
            
            progress_bar.empty()
            st.session_state.screener_results = results_list

            if results_list:
                # Create summary data
                summary_data = []
                for r in results_list:
                    if not r or r.get('price') is None or r.get('volume') is None:
                        continue

                    price, volume, scores = r['price'], r['volume'], r['scores']
                    
                    # Apply filters
                    if not (min_price <= price <= max_price and volume >= min_volume):
                        continue
                    
                    # Apply trade type filter
                    passes_trade_type_filter = True
                    if trade_type != "🎯 All Signals":
                        if trade_type == "⚡ Day Trade" and scores.get('day_trade', 0) < 60:
                            passes_trade_type_filter = False
                        elif trade_type == "📊 Swing Trade" and scores.get('swing_trade', 0) < 70:
                            passes_trade_type_filter = False
                        elif trade_type == "💎 Position Trade" and scores.get('position_trade', 0) < 75:
                            passes_trade_type_filter = False
                    
                    if not passes_trade_type_filter:
                        continue
                    
                    # Add news sentiment to display
                    news_sentiment = "N/A"
                    if r.get('news_sentiment'):
                        news_label = r['news_sentiment'].get('label', 'N/A')
                        if news_label == 'Positive':
                            news_sentiment = "📈 Positive"
                        elif news_label == 'Negative':
                            news_sentiment = "📉 Negative"
                        else:
                            news_sentiment = "➡️ Neutral"

                    summary_data.append({
                        'Ticker': r.get('ticker', 'N/A'),
                        'Price': f"${price:.2f}",
                        '% Change': f"{r.get('change', 0):.2f}%",
                        'RVOL': f"{r.get('rvol', 0):.2f}",
                        'News': news_sentiment,
                        'Signals': ' '.join(r.get('signals', [])),
                        'Day Score': scores.get('day_trade', 0),
                        'Swing Score': scores.get('swing_trade', 0),
                        'Position Score': scores.get('position_trade', 0),
                        'AI Score': scores.get('overall', 0)
                    })
                
                if summary_data:
                    df = pd.DataFrame(summary_data).sort_values('AI Score', ascending=False)
                    
                    # Display metrics
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Stocks Matched", len(df))
                    col2.metric("Buy Signals", len([d for d in summary_data if d['Signals']]))
                    
                    # Count positive news
                    positive_news = len([d for d in summary_data if "Positive" in d['News']])
                    col3.metric("Positive News", positive_news)
                    
                    # Top pick
                    if not df.empty:
                        top_pick = df.iloc[0]['Ticker']
                        col4.metric("Top Pick", top_pick)
                    
                    # Display results with color coding
                    st.dataframe(
                        df.style.background_gradient(subset=['AI Score'], cmap='RdYlGn')
                        .background_gradient(subset=['Day Score', 'Swing Score', 'Position Score'], cmap='Blues'),
                        use_container_width=True
                    )
                    
                    st.session_state.trade_signals = summary_data
                else:
                    st.warning("No stocks matched your filter criteria after analysis.")
            else:
                st.info("No analysis results. Check if tickers were provided and analysis completed.")

with tab2:
    st.header("📈 Trading Signals")
    if st.session_state.get('trade_signals'):
        for signal_idx, signal_data in enumerate(st.session_state.trade_signals[:5]):
            ticker_symbol = signal_data.get('Ticker', f"unknown_{signal_idx}")
            
            # Get full data for this ticker
            full_data = next((r for r in st.session_state.get('screener_results', []) 
                            if r and r.get('ticker') == ticker_symbol), None)
            
            if not full_data:
                continue
                
            with st.expander(f"{ticker_symbol} - {signal_data.get('Signals', 'No Signals')} | {signal_data.get('News', '')}"):
                col1, col2 = st.columns(2)
                
                with col1:
                    st.subheader("📊 Technical Analysis")
                    tech = full_data.get('technicals', {})
                    if tech:
                        st.write(f"**Price:** ${tech.get('price', 0):.2f}")
                        
                        # RSI with proper display
                        rsi_val = tech.get('rsi')
                        if rsi_val is not None:
                            st.write(f"**RSI:** {rsi_val:.2f}")
                        else:
                            st.write("**RSI:** N/A")
                            
                        st.write(f"**Support:** ${tech.get('support', 0):.2f}")
                        st.write(f"**Resistance:** ${tech.get('resistance', 0):.2f}")
                        
                        # News sentiment display
                        if full_data.get('news_sentiment'):
                            news = full_data['news_sentiment']
                            st.markdown("### 📰 Latest News")
                            st.write(f"**Sentiment:** {news.get('label', 'N/A')} ({news.get('score', 0):.2f})")
                            st.write(f"**Headline:** {news.get('headline', 'N/A')[:100]}...")
                            st.write(f"**Source:** {news.get('source', 'N/A')}")
                            st.write(f"**Date:** {news.get('date', 'N/A')}")
                        
                        st.markdown("### 🎯 Trade Setup")
                        entry = tech.get('price')
                        stop = tech.get('support')
                        target = tech.get('resistance')

                        if all(val is not None and val > 0 for val in [entry, stop, target]) and entry > stop and target > entry:
                            risk_reward = (target - entry) / (entry - stop)
                            st.write(f"**Entry:** ${entry:.2f}")
                            st.write(f"**Stop Loss:** ${stop:.2f} (-{((entry-stop)/entry*100):.1f}%)")
                            st.write(f"**Target:** ${target:.2f} (+{((target-entry)/entry*100):.1f}%)")
                            st.write(f"**Risk/Reward:** 1:{risk_reward:.1f}")
                        else:
                            st.warning("Cannot calculate trade setup - invalid price levels")
                    
                with col2:
                    st.subheader("💰 Position Sizing")
                    account_size = st.number_input(
                        "Account Size ($)", 
                        value=10000, 
                        step=1000, 
                        min_value=0, 
                        key=f"acc_{ticker_symbol}_{signal_idx}"
                    )
                    risk_pct = st.slider(
                        "Risk per trade (%)", 
                        1, 5, 2, 
                        key=f"risk_{ticker_symbol}_{signal_idx}"
                    )
                    
                    if tech and tech.get('price') and tech.get('support'):
                        entry = tech['price']
                        stop = tech['support']
                        
                        if entry > stop:
                            risk_amount = account_size * (risk_pct / 100)
                            stop_distance = entry - stop
                            shares = int(risk_amount / stop_distance)
                            position_size = shares * entry
                            
                            st.write(f"**Risk Amount:** ${risk_amount:.2f}")
                            st.write(f"**Shares to Buy:** {shares}")
                            st.write(f"**Position Size:** ${position_size:.2f}")
                            st.write(f"**% of Account:** {(position_size/account_size*100):.1f}%")
                            
                            # Score breakdown
                            st.markdown("### 🎯 Score Breakdown")
                            scores = full_data.get('scores', {})
                            st.write(f"**Day Trade Score:** {scores.get('day_trade', 0):.0f}/100")
                            st.write(f"**Swing Trade Score:** {scores.get('swing_trade', 0):.0f}/100")
                            st.write(f"**Position Score:** {scores.get('position_trade', 0):.0f}/100")
                            st.write(f"**Overall AI Score:** {scores.get('overall', 0):.0f}/100")
                            
                            # 💡 SIMULATE TRADE BUTTON - AUTO-NAVIGATION FIX
                            st.markdown("---")
                            col_sim1, col_sim2 = st.columns(2)
                            
                            with col_sim1:
                                if st.button("🧾 Simulate Trade", key=f"sim_{ticker_symbol}_{signal_idx}", type="primary", use_container_width=True):
                                    # Determine trade type based on highest score
                                    if scores.get('day_trade', 0) >= scores.get('swing_trade', 0) and scores.get('day_trade', 0) >= scores.get('position_trade', 0):
                                        trade_type_sim = "Day Trade"
                                    elif scores.get('swing_trade', 0) >= scores.get('position_trade', 0):
                                        trade_type_sim = "Swing Trade"
                                    else:
                                        trade_type_sim = "Position Trade"
                                    
                                    # Set session state for paper trading
                                    st.session_state['prefill_ticker'] = ticker_symbol
                                    st.session_state['prefill_entry'] = entry
                                    st.session_state['prefill_exit'] = target if target else entry * 1.05
                                    st.session_state['prefill_type'] = trade_type_sim
                                    st.session_state['prefill_notes'] = f"AI Score: {scores.get('overall', 0):.0f} | News: {news.get('label', 'N/A') if full_data.get('news_sentiment') else 'N/A'}"
                                    
                                    # Auto-navigate to Paper Trading page
                                    st.switch_page("pages/6_🧾_Paper_Trading.py")
                            
                            with col_sim2:
                                if st.button("📓 Add to Journal", key=f"journal_{ticker_symbol}_{signal_idx}", use_container_width=True):
                                    st.session_state['journal_ticker'] = ticker_symbol
                                    st.switch_page("pages/5_📓_Journal.py")
    else:
        st.info("Run the scanner first to see detailed signals.")

with tab3:
    st.header("💼 Portfolio Tracker")
    if data_manager_instance:
        portfolio_stats = data_manager_instance.analyze_portfolio_performance()
        if portfolio_stats and portfolio_stats.get('total_trades', 0) > 0:
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric("Total Trades", portfolio_stats['total_trades'])
            with col2:
                st.metric("Win Rate", f"{portfolio_stats['win_rate']*100:.1f}%")
            with col3:
                st.metric("Total P&L", f"${portfolio_stats['total_pnl']:.2f}")
            with col4:
                st.metric("Profit Factor", f"{portfolio_stats.get('profit_factor', 0):.2f}")
                
            # Trade journal preview
            trades = data_manager_instance.get_trade_journal()
            if trades:
                st.subheader("Recent Trades")
                recent_trades = trades[-5:]  # Last 5 trades
                trade_df = pd.DataFrame(recent_trades)
                st.dataframe(trade_df, use_container_width=True)
        else:
            st.info("No trades recorded yet. Start adding trades in the Trade Journal page.")
    else:
        st.warning("Portfolio tracking requires DataManager. Please ensure it's properly configured.")

with tab4:
    st.header("📚 Trading Education")
    
    # Enhanced education content with news sentiment integration
    with st.expander("📰 Using News Sentiment in Trading"):
        st.markdown("""
        ### How News Sentiment Affects Scores:
        
        **Day Trading (1.5x multiplier)**
        - News has highest impact on day trades
        - Positive news can add up to 15 points
        - Negative news can subtract up to 15 points
        - React quickly to breaking news
        
        **Swing Trading (1x multiplier)**
        - Moderate impact from news
        - Look for sustained sentiment trends
        - Combine with technical levels
        
        **Position Trading (0.5x multiplier)**
        - Least affected by short-term news
        - Focus on fundamental changes
        - Consider long-term sentiment shifts
        
        ### Best Practices:
        1. **Positive News + Technical Breakout** = Strong Buy Signal
        2. **Negative News + Support Break** = Strong Sell Signal
        3. **Mixed Sentiment** = Wait for clarity
        4. Always confirm news with price action
        """)
    
    with st.expander("⚡ Day Trading Strategy"):
        st.markdown("""
        ### Entry Criteria:
        1. **AI Day Score > 60**
        2. **RVOL > 2.0** (High relative volume)
        3. **Positive news sentiment** (for longs)
        4. **Price breaking VWAP or key level**
        5. **Strong market (SPY green)**
        
        ### Risk Management:
        - Max 1-2% risk per trade
        - Stop loss at previous candle low
        - Take profits at resistance or 2:1 R/R
        - No overnight positions
        - Exit if news sentiment changes
        
        ### Best Times:
        - First 30 minutes (9:30-10:00 AM ET)
        - Power hour (3:00-4:00 PM ET)
        - Immediately after news releases
        """)
    
    with st.expander("📊 Swing Trading Strategy"):
        st.markdown("""
        ### Entry Criteria:
        1. **AI Swing Score > 70**
        2. **RSI oversold bounce or momentum**
        3. **Price above 20 SMA**
        4. **Volume confirmation**
        5. **Sustained positive sentiment**
        
        ### Position Management:
        - Hold 2-10 days
        - Trail stop at 20 SMA
        - Scale out at targets
        - Position size: 5-10% of account
        - Monitor daily news flow
        
        ### Best Setups:
        - Bull flag breakouts with positive news
        - Oversold bounces at support
        - Moving average reclaims
        - Sentiment shifts from negative to positive
        """)
    
    with st.expander("💎 Position Trading Strategy"):
        st.markdown("""
        ### Entry Criteria:
        1. **AI Position Score > 75**
        2. **Strong fundamentals (PE < 25, Growth > 15%)**
        3. **Technical uptrend (above 50 SMA)**
        4. **Sector leadership**
        5. **Long-term positive sentiment trend**
        
        ### Long-term Approach:
        - Hold weeks to months
        - Add on dips to support
        - Rebalance quarterly
        - Focus on quality over quantity
        - Track sentiment shifts over time
        """)

st.markdown("---")
st.markdown("🚨 **Disclaimer:** This tool is for educational purposes. Always do your own research and manage risk appropriately.")
