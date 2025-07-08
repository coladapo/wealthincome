"""
Page modules for the WealthIncome unified platform
"""

# Import all page renderers
try:
    from .dashboard import render_dashboard
    from .ai_signals import render_ai_signals
    from .trading import render_trading
    from .portfolio import render_portfolio
    from .analytics import render_analytics
    from .risk import render_risk_management
    from .news import render_news
    from .journal import render_journal
    from .settings import render_settings
except ImportError as e:
    # Handle missing modules gracefully
    import logging
    logging.warning(f"Some page modules could not be imported: {e}")

__all__ = [
    'render_dashboard',
    'render_ai_signals',
    'render_trading',
    'render_portfolio', 
    'render_analytics',
    'render_risk_management',
    'render_news',
    'render_journal',
    'render_settings'
]