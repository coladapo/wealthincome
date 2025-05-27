import sys
import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

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
    st.error(f"🚨 Failed to import 'data_manager': {e}")
    st.stop()

# Page config
try:
    st.set_page_config(page_title="📊 Chart Pattern Recognition", layout="wide")
except st.errors.StreamlitAPIException:
    pass

st.title('📊 Chart Pattern Recognition')

# Initialize session state
if 'pattern_results' not in st.session_state:
    st.session_state.pattern_results = {}

# Sidebar configuration
with st.sidebar:
    st.header("⚙️ Analysis Settings")
    
    # Check if coming from another page
    default_ticker = st.session_state.get('analyze_ticker', 'AAPL')
    
    ticker = st.text_input("Enter Stock Ticker:", value=default_ticker).upper()
    
    period = st.selectbox(
        "Time Period",
        ["1mo", "3mo", "6mo", "1y", "2y"],
        index=1
    )
    
    st.markdown("---")
    
    # Pattern filters
    st.subheader("🔍 Pattern Filters")
    show_support_resistance = st.checkbox("Support & Resistance", value=True)
    show_trend_lines = st.checkbox("Trend Lines", value=True)
    show_patterns = st.checkbox("Chart Patterns", value=True)
    show_volume = st.checkbox("Volume Analysis", value=True)

# Main content
tab1, tab2, tab3 = st.tabs(["📈 Chart Analysis", "📊 Technical Indicators", "🎯 Trade Setups"])

# Analysis section
col1, col2 = st.columns([3, 1])

with col1:
    if st.button("🔄 Analyze", type="primary", use_container_width=True):
        if ticker:
            with st.spinner(f"Analyzing {ticker}..."):
                # Get stock data using data_manager - FIXED VERSION
                stock_data = data_manager.get_stock_data([ticker], period=period)
                
                if stock_data and ticker in stock_data:
                    hist_data = stock_data[ticker].get('history')
                    info = stock_data[ticker].get('info', {})
                    
                    if hist_data is not None and not hist_data.empty:
                        # Calculate indicators
                        indicators = data_manager.calculate_indicators(hist_data)
                        
                        # Find patterns
                        patterns = data_manager.find_patterns(hist_data)
                        
                        # Store results in session state
                        st.session_state.pattern_results[ticker] = {
                            'data': hist_data,
                            'info': info,
                            'indicators': indicators,
                            'patterns': patterns,
                            'timestamp': datetime.now()
                        }
                        
                        st.success(f"✅ Analysis complete for {ticker}")
                    else:
                        st.error(f"No historical data available for {ticker}")
                else:
                    st.error(f"Failed to fetch data for {ticker}. Please check the ticker symbol.")

with col2:
    if ticker in st.session_state.pattern_results:
        result = st.session_state.pattern_results[ticker]
        info = result.get('info', {})
        
        st.metric("Current Price", f"${info.get('regularMarketPrice', 0):.2f}")
        st.metric("Change", f"{info.get('regularMarketChangePercent', 0):.2f}%")

