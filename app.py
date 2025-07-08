#!/usr/bin/env python3
"""
WealthIncome Unified Platform - Main Entry Point
Combines AI-driven frontend with trading platform capabilities
"""

import streamlit as st
import sys
import os
from datetime import datetime
import logging

# Page config MUST be first
st.set_page_config(
    page_title="WealthIncome AI Trading Platform", 
    page_icon="🚀", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# Setup paths and imports
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Import core modules
try:
    from config import AppConfig, get_config
    from core.data_manager import UnifiedDataManager
    from core.auth import AuthenticationManager
    from core.trading_engine import TradingEngine
    from ui.navigation import render_navigation
    from ui.components import render_header, render_footer
except ImportError as e:
    st.error(f"Failed to import required modules: {e}")
    st.error("Please ensure all dependencies are installed and the application is properly configured.")
    st.stop()

# Initialize configuration
config = get_config()

# Setup logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize core managers
@st.cache_resource
def initialize_managers():
    """Initialize core application managers"""
    try:
        auth_manager = AuthenticationManager(config)
        data_manager = UnifiedDataManager(config)
        trading_engine = TradingEngine(initial_cash=100000.0)
        
        # Connect trading engine with data manager
        trading_engine.set_data_manager(data_manager)
        trading_engine.set_config(config)
        
        return auth_manager, data_manager, trading_engine
    except Exception as e:
        logger.error(f"Failed to initialize managers: {e}")
        st.error(f"Application initialization failed: {e}")
        st.stop()

# Initialize application
auth_manager, data_manager, trading_engine = initialize_managers()

# Store managers in session state
st.session_state['auth_manager'] = auth_manager
st.session_state['data_manager'] = data_manager
st.session_state['trading_engine'] = trading_engine
st.session_state['config'] = config

def main():
    """Main application entry point"""
    
    # Render header
    render_header()
    
    # Check authentication
    if not auth_manager.is_authenticated():
        # Show login page
        auth_manager.render_login()
        return
    
    # Render main navigation
    selected_page = render_navigation()
    
    # Route to selected page
    try:
        if selected_page == "Dashboard":
            from page_modules.dashboard import render_dashboard
            render_dashboard()
        elif selected_page == "AI Signals":
            from page_modules.ai_signals import render_ai_signals
            render_ai_signals()
        elif selected_page == "Trading":
            from page_modules.trading import render_trading
            render_trading()
        elif selected_page == "Portfolio":
            from page_modules.portfolio import render_portfolio
            render_portfolio()
        elif selected_page == "Analytics":
            from page_modules.analytics import render_analytics
            render_analytics()
        elif selected_page == "Risk Management":
            from page_modules.risk import render_risk_management
            render_risk_management()
        elif selected_page == "News & Sentiment":
            from page_modules.news import render_news
            render_news()
        elif selected_page == "Journal":
            from page_modules.journal import render_journal
            render_journal()
        elif selected_page == "Settings":
            from page_modules.settings import render_settings
            render_settings()
        else:
            st.error(f"Unknown page: {selected_page}")
            
    except Exception as e:
        logger.error(f"Error rendering page {selected_page}: {e}")
        st.error(f"Error loading page: {e}")
    
    # Render footer
    render_footer()

if __name__ == "__main__":
    main()