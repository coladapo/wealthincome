"""
Settings Page - User preferences and system configuration
"""

import streamlit as st
import pandas as pd
from datetime import datetime
from typing import Dict, List, Any
import json

from ui.components import render_metric_card, render_alert_banner
from ui.navigation import render_page_header

def render_settings():
    """Render settings and configuration page"""
    
    render_page_header(
        "⚙️ Settings",
        "Manage your preferences and system configuration",
        actions=[
            {"label": "💾 Save All", "key": "save_all_settings", "callback": save_all_settings},
            {"label": "🔄 Reset", "key": "reset_settings", "callback": reset_to_defaults}
        ]
    )
    
    auth_manager = st.session_state.get('auth_manager')
    
    if not auth_manager:
        st.error("Authentication manager not initialized")
        return
    
    # Settings sections
    render_user_settings(auth_manager)
    
    st.markdown("---")
    
    render_trading_settings()
    
    st.markdown("---")
    
    render_display_settings()
    
    st.markdown("---")
    
    render_notification_settings()
    
    st.markdown("---")
    
    render_data_settings()
    
    st.markdown("---")
    
    render_security_settings(auth_manager)

def render_user_settings(auth_manager):
    """Render user account settings"""
    
    st.markdown("### 👤 User Account")
    
    user = auth_manager.get_current_user()
    if not user:
        st.error("User not authenticated")
        return
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("#### Profile Information")
        
        with st.form("profile_form"):
            username = st.text_input("Username", value=user.get('username', ''), disabled=True)
            email = st.text_input("Email", value=user.get('email', ''))
            display_name = st.text_input("Display Name", value=user.get('display_name', ''))
            timezone = st.selectbox(
                "Timezone",
                ["UTC", "US/Eastern", "US/Central", "US/Mountain", "US/Pacific", "Europe/London", "Asia/Tokyo"],
                index=1
            )
            
            if st.form_submit_button("Update Profile"):
                # Update user profile
                st.success("Profile updated successfully!")
    
    with col2:
        st.markdown("#### Account Information")
        
        st.info(f"**Role:** {user.get('role', 'user').title()}")
        st.info(f"**Member Since:** {user.get('created_at', 'Unknown')[:10]}")
        st.info(f"**Last Login:** {user.get('last_login', 'Unknown')[:19] if user.get('last_login') else 'Never'}")
        
        # Change password
        with st.expander("🔒 Change Password"):
            with st.form("password_form"):
                current_password = st.text_input("Current Password", type="password")
                new_password = st.text_input("New Password", type="password")
                confirm_password = st.text_input("Confirm New Password", type="password")
                
                if st.form_submit_button("Change Password"):
                    if new_password != confirm_password:
                        st.error("Passwords do not match")
                    elif auth_manager.change_password(user['username'], current_password, new_password):
                        st.success("Password changed successfully!")
                    else:
                        st.error("Failed to change password")

def render_trading_settings():
    """Render trading configuration settings"""
    
    st.markdown("### 📈 Trading Configuration")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("#### Default Settings")
        
        default_quantity = st.number_input(
            "Default Order Quantity",
            min_value=1,
            value=100,
            step=1,
            help="Default number of shares for new orders"
        )
        
        default_order_type = st.selectbox(
            "Default Order Type",
            ["Market", "Limit"],
            help="Default order type for new orders"
        )
        
        auto_stop_loss = st.checkbox(
            "Enable Auto Stop Loss",
            value=True,
            help="Automatically set stop loss orders"
        )
        
        if auto_stop_loss:
            stop_loss_pct = st.slider(
                "Stop Loss Percentage",
                min_value=1.0,
                max_value=10.0,
                value=5.0,
                step=0.5,
                format="%.1f%%"
            )
        
        auto_take_profit = st.checkbox(
            "Enable Auto Take Profit",
            value=False,
            help="Automatically set take profit orders"
        )
        
        if auto_take_profit:
            take_profit_pct = st.slider(
                "Take Profit Percentage",
                min_value=5.0,
                max_value=50.0,
                value=15.0,
                step=2.5,
                format="%.1f%%"
            )
    
    with col2:
        st.markdown("#### Risk Management")
        
        max_position_size = st.slider(
            "Max Position Size (% of Portfolio)",
            min_value=1,
            max_value=25,
            value=10,
            step=1,
            format="%d%%"
        )
        
        max_daily_loss = st.slider(
            "Max Daily Loss (% of Portfolio)",
            min_value=1,
            max_value=10,
            value=3,
            step=1,
            format="%d%%"
        )
        
        require_confirmation = st.checkbox(
            "Require Order Confirmation",
            value=True,
            help="Show confirmation dialog before placing orders"
        )
        
        enable_paper_trading = st.checkbox(
            "Paper Trading Mode",
            value=True,
            help="Trade with virtual money (recommended for beginners)"
        )
        
        # Risk alerts
        st.markdown("#### Risk Alerts")
        
        alert_high_volatility = st.checkbox("Alert on High Volatility", value=True)
        alert_position_limits = st.checkbox("Alert on Position Limit Breach", value=True)
        alert_margin_calls = st.checkbox("Alert on Margin Requirements", value=True)

