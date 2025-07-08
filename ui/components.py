"""
Reusable UI Components
Shared components for the unified platform
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import numpy as np

def render_header():
    """Render main application header"""
    
    # Custom CSS for header styling
    st.markdown("""
    <style>
        .main-header {
            background: linear-gradient(90deg, #1e1e1e 0%, #2d2d2d 100%);
            padding: 1rem;
            border-radius: 10px;
            margin-bottom: 2rem;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        .header-title {
            color: #ffffff;
            font-size: 2rem;
            font-weight: bold;
            margin: 0;
        }
        .header-subtitle {
            color: #b0b0b0;
            font-size: 0.9rem;
            margin: 0;
        }
        .status-indicator {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            margin-top: 0.5rem;
        }
        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background-color: #00ff00;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
        }
    </style>
    """, unsafe_allow_html=True)
    
    # Header content
    col1, col2, col3 = st.columns([3, 2, 1])
    
    with col1:
        st.markdown("""
        <div class="main-header">
            <h1 class="header-title">🚀 WealthIncome AI</h1>
            <p class="header-subtitle">Unified Trading Platform • Real-time Intelligence</p>
            <div class="status-indicator">
                <div class="status-dot"></div>
                <span style="color: #00ff00; font-size: 0.8rem;">Live Market Data</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        # Quick market overview
        render_mini_market_overview()
    
    with col3:
        # User info and quick actions
        render_user_info()

def render_mini_market_overview():
    """Render mini market overview in header"""
    
    try:
        data_manager = st.session_state.get('data_manager')
        if not data_manager:
            return
        
        # Get major indices
        indices = data_manager.get_market_indices()
        
        if indices:
            st.markdown("#### 📈 Market Pulse")
            for symbol, data in list(indices.items())[:2]:  # Show top 2
                delta_color = "normal" if data.change_percent >= 0 else "inverse"
                st.metric(
                    symbol,
                    f"${data.price:.2f}",
                    f"{data.change_percent:+.2f}%",
                    delta_color=delta_color
                )
    except Exception as e:
        st.caption("Market data loading...")

def render_user_info():
    """Render user information and quick actions"""
    
    user = st.session_state.get('auth_user')
    if user:
        st.markdown(f"👤 **{user['username']}**")
        st.caption(f"Last login: {user.get('last_login', 'N/A')[:10]}")
        
        if st.button("🚪 Logout", use_container_width=True, type="secondary"):
            auth_manager = st.session_state.get('auth_manager')
            if auth_manager:
                auth_manager.logout()
                st.rerun()

def render_footer():
    """Render application footer"""
    
    st.markdown("---")
    
    col1, col2, col3 = st.columns([2, 2, 1])
    
    with col1:
        st.caption("🔒 **Risk Disclaimer**: AI recommendations are for educational purposes only. Always do your own research.")
    
    with col2:
        st.caption(f"© 2024 WealthIncome AI • Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    with col3:
        config = st.session_state.get('config')
        if config:
            st.caption(f"v{config.APP_VERSION}")

def render_metric_card(title: str, value: str, delta: str = None, delta_color: str = "normal", icon: str = "📊"):
    """Render a metric card component"""
    
    # Custom CSS for metric cards
    st.markdown("""
    <style>
        .metric-card {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 10px;
            padding: 1rem;
            border: 1px solid rgba(255, 255, 255, 0.1);
            transition: transform 0.2s ease;
        }
        .metric-card:hover {
            transform: translateY(-2px);
            border-color: rgba(255, 255, 255, 0.2);
        }
        .metric-icon {
            font-size: 1.5rem;
            margin-bottom: 0.5rem;
        }
        .metric-title {
            color: #b0b0b0;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .metric-value {
            color: #ffffff;
            font-size: 1.8rem;
            font-weight: bold;
            margin: 0.2rem 0;
        }
        .metric-delta {
            font-size: 0.9rem;
            font-weight: 500;
        }
        .delta-positive { color: #00ff00; }
        .delta-negative { color: #ff6b6b; }
        .delta-neutral { color: #b0b0b0; }
    </style>
    """, unsafe_allow_html=True)
    
    # Determine delta class
    delta_class = "delta-neutral"
    if delta and delta.startswith('+'):
        delta_class = "delta-positive" if delta_color == "normal" else "delta-negative"
    elif delta and delta.startswith('-'):
        delta_class = "delta-negative" if delta_color == "normal" else "delta-positive"
    
    # Render card
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-icon">{icon}</div>
        <div class="metric-title">{title}</div>
        <div class="metric-value">{value}</div>
        {f'<div class="metric-delta {delta_class}">{delta}</div>' if delta else ''}
    </div>
    """, unsafe_allow_html=True)

def render_confidence_indicator(confidence: float, size: str = "normal"):
    """Render AI confidence indicator"""
    
    # Determine confidence level and color
    if confidence >= 0.8:
        level = "High"
        color = "#00ff00"
        emoji = "🟢"
    elif confidence >= 0.6:
        level = "Medium"
        color = "#FFD700"
        emoji = "🟡"
    else:
        level = "Low"
        color = "#ff6b6b"
        emoji = "🔴"
    
    # Size adjustments
    if size == "large":
        font_size = "2rem"
        progress_height = "15px"
    elif size == "small":
        font_size = "0.9rem"
        progress_height = "6px"
    else:
        font_size = "1.2rem"
        progress_height = "10px"
    
    # Render confidence indicator
    st.markdown(f"""
    <div style="text-align: center; margin: 1rem 0;">
        <div style="font-size: {font_size}; font-weight: bold; color: {color}; margin-bottom: 0.5rem;">
            {emoji} {confidence*100:.0f}% Confidence
        </div>
        <div style="background: rgba(255,255,255,0.1); border-radius: 10px; height: {progress_height}; margin: 0.5rem 0;">
            <div style="background: {color}; height: 100%; width: {confidence*100}%; border-radius: 10px; transition: width 0.3s ease;"></div>
        </div>
        <div style="font-size: 0.8rem; color: #b0b0b0;">{level} Confidence</div>
    </div>
    """, unsafe_allow_html=True)

def render_stock_ticker(symbol: str, price: float, change: float, change_percent: float):
    """Render animated stock ticker"""
    
    # Determine color based on change
    color = "#00ff00" if change >= 0 else "#ff6b6b"
    arrow = "▲" if change >= 0 else "▼"
    
    st.markdown(f"""
    <div style="
        background: linear-gradient(135deg, #1e1e1e 0%, #2d2d2d 100%);
        border-radius: 10px;
        padding: 1rem;
        border: 1px solid rgba(255, 255, 255, 0.1);
        text-align: center;
        margin: 0.5rem 0;
    ">
        <div style="font-size: 1.2rem; font-weight: bold; color: #ffffff; margin-bottom: 0.5rem;">
            {symbol}
        </div>
        <div style="font-size: 1.8rem; font-weight: bold; color: #ffffff; margin-bottom: 0.3rem;">
            ${price:.2f}
        </div>
        <div style="font-size: 1rem; color: {color}; font-weight: 500;">
            {arrow} ${change:+.2f} ({change_percent:+.2f}%)
        </div>
    </div>
    """, unsafe_allow_html=True)

def render_alert_banner(message: str, alert_type: str = "info", dismissible: bool = True):
    """Render alert banner"""
    
    # Alert styling
    styles = {
        "success": {"bg": "#00ff0020", "border": "#00ff00", "icon": "✅"},
        "warning": {"bg": "#FFD70020", "border": "#FFD700", "icon": "⚠️"},
        "error": {"bg": "#ff6b6b20", "border": "#ff6b6b", "icon": "❌"},
        "info": {"bg": "#0080ff20", "border": "#0080ff", "icon": "ℹ️"}
    }
    
    style = styles.get(alert_type, styles["info"])
    
    alert_html = f"""
    <div style="
        background: {style['bg']};
        border: 1px solid {style['border']};
        border-radius: 8px;
        padding: 1rem;
        margin: 1rem 0;
        display: flex;
        align-items: center;
        gap: 1rem;
    ">
        <span style="font-size: 1.2rem;">{style['icon']}</span>
        <span style="flex: 1; color: #ffffff;">{message}</span>
    """
    
    if dismissible:
        alert_html += """
        <button onclick="this.parentElement.style.display='none'" style="
            background: none;
            border: none;
            color: #ffffff;
            cursor: pointer;
            font-size: 1.2rem;
        ">×</button>
        """
    
    alert_html += "</div>"
    
    st.markdown(alert_html, unsafe_allow_html=True)

def render_loading_spinner(message: str = "Loading..."):
    """Render loading spinner"""
    
    st.markdown(f"""
    <div style="text-align: center; margin: 2rem 0;">
        <div style="
            border: 4px solid rgba(255, 255, 255, 0.1);
            border-top: 4px solid #00ff00;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto 1rem auto;
        "></div>
        <p style="color: #b0b0b0;">{message}</p>
    </div>
    <style>
        @keyframes spin {{
            0% {{ transform: rotate(0deg); }}
            100% {{ transform: rotate(360deg); }}
        }}
    </style>
    """, unsafe_allow_html=True)

def render_progress_bar(progress: float, label: str = "", color: str = "#00ff00"):
    """Render progress bar"""
    
    progress = max(0, min(1, progress))  # Clamp between 0 and 1
    
    st.markdown(f"""
    <div style="margin: 1rem 0;">
        {f'<div style="color: #ffffff; margin-bottom: 0.5rem; font-weight: 500;">{label}</div>' if label else ''}
        <div style="
            background: rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            height: 20px;
            overflow: hidden;
        ">
            <div style="
                background: {color};
                height: 100%;
                width: {progress*100}%;
                border-radius: 10px;
                transition: width 0.3s ease;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #000000;
                font-weight: bold;
                font-size: 0.8rem;
            ">
                {progress*100:.0f}%
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

def render_info_tooltip(text: str, tooltip: str):
    """Render text with hover tooltip"""
    
    st.markdown(f"""
    <div title="{tooltip}" style="
        display: inline-block;
        cursor: help;
        border-bottom: 1px dotted #b0b0b0;
        color: #ffffff;
    ">
        {text}
    </div>
    """, unsafe_allow_html=True)

def render_status_badge(status: str, variant: str = "default"):
    """Render status badge"""
    
    variants = {
        "success": {"bg": "#00ff00", "color": "#000000"},
        "warning": {"bg": "#FFD700", "color": "#000000"},
        "error": {"bg": "#ff6b6b", "color": "#ffffff"},
        "info": {"bg": "#0080ff", "color": "#ffffff"},
        "default": {"bg": "#b0b0b0", "color": "#000000"}
    }
    
    style = variants.get(variant, variants["default"])
    
    st.markdown(f"""
    <span style="
        background: {style['bg']};
        color: {style['color']};
        padding: 0.2rem 0.6rem;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: bold;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    ">
        {status}
    </span>
    """, unsafe_allow_html=True)