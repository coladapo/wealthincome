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

# Import data_manager
try:
    from data_manager import data_manager
except ImportError as e:
    st.error(f"Could not import data_manager: {e}")
    data_manager = None

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

# Scoring System Documentation
with st.expander("📚 Understanding the Scoring System", expanded=False):
    st.markdown("""
    ### 🎯 The Four-Score System Explained
    
    Our AI analyzes stocks using four distinct scoring methodologies, each optimized for different trading styles:
    
    #### ⚡ **Day Score (0-100)** - For Intraday Trading
    - **What it measures**: Short-term momentum, volume surges, and intraday price action
    - **Key factors**:
        - Price movement today (30% weight)
        - Volume surge vs average (30% weight)
        - Price position in day's range (20% weight)
        - Technical momentum indicators (20% weight)
    - **Good score**: 60+ indicates strong day trading opportunity
    - **Use when**: You plan to enter and exit within the same day
    
    #### 📊 **Swing Score (0-100)** - For Multi-Day Positions
    - **What it measures**: Technical setups, trend strength, and 2-10 day momentum
    - **Key factors**:
        - Moving average alignment (30% weight)
        - RSI and MACD signals (25% weight)
        - Support/Resistance levels (25% weight)
        - Volume confirmation (20% weight)
    - **Good score**: 60+ indicates solid swing trade setup
    - **Use when**: You plan to hold for several days to weeks
    
    #### 💎 **Position Score (0-100)** - For Long-Term Investment
    - **What it measures**: Fundamental strength and long-term growth potential
    - **Key factors**:
        - P/E and PEG ratios (35% weight)
        - Revenue growth & margins (35% weight)
        - Debt levels & financial health (20% weight)
        - Technical trend confirmation (10% weight)
    - **Good score**: 65+ indicates investment-grade opportunity
    - **Use when**: You plan to hold for months to years
    
    #### 🤖 **AI Score (0-100)** - Overall Rating
    - **What it measures**: Weighted average of all three scores
    - **Calculation**: (Day Score × 0.3) + (Swing Score × 0.4) + (Position Score × 0.3)
    - **Good score**: 70+ indicates strong opportunity across multiple timeframes
    - **Use when**: You want a single metric to rank opportunities
    
    ### 📈 How Professionals Use These Scores
    
    1. **Hedge Funds**: Focus on stocks with AI Score > 70 and specific strategy scores > 75
    2. **Day Traders**: Filter for Day Score > 60 with RVOL > 2.0
    3. **Swing Traders**: Look for Swing Score > 60 with good risk/reward setups
    4. **Portfolio Managers**: Emphasize Position Score > 65 with strong fundamentals
    
    💡 **Pro Tip**: The best trades often have high scores in multiple categories!
    """)

# Sidebar configuration
with st.sidebar:
    st.header("⚙️ Screener Settings")
    
    # Trading strategy selector with explanation
    trade_type = st.selectbox(
        "Trading Strategy",
        ["🎯 All Signals", "⚡ Day Trade", "📊 Swing Trade", "💎 Position Trade"],
        help="Filter results based on your trading timeframe"
    )
    
    # Data source
    data_source = st.selectbox(
        "Data Source",
        ["📋 My Watchlist", "📊 Manual Tickers", "🔥 Top Movers", "📈 S&P 500 Leaders"],
        index=1,  # Default to Manual Tickers
        help="Choose where to scan for opportunities"
    )
    
    st.markdown("---")
    st.subheader("🎛️ Advanced Filters")
    
    # Price filters
    col1, col2 = st.columns(2)
    with col1:
        min_price = st.number_input("Min Price ($)", value=5.0, step=1.0, min_value=0.0)
    with col2:
        max_price = st.number_input("Max Price ($)", value=500.0, step=10.0, min_value=0.0)
    
    # Volume filter
    min_volume_m = st.number_input("Min Avg Volume (M)", value=1.0, step=0.5, min_value=0.0,
                                   help="Minimum average daily volume in millions")
    min_volume = min_volume_m * 1_000_000
    
    # Score filters
    min_score = st.slider("Minimum Score Threshold", 0, 100, 60,
                          help="Only show stocks with at least one score above this")
    
    # Feature toggles
    st.markdown("---")
    st.subheader("🔧 Analysis Features")
    use_news_sentiment = st.checkbox("Include News Sentiment", value=True)
    use_earnings_data = st.checkbox("Include Earnings Analysis", value=True)
    use_insider_data = st.checkbox("Include Insider Activity", value=False)
    show_research_summary = st.checkbox("Generate AI Research Summary", value=True)
    debug_mode = st.checkbox("Debug Mode", value=False, help="Show detailed scoring information")
    st.session_state['debug_mode'] = debug_mode

