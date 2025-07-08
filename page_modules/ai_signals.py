"""
AI Signals Page - Advanced AI-powered trading signals and analysis
Integrates the enhanced AI capabilities from the frontend redesign
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import logging
import numpy as np

from ui.components import (
    render_confidence_indicator, render_metric_card,
    render_stock_ticker, render_alert_banner, render_progress_bar
)
from ui.navigation import render_page_header

logger = logging.getLogger(__name__)

def render_ai_signals():
    """Render AI signals page with enhanced capabilities"""
    
    # Page header
    render_page_header(
        "🧠 AI Signals",
        "Advanced AI-powered trading signals with confidence scoring",
        actions=[
            {"label": "🔄 Refresh Signals", "key": "refresh_signals", "callback": refresh_signals},
            {"label": "⚙️ Configure AI", "key": "config_ai", "callback": show_ai_config}
        ]
    )
    
    # Get data manager
    data_manager = st.session_state.get('data_manager')
    config = st.session_state.get('config')
    
    if not data_manager or not config:
        st.error("System not properly initialized")
        return
    
    # AI Status and Overview
    render_ai_status(config)
    
    st.markdown("---")
    
    # Signal Filters and Controls
    render_signal_controls()
    
    st.markdown("---")
    
    # Main Signals Display
    render_signals_dashboard(data_manager, config)
    
    st.markdown("---")
    
    # Detailed Analysis Section
    render_detailed_analysis(data_manager)

def render_ai_status(config):
    """Render AI system status and overview"""
    
    st.markdown("### 🤖 AI System Status")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        # AI System Health
        render_metric_card(
            "AI System",
            "🟢 Online",
            icon="🤖"
        )
    
    with col2:
        # Model Confidence
        model_confidence = 0.87  # Simulated
        render_metric_card(
            "Model Confidence",
            f"{model_confidence*100:.0f}%",
            delta="+3% vs yesterday",
            icon="🎯"
        )
    
    with col3:
        # Signals Generated Today
        signals_today = 23  # Simulated
        render_metric_card(
            "Signals Today",
            str(signals_today),
            delta="+5 vs average",
            icon="📡"
        )
    
    with col4:
        # Success Rate
        success_rate = 0.78  # Simulated
        render_metric_card(
            "Success Rate (7d)",
            f"{success_rate*100:.0f}%",
            delta="+2% improvement",
            icon="📈"
        )

def render_signal_controls():
    """Render signal filtering and control options"""
    
    st.markdown("### ⚙️ Signal Controls")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        # Signal type filter
        signal_type = st.selectbox(
            "Signal Type",
            ["All Signals", "Buy Signals", "Sell Signals", "Hold Signals", "Watch Signals"],
            key="signal_type_filter"
        )
    
    with col2:
        # Confidence threshold
        confidence_threshold = st.slider(
            "Min Confidence",
            min_value=0.5,
            max_value=0.95,
            value=0.7,
            step=0.05,
            key="confidence_threshold"
        )
    
    with col3:
        # Time horizon
        time_horizon = st.selectbox(
            "Time Horizon",
            ["Intraday", "Short-term (1-5 days)", "Medium-term (1-4 weeks)", "Long-term (1-3 months)"],
            index=1,
            key="time_horizon"
        )
    
    with col4:
        # Market sector filter
        sector_filter = st.selectbox(
            "Sector Focus",
            ["All Sectors", "Technology", "Healthcare", "Financial", "Energy", "Consumer", "Industrial"],
            key="sector_filter"
        )
    
    # Advanced options in expander
    with st.expander("🔧 Advanced AI Settings"):
        col1, col2 = st.columns(2)
        
        with col1:
            enable_news_sentiment = st.toggle(
                "Include News Sentiment",
                value=True,
                help="Factor in news sentiment analysis"
            )
            
            enable_technical_analysis = st.toggle(
                "Enhanced Technical Analysis",
                value=True,
                help="Use advanced technical indicators"
            )
        
        with col2:
            enable_social_sentiment = st.toggle(
                "Social Media Sentiment",
                value=False,
                help="Include social media sentiment (Premium feature)"
            )
            
            enable_options_flow = st.toggle(
                "Options Flow Analysis",
                value=False,
                help="Include unusual options activity (Premium feature)"
            )

def render_signals_dashboard(data_manager, config):
    """Render main signals dashboard"""
    
    st.markdown("### 📡 Live AI Signals")
    
    # Get current watchlist
    watchlist = data_manager.get_watchlist()
    
    if not watchlist:
        st.info("Add stocks to your watchlist to generate AI signals")
        return
    
    # Generate signals for watchlist
    with st.spinner("🤖 AI analyzing market signals..."):
        signals = generate_ai_signals(data_manager, watchlist, config)
    
    if not signals:
        st.warning("No signals found matching current criteria")
        return
    
    # Display signals in cards
    for i, signal in enumerate(signals):
        render_signal_card(signal, i)
        
        if i < len(signals) - 1:
            st.markdown("---")

def render_signal_card(signal: Dict[str, Any], index: int):
    """Render individual signal card"""
    
    # Determine signal styling
    signal_type = signal['signal_type']
    confidence = signal['confidence']
    
    # Signal type styling
    if signal_type == 'BUY':
        signal_color = "#00ff00"
        signal_emoji = "📈"
        action_label = "Buy"
    elif signal_type == 'SELL':
        signal_color = "#ff6b6b"
        signal_emoji = "📉"
        action_label = "Sell"
    elif signal_type == 'WATCH':
        signal_color = "#FFD700"
        signal_emoji = "👁️"
        action_label = "Watch"
    else:
        signal_color = "#b0b0b0"
        signal_emoji = "⏸️"
        action_label = "Hold"
    
    # Main signal card
    with st.container():
        col1, col2, col3 = st.columns([2, 2, 1])
        
        with col1:
            # Stock info
            st.markdown(f"### {signal_emoji} {signal['symbol']} - {signal['name']}")
            st.markdown(f"**${signal['price']:.2f}** ({signal['change_percent']:+.2f}%)")
            
            # Signal type and confidence
            st.markdown(f"""
            <div style="margin: 1rem 0;">
                <span style="
                    background: {signal_color};
                    color: #000000;
                    padding: 0.3rem 0.8rem;
                    border-radius: 15px;
                    font-weight: bold;
                    font-size: 0.9rem;
                ">
                    {signal_emoji} {signal_type}
                </span>
            </div>
            """, unsafe_allow_html=True)
            
            # Primary reasoning
            st.markdown(f"**💡 {signal['primary_reason']}**")
            
        with col2:
            # Confidence indicator
            st.markdown("#### 🎯 AI Confidence")
            render_confidence_indicator(confidence, size="normal")
            
            # Key metrics
            st.markdown("#### 📊 Key Metrics")
            metrics = signal.get('metrics', {})
            for metric, value in metrics.items():
                st.caption(f"**{metric}:** {value}")
        
        with col3:
            # Action buttons
            st.markdown("#### ⚡ Actions")
            
            if st.button(f"📈 Trade", key=f"trade_signal_{index}", use_container_width=True, type="primary"):
                st.session_state['current_page'] = "Trading"
                st.session_state['selected_symbol'] = signal['symbol']
                st.session_state['suggested_action'] = signal_type.lower()
                st.rerun()
            
            if st.button(f"📊 Analyze", key=f"analyze_signal_{index}", use_container_width=True):
                st.session_state['analyze_symbol'] = signal['symbol']
                show_detailed_signal_analysis(signal)
            
            if st.button(f"👁️ Watch", key=f"watch_signal_{index}", use_container_width=True):
                data_manager.add_to_watchlist(signal['symbol'])
                st.success("Added to watchlist!")
        
        # Expandable detailed analysis
        with st.expander(f"🔍 Detailed Analysis - {signal['symbol']}"):
            render_signal_details(signal)

def render_signal_details(signal: Dict[str, Any]):
    """Render detailed signal analysis"""
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("#### 📈 Technical Analysis")
        
        # Technical indicators
        tech_analysis = signal.get('technical_analysis', {})
        for indicator, value in tech_analysis.items():
            st.write(f"**{indicator}:** {value}")
        
        st.markdown("#### 📰 Sentiment Analysis")
        
        # News sentiment
        sentiment = signal.get('sentiment', {})
        sentiment_score = sentiment.get('score', 0.5)
        sentiment_label = sentiment.get('label', 'Neutral')
        
        # Sentiment visualization
        fig = go.Figure(go.Indicator(
            mode = "gauge+number",
            value = sentiment_score,
            domain = {'x': [0, 1], 'y': [0, 1]},
            title = {'text': "News Sentiment"},
            gauge = {
                'axis': {'range': [None, 1]},
                'bar': {'color': "darkblue"},
                'steps': [
                    {'range': [0, 0.4], 'color': "lightgray"},
                    {'range': [0.4, 0.6], 'color': "gray"},
                    {'range': [0.6, 1], 'color': "lightgreen"}
                ],
                'threshold': {
                    'line': {'color': "red", 'width': 4},
                    'thickness': 0.75,
                    'value': 0.9
                }
            }
        ))
        
        fig.update_layout(height=250, template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True, key=f"ai_signals_confidence_gauge_{signal['symbol']}")
    
    with col2:
        st.markdown("#### 🎯 Signal Breakdown")
        
        # Component scores
        components = signal.get('component_scores', {})
        
        for component, score in components.items():
            render_progress_bar(
                score,
                label=component.replace('_', ' ').title(),
                color="#00ff00" if score >= 0.7 else "#FFD700" if score >= 0.5 else "#ff6b6b"
            )
        
        st.markdown("#### 💡 AI Reasoning")
        
        # All reasoning points
        reasoning = signal.get('reasoning', [])
        for reason in reasoning:
            st.write(f"• {reason}")
        
        st.markdown("#### ⚠️ Risk Factors")
        
        # Risk factors
        risks = signal.get('risk_factors', [])
        if risks:
            for risk in risks:
                st.write(f"⚠️ {risk}")
        else:
            st.write("No significant risk factors identified")

def generate_ai_signals(data_manager, watchlist: List[str], config) -> List[Dict[str, Any]]:
    """Generate AI signals for watchlist stocks"""
    
    signals = []
    
    # Get stock data
    stock_data = data_manager.get_stock_data(watchlist, period="5d")
    
    for symbol in watchlist[:10]:  # Limit to 10 for performance
        if symbol not in stock_data or not stock_data[symbol]:
            continue
        
        data = stock_data[symbol]
        info = data.get('info', {})
        
        # Generate signal
        signal = generate_mock_signal(symbol, info, data, data_manager)
        
        # Apply filters
        confidence_threshold = st.session_state.get('confidence_threshold', 0.7)
        if signal['confidence'] >= confidence_threshold:
            signals.append(signal)
    
    # Sort by confidence
    signals.sort(key=lambda x: x['confidence'], reverse=True)
    
    return signals[:8]  # Return top 8 signals

def generate_mock_signal(symbol: str, info: Dict, data: Dict, data_manager) -> Dict[str, Any]:
    """Generate mock AI signal for demonstration"""
    
    # Get basic price info
    price = info.get('regularMarketPrice', 0)
    change_percent = info.get('regularMarketChangePercent', 0)
    volume = info.get('regularMarketVolume', 0)
    avg_volume = info.get('averageVolume', 1)
    market_cap = info.get('marketCap', 0)
    
    # Simulate signal generation
    np.random.seed(hash(symbol) % 1000)  # Consistent randomness per symbol
    
    # Determine signal type based on various factors
    signal_score = 0
    
    # Technical factors
    if change_percent > 2:
        signal_score += 0.3
    elif change_percent > 0:
        signal_score += 0.1
    elif change_percent < -3:
        signal_score -= 0.3
    
    # Volume factor
    if volume > avg_volume * 1.5:
        signal_score += 0.2
    
    # Add some randomness for variety
    signal_score += np.random.uniform(-0.2, 0.3)
    
    # Determine signal type
    if signal_score > 0.3:
        signal_type = "BUY"
    elif signal_score < -0.2:
        signal_type = "SELL"
    elif signal_score > 0.1:
        signal_type = "WATCH"
    else:
        signal_type = "HOLD"
    
    # Generate confidence score
    confidence = max(0.5, min(0.95, 0.7 + np.random.uniform(-0.15, 0.2)))
    
    # Generate reasoning
    reasoning = generate_signal_reasoning(signal_type, info, change_percent, volume, avg_volume)
    
    # Generate component scores
    component_scores = {
        'technical_strength': max(0.3, min(0.95, confidence + np.random.uniform(-0.1, 0.1))),
        'volume_analysis': max(0.3, min(0.95, (volume / avg_volume - 1) * 0.5 + 0.6)),
        'momentum_indicator': max(0.3, min(0.95, (change_percent / 10) + 0.6)),
        'market_sentiment': max(0.3, min(0.95, confidence + np.random.uniform(-0.15, 0.15)))
    }
    
    # Generate technical analysis
    technical_analysis = {
        'RSI': f"{np.random.uniform(30, 70):.1f}",
        'MACD': "Bullish" if signal_type == "BUY" else "Bearish" if signal_type == "SELL" else "Neutral",
        'Support Level': f"${price * 0.95:.2f}",
        'Resistance Level': f"${price * 1.05:.2f}",
        'Volume Ratio': f"{volume/avg_volume:.1f}x"
    }
    
    # Generate sentiment data
    sentiment_score = max(0.2, min(0.8, confidence + np.random.uniform(-0.2, 0.2)))
    sentiment_label = "Positive" if sentiment_score > 0.6 else "Negative" if sentiment_score < 0.4 else "Neutral"
    
    return {
        'symbol': symbol,
        'name': info.get('shortName', symbol),
        'price': price,
        'change_percent': change_percent,
        'signal_type': signal_type,
        'confidence': confidence,
        'primary_reason': reasoning[0] if reasoning else "AI analysis indicates favorable conditions",
        'reasoning': reasoning,
        'component_scores': component_scores,
        'technical_analysis': technical_analysis,
        'sentiment': {
            'score': sentiment_score,
            'label': sentiment_label
        },
        'metrics': {
            'Volume': f"{volume:,}",
            'Market Cap': f"${market_cap/1e9:.1f}B" if market_cap > 1e9 else f"${market_cap/1e6:.0f}M",
            'Avg Volume': f"{avg_volume:,}"
        },
        'risk_factors': generate_risk_factors(signal_type, info),
        'timestamp': datetime.now()
    }

def generate_signal_reasoning(signal_type: str, info: Dict, change_percent: float, volume: int, avg_volume: int) -> List[str]:
    """Generate reasoning for the signal"""
    
    reasoning = []
    
    if signal_type == "BUY":
        reasoning.extend([
            "Strong upward momentum detected with increasing volume",
            "Technical indicators showing bullish divergence",
            "AI model confidence above threshold for entry",
            "Risk-reward ratio favorable for position"
        ])
        
        if change_percent > 3:
            reasoning.append("Significant price breakout with momentum")
        
        if volume > avg_volume * 1.5:
            reasoning.append("Above-average volume confirms institutional interest")
            
    elif signal_type == "SELL":
        reasoning.extend([
            "Bearish signals emerging from technical analysis",
            "Risk management suggests position reduction",
            "Market conditions unfavorable for continued holding"
        ])
        
        if change_percent < -2:
            reasoning.append("Negative momentum accelerating")
            
    elif signal_type == "WATCH":
        reasoning.extend([
            "Setup developing but not yet confirmed",
            "Monitor for volume confirmation",
            "Key technical levels approaching"
        ])
    else:
        reasoning.extend([
            "Current position optimal given market conditions",
            "No clear directional bias detected",
            "Maintain current exposure level"
        ])
    
    return reasoning

def generate_risk_factors(signal_type: str, info: Dict) -> List[str]:
    """Generate risk factors for the signal"""
    
    risks = []
    
    # General market risks
    risks.append("General market volatility may impact position")
    
    # Signal-specific risks
    if signal_type == "BUY":
        risks.extend([
            "Potential for profit-taking at resistance levels",
            "Market correction could affect momentum"
        ])
    elif signal_type == "SELL":
        risks.extend([
            "Potential for short squeeze if sentiment improves",
            "Support levels may provide buying interest"
        ])
    
    # Volume-based risks
    volume = info.get('regularMarketVolume', 0)
    avg_volume = info.get('averageVolume', 1)
    
    if volume < avg_volume * 0.5:
        risks.append("Low volume may indicate lack of conviction")
    
    return risks

def show_detailed_signal_analysis(signal: Dict[str, Any]):
    """Show detailed analysis modal"""
    
    # This would typically open a modal or new page
    # For now, we'll use an expander that's automatically expanded
    st.success(f"Detailed analysis for {signal['symbol']} loaded above!")

def render_detailed_analysis(data_manager):
    """Render detailed analysis section"""
    
    st.markdown("### 📊 Advanced Analysis")
    
    with st.expander("🧠 AI Model Performance", expanded=False):
        col1, col2 = st.columns(2)
        
        with col1:
            # Model accuracy over time
            dates = pd.date_range(start='2024-01-01', end=datetime.now(), freq='D')
            accuracy = np.random.uniform(0.65, 0.85, len(dates))
            accuracy = pd.Series(accuracy, index=dates).rolling(7).mean()  # Smooth
            
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=dates,
                y=accuracy,
                mode='lines',
                name='7-day Average Accuracy',
                line=dict(color='#00ff00', width=2)
            ))
            
            fig.update_layout(
                title="AI Model Accuracy Over Time",
                xaxis_title="Date",
                yaxis_title="Accuracy",
                template="plotly_dark",
                height=300
            )
            
            st.plotly_chart(fig, use_container_width=True, key="ai_model_accuracy_chart")
        
        with col2:
            # Signal distribution
            signal_types = ['BUY', 'SELL', 'WATCH', 'HOLD']
            counts = [35, 15, 25, 25]  # Simulated
            
            fig = px.pie(
                values=counts,
                names=signal_types,
                title="Signal Distribution (Last 7 days)"
            )
            
            fig.update_layout(
                template="plotly_dark",
                height=300
            )
            
            st.plotly_chart(fig, use_container_width=True, key="ai_signals_distribution_pie")

def refresh_signals():
    """Refresh AI signals"""
    st.cache_data.clear()
    st.success("AI signals refreshed!")
    st.rerun()

def show_ai_config():
    """Show AI configuration options"""
    st.info("AI configuration panel would open here")

# Additional helper functions would go here...