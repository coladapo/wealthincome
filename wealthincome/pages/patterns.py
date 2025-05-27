import sys
import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import yfinance as yf
import ta
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

# Page configuration
try:
    st.set_page_config(page_title="📈 Chart Pattern Recognition", layout="wide")
except st.errors.StreamlitAPIException:
    pass

st.title('📈 Chart Pattern Recognition')
st.caption("Identify key technical patterns and trading opportunities")

# Initialize session state
if 'pattern_results' not in st.session_state:
    st.session_state.pattern_results = {}

# Sidebar configuration
with st.sidebar:
    st.header("⚙️ Pattern Settings")
    
    # Pattern types to detect
    st.subheader("🔍 Pattern Types")
    detect_support_resistance = st.checkbox("Support/Resistance Levels", value=True)
    detect_trend_lines = st.checkbox("Trend Lines", value=True)
    detect_channels = st.checkbox("Price Channels", value=True)
    detect_triangles = st.checkbox("Triangle Patterns", value=True)
    detect_flags = st.checkbox("Bull/Bear Flags", value=True)
    detect_double_tops = st.checkbox("Double Tops/Bottoms", value=True)
    
    # Timeframe settings
    st.subheader("⏱️ Timeframe")
    period = st.selectbox("Analysis Period", ["1mo", "3mo", "6mo", "1y"], index=1)
    
    # Pattern sensitivity
    st.subheader("🎚️ Sensitivity")
    sensitivity = st.slider("Pattern Detection Sensitivity", 1, 10, 5)

# Pattern detection functions
def find_support_resistance(df, sensitivity=5):
    """Find support and resistance levels"""
    levels = []
    
    # Use rolling windows to find local extrema
    window = max(5, 20 - sensitivity * 2)
    
    # Find peaks (resistance)
    highs = df['High'].rolling(window=window, center=True).max()
    resistance_points = df[df['High'] == highs]['High'].unique()
    
    # Find troughs (support)
    lows = df['Low'].rolling(window=window, center=True).min()
    support_points = df[df['Low'] == lows]['Low'].unique()
    
    # Cluster nearby levels
    for level in resistance_points[-10:]:  # Last 10 resistance levels
        levels.append({'price': level, 'type': 'resistance', 'strength': 1})
    
    for level in support_points[-10:]:  # Last 10 support levels
        levels.append({'price': level, 'type': 'support', 'strength': 1})
    
    return levels

def find_trend_lines(df, sensitivity=5):
    """Find trend lines using linear regression on pivots"""
    trend_lines = []
    
    # Find pivot points
    window = max(5, 20 - sensitivity * 2)
    
    # Uptrend line (connecting lows)
    lows = df['Low'].rolling(window=window, center=True).min()
    pivot_lows = df[df['Low'] == lows].reset_index()
    
    if len(pivot_lows) >= 2:
        # Fit line through pivot lows
        x = np.arange(len(pivot_lows))
        y = pivot_lows['Low'].values
        
        if len(x) > 1:
            z = np.polyfit(x, y, 1)
            slope = z[0]
            intercept = z[1]
            
            # Project to current
            current_support = slope * len(pivot_lows) + intercept
            
            trend_lines.append({
                'type': 'uptrend',
                'slope': slope,
                'current_price': current_support,
                'strength': abs(slope)
            })
    
    # Downtrend line (connecting highs)
    highs = df['High'].rolling(window=window, center=True).max()
    pivot_highs = df[df['High'] == highs].reset_index()
    
    if len(pivot_highs) >= 2:
        x = np.arange(len(pivot_highs))
        y = pivot_highs['High'].values
        
        if len(x) > 1:
            z = np.polyfit(x, y, 1)
            slope = z[0]
            intercept = z[1]
            
            current_resistance = slope * len(pivot_highs) + intercept
            
            trend_lines.append({
                'type': 'downtrend',
                'slope': slope,
                'current_price': current_resistance,
                'strength': abs(slope)
            })
    
    return trend_lines