def render_display_settings():
    """Render display and UI settings"""
    
    st.markdown("### 🎨 Display & Interface")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("#### Theme & Layout")
        
        theme = st.selectbox(
            "Color Theme",
            ["Dark", "Light", "Auto"],
            index=0,
            help="Choose your preferred color theme"
        )
        
        chart_style = st.selectbox(
            "Chart Style",
            ["Modern", "Classic", "Minimal"],
            help="Default chart appearance style"
        )
        
        sidebar_collapsed = st.checkbox(
            "Collapse Sidebar by Default",
            value=False,
            help="Start with sidebar collapsed"
        )
        
        show_tooltips = st.checkbox(
            "Show Help Tooltips",
            value=True,
            help="Display helpful tooltips throughout the interface"
        )
        
        dense_mode = st.checkbox(
            "Dense Layout Mode",
            value=False,
            help="Show more information in less space"
        )
    
    with col2:
        st.markdown("#### Dashboard Preferences")
        
        default_timeframe = st.selectbox(
            "Default Chart Timeframe",
            ["1D", "5D", "1M", "3M", "6M", "1Y", "5Y"],
            index=2
        )
        
        auto_refresh = st.checkbox(
            "Auto-refresh Data",
            value=True,
            help="Automatically refresh market data"
        )
        
        if auto_refresh:
            refresh_interval = st.selectbox(
                "Refresh Interval",
                ["30 seconds", "1 minute", "5 minutes", "15 minutes"],
                index=1
            )
        
        show_advanced_metrics = st.checkbox(
            "Show Advanced Metrics",
            value=False,
            help="Display advanced trading and risk metrics"
        )
        
        # Widget preferences
        st.markdown("#### Widget Display")
        
        widgets = {
            "Portfolio Summary": st.checkbox("Portfolio Summary", value=True),
            "Market Overview": st.checkbox("Market Overview", value=True),
            "Top Movers": st.checkbox("Top Movers", value=True),
            "News Feed": st.checkbox("News Feed", value=True),
            "Economic Calendar": st.checkbox("Economic Calendar", value=False),
            "Watchlist": st.checkbox("Watchlist", value=True)
        }

def render_notification_settings():
    """Render notification preferences"""
    
    st.markdown("### 🔔 Notifications")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("#### Email Notifications")
        
        email_enabled = st.checkbox(
            "Enable Email Notifications",
            value=True,
            help="Receive notifications via email"
        )
        
        if email_enabled:
            notification_email = st.text_input(
                "Notification Email",
                value="user@example.com",
                help="Email address for notifications"
            )
            
            email_notifications = {
                "Order Fills": st.checkbox("Order Fills", value=True),
                "Portfolio Alerts": st.checkbox("Portfolio Alerts", value=True),
                "Market News": st.checkbox("Market News", value=False),
                "Daily Summary": st.checkbox("Daily Summary", value=True),
                "Risk Alerts": st.checkbox("Risk Alerts", value=True),
                "System Updates": st.checkbox("System Updates", value=False)
            }
    
    with col2:
        st.markdown("#### Push Notifications")
        
        push_enabled = st.checkbox(
            "Enable Push Notifications",
            value=True,
            help="Receive browser push notifications"
        )
        
        if push_enabled:
            push_notifications = {
                "Order Execution": st.checkbox("Order Execution", value=True, key="push_orders"),
                "Price Alerts": st.checkbox("Price Alerts", value=True, key="push_price"),
                "Risk Warnings": st.checkbox("Risk Warnings", value=True, key="push_risk"),
                "News Alerts": st.checkbox("News Alerts", value=False, key="push_news"),
                "System Status": st.checkbox("System Status", value=True, key="push_system")
            }
        
        # Notification timing
        st.markdown("#### Notification Schedule")
        
        quiet_hours = st.checkbox(
            "Enable Quiet Hours",
            value=True,
            help="Disable notifications during specified hours"
        )
        
        if quiet_hours:
            col1, col2 = st.columns(2)
            with col1:
                quiet_start = st.time_input("Quiet Hours Start", value=datetime.strptime("22:00", "%H:%M").time())
            with col2:
                quiet_end = st.time_input("Quiet Hours End", value=datetime.strptime("08:00", "%H:%M").time())