# Enhanced stock analysis function
def analyze_stock_enhanced(ticker_symbol):
    """Professional-grade stock analysis"""
    try:
        # Get comprehensive data
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
        
        # Basic validation
        if not info or (not info.get('regularMarketPrice') and not info.get('currentPrice')):
            return None
        
        # Initialize results
        analysis = {
            'ticker': ticker_symbol,
            'price': info.get('regularMarketPrice', info.get('currentPrice', 0)),
            'change': info.get('regularMarketChangePercent', 0),
            'volume': info.get('regularMarketVolume', 0),
            'avg_volume': info.get('averageVolume', 1),
            'market_cap': info.get('marketCap', 0),
            'sector': info.get('sector', 'Unknown'),
            'industry': info.get('industry', 'Unknown')
        }
        
        # Calculate key metrics
        analysis['rvol'] = analysis['volume'] / analysis['avg_volume'] if analysis['avg_volume'] > 0 else 0
        
        # Get historical data
        hist_1mo = ticker.history(period="1mo")
        hist_1y = ticker.history(period="1y")
        
        # Technical Analysis
        if len(hist_1mo) >= 20:
            analysis['technicals'] = calculate_technical_indicators(hist_1mo, analysis['price'])
        else:
            analysis['technicals'] = None
        
        # Fundamental Analysis
        analysis['fundamentals'] = {
            'pe_ratio': info.get('trailingPE'),
            'forward_pe': info.get('forwardPE'),
            'peg_ratio': info.get('pegRatio'),
            'price_to_book': info.get('priceToBook'),
            'price_to_sales': info.get('priceToSalesTrailing12Months'),
            'revenue_growth': info.get('revenueGrowth'),
            'earnings_growth': info.get('earningsGrowth'),
            'profit_margins': info.get('profitMargins'),
            'operating_margins': info.get('operatingMargins'),
            'roe': info.get('returnOnEquity'),
            'debt_to_equity': info.get('debtToEquity'),
            'current_ratio': info.get('currentRatio'),
            'free_cash_flow': info.get('freeCashflow'),
            'dividend_yield': info.get('dividendYield')
        }
        
        # Earnings Data
        if use_earnings_data:
            try:
                earnings_hist = ticker.quarterly_earnings
                if not earnings_hist.empty:
                    recent_earnings = earnings_hist.head(4)
                    analysis['earnings'] = {
                        'last_report_date': recent_earnings.index[0].strftime('%Y-%m-%d') if len(recent_earnings) > 0 else None,
                        'last_eps': recent_earnings['Earnings'].iloc[0] if len(recent_earnings) > 0 else None,
                        'last_revenue': recent_earnings['Revenue'].iloc[0] if len(recent_earnings) > 0 else None,
                        'earnings_trend': 'Growing' if len(recent_earnings) > 1 and recent_earnings['Earnings'].iloc[0] > recent_earnings['Earnings'].iloc[1] else 'Declining',
                        'next_earnings_date': info.get('earningsTimestamp', None)
                    }
                else:
                    analysis['earnings'] = None
            except:
                analysis['earnings'] = None
        
        # News Sentiment
        if use_news_sentiment and data_manager:
            news = data_manager.get_latest_news_sentiment(ticker_symbol)
            analysis['news_sentiment'] = news
        else:
            analysis['news_sentiment'] = None
        
        # Performance Metrics
        if len(hist_1y) > 0:
            year_ago_price = hist_1y['Close'].iloc[0]
            analysis['performance'] = {
                '1y_return': ((analysis['price'] - year_ago_price) / year_ago_price * 100) if year_ago_price > 0 else 0,
                '52w_high': info.get('fiftyTwoWeekHigh', analysis['price']),
                '52w_low': info.get('fiftyTwoWeekLow', analysis['price']),
                'from_52w_high': ((analysis['price'] - info.get('fiftyTwoWeekHigh', analysis['price'])) / info.get('fiftyTwoWeekHigh', analysis['price']) * 100) if info.get('fiftyTwoWeekHigh') else 0,
                'from_52w_low': ((analysis['price'] - info.get('fiftyTwoWeekLow', analysis['price'])) / info.get('fiftyTwoWeekLow', analysis['price']) * 100) if info.get('fiftyTwoWeekLow') else 0
            }
        else:
            analysis['performance'] = None
        
        # Calculate comprehensive scores
        analysis['scores'] = calculate_professional_scores(analysis)
        
        # Generate trading signals
        signals = []
        if analysis['scores']['day_score'] >= 60:  # Lowered from 70
            signals.append("⚡ DAY")
        if analysis['scores']['swing_score'] >= 60:  # Lowered from 70
            signals.append("📊 SWING")
        if analysis['scores']['position_score'] >= 65:  # Lowered from 75
            signals.append("💎 POSITION")
        
        # If debug mode is on, show why no signals
        if st.session_state.get('debug_mode', False) and not signals:
            st.caption(f"Debug {ticker_symbol}: Day={analysis['scores']['day_score']:.0f}, Swing={analysis['scores']['swing_score']:.0f}, Position={analysis['scores']['position_score']:.0f}")
        
        analysis['signals'] = signals
        
        return analysis
        
    except Exception as e:
        st.error(f"Error analyzing {ticker_symbol}: {str(e)}")
        return None

