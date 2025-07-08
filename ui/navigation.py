"""
Unified Navigation System
Provides consistent navigation across the platform
"""

import streamlit as st
from typing import List, Dict, Any

def render_navigation() -> str:
    """Render main navigation and return selected page"""
    
    # Navigation configuration
    nav_items = [
        {"name": "Dashboard", "icon": "🏠", "description": "Overview and AI insights"},
        {"name": "AI Signals", "icon": "🧠", "description": "AI-powered trading signals"},
        {"name": "Trading", "icon": "📈", "description": "Execute trades and manage positions"},
        {"name": "Portfolio", "icon": "💼", "description": "Portfolio analysis and performance"},
        {"name": "Analytics", "icon": "📊", "description": "Advanced market analytics"},
        {"name": "Risk Management", "icon": "🛡️", "description": "Risk monitoring and controls"},
        {"name": "News & Sentiment", "icon": "📰", "description": "Market news and sentiment analysis"},
        {"name": "Journal", "icon": "📓", "description": "Trading journal and notes"},
        {"name": "Settings", "icon": "⚙️", "description": "Platform settings and preferences"},
    ]
    
    # Sidebar navigation
    with st.sidebar:
        st.markdown("### 🚀 WealthIncome AI")
        st.markdown("*Unified Trading Platform*")
        st.markdown("---")
        
        # Navigation menu
        selected_page = None
        for item in nav_items:
            if st.button(
                f"{item['icon']} {item['name']}", 
                key=f"nav_{item['name']}", 
                use_container_width=True,
                help=item['description']
            ):
                selected_page = item['name']
                st.session_state['current_page'] = selected_page
        
        # Return current page from session state or default
        if selected_page:
            return selected_page
        elif 'current_page' in st.session_state:
            return st.session_state['current_page']
        else:
            st.session_state['current_page'] = "Dashboard"
            return "Dashboard"

def render_top_navigation():
    """Render top navigation bar"""
    
    col1, col2, col3 = st.columns([2, 6, 2])
    
    with col1:
        st.markdown("### 🚀 WealthIncome AI")
    
    with col2:
        # Quick search
        search_query = st.text_input(
            "Search",
            placeholder="Search stocks, news, strategies...",
            label_visibility="collapsed"
        )
        
        if search_query:
            st.session_state['search_query'] = search_query
    
    with col3:
        # Quick actions
        if st.button("🔄 Refresh", help="Refresh data"):
            st.cache_data.clear()
            st.rerun()

def render_breadcrumb(pages: List[str]):
    """Render breadcrumb navigation"""
    breadcrumb = " > ".join(pages)
    st.caption(f"📍 {breadcrumb}")

def render_page_header(title: str, subtitle: str = None, actions: List[Dict[str, Any]] = None):
    """Render standardized page header"""
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        st.title(title)
        if subtitle:
            st.caption(subtitle)
    
    with col2:
        if actions:
            for action in actions:
                if st.button(
                    action.get('label', 'Action'),
                    key=action.get('key', f"action_{title}"),
                    type=action.get('type', 'secondary'),
                    use_container_width=True
                ):
                    if action.get('callback'):
                        action['callback']()

def render_status_bar():
    """Render system status bar"""
    
    # Get system status
    data_manager = st.session_state.get('data_manager')
    config = st.session_state.get('config')
    
    if not data_manager or not config:
        return
    
    status = data_manager.get_health_status()
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        # Market status
        st.metric("Market", "Open" if _is_market_open() else "Closed")
    
    with col2:
        # Data connection
        data_status = "🟢 Connected" if status['data_manager'] == 'healthy' else "🔴 Disconnected"
        st.caption(f"Data: {data_status}")
    
    with col3:
        # Redis status
        redis_status = status.get('redis_connection', 'unknown')
        redis_indicator = "🟢" if redis_status == 'healthy' else "🟡" if redis_status == 'disabled' else "🔴"
        st.caption(f"Cache: {redis_indicator} {redis_status.title()}")
    
    with col4:
        # Last update
        st.caption(f"Updated: {status['last_update'][:19]}")

def _is_market_open() -> bool:
    """Check if market is currently open"""
    from datetime import datetime, time
    import pytz
    
    # US market hours (9:30 AM - 4:00 PM EST)
    now = datetime.now(pytz.timezone('US/Eastern'))
    market_open = time(9, 30)
    market_close = time(16, 0)
    
    # Check if it's a weekday and within market hours
    is_weekday = now.weekday() < 5
    is_market_hours = market_open <= now.time() <= market_close
    
    return is_weekday and is_market_hours

def render_quick_actions():
    """Render quick action buttons"""
    
    st.markdown("### ⚡ Quick Actions")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        if st.button("🎯 AI Scan", use_container_width=True, help="Run AI market scan"):
            st.session_state['current_page'] = "AI Signals"
            st.rerun()
    
    with col2:
        if st.button("📈 Quick Trade", use_container_width=True, help="Open trading interface"):
            st.session_state['current_page'] = "Trading"
            st.rerun()
    
    with col3:
        if st.button("📊 Portfolio", use_container_width=True, help="View portfolio"):
            st.session_state['current_page'] = "Portfolio"
            st.rerun()
    
    with col4:
        if st.button("📰 News", use_container_width=True, help="Latest market news"):
            st.session_state['current_page'] = "News & Sentiment"
            st.rerun()

def render_feature_toggle(feature_name: str, description: str, enabled: bool = True) -> bool:
    """Render feature toggle switch"""
    
    return st.toggle(
        description,
        value=enabled,
        key=f"feature_{feature_name}",
        help=f"Enable/disable {feature_name}"
    )

def render_navigation_footer():
    """Render navigation footer with additional links"""
    
    st.sidebar.markdown("---")
    
    # System status indicator
    col1, col2 = st.sidebar.columns(2)
    with col1:
        st.caption("🟢 System Online")
    with col2:
        st.caption("📡 Real-time Data")
    
    # Additional links
    st.sidebar.markdown("#### Quick Links")
    st.sidebar.markdown("- [📖 Documentation](https://docs.wealthincome.ai)")
    st.sidebar.markdown("- [💬 Support](https://support.wealthincome.ai)")
    st.sidebar.markdown("- [🐛 Report Issue](https://github.com/wealthincome/issues)")
    
    # Version info
    config = st.session_state.get('config')
    if config:
        st.sidebar.caption(f"v{config.APP_VERSION} | {config.ENVIRONMENT}")

def get_current_page() -> str:
    """Get the currently selected page"""
    return st.session_state.get('current_page', 'Dashboard')

def set_current_page(page: str):
    """Set the current page"""
    st.session_state['current_page'] = page