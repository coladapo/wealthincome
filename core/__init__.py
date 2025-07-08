"""
Core modules for the WealthIncome unified platform
"""

from .data_manager import UnifiedDataManager
from .auth import AuthenticationManager
from .trading_engine import TradingEngine
from .ai_engine import AIEngine

__all__ = [
    'UnifiedDataManager',
    'AuthenticationManager', 
    'TradingEngine',
    'AIEngine'
]