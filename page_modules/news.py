"""
News & Sentiment Page - Market news and sentiment analysis
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
from typing import Dict, List, Any
import numpy as np

from ui.components import render_metric_card, render_confidence_indicator
from ui.navigation import render_page_header

def render_news():
    """Render news and sentiment analysis page"""
    
    render_page_header(
        "📰 News & Sentiment",
        "Market news and sentiment analysis for informed trading decisions",
        actions=[
            {"label": "🔄 Refresh", "key": "refresh_news", "callback": refresh_news_data},
            {"label": "📊 Sentiment Report", "key": "generate_sentiment_report", "callback": generate_sentiment_report}
        ]
    )
    
    # Market Sentiment Overview
    render_sentiment_overview()
    
    st.markdown("---")
    
    # News and Analysis
    col1, col2 = st.columns([2, 1])
    
    with col1:
        render_news_feed()
    
    with col2:
        render_sentiment_analysis()
    
    st.markdown("---")
    
    # Detailed Analysis
    render_detailed_analysis()

def render_sentiment_overview():
    """Render overall market sentiment overview"""
    
    st.markdown("### 📊 Market Sentiment Overview")
    
    # Generate mock sentiment data
    overall_sentiment = np.random.uniform(30, 70)
    fear_greed_index = np.random.uniform(20, 80)
    news_sentiment = np.random.uniform(40, 80)
    social_sentiment = np.random.uniform(35, 75)
    analyst_sentiment = np.random.uniform(45, 85)
    
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        sentiment_text = get_sentiment_text(overall_sentiment)
        render_metric_card(
            "Overall Sentiment",
            f"{overall_sentiment:.0f}/100",
            delta=sentiment_text,
            icon="🎯"
        )
    
    with col2:
        fg_text = get_fear_greed_text(fear_greed_index)
        render_metric_card(
            "Fear & Greed",
            f"{fear_greed_index:.0f}",
            delta=fg_text,
            icon="😰" if fear_greed_index < 40 else "😊"
        )
    
    with col3:
        render_metric_card(
            "News Sentiment",
            f"{news_sentiment:.0f}/100",
            delta=get_sentiment_text(news_sentiment),
            icon="📰"
        )
    
    with col4:
        render_metric_card(
            "Social Media",
            f"{social_sentiment:.0f}/100",
            delta=get_sentiment_text(social_sentiment),
            icon="📱"
        )
    
    with col5:
        render_metric_card(
            "Analyst Ratings",
            f"{analyst_sentiment:.0f}/100",
            delta=get_sentiment_text(analyst_sentiment),
            icon="🏦"
        )

def get_sentiment_text(score):
    """Convert sentiment score to text"""
    if score >= 70:
        return "Very Bullish"
    elif score >= 60:
        return "Bullish"
    elif score >= 40:
        return "Neutral"
    elif score >= 30:
        return "Bearish"
    else:
        return "Very Bearish"

def get_fear_greed_text(score):
    """Convert fear & greed score to text"""
    if score >= 75:
        return "Extreme Greed"
    elif score >= 55:
        return "Greed"
    elif score >= 45:
        return "Neutral"
    elif score >= 25:
        return "Fear"
    else:
        return "Extreme Fear"

def render_news_feed():
    """Render market news feed"""
    
    st.markdown("### 📰 Latest Market News")
    
    # Generate mock news data
    news_items = generate_mock_news()
    
    # News filter
    col1, col2, col3 = st.columns(3)
    
    with col1:
        category_filter = st.selectbox(
            "Category",
            ["All", "Markets", "Technology", "Healthcare", "Finance", "Energy", "Economic"]
        )
    
    with col2:
        sentiment_filter = st.selectbox(
            "Sentiment",
            ["All", "Positive", "Neutral", "Negative"]
        )
    
    with col3:
        timeframe = st.selectbox(
            "Timeframe",
            ["Today", "This Week", "This Month"]
        )
    
    # Display news items
    for i, news in enumerate(news_items[:10]):  # Show top 10 news items
        with st.container():
            col1, col2 = st.columns([3, 1])
            
            with col1:
                st.markdown(f"**{news['title']}**")
                st.caption(f"{news['source']} • {news['timestamp'].strftime('%Y-%m-%d %H:%M')}")
                st.write(news['summary'])
                
                # Tags
                tags_html = ""
                for tag in news['tags']:
                    color = get_tag_color(tag)
                    tags_html += f'<span style="background-color: {color}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px; margin-right: 5px;">{tag}</span>'
                st.markdown(tags_html, unsafe_allow_html=True)
            
            with col2:
                # Sentiment indicator
                sentiment_score = news['sentiment_score']
                sentiment_color = get_sentiment_color(sentiment_score)
                
                st.markdown(
                    f'<div style="text-align: center; padding: 10px; background-color: {sentiment_color}; border-radius: 5px; color: white;">'
                    f'<strong>Sentiment</strong><br>{sentiment_score:.0f}/100</div>',
                    unsafe_allow_html=True
                )
                
                # Impact score
                impact = news['market_impact']
                st.metric("Impact", f"{impact}/5", delta="Market")
            
            st.markdown("---")

def generate_mock_news():
    """Generate mock news data"""
    
    news_templates = [
        {
            "title": "Federal Reserve Signals Potential Rate Changes in Q4",
            "source": "Financial Times",
            "summary": "The Federal Reserve hints at possible monetary policy adjustments following latest inflation data, with markets showing mixed reactions to the announcement.",
            "tags": ["Fed", "Interest Rates", "Monetary Policy"],
            "sentiment_score": np.random.uniform(40, 70),
            "market_impact": np.random.randint(3, 5)
        },
        {
            "title": "Tech Sector Shows Strong Earnings Growth Despite Market Volatility",
            "source": "Bloomberg",
            "summary": "Major technology companies report better-than-expected quarterly results, driving optimism in the sector despite broader market concerns.",
            "tags": ["Technology", "Earnings", "Growth"],
            "sentiment_score": np.random.uniform(60, 85),
            "market_impact": np.random.randint(2, 4)
        },
        {
            "title": "Energy Prices Surge on Geopolitical Tensions",
            "source": "Reuters",
            "summary": "Oil and gas prices climb as international tensions affect supply chains, with energy stocks seeing significant movement in pre-market trading.",
            "tags": ["Energy", "Geopolitics", "Commodities"],
            "sentiment_score": np.random.uniform(30, 60),
            "market_impact": np.random.randint(4, 5)
        },
        {
            "title": "Healthcare Innovation Drives Sector Optimism",
            "source": "Wall Street Journal",
            "summary": "Breakthrough developments in biotechnology and pharmaceuticals boost investor confidence in healthcare sector fundamentals.",
            "tags": ["Healthcare", "Innovation", "Biotech"],
            "sentiment_score": np.random.uniform(65, 90),
            "market_impact": np.random.randint(2, 4)
        },
        {
            "title": "Consumer Spending Data Reveals Economic Resilience",
            "source": "CNBC",
            "summary": "Latest consumer spending figures indicate continued economic strength despite inflationary pressures and market uncertainties.",
            "tags": ["Consumer", "Economic Data", "Spending"],
            "sentiment_score": np.random.uniform(50, 75),
            "market_impact": np.random.randint(3, 5)
        }
    ]
    
    # Add timestamps
    news_items = []
    for i, template in enumerate(news_templates):
        news = template.copy()
        news['timestamp'] = datetime.now() - timedelta(hours=i*2, minutes=np.random.randint(0, 120))
        news_items.append(news)
    
    return news_items

def get_tag_color(tag):
    """Get color for news tag"""
    tag_colors = {
        "Fed": "#FF6B6B",
        "Interest Rates": "#4ECDC4",
        "Technology": "#45B7D1",
        "Earnings": "#96CEB4",
        "Energy": "#FFEAA7",
        "Healthcare": "#DDA0DD",
        "Economic Data": "#98D8C8"
    }
    return tag_colors.get(tag, "#95A5A6")

def get_sentiment_color(score):
    """Get color based on sentiment score"""
    if score >= 70:
        return "#27AE60"  # Green
    elif score >= 55:
        return "#2ECC71"  # Light Green
    elif score >= 45:
        return "#95A5A6"  # Gray
    elif score >= 30:
        return "#E67E22"  # Orange
    else:
        return "#E74C3C"  # Red

def render_sentiment_analysis():
    """Render sentiment analysis charts and metrics"""
    
    st.markdown("### 🎯 Sentiment Analysis")
    
    # Sentiment gauge
    overall_sentiment = np.random.uniform(30, 70)
    
    fig_gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=overall_sentiment,
        domain={'x': [0, 1], 'y': [0, 1]},
        title={'text': "Market Sentiment"},
        gauge={
            'axis': {'range': [None, 100]},
            'bar': {'color': "darkblue"},
            'steps': [
                {'range': [0, 25], 'color': "red"},
                {'range': [25, 50], 'color': "orange"},
                {'range': [50, 75], 'color': "yellow"},
                {'range': [75, 100], 'color': "green"}
            ],
            'threshold': {
                'line': {'color': "red", 'width': 4},
                'thickness': 0.75,
                'value': 90
            }
        }
    ))
    
    fig_gauge.update_layout(
        template="plotly_dark",
        height=250
    )
    
    st.plotly_chart(fig_gauge, use_container_width=True, key="sentiment_gauge")
    
    # Sentiment breakdown
    st.markdown("#### Sentiment Breakdown")
    
    sentiment_data = {
        "Positive": np.random.randint(40, 60),
        "Neutral": np.random.randint(20, 40),
        "Negative": np.random.randint(10, 30)
    }
    
    fig_pie = px.pie(
        values=list(sentiment_data.values()),
        names=list(sentiment_data.keys()),
        title="News Sentiment Distribution",
        color_discrete_map={
            "Positive": "#27AE60",
            "Neutral": "#95A5A6", 
            "Negative": "#E74C3C"
        }
    )
    
    fig_pie.update_layout(
        template="plotly_dark",
        height=300
    )
    
    st.plotly_chart(fig_pie, use_container_width=True, key="sentiment_pie")
    
    # Trending topics
    st.markdown("#### 🔥 Trending Topics")
    
    trending_topics = [
        {"topic": "Federal Reserve", "mentions": 156, "sentiment": 45},
        {"topic": "AI Technology", "mentions": 134, "sentiment": 78},
        {"topic": "Energy Crisis", "mentions": 98, "sentiment": 32},
        {"topic": "Healthcare", "mentions": 87, "sentiment": 65},
        {"topic": "Inflation", "mentions": 76, "sentiment": 38}
    ]
    
    for topic in trending_topics:
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            st.write(f"**{topic['topic']}**")
        with col2:
            st.write(f"{topic['mentions']} mentions")
        with col3:
            sentiment_emoji = "📈" if topic['sentiment'] > 60 else "📉" if topic['sentiment'] < 40 else "➡️"
            st.write(f"{sentiment_emoji} {topic['sentiment']}")

def render_detailed_analysis():
    """Render detailed sentiment and news analysis"""
    
    # Sentiment Timeline
    with st.expander("📈 Sentiment Timeline", expanded=False):
        render_sentiment_timeline()
    
    # Sector Sentiment
    with st.expander("🏭 Sector Sentiment", expanded=False):
        render_sector_sentiment()
    
    # News Impact Analysis
    with st.expander("💥 News Impact Analysis", expanded=False):
        render_news_impact_analysis()

def render_sentiment_timeline():
    """Render sentiment over time"""
    
    st.markdown("#### Sentiment Trends Over Time")
    
    # Generate mock timeline data
    dates = pd.date_range(start=datetime.now() - timedelta(days=30), end=datetime.now(), freq='D')
    
    market_sentiment = 50 + np.random.normal(0, 10, len(dates))
    news_sentiment = 50 + np.random.normal(0, 8, len(dates))
    social_sentiment = 50 + np.random.normal(0, 12, len(dates))
    
    fig_timeline = go.Figure()
    
    fig_timeline.add_trace(go.Scatter(
        x=dates,
        y=market_sentiment,
        mode='lines',
        name='Market Sentiment',
        line=dict(color='blue', width=2)
    ))
    
    fig_timeline.add_trace(go.Scatter(
        x=dates,
        y=news_sentiment,
        mode='lines',
        name='News Sentiment',
        line=dict(color='green', width=2)
    ))
    
    fig_timeline.add_trace(go.Scatter(
        x=dates,
        y=social_sentiment,
        mode='lines',
        name='Social Sentiment',
        line=dict(color='orange', width=2)
    ))
    
    fig_timeline.update_layout(
        title="30-Day Sentiment Trends",
        xaxis_title="Date",
        yaxis_title="Sentiment Score",
        template="plotly_dark",
        height=400
    )
    
    st.plotly_chart(fig_timeline, use_container_width=True, key="sentiment_timeline")

def render_sector_sentiment():
    """Render sentiment by sector"""
    
    st.markdown("#### Sentiment by Sector")
    
    sectors = [
        "Technology", "Healthcare", "Financial", "Energy", 
        "Consumer", "Industrial", "Real Estate", "Utilities"
    ]
    
    sector_sentiment = [np.random.uniform(30, 80) for _ in sectors]
    
    fig_sector = go.Figure(data=[
        go.Bar(
            x=sectors,
            y=sector_sentiment,
            marker_color=[get_sentiment_color(s) for s in sector_sentiment]
        )
    ])
    
    fig_sector.update_layout(
        title="Current Sector Sentiment",
        xaxis_title="Sector",
        yaxis_title="Sentiment Score",
        template="plotly_dark",
        height=400
    )
    
    st.plotly_chart(fig_sector, use_container_width=True, key="sector_sentiment_chart")

def render_news_impact_analysis():
    """Render news impact on market movements"""
    
    st.markdown("#### News Impact on Market Movements")
    
    # Generate mock impact data
    impact_events = [
        {"event": "Fed Rate Decision", "impact": 2.3, "direction": "up"},
        {"event": "Tech Earnings Beat", "impact": 1.8, "direction": "up"},
        {"event": "Geopolitical Tensions", "impact": -1.5, "direction": "down"},
        {"event": "Inflation Data Release", "impact": -0.8, "direction": "down"},
        {"event": "GDP Growth Report", "impact": 1.2, "direction": "up"}
    ]
    
    events = [e["event"] for e in impact_events]
    impacts = [e["impact"] for e in impact_events]
    colors = ['green' if i > 0 else 'red' for i in impacts]
    
    fig_impact = go.Figure(data=[
        go.Bar(
            x=events,
            y=impacts,
            marker_color=colors
        )
    ])
    
    fig_impact.update_layout(
        title="Market Impact of Recent News Events (%)",
        xaxis_title="News Event",
        yaxis_title="Market Impact (%)",
        template="plotly_dark",
        height=400
    )
    
    st.plotly_chart(fig_impact, use_container_width=True, key="news_impact_chart")
    
    # Impact summary
    st.markdown("#### Impact Summary")
    
    total_positive = sum(i["impact"] for i in impact_events if i["impact"] > 0)
    total_negative = sum(i["impact"] for i in impact_events if i["impact"] < 0)
    net_impact = total_positive + total_negative
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Positive Impact", f"+{total_positive:.1f}%")
    
    with col2:
        st.metric("Negative Impact", f"{total_negative:.1f}%")
    
    with col3:
        st.metric("Net Impact", f"{net_impact:+.1f}%")

def refresh_news_data():
    """Refresh news and sentiment data"""
    st.cache_data.clear()
    st.success("News data refreshed!")
    st.rerun()

def generate_sentiment_report():
    """Generate sentiment analysis report"""
    st.info("Sentiment report generation feature would be implemented here")
    # In a real implementation, this would generate a comprehensive sentiment analysis report