def calculate_technical_indicators(hist_data, current_price):
    """Calculate comprehensive technical indicators"""
    if hist_data is None or hist_data.empty:
        return None
        
    close_prices = hist_data['Close']
    
    indicators = {
        'price': current_price,
        'sma_20': None,
        'sma_50': None,
        'sma_200': None,
        'ema_12': None,
        'ema_26': None,
        'rsi': None,
        'macd': None,
        'macd_signal': None,
        'macd_histogram': None,
        'bb_upper': None,
        'bb_lower': None,
        'bb_width': None,
        'bb_position': None,
        'support': None,
        'resistance': None,
        'volume_sma': None,
        'volume_ratio': 1,
        'range': 0,
        'true_range': 0
    }
    
    try:
        # Moving averages
        if len(close_prices) >= 20:
            indicators['sma_20'] = close_prices.rolling(20).mean().iloc[-1]
        if len(close_prices) >= 50:
            indicators['sma_50'] = close_prices.rolling(50).mean().iloc[-1]
        if len(close_prices) >= 200:
            indicators['sma_200'] = close_prices.rolling(200).mean().iloc[-1]
        
        # EMAs
        if len(close_prices) >= 12:
            indicators['ema_12'] = close_prices.ewm(span=12, adjust=False).mean().iloc[-1]
        if len(close_prices) >= 26:
            indicators['ema_26'] = close_prices.ewm(span=26, adjust=False).mean().iloc[-1]
        
        # RSI
        if len(close_prices) >= 14:
            delta = close_prices.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            indicators['rsi'] = 100 - (100 / (1 + rs)).iloc[-1]
        
        # MACD
        if len(close_prices) >= 26:
            exp1 = close_prices.ewm(span=12, adjust=False).mean()
            exp2 = close_prices.ewm(span=26, adjust=False).mean()
            macd_line = exp1 - exp2
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            indicators['macd'] = macd_line.iloc[-1]
            indicators['macd_signal'] = signal_line.iloc[-1]
            indicators['macd_histogram'] = indicators['macd'] - indicators['macd_signal']
        
        # Bollinger Bands
        if len(close_prices) >= 20:
            sma = close_prices.rolling(20).mean()
            std = close_prices.rolling(20).std()
            indicators['bb_upper'] = (sma + (std * 2)).iloc[-1]
            indicators['bb_lower'] = (sma - (std * 2)).iloc[-1]
            indicators['bb_width'] = indicators['bb_upper'] - indicators['bb_lower']
            if indicators['bb_width'] > 0:
                indicators['bb_position'] = (current_price - indicators['bb_lower']) / indicators['bb_width']
            
        # Support and Resistance
        if len(hist_data) >= 20:
            indicators['support'] = hist_data['Low'].rolling(20).min().iloc[-1]
            indicators['resistance'] = hist_data['High'].rolling(20).max().iloc[-1]
        
        # Volume indicators
        if len(hist_data) >= 20:
            indicators['volume_sma'] = hist_data['Volume'].rolling(20).mean().iloc[-1]
            if indicators['volume_sma'] > 0:
                indicators['volume_ratio'] = hist_data['Volume'].iloc[-1] / indicators['volume_sma']
        
        # Price action
        indicators['range'] = hist_data['High'].iloc[-1] - hist_data['Low'].iloc[-1]
        if len(hist_data) > 1:
            indicators['true_range'] = max(
                hist_data['High'].iloc[-1] - hist_data['Low'].iloc[-1],
                abs(hist_data['High'].iloc[-1] - hist_data['Close'].iloc[-2]),
                abs(hist_data['Low'].iloc[-1] - hist_data['Close'].iloc[-2])
            )
        
    except Exception as e:
        st.error(f"Error calculating indicators: {e}")
    
    return indicators

def calculate_professional_scores(analysis):
    """Calculate scores using hedge fund-grade methodology"""
    scores = {
        'day_score': 0,
        'swing_score': 0,
        'position_score': 0,
        'ai_score': 0
    }
    
    try:
        # Day Trading Score (0-100)
        if analysis.get('technicals'):
            tech = analysis['technicals']
            
            # Intraday momentum (30 points)
            change_pct = analysis.get('change', 0)
            if change_pct > 3:
                scores['day_score'] += 30
            elif change_pct > 2:
                scores['day_score'] += 20
            elif change_pct > 1:
                scores['day_score'] += 10
            elif change_pct < -2:
                scores['day_score'] -= 10
            
            # Volume surge (30 points)
            rvol = analysis.get('rvol', 0)
            if rvol > 3:
                scores['day_score'] += 30
            elif rvol > 2:
                scores['day_score'] += 20
            elif rvol > 1.5:
                scores['day_score'] += 10
            
            # Technical momentum (20 points)
            if tech.get('rsi') is not None:
                if 30 < tech['rsi'] < 70:
                    scores['day_score'] += 10
                if tech['rsi'] < 30 or tech['rsi'] > 70:
                    scores['day_score'] += 10  # Extremes for reversals
            
            # Price position (20 points)
            if tech.get('bb_position') is not None:
                if tech['bb_position'] > 0.8 or tech['bb_position'] < 0.2:
                    scores['day_score'] += 20
                elif 0.6 < tech['bb_position'] < 0.8 or 0.2 < tech['bb_position'] < 0.4:
                    scores['day_score'] += 10
        
        # Swing Trading Score (0-100)
        if analysis.get('technicals'):
            tech = analysis['technicals']
            
            # Trend alignment (30 points)
            price = tech.get('price', 0)
            sma20 = tech.get('sma_20')
            sma50 = tech.get('sma_50')
            
            if price and sma20 and sma50:
                if price > sma20 > sma50:
                    scores['swing_score'] += 30
                elif price > sma20:
                    scores['swing_score'] += 15
            elif price and sma20:  # Only 20 SMA available
                if price > sma20:
                    scores['swing_score'] += 20
            
            # MACD Signal (25 points)
            if tech.get('macd') is not None and tech.get('macd_signal') is not None:
                if tech['macd'] > tech['macd_signal']:
                    scores['swing_score'] += 25
                    if tech.get('macd_histogram', 0) > 0:
                        scores['swing_score'] += 5
            
            # Support/Resistance (25 points)
            if price and tech.get('support') and tech.get('resistance'):
                support = tech['support']
                resistance = tech['resistance']
                if resistance > support:  # Valid range
                    range_position = (price - support) / (resistance - support)
                    if range_position < 0.3:  # Near support
                        scores['swing_score'] += 25
                    elif range_position > 0.7:  # Breaking resistance
                        scores['swing_score'] += 20
            
            # RSI conditions (20 points)
            if tech.get('rsi') is not None:
                if 40 < tech['rsi'] < 60:
                    scores['swing_score'] += 20
                elif tech['rsi'] < 30:
                    scores['swing_score'] += 15  # Oversold bounce
        
        # Position Trading Score (0-100)
        if analysis.get('fundamentals'):
            fund = analysis['fundamentals']
            
            # Valuation metrics (35 points)
            pe = fund.get('pe_ratio')
            if pe and isinstance(pe, (int, float)) and 0 < pe < 20:
                scores['position_score'] += 20
            elif pe and isinstance(pe, (int, float)) and 20 < pe < 30:
                scores['position_score'] += 10
            
            peg = fund.get('peg_ratio')
            if peg and isinstance(peg, (int, float)) and 0 < peg < 1:
                scores['position_score'] += 15
            elif peg and isinstance(peg, (int, float)) and 1 < peg < 1.5:
                scores['position_score'] += 8
            
            # Growth metrics (35 points)
            rev_growth = fund.get('revenue_growth')
            if rev_growth and isinstance(rev_growth, (int, float)) and rev_growth > 0.20:
                scores['position_score'] += 20
            elif rev_growth and isinstance(rev_growth, (int, float)) and rev_growth > 0.10:
                scores['position_score'] += 10
            
            margins = fund.get('profit_margins')
            if margins and isinstance(margins, (int, float)) and margins > 0.20:
                scores['position_score'] += 15
            elif margins and isinstance(margins, (int, float)) and margins > 0.10:
                scores['position_score'] += 8
            
            # Financial health (20 points)
            debt_equity = fund.get('debt_to_equity')
            if debt_equity is not None and isinstance(debt_equity, (int, float)):
                if debt_equity < 0.5:
                    scores['position_score'] += 10
                elif debt_equity > 2:
                    scores['position_score'] -= 5
            
            roe = fund.get('roe')
            if roe and isinstance(roe, (int, float)) and roe > 0.15:
                scores['position_score'] += 10
            
            # Technical confirmation (10 points)
            if analysis.get('performance'):
                from_52w_low = analysis['performance'].get('from_52w_low', 0)
                if isinstance(from_52w_low, (int, float)) and from_52w_low > 20:
                    scores['position_score'] += 10
        
        # News sentiment modifier (applies to all scores)
        if analysis.get('news_sentiment'):
            sentiment_modifier = 0
            if analysis['news_sentiment']['label'] == 'Positive':
                sentiment_modifier = 10
            elif analysis['news_sentiment']['label'] == 'Negative':
                sentiment_modifier = -10
            
            scores['day_score'] = max(0, min(100, scores['day_score'] + sentiment_modifier * 1.5))
            scores['swing_score'] = max(0, min(100, scores['swing_score'] + sentiment_modifier))
            scores['position_score'] = max(0, min(100, scores['position_score'] + sentiment_modifier * 0.5))
        
        # Ensure scores are in valid range
        for key in ['day_score', 'swing_score', 'position_score']:
            scores[key] = max(0, min(100, scores[key]))
        
        # Calculate AI Score (weighted average)
        scores['ai_score'] = (scores['day_score'] * 0.3 + 
                             scores['swing_score'] * 0.4 + 
                             scores['position_score'] * 0.3)
    
    except Exception as e:
        st.error(f"Error calculating scores: {e}")
        # Return default scores on error
        return scores
    
    return scores

