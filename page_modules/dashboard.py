"""
Dashboard Page - Main overview with AI insights and market summary
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Any
import logging

from ui.components import (
    render_metric_card, render_confidence_indicator, 
    render_stock_ticker, render_alert_banner, render_loading_spinner
)
from ui.navigation import render_page_header, render_quick_actions

logger = logging.getLogger(__name__)

def render_dashboard():
    """Render main dashboard page"""
    
    # Page header
    render_page_header(
        "🏠 Dashboard",
        "AI-powered market insights and portfolio overview",
        actions=[
            {"label": "🔄 Refresh", "key": "refresh_dashboard", "callback": lambda: st.cache_data.clear()}
        ]
    )
    
    # Get managers from session
    data_manager = st.session_state.get('data_manager')
    config = st.session_state.get('config')
    
    if not data_manager or not config:
        st.error("System not properly initialized. Please refresh the page.")
        return
    
    # Market overview section
    render_market_overview(data_manager)
    
    st.markdown("---")
    
    # AI insights section
    render_ai_insights(data_manager, config)
    
    st.markdown("---")
    
    # Portfolio summary
    render_portfolio_summary(data_manager)
    
    st.markdown("---")
    
    # Top opportunities
    render_top_opportunities(data_manager, config)
    
    st.markdown("---")
    
    # Quick actions
    render_quick_actions()

def render_market_overview(data_manager):
    """Render market overview section"""
    
    st.markdown("### 🌍 Market Overview")
    
    try:
        # Get market indices
        with st.spinner("Loading market data..."):
            indices_data = data_manager.get_market_indices()
        
        if indices_data:
            # Display market indices
            cols = st.columns(4)
            index_names = {
                'SPY': 'S&P 500',
                'QQQ': 'NASDAQ',
                'DIA': 'DOW',
                'IWM': 'Russell 2000'
            }
            
            for i, (symbol, data) in enumerate(indices_data.items()):
                with cols[i % 4]:
                    delta_color = "normal" if data.change_percent >= 0 else "inverse"
                    st.metric(
                        index_names.get(symbol, symbol),
                        f"${data.price:.2f}",
                        f"{data.change_percent:+.2f}%",
                        delta_color=delta_color
                    )
        else:
            st.warning("Market data temporarily unavailable")
            
    except Exception as e:
        logger.error(f"Error loading market overview: {e}")
        st.error("Unable to load market data")

def render_ai_insights(data_manager, config):
    """Render AI insights section"""
    
    st.markdown("### 🧠 AI Market Intelligence")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        # Market sentiment analysis
        st.markdown("#### 📊 Market Sentiment")
        
        # Simulated AI sentiment data
        sentiment_data = {
            'Overall Market': 0.72,
            'Tech Sector': 0.85,
            'Financial Sector': 0.65,
            'Energy Sector': 0.58,
            'Healthcare': 0.71
        }
        
        # Create sentiment chart
        fig = go.Figure()
        
        sectors = list(sentiment_data.keys())
        sentiments = list(sentiment_data.values())
        colors = ['#00ff00' if s >= 0.7 else '#FFD700' if s >= 0.5 else '#ff6b6b' for s in sentiments]
        
        fig.add_trace(go.Bar(
            x=sectors,
            y=sentiments,
            marker_color=colors,
            text=[f"{s:.0%}" for s in sentiments],
            textposition='auto',
        ))
        
        fig.update_layout(
            title="AI Sentiment Analysis",
            xaxis_title="Sectors",
            yaxis_title="Sentiment Score",
            yaxis=dict(range=[0, 1]),
            template="plotly_dark",
            height=300
        )
        
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        # AI confidence indicator
        st.markdown("#### 🎯 AI Confidence")
        
        # Overall AI confidence (simulated)
        overall_confidence = 0.78
        render_confidence_indicator(overall_confidence, size="large")
        
        # Key insights
        st.markdown("#### 💡 Key Insights")
        insights = [
            "🔥 Tech sector showing strong momentum",
            "📈 RSI indicates oversold conditions in energy",
            "🎯 High volume in growth stocks suggests institutional buying",
            "⚠️ Watch for potential volatility around Fed announcements"
        ]
        
        for insight in insights:
            st.markdown(f"• {insight}")

def render_portfolio_summary(data_manager):
    """Render portfolio summary section"""
    
    st.markdown("### 💼 Portfolio Summary")
    
    try:
        # Get portfolio data
        portfolio_data = data_manager.get_portfolio_data()
        performance_data = data_manager.analyze_portfolio_performance()
        
        if portfolio_data:
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                total_value = portfolio_data.get('total_value', 0)
                render_metric_card(
                    "Total Value",
                    f"${total_value:,.0f}",
                    icon="💰"
                )
            
            with col2:
                daily_return = portfolio_data.get('performance', {}).get('daily_return_percent', 0)
                render_metric_card(
                    "Daily Return",
                    f"{daily_return:+.2f}%",
                    delta=f"${portfolio_data.get('performance', {}).get('daily_return', 0):+.0f}",
                    delta_color="normal" if daily_return >= 0 else "inverse",
                    icon="📈"
                )
            
            with col3:
                if performance_data and performance_data.get('total_trades', 0) > 0:
                    win_rate = performance_data.get('win_rate', 0) * 100
                    render_metric_card(
                        "Win Rate",
                        f"{win_rate:.0f}%",
                        icon="🎯"
                    )
                else:
                    render_metric_card(
                        "Win Rate",
                        "N/A",
                        icon="🎯"
                    )
            
            with col4:
                cash_amount = portfolio_data.get('cash', 0)
                render_metric_card(
                    "Available Cash",
                    f"${cash_amount:,.0f}",
                    icon="💵"
                )
            
            # Portfolio chart
            if portfolio_data.get('positions'):
                render_portfolio_chart(portfolio_data['positions'])
                
        else:
            st.info("Portfolio data will appear here once you start trading")
            
    except Exception as e:
        logger.error(f"Error loading portfolio summary: {e}")
        st.error("Unable to load portfolio data")

def render_portfolio_chart(positions: Dict[str, Any]):
    """Render portfolio allocation chart"""
    
    if not positions:
        return
    
    # Prepare data for pie chart
    symbols = list(positions.keys())
    values = [pos.get('market_value', 0) for pos in positions.values()]
    
    if sum(values) == 0:
        return
    
    # Create pie chart
    fig = px.pie(
        values=values,
        names=symbols,
        title="Portfolio Allocation"
    )
    
    fig.update_layout(
        template="plotly_dark",
        height=300
    )
    
    st.plotly_chart(fig, use_container_width=True)

def render_top_opportunities(data_manager, config):
    """Render top AI-selected opportunities"""
    
    st.markdown("### 🎯 Today's Top Opportunities")
    st.caption("AI-selected stocks with highest confidence scores")
    
    try:
        # Get watchlist
        watchlist = data_manager.get_watchlist()
        
        if not watchlist:
            st.info("Add stocks to your watchlist to see AI opportunities")
            return
        
        with st.spinner("🤖 AI analyzing opportunities..."):
            # Get stock data for watchlist
            stock_data = data_manager.get_stock_data(watchlist[:8], period="5d")
        
        if not stock_data:
            st.warning("Unable to load stock data for analysis")
            return
        
        # Simulate AI analysis and ranking
        opportunities = []
        
        for symbol, data in stock_data.items():
            if not data:
                continue
                
            info = data.get('info', {})
            price = info.get('regularMarketPrice', 0)
            change_percent = info.get('regularMarketChangePercent', 0)
            
            # Simulate confidence score based on various factors
            confidence = calculate_mock_confidence(info, data)
            
            opportunities.append({
                'symbol': symbol,
                'name': info.get('shortName', symbol),
                'price': price,
                'change_percent': change_percent,
                'confidence': confidence,
                'volume': info.get('regularMarketVolume', 0),
                'market_cap': info.get('marketCap', 0)
            })
        
        # Sort by confidence
        opportunities.sort(key=lambda x: x['confidence'], reverse=True)
        
        # Display top opportunities
        for i, opp in enumerate(opportunities[:4]):
            col1, col2 = st.columns([3, 1])
            
            with col1:
                # Stock info
                st.markdown(f"**{opp['symbol']} - {opp['name']}**")
                
                # Price and change
                delta_color = "🟢" if opp['change_percent'] >= 0 else "🔴"
                st.markdown(f"${opp['price']:.2f} {delta_color} {opp['change_percent']:+.2f}%")
                
                # Confidence indicator
                confidence_color = "#00ff00" if opp['confidence'] >= 0.8 else "#FFD700" if opp['confidence'] >= 0.6 else "#ff6b6b"
                st.markdown(f"🎯 **AI Confidence: {opp['confidence']*100:.0f}%**")
                
                # Volume info
                if opp['volume'] > 0:
                    st.caption(f"Volume: {opp['volume']:,}")
            
            with col2:
                # Action buttons
                if st.button(f"📈 Trade {opp['symbol']}", key=f"trade_{opp['symbol']}", use_container_width=True):
                    st.session_state['current_page'] = "Trading"
                    st.session_state['selected_symbol'] = opp['symbol']
                    st.rerun()
                
                if st.button(f"📊 Analysis", key=f"analyze_{opp['symbol']}", use_container_width=True):
                    st.session_state['current_page'] = "Analytics"
                    st.session_state['selected_symbol'] = opp['symbol']
                    st.rerun()
            
            if i < len(opportunities) - 1:
                st.markdown("---")
                
    except Exception as e:
        logger.error(f"Error loading opportunities: {e}")
        st.error("Unable to load trading opportunities")

def calculate_mock_confidence(info: Dict[str, Any], data: Dict[str, Any]) -> float:
    """Calculate mock confidence score for demonstration"""
    
    # Base confidence
    confidence = 0.5
    
    # Factor in market cap (larger = more stable)
    market_cap = info.get('marketCap', 0)
    if market_cap > 100e9:  # > 100B
        confidence += 0.1
    elif market_cap > 10e9:  # > 10B
        confidence += 0.05
    
    # Factor in volume
    volume = info.get('regularMarketVolume', 0)
    avg_volume = info.get('averageVolume', 1)
    if volume > avg_volume * 1.5:  # High volume
        confidence += 0.1
    
    # Factor in price movement
    change_percent = info.get('regularMarketChangePercent', 0)
    if 0 < change_percent < 5:  # Moderate positive movement
        confidence += 0.15
    elif change_percent > 5:  # Strong positive movement
        confidence += 0.05
    
    # Factor in technical indicators (simulated)
    # In real implementation, this would use actual technical analysis
    confidence += np.random.uniform(-0.1, 0.2)
    
    return max(0.3, min(0.95, confidence))  # Clamp between 30% and 95%

# Additional helper functions
import numpy as np

def get_market_summary():
    """Get AI-generated market summary"""
    summaries = [
        "Markets showing strong momentum with tech leading gains. AI confidence remains high.",
        "Mixed signals across sectors. Recommend focusing on high-conviction plays.",
        "Volatility increasing. Consider defensive positions and tight risk management.",
        "Strong institutional buying detected. Momentum likely to continue short-term.",
        "Market consolidation phase. Look for breakout opportunities in quality names."
    ]
    
    return np.random.choice(summaries)