def find_price_channels(df, sensitivity=5):
    """Find price channels (parallel trend lines)"""
    channels = []
    
    # Simple channel detection using rolling highs/lows
    window = max(20, 50 - sensitivity * 5)
    
    upper_channel = df['High'].rolling(window=window).max()
    lower_channel = df['Low'].rolling(window=window).min()
    mid_channel = (upper_channel + lower_channel) / 2
    
    # Current channel info
    current_upper = upper_channel.iloc[-1]
    current_lower = lower_channel.iloc[-1]
    current_mid = mid_channel.iloc[-1]
    channel_width = current_upper - current_lower
    
    # Position in channel
    current_price = df['Close'].iloc[-1]
    position_in_channel = (current_price - current_lower) / channel_width if channel_width > 0 else 0.5
    
    channels.append({
        'upper': current_upper,
        'lower': current_lower,
        'middle': current_mid,
        'width': channel_width,
        'position': position_in_channel,
        'type': 'horizontal' if abs(upper_channel.diff().mean()) < 0.01 else 'trending'
    })
    
    return channels

def find_triangle_patterns(df, min_points=5):
    """Find triangle patterns (ascending, descending, symmetrical)"""
    triangles = []
    
    # Get recent price action (last 50 bars)
    recent_df = df.tail(50)
    
    # Find converging trend lines
    highs = recent_df['High'].values
    lows = recent_df['Low'].values
    x = np.arange(len(highs))
    
    # Fit lines to highs and lows
    if len(x) >= min_points:
        high_slope = np.polyfit(x, highs, 1)[0]
        low_slope = np.polyfit(x, lows, 1)[0]
        
        # Classify triangle type
        if high_slope < -0.001 and low_slope > 0.001:
            triangle_type = "symmetrical"
        elif abs(high_slope) < 0.001 and low_slope > 0.001:
            triangle_type = "ascending"
        elif high_slope < -0.001 and abs(low_slope) < 0.001:
            triangle_type = "descending"
        else:
            triangle_type = None
        
        if triangle_type:
            triangles.append({
                'type': triangle_type,
                'high_slope': high_slope,
                'low_slope': low_slope,
                'apex_distance': len(recent_df),  # Bars until apex
                'current_width': highs[-1] - lows[-1]
            })
    
    return triangles

def find_flag_patterns(df, min_flagpole=0.10):
    """Find bull and bear flag patterns"""
    flags = []
    
    # Look for strong moves followed by consolidation
    for i in range(20, len(df) - 10):
        # Check for flagpole (strong move)
        flagpole_start = i - 20
        flagpole_end = i
        
        price_change = (df['Close'].iloc[flagpole_end] - df['Close'].iloc[flagpole_start]) / df['Close'].iloc[flagpole_start]
        
        if abs(price_change) >= min_flagpole:
            # Check for consolidation after flagpole
            consolidation = df.iloc[flagpole_end:flagpole_end + 10]
            consolidation_range = consolidation['High'].max() - consolidation['Low'].min()
            flagpole_range = abs(df['High'].iloc[flagpole_start:flagpole_end].max() - df['Low'].iloc[flagpole_start:flagpole_end].min())
            
            # Flag should be smaller range than flagpole
            if consolidation_range < flagpole_range * 0.5:
                flag_type = "bull_flag" if price_change > 0 else "bear_flag"
                
                # Only add if it's recent
                if i >= len(df) - 30:
                    flags.append({
                        'type': flag_type,
                        'flagpole_change': price_change,
                        'consolidation_range': consolidation_range,
                        'start_index': flagpole_start,
                        'flag_index': flagpole_end
                    })
    
    return flags

