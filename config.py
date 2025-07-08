"""
Unified Application Configuration
Centralizes all configuration settings for the WealthIncome platform
"""

import os
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from pathlib import Path
import streamlit as st

@dataclass
class AppConfig:
    """Application configuration settings"""
    
    # Application Info
    APP_NAME: str = "WealthIncome AI Trading Platform"
    APP_VERSION: str = "1.0.0"
    APP_DESCRIPTION: str = "Unified AI-driven trading platform with real-time analytics"
    
    # Environment
    ENVIRONMENT: str = "development"  # development, staging, production
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    
    # Database
    DATABASE_URL: Optional[str] = None
    REDIS_URL: Optional[str] = None
    
    # API Keys
    OPENAI_API_KEY: Optional[str] = None
    ALPHA_VANTAGE_API_KEY: Optional[str] = None
    FINNHUB_API_KEY: Optional[str] = None
    NEWS_API_KEY: Optional[str] = None
    
    # Data Sources
    DEFAULT_DATA_PROVIDER: str = "yfinance"  # yfinance, alpha_vantage, finnhub
    MARKET_DATA_CACHE_TTL: int = 300  # 5 minutes
    NEWS_CACHE_TTL: int = 900  # 15 minutes
    
    # Trading Parameters
    DEFAULT_PORTFOLIO_VALUE: float = 100000.0
    MAX_POSITION_SIZE: float = 0.1  # 10% max per position
    DEFAULT_STOP_LOSS: float = 0.05  # 5% stop loss
    DEFAULT_TAKE_PROFIT: float = 0.15  # 15% take profit
    
    # AI/ML Settings
    CONFIDENCE_THRESHOLD: float = 0.7
    MAX_SIGNALS_PER_DAY: int = 10
    AI_MODEL_UPDATE_INTERVAL: int = 3600  # 1 hour
    
    # UI Settings
    CHART_HEIGHT: int = 400
    CHART_WIDTH: int = 800
    SIDEBAR_WIDTH: int = 300
    THEME: str = "dark"
    
    # Security
    SESSION_TIMEOUT: int = 3600  # 1 hour
    MAX_LOGIN_ATTEMPTS: int = 3
    PASSWORD_MIN_LENGTH: int = 8
    
    # File Paths
    DATA_DIR: Path = Path("data")
    CACHE_DIR: Path = Path("data/cache")
    PERSISTENT_DIR: Path = Path("data/persistent")
    LOGS_DIR: Path = Path("logs")
    
    # WebSocket Settings (disabled for Streamlit Cloud)
    WS_ENABLED: bool = False
    WS_PORT: int = 8765
    WS_RECONNECT_ATTEMPTS: int = 5
    WS_HEARTBEAT_INTERVAL: int = 30
    
    # Performance
    MAX_WATCHLIST_SIZE: int = 50
    MAX_HISTORICAL_DAYS: int = 365
    BATCH_SIZE: int = 100
    
    # Features Flags
    ENABLE_PAPER_TRADING: bool = True
    ENABLE_LIVE_TRADING: bool = False
    ENABLE_AI_INSIGHTS: bool = True
    ENABLE_NEWS_SENTIMENT: bool = True
    ENABLE_SOCIAL_SENTIMENT: bool = False
    ENABLE_OPTIONS_TRADING: bool = False
    
    def __post_init__(self):
        """Post-initialization setup"""
        # Create directories
        for dir_path in [self.DATA_DIR, self.CACHE_DIR, self.PERSISTENT_DIR, self.LOGS_DIR]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # Load environment variables
        self._load_from_env()
        
        # Validate configuration
        self._validate_config()
    
    def _load_from_env(self):
        """Load configuration from environment variables"""
        env_mappings = {
            'ENVIRONMENT': str,
            'DEBUG': lambda x: x.lower() == 'true',
            'LOG_LEVEL': str,
            'DATABASE_URL': str,
            'REDIS_URL': str,
            'OPENAI_API_KEY': str,
            'ALPHA_VANTAGE_API_KEY': str,
            'FINNHUB_API_KEY': str,
            'NEWS_API_KEY': str,
            'DEFAULT_DATA_PROVIDER': str,
            'DEFAULT_PORTFOLIO_VALUE': float,
            'CONFIDENCE_THRESHOLD': float,
            'WS_ENABLED': lambda x: x.lower() == 'true',
            'ENABLE_LIVE_TRADING': lambda x: x.lower() == 'true',
        }
        
        for env_var, converter in env_mappings.items():
            value = os.getenv(env_var)
            if value is not None:
                try:
                    setattr(self, env_var, converter(value))
                except (ValueError, TypeError) as e:
                    logging.warning(f"Invalid value for {env_var}: {value} - {e}")
    
    def _validate_config(self):
        """Validate configuration settings"""
        if self.ENABLE_LIVE_TRADING and not self.OPENAI_API_KEY:
            logging.warning("Live trading enabled but OpenAI API key not configured")
        
        if self.DEFAULT_PORTFOLIO_VALUE <= 0:
            raise ValueError("DEFAULT_PORTFOLIO_VALUE must be positive")
        
        if not 0 < self.MAX_POSITION_SIZE <= 1:
            raise ValueError("MAX_POSITION_SIZE must be between 0 and 1")
        
        if not 0 < self.CONFIDENCE_THRESHOLD <= 1:
            raise ValueError("CONFIDENCE_THRESHOLD must be between 0 and 1")
    
    def get_api_key(self, provider: str) -> Optional[str]:
        """Get API key for specific provider"""
        key_mapping = {
            'openai': self.OPENAI_API_KEY,
            'alpha_vantage': self.ALPHA_VANTAGE_API_KEY,
            'finnhub': self.FINNHUB_API_KEY,
            'news': self.NEWS_API_KEY,
        }
        return key_mapping.get(provider.lower())
    
    def is_feature_enabled(self, feature: str) -> bool:
        """Check if a feature is enabled"""
        feature_mapping = {
            'paper_trading': self.ENABLE_PAPER_TRADING,
            'live_trading': self.ENABLE_LIVE_TRADING,
            'ai_insights': self.ENABLE_AI_INSIGHTS,
            'news_sentiment': self.ENABLE_NEWS_SENTIMENT,
            'social_sentiment': self.ENABLE_SOCIAL_SENTIMENT,
            'options_trading': self.ENABLE_OPTIONS_TRADING,
            'websocket': self.WS_ENABLED,
        }
        return feature_mapping.get(feature.lower(), False)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary"""
        return {
            field.name: getattr(self, field.name)
            for field in self.__dataclass_fields__.values()
        }

@st.cache_resource
def get_config() -> AppConfig:
    """Get application configuration (cached)"""
    return AppConfig()

def update_config(**kwargs) -> AppConfig:
    """Update configuration with new values"""
    config = get_config()
    for key, value in kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)
        else:
            logging.warning(f"Unknown configuration key: {key}")
    return config

# Environment-specific configurations
ENVIRONMENT_CONFIGS = {
    'development': {
        'DEBUG': True,
        'LOG_LEVEL': 'DEBUG',
        'ENVIRONMENT': 'development',
    },
    'staging': {
        'DEBUG': False,
        'LOG_LEVEL': 'INFO',
        'ENVIRONMENT': 'staging',
    },
    'production': {
        'DEBUG': False,
        'LOG_LEVEL': 'WARNING',
        'ENVIRONMENT': 'production',
        'ENABLE_LIVE_TRADING': True,
    }
}

def load_environment_config(environment: str = None) -> AppConfig:
    """Load configuration for specific environment"""
    if not environment:
        environment = os.getenv('ENVIRONMENT', 'development')
    
    base_config = AppConfig()
    env_overrides = ENVIRONMENT_CONFIGS.get(environment, {})
    
    for key, value in env_overrides.items():
        if hasattr(base_config, key):
            setattr(base_config, key, value)
    
    return base_config