# Display results
if ticker in st.session_state.pattern_results:
    result = st.session_state.pattern_results[ticker]
    data = result['data']
    indicators = result['indicators']
    patterns = result['patterns']
    
    with tab1:
        st.header(f"📈 {ticker} Chart Analysis")
        
        # Create candlestick chart
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.7, 0.3]
        )
        
        # Candlestick
        fig.add_trace(
            go.Candlestick(
                x=data.index,
                open=data['Open'],
                high=data['High'],
                low=data['Low'],
                close=data['Close'],
                name='Price'
            ),
            row=1, col=1
        )
        
        # Add moving averages
        if 'sma_20' in indicators:
            fig.add_trace(
                go.Scatter(
                    x=data.index,
                    y=[indicators['sma_20']] * len(data),
                    mode='lines',
                    name='SMA 20',
                    line=dict(color='orange', width=2)
                ),
                row=1, col=1
            )
        
        if 'sma_50' in indicators:
            fig.add_trace(
                go.Scatter(
                    x=data.index,
                    y=[indicators['sma_50']] * len(data),
                    mode='lines',
                    name='SMA 50',
                    line=dict(color='blue', width=2)
                ),
                row=1, col=1
            )
        
        # Support and Resistance
        if show_support_resistance and 'support' in indicators and 'resistance' in indicators:
            fig.add_hline(
                y=indicators['support'],
                line_dash="dash",
                line_color="green",
                annotation_text=f"Support: ${indicators['support']:.2f}",
                row=1, col=1
            )
            
            fig.add_hline(
                y=indicators['resistance'],
                line_dash="dash",
                line_color="red",
                annotation_text=f"Resistance: ${indicators['resistance']:.2f}",
                row=1, col=1
            )
        
        # Volume
        if show_volume:
            fig.add_trace(
                go.Bar(
                    x=data.index,
                    y=data['Volume'],
                    name='Volume',
                    marker_color='lightblue'
                ),
                row=2, col=1
            )
        
        # Update layout
        fig.update_layout(
            title=f"{ticker} - {period} Chart",
            yaxis_title="Price",
            yaxis2_title="Volume",
            xaxis_rangeslider_visible=False,
            height=700
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
        # Pattern Detection Results
        if patterns:
            st.subheader("🎯 Detected Patterns")
            
            for pattern_name, pattern_info in patterns.items():
                col1, col2, col3 = st.columns([2, 1, 1])
                
                with col1:
                    st.write(f"**{pattern_info.get('type', pattern_name)}**")
                    if 'description' in pattern_info:
                        st.write(pattern_info['description'])
                
                with col2:
                    strength = pattern_info.get('strength', 'Unknown')
                    if strength == 'Strong':
                        st.success(f"Strength: {strength}")
                    elif strength == 'Moderate':
                        st.warning(f"Strength: {strength}")
                    else:
                        st.info(f"Strength: {strength}")
                
                with col3:
                    if 'target' in pattern_info:
                        st.metric("Target", f"${pattern_info['target']:.2f}")
    
    with tab2:
        st.header("📊 Technical Indicators")
        
        if indicators:
            # Display indicators in columns
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.subheader("Price Levels")
                st.write(f"**Current Price:** ${indicators.get('current_price', 0):.2f}")
                st.write(f"**Support:** ${indicators.get('support', 0):.2f}")
                st.write(f"**Resistance:** ${indicators.get('resistance', 0):.2f}")
                
                if 'bb_upper' in indicators and 'bb_lower' in indicators:
                    st.write(f"**BB Upper:** ${indicators.get('bb_upper', 0):.2f}")
                    st.write(f"**BB Lower:** ${indicators.get('bb_lower', 0):.2f}")
            
            with col2:
                st.subheader("Moving Averages")
                if 'sma_20' in indicators:
                    st.write(f"**SMA 20:** ${indicators.get('sma_20', 0):.2f}")
                if 'sma_50' in indicators:
                    st.write(f"**SMA 50:** ${indicators.get('sma_50', 0):.2f}")
                if 'sma_200' in indicators:
                    st.write(f"**SMA 200:** ${indicators.get('sma_200', 0):.2f}")
            
            with col3:
                st.subheader("Momentum")
                if 'rsi' in indicators:
                    rsi_value = indicators.get('rsi', 50)
                    st.write(f"**RSI:** {rsi_value:.2f}")
                    
                    if rsi_value > 70:
                        st.warning("Overbought")
                    elif rsi_value < 30:
                        st.success("Oversold")
                    else:
                        st.info("Neutral")
                
                if 'macd' in indicators and 'macd_signal' in indicators:
                    macd = indicators.get('macd', 0)
                    signal = indicators.get('macd_signal', 0)
                    st.write(f"**MACD:** {macd:.3f}")
                    st.write(f"**Signal:** {signal:.3f}")
                    
                    if macd > signal:
                        st.success("Bullish Cross")
                    else:
                        st.warning("Bearish Cross")
            
            # Volume Analysis
            st.markdown("---")
            st.subheader("📊 Volume Analysis")
            
            if 'volume' in indicators and 'volume_sma' in indicators:
                vol_ratio = indicators.get('volume_ratio', 0)
                st.metric(
                    "Volume Ratio",
                    f"{vol_ratio:.2f}x",
                    "High Volume" if vol_ratio > 1.5 else "Normal Volume"
                )
    
    with tab3:
        st.header("🎯 Trade Setup Recommendations")
        
        # Calculate trade setups based on patterns and indicators
        if indicators and patterns:
            current_price = indicators.get('current_price', 0)
            support = indicators.get('support', 0)
            resistance = indicators.get('resistance', 0)
            
            # Long setup
            if current_price > 0 and support > 0 and resistance > 0:
                st.subheader("📈 Long Trade Setup")
                
                col1, col2 = st.columns(2)
                
                with col1:
                    entry = current_price
                    stop_loss = support * 0.98  # 2% below support
                    target1 = resistance
                    target2 = resistance * 1.05  # 5% above resistance
                    
                    risk = entry - stop_loss
                    reward1 = target1 - entry
                    reward2 = target2 - entry
                    
                    rr_ratio1 = reward1 / risk if risk > 0 else 0
                    rr_ratio2 = reward2 / risk if risk > 0 else 0
                    
                    st.write(f"**Entry:** ${entry:.2f}")
                    st.write(f"**Stop Loss:** ${stop_loss:.2f} ({(stop_loss/entry-1)*100:.1f}%)")
                    st.write(f"**Target 1:** ${target1:.2f} ({(target1/entry-1)*100:.1f}%)")
                    st.write(f"**Target 2:** ${target2:.2f} ({(target2/entry-1)*100:.1f}%)")
                    st.write(f"**Risk/Reward:** 1:{rr_ratio1:.1f} / 1:{rr_ratio2:.1f}")
                
                with col2:
                    # Position sizing
                    st.subheader("Position Sizing")
                    account_size = st.number_input("Account Size ($)", value=10000, step=1000)
                    risk_percent = st.slider("Risk per trade (%)", 1, 5, 2)
                    
                    risk_amount = account_size * (risk_percent / 100)
                    shares = int(risk_amount / risk) if risk > 0 else 0
                    position_size = shares * entry
                    
                    st.write(f"**Risk Amount:** ${risk_amount:.2f}")
                    st.write(f"**Shares:** {shares}")
                    st.write(f"**Position Size:** ${position_size:.2f}")
                    st.write(f"**% of Account:** {(position_size/account_size*100):.1f}%")
            
            # Trading signals summary
            st.markdown("---")
            st.subheader("📊 Signal Summary")
            
            signals = []
            
            # Check for bullish signals
            if indicators.get('rsi', 50) < 30:
                signals.append("✅ RSI Oversold")
            
            if patterns.get('support_bounce'):
                signals.append("✅ Support Bounce Pattern")
            
            if patterns.get('breakout'):
                signals.append("✅ Breakout Pattern")
            
            current = indicators.get('current_price', 0)
            sma20 = indicators.get('sma_20', 0)
            sma50 = indicators.get('sma_50', 0)
            
            if current > sma20 > sma50:
                signals.append("✅ Bullish MA Alignment")
            
            # Display signals
            if signals:
                for signal in signals:
                    st.write(signal)
            else:
                st.info("No strong signals detected at current levels")

else:
    # No analysis yet
    st.info("👆 Enter a ticker and click 'Analyze' to start pattern recognition")
    
    # Quick examples
    st.markdown("### 💡 Quick Start")
    st.markdown("""
    Try analyzing these popular stocks:
    - **AAPL** - Apple Inc.
    - **MSFT** - Microsoft
    - **GOOGL** - Alphabet
    - **TSLA** - Tesla
    - **SPY** - S&P 500 ETF
    """)

# Add to journal button
if ticker in st.session_state.pattern_results and st.button("📓 Add to Trade Journal", key="add_to_journal"):
    st.session_state['journal_ticker'] = ticker
    st.switch_page("pages/5_📓_Journal.py")