# Position sizing calculation
def calculate_position_size(account_size, risk_percent, entry_price, stop_loss_price):
    """Calculate position size using professional risk management"""
    risk_amount = account_size * (risk_percent / 100)
    price_risk = abs(entry_price - stop_loss_price)
    
    if price_risk > 0:
        shares = int(risk_amount / price_risk)
        position_value = shares * entry_price
        
        # Kelly Criterion adjustment (optional)
        win_rate = 0.55  # Assumed win rate
        avg_win_loss_ratio = 1.5  # Assumed R:R
        kelly_percent = (win_rate * avg_win_loss_ratio - (1 - win_rate)) / avg_win_loss_ratio
        kelly_adjusted_shares = int(shares * min(kelly_percent, 0.25))  # Cap at 25%
        
        return {
            'shares': shares,
            'position_value': position_value,
            'risk_amount': risk_amount,
            'kelly_shares': kelly_adjusted_shares,
            'percent_of_account': (position_value / account_size) * 100
        }
    return None

# Main content tabs
tab1, tab2, tab3, tab4 = st.tabs(["📊 Scanner", "📈 Signals", "💼 Portfolio", "📚 Education"])

with tab1:
    st.header("🔍 Stock Scanner")
    
    # Get tickers based on data source
    tickers = []
    if data_source == "📋 My Watchlist":
        if data_manager:
            tickers = data_manager.get_watchlist()
            if tickers:
                st.info(f"📊 Scanning {len(tickers)} stocks from your watchlist")
            else:
                st.warning("Your watchlist is empty. Add stocks in the Watchlist page.")
    elif data_source == "📊 Manual Tickers":
        default_tickers = "NVDA,AAPL,MSFT,GOOGL,AMZN,META,TSLA"
        ticker_input = st.text_area("Enter tickers (comma-separated):", value=default_tickers, height=100)
        tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
    elif data_source == "🔥 Top Movers":
        # In production, this would fetch real movers
        tickers = ["NVDA", "SMCI", "ARM", "PLTR", "MARA", "COIN", "TSLA", "AMD"]
        st.info(f"📊 Scanning today's top {len(tickers)} movers")
    elif data_source == "📈 S&P 500 Leaders":
        # Top S&P 500 by market cap
        tickers = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK-B", "LLY", "JPM", "V"]
        st.info(f"📊 Scanning S&P 500 market leaders")
    
    # Run Analysis button
    if st.button("🚀 Run Analysis", type="primary", use_container_width=True):
        if not tickers:
            st.warning("Please select a data source or enter tickers")
        else:
            progress_bar = st.progress(0, text="Initializing scanner...")
            results = []
            
            # Analyze each ticker
            for i, ticker in enumerate(tickers):
                progress_bar.progress((i + 1) / len(tickers), 
                                    text=f"Analyzing {ticker} ({i+1}/{len(tickers)})...")
                
                analysis = analyze_stock_enhanced(ticker)
                if analysis:
                    results.append(analysis)
            
            progress_bar.empty()
            st.session_state.screener_results = results
            
            if results:
                # Filter results based on criteria
                filtered_results = []
                for r in results:
                    # Price filter
                    if not (min_price <= r['price'] <= max_price):
                        continue
                    
                    # Volume filter
                    if r['avg_volume'] < min_volume:
                        continue
                    
                    # Score filter
                    scores = r['scores']
                    if max(scores['day_score'], scores['swing_score'], scores['position_score']) < min_score:
                        continue
                    
                    # Strategy filter
                    if trade_type == "⚡ Day Trade" and scores['day_score'] < 60:
                        continue
                    elif trade_type == "📊 Swing Trade" and scores['swing_score'] < 60:
                        continue
                    elif trade_type == "💎 Position Trade" and scores['position_score'] < 65:
                        continue
                    
                    filtered_results.append(r)
                
                # Display results
                if filtered_results:
                    # Summary metrics
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Stocks Matched", len(filtered_results))
                    col2.metric("Avg AI Score", f"{np.mean([r['scores']['ai_score'] for r in filtered_results]):.1f}")
                    
                    buy_signals = sum(1 for r in filtered_results if r['signals'])
                    col3.metric("Buy Signals", buy_signals)
                    
                    positive_news = sum(1 for r in filtered_results 
                                       if r.get('news_sentiment', {}).get('label') == 'Positive')
                    col4.metric("Positive News", positive_news)
                    
                    # Results table
                    table_data = []
                    for r in filtered_results:
                        scores = r['scores']
                        table_data.append({
                            'Ticker': r['ticker'],
                            'Price': f"${r['price']:.2f}",
                            '% Change': f"{r['change']:.1f}%",
                            'RVOL': f"{r['rvol']:.1f}",
                            'News': r.get('news_sentiment', {}).get('label', 'N/A'),
                            'Signals': ' '.join(r['signals']),
                            'Day Score': scores['day_score'],
                            'Swing Score': scores['swing_score'],
                            'Position Score': scores['position_score'],
                            'AI Score': f"{scores['ai_score']:.1f}"
                        })
                    
                    df = pd.DataFrame(table_data)
                    
                    # Sort by AI Score
                    df['_ai_score_numeric'] = df['AI Score'].str.replace(r'[^\d.]', '', regex=True).astype(float)
                    df = df.sort_values('_ai_score_numeric', ascending=False)
                    df = df.drop('_ai_score_numeric', axis=1)
                    
                    st.dataframe(
                        df,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Day Score": st.column_config.ProgressColumn(
                                "Day Score",
                                help="0-100 score for day trading",
                                format="%d",
                                min_value=0,
                                max_value=100,
                            ),
                            "Swing Score": st.column_config.ProgressColumn(
                                "Swing Score",
                                help="0-100 score for swing trading",
                                format="%d",
                                min_value=0,
                                max_value=100,
                            ),
                            "Position Score": st.column_config.ProgressColumn(
                                "Position Score",
                                help="0-100 score for position trading",
                                format="%d",
                                min_value=0,
                                max_value=100,
                            ),
                        }
                    )
                    
                    st.session_state.trade_signals = filtered_results
                else:
                    st.warning("No stocks matched your criteria. Try adjusting filters.")
            else:
                st.error("No valid analysis results. Please check your tickers.")