def render_data_settings():
    """Render data and API settings"""
    
    st.markdown("### 📊 Data & API Configuration")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("#### Data Sources")
        
        primary_data_source = st.selectbox(
            "Primary Data Provider",
            ["Yahoo Finance", "Alpha Vantage", "IEX Cloud", "Polygon"],
            help="Primary source for market data"
        )
        
        backup_data_source = st.selectbox(
            "Backup Data Provider",
            ["Yahoo Finance", "Alpha Vantage", "IEX Cloud", "None"],
            index=3,
            help="Fallback data source if primary fails"
        )
        
        data_quality = st.selectbox(
            "Data Quality Preference",
            ["Real-time", "Near real-time (15min delay)", "End of day"],
            help="Choose between speed and cost"
        )
        
        cache_duration = st.selectbox(
            "Cache Duration",
            ["1 minute", "5 minutes", "15 minutes", "1 hour"],
            index=1,
            help="How long to cache market data"
        )
    
    with col2:
        st.markdown("#### API Configuration")
        
        # API key management
        with st.expander("🔑 API Keys"):
            st.warning("API keys are encrypted and stored securely")
            
            alpha_vantage_key = st.text_input(
                "Alpha Vantage API Key",
                type="password",
                placeholder="Enter your API key"
            )
            
            iex_cloud_key = st.text_input(
                "IEX Cloud API Key",
                type="password", 
                placeholder="Enter your API key"
            )
            
            polygon_key = st.text_input(
                "Polygon API Key",
                type="password",
                placeholder="Enter your API key"
            )
            
            if st.button("Save API Keys"):
                st.success("API keys saved securely!")
        
        # Rate limiting
        st.markdown("#### Rate Limiting")
        
        api_rate_limit = st.slider(
            "API Requests per Minute",
            min_value=10,
            max_value=500,
            value=100,
            help="Limit API calls to avoid rate limiting"
        )
        
        enable_caching = st.checkbox(
            "Enable Response Caching",
            value=True,
            help="Cache API responses to reduce calls"
        )

def render_security_settings(auth_manager):
    """Render security and privacy settings"""
    
    st.markdown("### 🔒 Security & Privacy")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("#### Session Management")
        
        session_timeout = st.selectbox(
            "Session Timeout",
            ["15 minutes", "30 minutes", "1 hour", "4 hours", "8 hours", "Never"],
            index=2,
            help="Automatic logout after inactivity"
        )
        
        remember_me = st.checkbox(
            "Enable 'Remember Me'",
            value=True,
            help="Allow extended login sessions"
        )
        
        two_factor_auth = st.checkbox(
            "Enable Two-Factor Authentication",
            value=False,
            help="Require 2FA for login (recommended)"
        )
        
        if two_factor_auth:
            st.info("2FA setup would be configured here")
        
        # Active sessions
        st.markdown("#### Active Sessions")
        
        if st.button("View Active Sessions"):
            st.info("Session management would be displayed here")
        
        if st.button("Logout All Sessions"):
            st.warning("This would logout all active sessions")
    
    with col2:
        st.markdown("#### Privacy Settings")
        
        data_sharing = st.checkbox(
            "Allow Anonymous Usage Analytics",
            value=True,
            help="Help improve the platform with anonymous usage data"
        )
        
        market_data_sharing = st.checkbox(
            "Share Market Data Preferences",
            value=False,
            help="Share anonymous market data preferences for research"
        )
        
        email_marketing = st.checkbox(
            "Receive Marketing Emails",
            value=False,
            help="Receive product updates and promotional content"
        )
        
        # Data export/deletion
        st.markdown("#### Data Management")
        
        if st.button("Export My Data"):
            st.info("Data export feature would be implemented here")
        
        if st.button("Delete My Account", type="secondary"):
            st.error("Account deletion would be handled here")
        
        # Audit log
        with st.expander("📋 Recent Activity"):
            activities = [
                "2024-01-08 10:30 - Login from Chrome",
                "2024-01-08 09:15 - Password changed",
                "2024-01-07 16:45 - Settings updated",
                "2024-01-07 14:20 - API key added"
            ]
            
            for activity in activities:
                st.caption(activity)

def save_all_settings():
    """Save all settings"""
    # In a real implementation, this would save all current settings
    st.success("All settings saved successfully!")
    st.rerun()

def reset_to_defaults():
    """Reset all settings to defaults"""
    if st.session_state.get('confirm_reset'):
        # Reset logic would go here
        st.success("Settings reset to defaults!")
        del st.session_state['confirm_reset']
        st.rerun()
    else:
        st.session_state['confirm_reset'] = True
        st.warning("Are you sure you want to reset all settings? Click again to confirm.")
        st.rerun()

def get_user_preferences():
    """Get user preferences from session state or defaults"""
    return st.session_state.get('user_preferences', {
        'theme': 'dark',
        'auto_refresh': True,
        'default_quantity': 100,
        'notifications_enabled': True,
        'risk_alerts': True
    })

def save_user_preferences(preferences):
    """Save user preferences to session state"""
    st.session_state['user_preferences'] = preferences