def find_double_patterns(df, tolerance=0.02):
    """Find double tops and bottoms"""
    patterns = []
    
    # Look for two similar peaks or troughs
    window = 10
    
    # Find local maxima and minima
    for i in range(window, len(df) - window):
        # Check for local maximum (potential double top)
        if df['High'].iloc[i] == df['High'].iloc[i-window:i+window+1].max():
            # Look for another similar peak
            for j in range(max(0, i-50), i-window):
                if df['High'].iloc[j] == df['High'].iloc[j-window:j+window+1].max():
                    # Check if peaks are similar height
                    if abs(df['High'].iloc[i] - df['High'].iloc[j]) / df['High'].iloc[i] < tolerance:
                        # Check for valley between peaks
                        valley = df['Low'].iloc[j:i].min()
                        if valley < min(df['High'].iloc[i], df['High'].iloc[j]) * 0.95:
                            if i >= len(df) - 30:  # Recent pattern
                                patterns.append({
                                    'type': 'double_top',
                                    'first_peak': df['High'].iloc[j],
                                    'second_peak': df['High'].iloc[i],
                                    'valley': valley,
                                    'neckline': valley
                                })
        
        # Check for local minimum (potential double bottom)
        if df['Low'].iloc[i] == df['Low'].iloc[i-window:i+window+1].min():
            for j in range(max(0, i-50), i-window):
                if df['Low'].iloc[j] == df['Low'].iloc[j-window:j+window+1].min():
                    if abs(df['Low'].iloc[i] - df['Low'].iloc[j]) / df['Low'].iloc[i] < tolerance:
                        peak = df['High'].iloc[j:i].max()
                        if peak > max(df['Low'].iloc[i], df['Low'].iloc[j]) * 1.05:
                            if i >= len(df) - 30:
                                patterns.append({
                                    'type': 'double_bottom',
                                    'first_trough': df['Low'].iloc[j],
                                    'second_trough': df['Low'].iloc[i],
                                    'peak': peak,
                                    'neckline': peak
                                })
    
    return patterns

# Main interface
col1, col2 = st.columns([1, 3])

with col1:
    st.header("📊 Stock Selection")
    
    # Get watchlist from data_manager
    watchlist = data_manager.get_watchlist()
    
    # Ticker input
    ticker_source = st.radio("Select source:", ["Manual Input", "Watchlist"])
    
    if ticker_source == "Manual Input":
        ticker = st.text_input("Enter ticker symbol:", value="AAPL").upper()
    else:
        if watchlist:
            ticker = st.selectbox("Select from watchlist:", watchlist)
        else:
            st.warning("Watchlist is empty!")
            ticker = st.text_input("Enter ticker symbol:", value="AAPL").upper()
    
    # Analyze button
    if st.button("🔄 Analyze", type="primary", use_container_width=True):
        with st.spinner(f"Analyzing {ticker}..."):
            # Get stock data using data_manager
            stock_data = data_manager.get_stock_data([ticker], period=period)
            
            if stock_data and ticker in stock_data:
                hist_data = stock_data[ticker].get('history')
                
                if hist_data is not None and not hist_data.empty:
                    # Store results in session state
                    st.session_state.pattern_results[ticker] = {
                        'data': hist_data,
                        'info': stock_data[ticker].get('info', {}),
                        'patterns': {},
                        'timestamp': datetime.now()
                    }
                    
                    # Detect patterns based on settings
                    if detect_support_resistance:
                        st.session_state.pattern_results[ticker]['patterns']['support_resistance'] = find_support_resistance(hist_data, sensitivity)
                    
                    if detect_trend_lines:
                        st.session_state.pattern_results[ticker]['patterns']['trend_lines'] = find_trend_lines(hist_data, sensitivity)
                    
                    if detect_channels:
                        st.session_state.pattern_results[ticker]['patterns']['channels'] = find_price_channels(hist_data, sensitivity)
                    
                    if detect_triangles:
                        st.session_state.pattern_results[ticker]['patterns']['triangles'] = find_triangle_patterns(hist_data)
                    
                    if detect_flags:
                        st.session_state.pattern_results[ticker]['patterns']['flags'] = find_flag_patterns(hist_data)
                    
                    if detect_double_tops:
                        st.session_state.pattern_results[ticker]['patterns']['double_patterns'] = find_double_patterns(hist_data)
                    
                    st.success(f"✅ Analysis complete for {ticker}")
                else:
                    st.error(f"No historical data available for {ticker}")
            else:
                st.error(f"Failed to fetch data for {ticker}")
    
    # Pattern summary
    if ticker in st.session_state.pattern_results:
        st.markdown("---")
        st.subheader("🎯 Patterns Found")
        
        patterns = st.session_state.pattern_results[ticker]['patterns']
        total_patterns = sum(len(p) if isinstance(p, list) else 1 for p in patterns.values())
        
        st.metric("Total Patterns", total_patterns)
        
        # List pattern types found
        for pattern_type, pattern_data in patterns.items():
            if pattern_data:
                count = len(pattern_data) if isinstance(pattern_data, list) else 1
                st.caption(f"• {pattern_type.replace('_', ' ').title()}: {count}")

