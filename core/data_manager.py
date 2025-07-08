"""
Unified Data Manager
Consolidates data management from both AI frontend and trading platform
"""

import os
import json
import logging
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union
import streamlit as st
from pathlib import Path
import threading
import time
import requests
from dataclasses import dataclass
import redis

logger = logging.getLogger(__name__)

@dataclass
class MarketData:
    """Market data structure"""
    symbol: str
    price: float
    change: float
    change_percent: float
    volume: int
    timestamp: datetime
    
@dataclass 
class NewsItem:
    """News item structure"""
    title: str
    content: str
    source: str
    url: str
    sentiment: float
    timestamp: datetime
    symbols: List[str]

class UnifiedDataManager:
    """Unified data management for the platform"""
    
    def __init__(self, config):
        self.config = config
        self.cache_dir = config.CACHE_DIR
        self.persistent_dir = config.PERSISTENT_DIR
        
        # Redis connection for real-time data
        self.redis_client = self._init_redis()
        
        # In-memory caches
        self._market_data_cache = {}
        self._news_cache = {}
        self._watchlist_cache = None
        self._portfolio_cache = None
        
        # Thread locks
        self._cache_lock = threading.Lock()
        
        # Start background data update thread
        if config.WS_ENABLED:
            self._start_background_updates()
    
    def _init_redis(self) -> Optional[redis.Redis]:
        """Initialize Redis connection"""
        try:
            if self.config.REDIS_URL:
                return redis.from_url(self.config.REDIS_URL)
            return None
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}")
            return None
    
    def _start_background_updates(self):
        """Start background thread for real-time data updates"""
        def update_loop():
            while True:
                try:
                    self._update_market_data()
                    time.sleep(30)  # Update every 30 seconds
                except Exception as e:
                    logger.error(f"Background update error: {e}")
                    time.sleep(60)  # Wait longer on error
        
        thread = threading.Thread(target=update_loop, daemon=True)
        thread.start()
        logger.info("Background data update thread started")
    
    def get_stock_data(self, symbols: List[str], period: str = "1d") -> Dict[str, Dict]:
        """Get stock data for multiple symbols"""
        if not symbols:
            return {}
        
        # Check cache first
        cache_key = f"stock_data_{'-'.join(symbols)}_{period}"
        if cache_key in self._market_data_cache:
            cache_time, data = self._market_data_cache[cache_key]
            if (datetime.now() - cache_time).seconds < self.config.MARKET_DATA_CACHE_TTL:
                return data
        
        try:
            # Fetch from yfinance
            tickers = yf.Tickers(' '.join(symbols))
            result = {}
            
            for symbol in symbols:
                try:
                    ticker = tickers.tickers[symbol]
                    info = ticker.info
                    hist = ticker.history(period=period)
                    
                    result[symbol] = {
                        'info': info,
                        'history': hist,
                        'price': info.get('regularMarketPrice', 0),
                        'change': info.get('regularMarketChange', 0),
                        'change_percent': info.get('regularMarketChangePercent', 0),
                        'volume': info.get('regularMarketVolume', 0),
                        'timestamp': datetime.now()
                    }
                except Exception as e:
                    logger.error(f"Error fetching data for {symbol}: {e}")
                    result[symbol] = None
            
            # Cache the result
            with self._cache_lock:
                self._market_data_cache[cache_key] = (datetime.now(), result)
            
            return result
            
        except Exception as e:
            logger.error(f"Error fetching stock data: {e}")
            return {}
    
    def get_real_time_price(self, symbol: str) -> Optional[MarketData]:
        """Get real-time price for a symbol"""
        try:
            # Try Redis first for real-time data
            if self.redis_client:
                cached_data = self.redis_client.get(f"price:{symbol}")
                if cached_data:
                    data = json.loads(cached_data)
                    return MarketData(**data)
            
            # Fallback to yfinance
            ticker = yf.Ticker(symbol)
            info = ticker.info
            
            return MarketData(
                symbol=symbol,
                price=info.get('regularMarketPrice', 0),
                change=info.get('regularMarketChange', 0),
                change_percent=info.get('regularMarketChangePercent', 0),
                volume=info.get('regularMarketVolume', 0),
                timestamp=datetime.now()
            )
            
        except Exception as e:
            logger.error(f"Error getting real-time price for {symbol}: {e}")
            return None
    
    def _update_market_data(self):
        """Update market data in background"""
        try:
            # Get current watchlist
            watchlist = self.get_watchlist()
            if not watchlist:
                return
            
            # Fetch latest data
            data = self.get_stock_data(watchlist, period="1d")
            
            # Update Redis cache if available
            if self.redis_client:
                for symbol, stock_data in data.items():
                    if stock_data:
                        market_data = MarketData(
                            symbol=symbol,
                            price=stock_data['price'],
                            change=stock_data['change'],
                            change_percent=stock_data['change_percent'],
                            volume=stock_data['volume'],
                            timestamp=stock_data['timestamp']
                        )
                        
                        self.redis_client.setex(
                            f"price:{symbol}",
                            self.config.MARKET_DATA_CACHE_TTL,
                            json.dumps(market_data.__dict__, default=str)
                        )
            
        except Exception as e:
            logger.error(f"Error updating market data: {e}")
    
    def get_watchlist(self) -> List[str]:
        """Get user's watchlist"""
        if self._watchlist_cache is not None:
            return self._watchlist_cache
        
        try:
            watchlist_file = self.cache_dir / "watchlist_storage.json"
            if watchlist_file.exists():
                with open(watchlist_file, 'r') as f:
                    data = json.load(f)
                    self._watchlist_cache = data.get('symbols', [])
            else:
                # Default watchlist
                self._watchlist_cache = ['AAPL', 'MSFT', 'GOOGL', 'NVDA', 'TSLA', 'AMD', 'META', 'AMZN']
                self._save_watchlist()
            
            return self._watchlist_cache
            
        except Exception as e:
            logger.error(f"Error loading watchlist: {e}")
            return ['AAPL', 'MSFT', 'GOOGL', 'NVDA']  # Fallback
    
    def add_to_watchlist(self, symbol: str) -> bool:
        """Add symbol to watchlist"""
        try:
            watchlist = self.get_watchlist()
            if symbol not in watchlist and len(watchlist) < self.config.MAX_WATCHLIST_SIZE:
                watchlist.append(symbol.upper())
                self._watchlist_cache = watchlist
                self._save_watchlist()
                return True
            return False
        except Exception as e:
            logger.error(f"Error adding to watchlist: {e}")
            return False
    
    def remove_from_watchlist(self, symbol: str) -> bool:
        """Remove symbol from watchlist"""
        try:
            watchlist = self.get_watchlist()
            if symbol in watchlist:
                watchlist.remove(symbol)
                self._watchlist_cache = watchlist
                self._save_watchlist()
                return True
            return False
        except Exception as e:
            logger.error(f"Error removing from watchlist: {e}")
            return False
    
    def _save_watchlist(self):
        """Save watchlist to file"""
        try:
            watchlist_file = self.cache_dir / "watchlist_storage.json"
            data = {
                'symbols': self._watchlist_cache or [],
                'updated_at': datetime.now().isoformat()
            }
            with open(watchlist_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving watchlist: {e}")
    
    def get_news(self, symbols: List[str] = None, limit: int = 20) -> List[NewsItem]:
        """Get news for symbols"""
        try:
            # Check cache first
            cache_key = f"news_{'-'.join(symbols or ['general'])}_{limit}"
            if cache_key in self._news_cache:
                cache_time, news = self._news_cache[cache_key]
                if (datetime.now() - cache_time).seconds < self.config.NEWS_CACHE_TTL:
                    return news
            
            news_items = []
            
            # Try to fetch from yfinance news first
            if symbols:
                for symbol in symbols:
                    try:
                        ticker = yf.Ticker(symbol)
                        news = ticker.news
                        
                        for item in news[:limit//len(symbols)]:
                            news_items.append(NewsItem(
                                title=item.get('title', ''),
                                content=item.get('summary', ''),
                                source=item.get('publisher', ''),
                                url=item.get('link', ''),
                                sentiment=0.5,  # Neutral default
                                timestamp=datetime.fromtimestamp(item.get('providerPublishTime', time.time())),
                                symbols=[symbol]
                            ))
                    except Exception as e:
                        logger.error(f"Error fetching news for {symbol}: {e}")
            
            # Cache the result
            with self._cache_lock:
                self._news_cache[cache_key] = (datetime.now(), news_items)
            
            return news_items
            
        except Exception as e:
            logger.error(f"Error fetching news: {e}")
            return []
    
    def get_latest_news_sentiment(self, symbol: str) -> Optional[Dict]:
        """Get latest news sentiment for a symbol"""
        try:
            news_items = self.get_news([symbol], limit=5)
            if not news_items:
                return None
            
            # Simple sentiment aggregation
            sentiments = [item.sentiment for item in news_items]
            avg_sentiment = sum(sentiments) / len(sentiments)
            
            if avg_sentiment > 0.6:
                label = "Positive"
            elif avg_sentiment < 0.4:
                label = "Negative"
            else:
                label = "Neutral"
            
            return {
                'label': label,
                'score': avg_sentiment,
                'count': len(news_items)
            }
            
        except Exception as e:
            logger.error(f"Error getting news sentiment for {symbol}: {e}")
            return None
    
    def get_portfolio_data(self) -> Dict[str, Any]:
        """Get portfolio performance data"""
        try:
            portfolio_file = self.persistent_dir / "portfolio_data.json"
            if portfolio_file.exists():
                with open(portfolio_file, 'r') as f:
                    return json.load(f)
            else:
                # Default portfolio structure
                return {
                    'total_value': self.config.DEFAULT_PORTFOLIO_VALUE,
                    'cash': self.config.DEFAULT_PORTFOLIO_VALUE,
                    'positions': {},
                    'performance': {
                        'total_return': 0.0,
                        'total_return_percent': 0.0,
                        'daily_return': 0.0,
                        'daily_return_percent': 0.0
                    },
                    'updated_at': datetime.now().isoformat()
                }
        except Exception as e:
            logger.error(f"Error loading portfolio data: {e}")
            return {}
    
    def analyze_portfolio_performance(self) -> Dict[str, Any]:
        """Analyze portfolio performance"""
        try:
            trades_file = self.persistent_dir / "paper_trades_pro.csv"
            if not trades_file.exists():
                return {}
            
            # Load trades data
            df = pd.read_csv(trades_file)
            if df.empty:
                return {}
            
            # Calculate performance metrics
            total_trades = len(df)
            winning_trades = len(df[df['PnL'] > 0]) if 'PnL' in df.columns else 0
            win_rate = winning_trades / total_trades if total_trades > 0 else 0
            total_pnl = df['PnL'].sum() if 'PnL' in df.columns else 0
            
            return {
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'losing_trades': total_trades - winning_trades,
                'win_rate': win_rate,
                'total_pnl': total_pnl,
                'avg_pnl_per_trade': total_pnl / total_trades if total_trades > 0 else 0,
                'updated_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error analyzing portfolio performance: {e}")
            return {}
    
    def save_trade(self, trade_data: Dict[str, Any]) -> bool:
        """Save a trade to the journal"""
        try:
            trades_file = self.persistent_dir / "paper_trades_pro.csv"
            
            # Create DataFrame from trade data
            trade_df = pd.DataFrame([trade_data])
            
            # Append to existing file or create new one
            if trades_file.exists():
                existing_df = pd.read_csv(trades_file)
                combined_df = pd.concat([existing_df, trade_df], ignore_index=True)
            else:
                combined_df = trade_df
            
            # Save to file
            combined_df.to_csv(trades_file, index=False)
            
            # Clear portfolio cache
            self._portfolio_cache = None
            
            return True
            
        except Exception as e:
            logger.error(f"Error saving trade: {e}")
            return False
    
    def get_market_indices(self) -> Dict[str, MarketData]:
        """Get major market indices data"""
        indices = ['SPY', 'QQQ', 'DIA', 'IWM']
        data = self.get_stock_data(indices, period="1d")
        
        result = {}
        for symbol in indices:
            if symbol in data and data[symbol]:
                stock_data = data[symbol]
                result[symbol] = MarketData(
                    symbol=symbol,
                    price=stock_data['price'],
                    change=stock_data['change'],
                    change_percent=stock_data['change_percent'],
                    volume=stock_data['volume'],
                    timestamp=stock_data['timestamp']
                )
        
        return result
    
    def cleanup_cache(self):
        """Clean up old cache files"""
        try:
            # Clear in-memory caches
            with self._cache_lock:
                self._market_data_cache.clear()
                self._news_cache.clear()
            
            # Reset cached data
            self._watchlist_cache = None
            self._portfolio_cache = None
            
            logger.info("Cache cleaned up successfully")
            
        except Exception as e:
            logger.error(f"Error cleaning up cache: {e}")
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get system health status"""
        status = {
            'data_manager': 'healthy',
            'redis_connection': 'unknown',
            'cache_size': len(self._market_data_cache),
            'last_update': datetime.now().isoformat()
        }
        
        # Check Redis connection
        if self.redis_client:
            try:
                self.redis_client.ping()
                status['redis_connection'] = 'healthy'
            except:
                status['redis_connection'] = 'unhealthy'
        else:
            status['redis_connection'] = 'disabled'
        
        return status