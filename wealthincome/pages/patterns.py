import sys
import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yfinance as yf
import ta
from datetime import datetime, timedelta
import numpy as np

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
    st.set_page_config(page_title="📈 Chart Pattern Recognition", layout="wide")
except st.errors.StreamlitAPIException:
    pass

st.title('📈 Chart Pattern Recognition & Technical Analysis')

# Check if coming from another page with a ticker
default_ticker = ""
if 'analyze_ticker' in st.session_state and st.session_state.analyze_ticker:
    default_ticker = st.session_state.analyze_ticker
    del st.session_state.analyze_ticker

# Sidebar controls
with st.sidebar:
    st.header("📊 Chart Settings")
    
    ticker = st.text_input("Ticker Symbol", value=default_ticker or "AAPL", placeholder="AAPL").upper()
    
    period = st.selectbox(
        "Time Period",
        ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y"],
        index=2
    )
    
    interval = st.selectbox(
        "Interval",
        ["1m", "5m", "15m", "30m", "1h", "1d", "1wk"],
        index=5
    )
    
    # Validate interval based on period
    if period in ["1d", "5d"] and interval in ["1d", "1wk"]:
        st.warning("Short periods work better with intraday intervals")
    
    st.markdown("---")
    
    # Technical indicators
    st.subheader("📈 Technical Indicators")
    show_ma = st.checkbox("Moving Averages", value=True)
    ma_periods = st.multiselect("MA Periods", [20, 50, 200], default=[20, 50])
    
    show_bb = st.checkbox("Bollinger Bands", value=True)
    show_rsi = st.checkbox("RSI", value=True)
    show_macd = st.checkbox("MACD", value=False)
    show_volume = st.checkbox("Volume", value=True)
    
    st.markdown("---")
    
    # Pattern detection
    st.subheader("🔍 Pattern Detection")
    detect_patterns = st.checkbox("Auto-detect Patterns", value=True)
    show_support_resistance = st.checkbox("Support/Resistance", value=True)
    show_trendlines = st.checkbox("Trendlines", value=False)

# Main content area
if st.button("🔄 Analyze", type="primary", use_container_width=True):
    with st.spinner(f"Analyzing {ticker}..."):
        # Get comprehensive data
        data = data_manager.get_comprehensive_data(ticker)
        
        if 'error' in data:
            st.error(f"Failed to fetch data: {data['error']}")
        else:
            # Store in session state for access across the page
            st.session_state['current_analysis'] = data