with tab2:
    st.header("📈 Trading Signals - Professional Analysis")
    
    if st.session_state.get('trade_signals'):
        # Allow selection of which signal to analyze
        ticker_list = [r['ticker'] for r in st.session_state.trade_signals]
        selected_ticker = st.selectbox("Select stock for detailed analysis:", ticker_list)
        
        # Find the selected analysis
        selected_analysis = next((r for r in st.session_state.trade_signals if r['ticker'] == selected_ticker), None)
        
        if selected_analysis:
            # Header with key metrics
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Price", f"${selected_analysis['price']:.2f}", 
                         f"{selected_analysis['change']:.1f}%")
            with col2:
                st.metric("AI Score", f"{selected_analysis['scores']['ai_score']:.1f}")
            with col3:
                st.metric("Signals", ' '.join(selected_analysis['signals']) or "None")
            with col4:
                news_label = selected_analysis.get('news_sentiment', {}).get('label', 'N/A')
                news_color = "🟢" if news_label == "Positive" else "🔴" if news_label == "Negative" else "⚪"
                st.metric("News", f"{news_color} {news_label}")
            
            # Detailed Analysis Tabs
            analysis_tab1, analysis_tab2, analysis_tab3, analysis_tab4, analysis_tab5 = st.tabs(
                ["📊 Technical", "💰 Fundamentals", "📰 News & Sentiment", "📈 Trade Setup", "📑 Research Report"]
            )
            
            with analysis_tab1:
                st.subheader("📊 Technical Analysis")
                
                if selected_analysis.get('technicals'):
                    tech = selected_analysis['technicals']
                    
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        st.markdown("### Price Levels")
                        st.write(f"**Current Price:** ${tech['price']:.2f}")
                        st.write(f"**Support:** ${tech.get('support', 0):.2f}")
                        st.write(f"**Resistance:** ${tech.get('resistance', 0):.2f}")
                        if tech.get('bb_upper') and tech.get('bb_lower'):
                            st.write(f"**BB Upper:** ${tech['bb_upper']:.2f}")
                            st.write(f"**BB Lower:** ${tech['bb_lower']:.2f}")
                    
                    with col2:
                        st.markdown("### Moving Averages")
                        if tech.get('sma_20') is not None:
                            st.write(f"**SMA 20:** ${tech['sma_20']:.2f}")
                        else:
                            st.write("**SMA 20:** N/A")
                            
                        if tech.get('sma_50') is not None:
                            st.write(f"**SMA 50:** ${tech['sma_50']:.2f}")
                        else:
                            st.write("**SMA 50:** N/A")
                            
                        if tech.get('sma_200') is not None:
                            st.write(f"**SMA 200:** ${tech['sma_200']:.2f}")
                        else:
                            st.write("**SMA 200:** N/A")
                        
                        # Trend determination
                        if tech.get('sma_20') and tech.get('sma_50'):
                            if tech['price'] > tech['sma_20'] > tech.get('sma_50', 0):
                                st.success("📈 Bullish Trend")
                            elif tech['price'] < tech['sma_20'] < tech.get('sma_50', float('inf')):
                                st.error("📉 Bearish Trend")
                            else:
                                st.info("↔️ Neutral Trend")
                    
                    with col3:
                        st.markdown("### Momentum Indicators")
                        if tech.get('rsi') is not None:
                            rsi_value = tech['rsi']
                            st.write(f"**RSI (14):** {rsi_value:.1f}")
                            if rsi_value > 70:
                                st.warning("⚠️ Overbought")
                            elif rsi_value < 30:
                                st.success("🎯 Oversold")
                        
                        if tech.get('macd') is not None and tech.get('macd_signal') is not None:
                            st.write(f"**MACD:** {tech['macd']:.3f}")
                            st.write(f"**Signal:** {tech['macd_signal']:.3f}")
                            if tech['macd'] > tech['macd_signal']:
                                st.success("📈 Bullish Cross")
                            else:
                                st.warning("📉 Bearish Cross")
            
            with analysis_tab2:
                st.subheader("💰 Fundamental Analysis")
                
                if selected_analysis.get('fundamentals'):
                    fund = selected_analysis['fundamentals']
                    
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        st.markdown("### Valuation Metrics")
                        metrics = {
                            "P/E Ratio": fund.get('pe_ratio'),
                            "Forward P/E": fund.get('forward_pe'),
                            "PEG Ratio": fund.get('peg_ratio'),
                            "P/B Ratio": fund.get('price_to_book'),
                            "P/S Ratio": fund.get('price_to_sales')
                        }
                        
                        for metric, value in metrics.items():
                            if value is not None and value > 0:
                                st.write(f"**{metric}:** {value:.2f}")
                            else:
                                st.write(f"**{metric}:** N/A")
                    
                    with col2:
                        st.markdown("### Growth & Profitability")
                        growth_metrics = {
                            "Revenue Growth": fund.get('revenue_growth'),
                            "Earnings Growth": fund.get('earnings_growth'),
                            "Profit Margin": fund.get('profit_margins'),
                            "Operating Margin": fund.get('operating_margins'),
                            "ROE": fund.get('roe')
                        }
                        
                        for metric, value in growth_metrics.items():
                            if value is not None:
                                if "Growth" in metric or "Margin" in metric or "ROE" in metric:
                                    st.write(f"**{metric}:** {value*100:.1f}%")
                                else:
                                    st.write(f"**{metric}:** {value:.2f}")
                            else:
                                st.write(f"**{metric}:** N/A")
                    
                    with col3:
                        st.markdown("### Financial Health")
                        health_metrics = {
                            "Debt/Equity": fund.get('debt_to_equity'),
                            "Current Ratio": fund.get('current_ratio'),
                            "Free Cash Flow": fund.get('free_cash_flow'),
                            "Dividend Yield": fund.get('dividend_yield')
                        }
                        
                        for metric, value in health_metrics.items():
                            if value is not None:
                                if metric == "Free Cash Flow":
                                    st.write(f"**{metric}:** ${value/1e9:.2f}B" if value > 1e9 else f"${value/1e6:.0f}M")
                                elif metric == "Dividend Yield":
                                    st.write(f"**{metric}:** {value*100:.2f}%")
                                else:
                                    st.write(f"**{metric}:** {value:.2f}")
                            else:
                                st.write(f"**{metric}:** N/A")
                
                # Earnings section
                if selected_analysis.get('earnings'):
                    st.markdown("---")
                    st.markdown("### 📊 Earnings History")
                    earnings = selected_analysis['earnings']
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        if earnings.get('last_report_date'):
                            st.write(f"**Last Report:** {earnings['last_report_date']}")
                        if earnings.get('last_eps') is not None:
                            st.write(f"**Last EPS:** ${earnings['last_eps']:.2f}")
                        if earnings.get('earnings_trend'):
                            trend_emoji = "📈" if earnings['earnings_trend'] == 'Growing' else "📉"
                            st.write(f"**Trend:** {trend_emoji} {earnings['earnings_trend']}")
                    
                    with col2:
                        if earnings.get('next_earnings_date'):
                            next_date = datetime.fromtimestamp(earnings['next_earnings_date'])
                            days_until = (next_date - datetime.now()).days
                            st.write(f"**Next Earnings:** {next_date.strftime('%Y-%m-%d')}")
                            st.write(f"**Days Until:** {days_until}")
            
            with analysis_tab3:
                st.subheader("📰 News & Market Sentiment")
                
                # Latest news
                if selected_analysis.get('news_sentiment'):
                    news = selected_analysis['news_sentiment']
                    
                    # Sentiment display
                    sentiment_color = "green" if news['label'] == "Positive" else "red" if news['label'] == "Negative" else "gray"
                    st.markdown(f"""
                    <div style='padding: 1rem; border-radius: 0.5rem; background-color: {sentiment_color}; color: white;'>
                        <h3>Sentiment: {news['label']} (Score: {news['score']:.2f})</h3>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    st.markdown("### Latest Headline")
                    st.write(f"**{news.get('headline', 'No headline available')}**")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**Source:** {news.get('source', 'Unknown')}")
                    with col2:
                        st.write(f"**Date:** {news.get('date', 'Unknown')}")
                    
                    if news.get('link'):
                        st.markdown(f"[Read Full Article]({news['link']})")
                
                # Market sentiment indicators
                st.markdown("---")
                st.markdown("### 🎯 Market Sentiment Indicators")
                
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    # Volume sentiment
                    rvol = selected_analysis.get('rvol', 1)
                    if rvol > 2:
                        st.success(f"🔥 High Volume ({rvol:.1f}x avg)")
                    elif rvol > 1.5:
                        st.info(f"📊 Above Avg Volume ({rvol:.1f}x)")
                    else:
                        st.warning(f"😴 Low Volume ({rvol:.1f}x)")
                
                with col2:
                    # Price action sentiment
                    change = selected_analysis.get('change', 0)
                    if abs(change) > 3:
                        st.warning(f"⚡ High Volatility ({change:.1f}%)")
                    else:
                        st.info(f"📊 Normal Movement ({change:.1f}%)")
                
                with col3:
                    # 52-week position
                    if selected_analysis.get('performance'):
                        from_low = selected_analysis['performance'].get('from_52w_low', 0)
                        from_high = selected_analysis['performance'].get('from_52w_high', 0)
                        
                        if from_high > -10:
                            st.success(f"🎯 Near 52W High ({from_high:.1f}%)")
                        elif from_low < 20:
                            st.warning(f"⚠️ Near 52W Low (+{from_low:.1f}%)")
                        else:
                            st.info("📊 Mid-range")
            
            with analysis_tab4:
                st.subheader("📈 Professional Trade Setup")
                
                # Position sizing calculator
                col1, col2 = st.columns([3, 2])
                
                with col1:
                    st.markdown("### 🎯 Entry & Exit Levels")
                    
                    if selected_analysis.get('technicals'):
                        tech = selected_analysis['technicals']
                        
                        # Determine trade direction
                        scores = selected_analysis['scores']
                        if scores['day_score'] >= scores['swing_score'] and scores['day_score'] >= scores['position_score']:
                            trade_type = "Day Trade"
                            holding_period = "Intraday"
                        elif scores['swing_score'] >= scores['position_score']:
                            trade_type = "Swing Trade"
                            holding_period = "2-10 days"
                        else:
                            trade_type = "Position Trade"
                            holding_period = "Weeks to months"
                        
                        st.info(f"**Recommended Strategy:** {trade_type} ({holding_period})")
                        
                        # Calculate levels
                        entry = tech['price']
                        
                        if trade_type == "Day Trade":
                            stop = tech.get('support', entry * 0.98)
                            target1 = tech.get('resistance', entry * 1.02)
                            target2 = entry + (entry - stop) * 2  # 2:1 R:R
                        else:
                            stop = tech.get('support', entry * 0.95)
                            target1 = tech.get('resistance', entry * 1.05)
                            target2 = entry + (entry - stop) * 3  # 3:1 R:R
                        
                        # Display levels
                        st.write(f"**Entry Price:** ${entry:.2f}")
                        st.write(f"**Stop Loss:** ${stop:.2f} ({((stop-entry)/entry*100):.1f}%)")
                        st.write(f"**Target 1:** ${target1:.2f} ({((target1-entry)/entry*100):+.1f}%)")
                        st.write(f"**Target 2:** ${target2:.2f} ({((target2-entry)/entry*100):+.1f}%)")
                        
                        # Risk/Reward
                        risk = entry - stop
                        reward1 = target1 - entry
                        reward2 = target2 - entry
                        
                        rr1 = reward1 / risk if risk > 0 else 0
                        rr2 = reward2 / risk if risk > 0 else 0
                        
                        st.write(f"**Risk/Reward Ratios:** 1:{rr1:.1f} / 1:{rr2:.1f}")
                
                with col2:
                    st.markdown("### 💰 Position Sizing")
                    
                    account_size = st.number_input("Account Size ($)", 
                                                   min_value=1000, 
                                                   value=10000, 
                                                   step=1000)
                    
                    risk_percent = st.slider("Risk per Trade (%)", 
                                           min_value=0.5, 
                                           max_value=5.0, 
                                           value=2.0, 
                                           step=0.5)
                    
                    if 'entry' in locals() and 'stop' in locals():
                        position_calc = calculate_position_size(account_size, risk_percent, entry, stop)
                        
                        if position_calc:
                            st.write(f"**Risk Amount:** ${position_calc['risk_amount']:.2f}")
                            st.write(f"**Shares to Buy:** {position_calc['shares']}")
                            st.write(f"**Position Size:** ${position_calc['position_value']:,.2f}")
                            st.write(f"**% of Account:** {position_calc['percent_of_account']:.1f}%")
                            
                            # Kelly Criterion suggestion
                            if position_calc['kelly_shares'] < position_calc['shares']:
                                st.info(f"💡 Kelly Criterion suggests: {position_calc['kelly_shares']} shares")
                            
                            # Add to cart buttons
                            st.markdown("---")
                            col_a, col_b = st.columns(2)
                            
                            with col_a:
                                if st.button("🧾 Simulate Trade", type="primary", use_container_width=True):
                                    # Set session state for paper trading
                                    st.session_state['prefill_ticker'] = selected_ticker
                                    st.session_state['prefill_entry'] = entry
                                    st.session_state['prefill_exit'] = target1
                                    st.session_state['prefill_type'] = trade_type
                                    st.session_state['prefill_shares'] = position_calc['shares']
                                    st.switch_page("pages/6_🧾_Paper_Trading.py")
                            
                            with col_b:
                                if st.button("📓 Add to Journal", use_container_width=True):
                                    st.session_state['journal_ticker'] = selected_ticker
                                    st.session_state['journal_entry'] = entry
                                    st.session_state['journal_stop'] = stop
                                    st.session_state['journal_target'] = target1
                                    st.switch_page("pages/5_📓_Journal.py")
            
            with analysis_tab5:
                st.subheader("📑 AI-Generated Research Report")
                
                if show_research_summary:
                    # Generate comprehensive research summary
                    report = f"""
                    ## {selected_ticker} - Investment Research Summary
                    
                    **Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}
                    
                    ### Executive Summary
                    {selected_ticker} is currently trading at ${selected_analysis['price']:.2f}, 
                    {'up' if selected_analysis['change'] > 0 else 'down'} {abs(selected_analysis['change']):.1f}% today. 
                    Our AI analysis assigns an overall score of {selected_analysis['scores']['ai_score']:.1f}/100, 
                    with particular strength in {max(selected_analysis['scores'], key=lambda k: selected_analysis['scores'][k] if k != 'ai_score' else 0).replace('_', ' ').title()}.
                    
                    ### Investment Thesis
                    """
                    
                    # Bullish/Bearish case
                    if selected_analysis['scores']['ai_score'] > 70:
                        report += f"""
                        **Bullish Case:**
                        - Strong technical momentum with price above key moving averages
                        - {selected_analysis.get('news_sentiment', {}).get('label', 'Neutral')} news sentiment supports upward movement
                        - Relative volume of {selected_analysis.get('rvol', 1):.1f}x indicates institutional interest
                        """
                    else:
                        report += f"""
                        **Neutral/Cautious View:**
                        - Mixed technical signals require careful entry timing
                        - Wait for clearer setup or improved fundamentals
                        - Consider smaller position size or wait for better entry
                        """
                    
                    # Fundamental highlights
                    if selected_analysis.get('fundamentals'):
                        fund = selected_analysis['fundamentals']
                        pe = fund.get('pe_ratio')
                        growth = fund.get('revenue_growth')
                        
                        report += f"""
                        
                        ### Fundamental Highlights
                        - Valuation: {'Attractive' if pe and pe < 20 else 'Fair' if pe and pe < 30 else 'Premium'} 
                          with P/E of {pe:.1f if pe else 'N/A'}
                        - Growth: {'Strong' if growth and growth > 0.15 else 'Moderate' if growth and growth > 0.05 else 'Weak'} 
                          revenue growth of {(growth*100):.1f if growth else 'N/A'}%
                        - Sector: {selected_analysis.get('sector', 'Unknown')}
                        - Industry: {selected_analysis.get('industry', 'Unknown')}
                        """
                    
                    # Risk factors
                    report += f"""
                    
                    ### Risk Factors
                    - Market volatility and sector rotation risk
                    - {'High valuation risk' if selected_analysis.get('fundamentals', {}).get('pe_ratio', 0) > 30 else 'Valuation within reasonable range'}
                    - {'Earnings announcement upcoming' if selected_analysis.get('earnings', {}).get('next_earnings_date') else 'No near-term earnings catalyst'}
                    
                    ### Recommended Action
                    Based on our analysis, {selected_ticker} is best suited for **{max(selected_analysis['scores'], key=lambda k: selected_analysis['scores'][k] if k != 'ai_score' else 0).replace('_', ' ').title()}** strategies.
                    
                    **Price Targets:**
                    - Entry: ${selected_analysis['price']:.2f}
                    - Target 1: ${selected_analysis['price'] * 1.05:.2f} (+5%)
                    - Target 2: ${selected_analysis['price'] * 1.10:.2f} (+10%)
                    - Stop Loss: ${selected_analysis['price'] * 0.95:.2f} (-5%)
                    
                    ---
                    *This report is generated by AI and should not be considered investment advice. Always do your own research.*
                    """
                    
                    st.markdown(report)
                    
                    # Download report button
                    st.download_button(
                        label="📥 Download Full Report",
                        data=report,
                        file_name=f"{selected_ticker}_research_{datetime.now().strftime('%Y%m%d')}.md",
                        mime="text/markdown"
                    )
    else:
        st.info("👆 Run the scanner to see detailed trading signals and analysis")

with tab3:
    st.header("💼 Portfolio Overview")
    st.info("Portfolio tracking integrates with your Trade Journal and Paper Trading modules")
    
    if st.button("📓 Go to Trade Journal"):
        st.switch_page("pages/5_📓_Journal.py")
    
    if st.button("🧾 Go to Paper Trading"):
        st.switch_page("pages/6_🧾_Paper_Trading.py")

with tab4:
    st.header("📚 Trading Education - Professional Strategies")
    
    with st.expander("🎓 How Professional Traders Use These Scores"):
        st.markdown("""
        ### The Professional Approach
        
        **1. Pre-Market Routine (30 mins before open)**
        - Run scanner on watchlist with all signals enabled
        - Sort by AI Score > 80 to find best opportunities
        - Check news sentiment for any overnight developments
        - Review support/resistance levels from technical analysis
        
        **2. Position Entry Checklist**
        - ✅ Score matches your strategy (Day/Swing > 60, Position > 65)
        - ✅ Risk/Reward ratio > 2:1
        - ✅ Volume confirmation (RVOL > 1.5)
        - ✅ Clear stop loss level identified
        - ✅ Position size calculated (max 2% risk)
        
        **3. Trade Management**
        - Day Trades: Use trailing stops after 1:1 R:R achieved
        - Swing Trades: Scale out 50% at target 1, let rest run
        - Position Trades: Add on dips, trim on rips
        
        **4. Post-Market Review**
        - Journal all trades with entry/exit reasoning
        - Review score accuracy vs actual performance
        - Adjust filters based on what's working
        
        ### 🏆 Hedge Fund Secrets
        
        **The 80/20 Rule**: 80% of profits come from 20% of trades
        - Focus on highest conviction setups (AI Score > 85)
        - Size up on A+ setups, size down on B setups
        
        **Risk Parity Approach**: Balance risk across strategies
        - 40% Swing trades (moderate risk/reward)
        - 30% Position trades (lower risk, steady returns)
        - 30% Day trades (higher risk, quick profits)
        
        **The Edge**: Combining multiple confirmations
        - Technical + Fundamental + Sentiment = Higher probability
        - Never rely on just one indicator or score
        """)

st.markdown("---")
st.caption("🚨 Remember: High scores indicate opportunity, not guarantee. Always use stop losses and proper position sizing.")