with col2:
    st.header("📈 Chart & Analysis")
    
    if ticker in st.session_state.pattern_results:
        results = st.session_state.pattern_results[ticker]
        df = results['data']
        info = results['info']
        patterns = results['patterns']
        
        # Create candlestick chart
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                           vertical_spacing=0.03,
                           row_heights=[0.7, 0.3])
        
        # Candlestick chart
        fig.add_trace(go.Candlestick(
            x=df.index,
            open=df['Open'],
            high=df['High'],
            low=df['Low'],
            close=df['Close'],
            name='Price',
            increasing=dict(line=dict(color='#26a69a')),
            decreasing=dict(line=dict(color='#ef5350'))
        ), row=1, col=1)
        
        # Add patterns to chart
        
        # Support/Resistance levels
        if 'support_resistance' in patterns:
            for level in patterns['support_resistance']:
                color = '#FF6B6B' if level['type'] == 'resistance' else '#4ECDC4'
                fig.add_hline(y=level['price'], line_dash="dash", 
                             line_color=color, opacity=0.6,
                             annotation_text=f"{level['type'].upper()} ${level['price']:.2f}",
                             annotation_position="right", row=1, col=1)
        
        # Channels
        if 'channels' in patterns and patterns['channels']:
            channel = patterns['channels'][0]
            fig.add_hline(y=channel['upper'], line_dash="solid", line_color="purple", 
                         opacity=0.5, annotation_text=f"Channel Top ${channel['upper']:.2f}", row=1, col=1)
            fig.add_hline(y=channel['lower'], line_dash="solid", line_color="purple", 
                         opacity=0.5, annotation_text=f"Channel Bottom ${channel['lower']:.2f}", row=1, col=1)
            fig.add_hline(y=channel['middle'], line_dash="dot", line_color="purple", 
                         opacity=0.3, row=1, col=1)
        
        # Volume
        colors = ['red' if df['Close'].iloc[i] < df['Open'].iloc[i] else 'green' 
                 for i in range(len(df))]
        
        fig.add_trace(go.Bar(
            x=df.index,
            y=df['Volume'],
            name='Volume',
            marker_color=colors,
            opacity=0.7
        ), row=2, col=1)
        
        # Update layout
        fig.update_layout(
            title=f"{ticker} - Pattern Analysis",
            xaxis_title="Date",
            yaxis_title="Price",
            template="plotly_dark",
            height=700,
            showlegend=False
        )
        
        fig.update_xaxes(rangeslider_visible=False)
        
        # Display chart
        st.plotly_chart(fig, use_container_width=True)
        
        # Pattern details
        st.markdown("---")
        st.subheader("🔍 Pattern Details")
        
        # Create tabs for different pattern types
        if patterns:
            pattern_tabs = st.tabs([k.replace('_', ' ').title() for k in patterns.keys() if patterns[k]])
            
            tab_index = 0
            for pattern_type, pattern_data in patterns.items():
                if pattern_data:
                    with pattern_tabs[tab_index]:
                        if pattern_type == 'support_resistance':
                            resistance_levels = [p for p in pattern_data if p['type'] == 'resistance']
                            support_levels = [p for p in pattern_data if p['type'] == 'support']
                            
                            col1, col2 = st.columns(2)
                            with col1:
                                st.write("**Resistance Levels:**")
                                for level in sorted(resistance_levels, key=lambda x: x['price'], reverse=True)[:5]:
                                    st.write(f"• ${level['price']:.2f}")
                            
                            with col2:
                                st.write("**Support Levels:**")
                                for level in sorted(support_levels, key=lambda x: x['price'], reverse=True)[:5]:
                                    st.write(f"• ${level['price']:.2f}")
                        
                        elif pattern_type == 'channels':
                            for channel in pattern_data:
                                st.write(f"**Channel Type:** {channel['type'].title()}")
                                st.write(f"**Upper:** ${channel['upper']:.2f}")
                                st.write(f"**Lower:** ${channel['lower']:.2f}")
                                st.write(f"**Width:** ${channel['width']:.2f}")
                                st.write(f"**Current Position:** {channel['position']:.1%} from bottom")
                        
                        elif pattern_type == 'triangles':
                            for triangle in pattern_data:
                                st.write(f"**Pattern:** {triangle['type'].replace('_', ' ').title()} Triangle")
                                st.write(f"**High Slope:** {triangle['high_slope']:.4f}")
                                st.write(f"**Low Slope:** {triangle['low_slope']:.4f}")
                                st.write(f"**Current Width:** ${triangle['current_width']:.2f}")
                        
                        elif pattern_type == 'flags':
                            for flag in pattern_data:
                                st.write(f"**Pattern:** {flag['type'].replace('_', ' ').title()}")
                                st.write(f"**Flagpole Move:** {flag['flagpole_change']:.1%}")
                                st.write(f"**Consolidation Range:** ${flag['consolidation_range']:.2f}")
                        
                        elif pattern_type == 'double_patterns':
                            for pattern in pattern_data:
                                st.write(f"**Pattern:** {pattern['type'].replace('_', ' ').title()}")
                                st.write(f"**Neckline:** ${pattern['neckline']:.2f}")
                                if 'peak' in pattern:
                                    st.write(f"**Peak Between:** ${pattern['peak']:.2f}")
                                if 'valley' in pattern:
                                    st.write(f"**Valley Between:** ${pattern['valley']:.2f}")
                        
                        elif pattern_type == 'trend_lines':
                            for trend in pattern_data:
                                st.write(f"**Trend:** {trend['type'].title()}")
                                st.write(f"**Current Level:** ${trend['current_price']:.2f}")
                                st.write(f"**Slope:** {trend['slope']:.4f}")
                                st.write(f"**Strength:** {trend['strength']:.4f}")
                    
                    tab_index += 1
        
        # Trading recommendations
        st.markdown("---")
        st.subheader("💡 Trading Insights")
        
        current_price = df['Close'].iloc[-1]
        
        # Generate insights based on patterns
        insights = []
        
        if 'channels' in patterns and patterns['channels']:
            channel = patterns['channels'][0]
            if channel['position'] < 0.3:
                insights.append("📈 Near channel bottom - potential bounce play")
            elif channel['position'] > 0.7:
                insights.append("📉 Near channel top - consider taking profits")
        
        if 'flags' in patterns and patterns['flags']:
            flag = patterns['flags'][-1]  # Most recent
            if flag['type'] == 'bull_flag':
                insights.append("🚀 Bull flag detected - continuation pattern")
            else:
                insights.append("🐻 Bear flag detected - potential further decline")
        
        if 'double_patterns' in patterns and patterns['double_patterns']:
            pattern = patterns['double_patterns'][-1]
            if pattern['type'] == 'double_top':
                insights.append("⚠️ Double top pattern - potential reversal")
            else:
                insights.append("✅ Double bottom pattern - potential reversal up")
        
        if insights:
            for insight in insights:
                st.info(insight)
        else:
            st.info("No clear trading signals from current patterns")
        
        # Risk levels
        st.markdown("---")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            nearest_support = None
            if 'support_resistance' in patterns:
                supports = [p['price'] for p in patterns['support_resistance'] if p['type'] == 'support' and p['price'] < current_price]
                if supports:
                    nearest_support = max(supports)
                    st.metric("Nearest Support", f"${nearest_support:.2f}", 
                             f"{((current_price - nearest_support) / current_price * 100):.1f}% below")
        
        with col2:
            st.metric("Current Price", f"${current_price:.2f}")
        
        with col3:
            nearest_resistance = None
            if 'support_resistance' in patterns:
                resistances = [p['price'] for p in patterns['support_resistance'] if p['type'] == 'resistance' and p['price'] > current_price]
                if resistances:
                    nearest_resistance = min(resistances)
                    st.metric("Nearest Resistance", f"${nearest_resistance:.2f}",
                             f"{((nearest_resistance - current_price) / current_price * 100):.1f}% above")
    
    else:
        st.info("👈 Select a ticker and click 'Analyze' to see patterns")

# Footer
st.markdown("---")
st.caption("Pattern recognition is probabilistic - always use proper risk management and confirm with other indicators.")