# Display analysis if available
if 'current_analysis' in st.session_state:
    data = st.session_state['current_analysis']
    
    # Get the stock data
    if data['basic'] and ticker in data['basic']:
        stock_data = data['basic'][ticker]
        hist = stock_data['history']
        info = stock_data['info']
        
        # Display current price info
        col1, col2, col3, col4, col5 = st.columns(5)
        
        with col1:
            current_price = info.get('regularMarketPrice', 0)
            st.metric("Price", f"${current_price:.2f}")
        
        with col2:
            change_pct = info.get('regularMarketChangePercent', 0)
            change_dollar = info.get('regularMarketChange', 0)
            st.metric("Change", f"{change_pct:.2f}%", f"${change_dollar:.2f}")
        
        with col3:
            volume = info.get('regularMarketVolume', 0)
            st.metric("Volume", f"{volume/1e6:.1f}M")
        
        with col4:
            day_high = info.get('dayHigh', 0)
            day_low = info.get('dayLow', 0)
            st.metric("Day Range", f"${day_low:.2f} - ${day_high:.2f}")
        
        with col5:
            market_cap = info.get('marketCap', 0)
            st.metric("Market Cap", f"${market_cap/1e9:.1f}B" if market_cap > 1e9 else f"${market_cap/1e6:.1f}M")
        
        # Create the main chart
        st.markdown("---")
        
        # Initialize subplots based on selected indicators
        n_subplots = 1 + (1 if show_volume else 0) + (1 if show_rsi else 0) + (1 if show_macd else 0)
        row_heights = [0.6] + [0.13] * (n_subplots - 1)
        
        subplot_titles = ["Price"]
        if show_volume:
            subplot_titles.append("Volume")
        if show_rsi:
            subplot_titles.append("RSI")
        if show_macd:
            subplot_titles.append("MACD")
        
        fig = make_subplots(
            rows=n_subplots,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            subplot_titles=subplot_titles,
            row_heights=row_heights
        )
        
        # Candlestick chart
        fig.add_trace(
            go.Candlestick(
                x=hist.index,
                open=hist['Open'],
                high=hist['High'],
                low=hist['Low'],
                close=hist['Close'],
                name="OHLC"
            ),
            row=1, col=1
        )
        
        # Add moving averages
        if show_ma:
            for period in ma_periods:
                if len(hist) >= period:
                    ma = hist['Close'].rolling(window=period).mean()
                    fig.add_trace(
                        go.Scatter(
                            x=hist.index,
                            y=ma,
                            mode='lines',
                            name=f'MA{period}',
                            line=dict(width=1)
                        ),
                        row=1, col=1
                    )
        
        # Add Bollinger Bands
        if show_bb and len(hist) >= 20:
            bb = ta.volatility.BollingerBands(hist['Close'])
            fig.add_trace(
                go.Scatter(
                    x=hist.index,
                    y=bb.bollinger_hband(),
                    mode='lines',
                    name='BB Upper',
                    line=dict(color='gray', width=1, dash='dash')
                ),
                row=1, col=1
            )
            fig.add_trace(
                go.Scatter(
                    x=hist.index,
                    y=bb.bollinger_lband(),
                    mode='lines',
                    name='BB Lower',
                    line=dict(color='gray', width=1, dash='dash'),
                    fill='tonexty',
                    fillcolor='rgba(128, 128, 128, 0.1)'
                ),
                row=1, col=1
            )
        
        # Add pattern detection results
        if detect_patterns and 'patterns' in data and data['patterns']:
            patterns = data['patterns']
            
            # Add support/resistance lines
            if show_support_resistance and 'support_resistance' in patterns:
                for level in patterns['support_resistance']:
                    color = 'red' if level['type'] == 'resistance' else 'green'
                    fig.add_hline(
                        y=level['level'],
                        line_dash="dash",
                        line_color=color,
                        annotation_text=f"{level['description']}: ${level['level']:.2f}",
                        annotation_position="right",
                        row=1, col=1
                    )
            
            # Add pattern annotations
            pattern_text = []
            if patterns.get('bull_flag'):
                pattern_text.append("🚩 Bull Flag Detected")
            if patterns.get('bear_flag'):
                pattern_text.append("🏴 Bear Flag Detected")
            if patterns.get('ascending_triangle'):
                pattern_text.append("📐 Ascending Triangle")
            if patterns.get('descending_triangle'):
                pattern_text.append("📐 Descending Triangle")
            
            if pattern_text:
                fig.add_annotation(
                    text="<br>".join(pattern_text),
                    xref="paper", yref="paper",
                    x=0.02, y=0.98,
                    showarrow=False,
                    bgcolor="rgba(255, 255, 255, 0.8)",
                    bordercolor="black",
                    borderwidth=1
                )
        
        # Current row for additional indicators
        current_row = 2
        
        # Add volume
        if show_volume:
            colors = ['red' if hist['Close'].iloc[i] < hist['Open'].iloc[i] else 'green' 
                     for i in range(len(hist))]
            
            fig.add_trace(
                go.Bar(
                    x=hist.index,
                    y=hist['Volume'],
                    name='Volume',
                    marker_color=colors
                ),
                row=current_row, col=1
            )
            current_row += 1
        
        # Add RSI
        if show_rsi and len(hist) >= 14:
            rsi = ta.momentum.RSIIndicator(hist['Close']).rsi()
            
            fig.add_trace(
                go.Scatter(
                    x=hist.index,
                    y=rsi,
                    mode='lines',
                    name='RSI',
                    line=dict(color='purple')
                ),
                row=current_row, col=1
            )
            
            # Add RSI levels
            fig.add_hline(y=70, line_dash="dash", line_color="red", row=current_row, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color="green", row=current_row, col=1)
            current_row += 1
        
        # Add MACD
        if show_macd and len(hist) >= 26:
            macd = ta.trend.MACD(hist['Close'])
            
            fig.add_trace(
                go.Scatter(
                    x=hist.index,
                    y=macd.macd(),
                    mode='lines',
                    name='MACD',
                    line=dict(color='blue')
                ),
                row=current_row, col=1
            )
            
            fig.add_trace(
                go.Scatter(
                    x=hist.index,
                    y=macd.macd_signal(),
                    mode='lines',
                    name='Signal',
                    line=dict(color='red')
                ),
                row=current_row, col=1
            )
            
            fig.add_trace(
                go.Bar(
                    x=hist.index,
                    y=macd.macd_diff(),
                    name='Histogram',
                    marker_color='gray'
                ),
                row=current_row, col=1
            )
        
        # Update layout
        fig.update_layout(
            title=f"{ticker} - {period} Chart",
            yaxis_title="Price",
            xaxis_rangeslider_visible=False,
            height=800,
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            )
        )
        
        # Update x-axis
        fig.update_xaxes(title_text="Date", row=n_subplots, col=1)
        
        # Display the chart
        st.plotly_chart(fig, use_container_width=True)
        
        # Pattern Details and Trading Signals
        st.markdown("---")
        
        col_left, col_right = st.columns(2)
        
        with col_left:
            st.subheader("🔍 Pattern Analysis")
            
            if 'patterns' in data and data['patterns']:
                patterns = data['patterns']
                
                # Pattern status
                pattern_found = False
                
                if patterns.get('bull_flag'):
                    st.success("🚩 **Bull Flag Pattern**")
                    st.write("Bullish continuation pattern. Look for breakout above flag resistance.")
                    pattern_found = True
                
                if patterns.get('bear_flag'):
                    st.error("🏴 **Bear Flag Pattern**")
                    st.write("Bearish continuation pattern. Watch for breakdown below flag support.")
                    pattern_found = True
                
                if patterns.get('channel'):
                    channel = patterns['channel']
                    st.info(f"📊 **{channel['trend'].title()} Channel** ({channel['strength']})")
                    st.write(f"Price moving in a {channel['type']} channel")
                    pattern_found = True
                
                if not pattern_found:
                    st.info("No significant patterns detected in current timeframe")
                
                # Support/Resistance levels
                if show_support_resistance and patterns.get('support_resistance'):
                    st.markdown("### 📏 Key Levels")
                    
                    levels_df = pd.DataFrame(patterns['support_resistance'])
                    if not levels_df.empty:
                        levels_df['level'] = levels_df['level'].apply(lambda x: f"${x:.2f}")
                        st.dataframe(
                            levels_df[['type', 'level', 'description']],
                            use_container_width=True,
                            hide_index=True
                        )
            else:
                st.info("Run pattern detection to see results")
        
        with col_right:
            st.subheader("📊 Trading Signals")
            
            if 'signals' in data and data['signals']:
                signals = data['signals']
                
                # Display scores
                st.markdown("### 📈 Signal Scores")
                
                score_col1, score_col2, score_col3 = st.columns(3)
                
                with score_col1:
                    day_score = signals.get('day_score', 0)
                    st.metric("Day Trade", f"{day_score:.0f}/100",
                             delta="BUY" if day_score > 70 else "WAIT")
                
                with score_col2:
                    swing_score = signals.get('swing_score', 0)
                    st.metric("Swing Trade", f"{swing_score:.0f}/100",
                             delta="BUY" if swing_score > 75 else "WAIT")
                
                with score_col3:
                    momentum = signals.get('momentum', 0)
                    st.metric("Momentum", f"{momentum:.0f}/100")
                
                # Trade setups
                st.markdown("### 🎯 Trade Setup")
                
                if 'patterns' in data and data['patterns'].get('support_resistance'):
                    levels = data['patterns']['support_resistance']
                    
                    # Find nearest support and resistance
                    supports = [l for l in levels if l['type'] == 'support']
                    resistances = [l for l in levels if l['type'] == 'resistance']
                    
                    if supports and resistances:
                        nearest_support = min(supports, key=lambda x: abs(x['level'] - current_price))
                        nearest_resistance = min(resistances, key=lambda x: abs(x['level'] - current_price))
                        
                        entry = current_price
                        stop = nearest_support['level'] * 0.99  # 1% below support
                        target = nearest_resistance['level'] * 0.99  # 1% below resistance
                        
                        risk = entry - stop
                        reward = target - entry
                        rr_ratio = reward / risk if risk > 0 else 0
                        
                        setup_data = {
                            'Entry': f"${entry:.2f}",
                            'Stop Loss': f"${stop:.2f}",
                            'Target': f"${target:.2f}",
                            'Risk': f"${risk:.2f}",
                            'Reward': f"${reward:.2f}",
                            'R:R Ratio': f"{rr_ratio:.2f}:1"
                        }
                        
                        setup_df = pd.DataFrame([setup_data]).T
                        setup_df.columns = ['Value']
                        st.dataframe(setup_df, use_container_width=True)
                        
                        # Position sizing
                        st.markdown("### 💰 Position Sizing")
                        
                        account_size = st.number_input("Account Size", value=10000, step=1000)
                        risk_pct = st.slider("Risk %", 1, 5, 2)
                        
                        risk_amount = account_size * (risk_pct / 100)
                        shares = int(risk_amount / risk) if risk > 0 else 0
                        position_size = shares * entry
                        
                        pos_col1, pos_col2 = st.columns(2)
                        
                        with pos_col1:
                            st.metric("Shares", shares)
                            st.metric("Position Size", f"${position_size:.2f}")
                        
                        with pos_col2:
                            st.metric("Risk Amount", f"${risk_amount:.2f}")
                            st.metric("Potential Profit", f"${reward * shares:.2f}")
        
        # Intraday analysis if available
        if data.get('intraday') and show_trendlines:
            st.markdown("---")
            st.subheader("📈 Intraday Analysis")
            
            intraday = data['intraday']
            
            col_i1, col_i2, col_i3, col_i4 = st.columns(4)
            
            with col_i1:
                pa = intraday.get('price_action', {})
                st.metric("Intraday Trend", pa.get('trend', 'N/A').upper())
            
            with col_i2:
                momentum = intraday.get('momentum', {})
                st.metric("Price Change", f"{momentum.get('price_change', 0):.2f}%")
            
            with col_i3:
                levels = intraday.get('levels', {})
                st.metric("VWAP", f"${levels.get('vwap', 0):.2f}")
            
            with col_i4:
                vol = intraday.get('volume_profile', {})
                st.metric("Volume vs Avg", 
                         f"{vol.get('total_volume', 0) / vol.get('avg_volume', 1):.2f}x")
        
        # Action buttons
        st.markdown("---")
        
        action_col1, action_col2, action_col3, action_col4 = st.columns(4)
        
        with action_col1:
            if st.button("📋 Add to Watchlist", use_container_width=True):
                reason = "Pattern detected" if pattern_found else "Manual addition"
                if data_manager.add_to_watchlist_with_reason(ticker, reason, "Pattern Analysis"):
                    st.success("Added to watchlist!")
                else:
                    st.info("Already in watchlist")
        
        with action_col2:
            if st.button("📓 Log Trade", use_container_width=True):
                st.session_state['journal_ticker'] = ticker
                st.switch_page("pages/journal.py")
        
        with action_col3:
            if st.button("📰 Check News", use_container_width=True):
                st.session_state['news_ticker_filter'] = ticker
                st.switch_page("pages/news.py")
        
        with action_col4:
            if st.button("🤖 AI Analysis", use_container_width=True):
                st.switch_page("pages/AISignals.py")
    else:
        st.info("Enter a ticker and click Analyze to see patterns")

# Educational content
st.markdown("---")
with st.expander("📚 Pattern Recognition Guide"):
    st.markdown("""
    ### Common Chart Patterns
    
    **🚩 Bull Flag**
    - Sharp move up (flagpole) followed by consolidation
    - Bullish continuation pattern
    - Entry: Break above flag resistance
    
    **🏴 Bear Flag**
    - Sharp move down followed by consolidation
    - Bearish continuation pattern
    - Entry: Break below flag support
    
    **📐 Triangles**
    - Ascending: Higher lows, flat top (bullish)
    - Descending: Lower highs, flat bottom (bearish)
    - Symmetrical: Could break either way
    
    **📊 Channels**
    - Parallel support and resistance lines
    - Trade the range or breakouts
    - Stronger in the direction of the trend
    
    ### Key Technical Indicators
    
    **Moving Averages**
    - 20 MA: Short-term trend
    - 50 MA: Medium-term trend
    - 200 MA: Long-term trend
    
    **RSI (Relative Strength Index)**
    - Above 70: Overbought
    - Below 30: Oversold
    - Divergences signal potential reversals
    
    **MACD**
    - Signal line crossovers
    - Histogram shows momentum
    - Divergences are powerful signals
    """)
