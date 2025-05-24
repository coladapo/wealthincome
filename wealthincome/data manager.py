# data_manager.py
"""
Centralized data management for the trading platform
Handles caching, data sharing between pages, and API optimization
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import json
from datetime import datetime, timedelta
from pathlib import Path
import pickle

class DataManager:
    """Manages all data operations across the platform"""
    
    def __init__(self):
        self.cache_dir = Path("cache")
        self.cache_dir.mkdir(exist_ok=True)
        
    @st.cache_data(ttl=300)  # 5 minute cache
    def get_stock_data(self, tickers, period="1mo"):
        """Fetch stock data with caching"""
        data = {}
        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                hist = stock.history(period=period)
                
                # Get intraday if market is open
                intraday = None
                if self.is_market_open():
                    intraday = stock.history(period="1d", interval="5m")
                
                data[ticker] = {
                    'info': info,
                    'history': hist,
                    'intraday': intraday,
                    'last_updated': datetime.now()
                }
            except:
                continue
        return data
    
    def is_market_open(self):
        """Check if US market is open"""
        now = datetime.now()
        # Simple check - enhance with holidays
        if now.weekday() >= 5:  # Weekend
            return False
        
        # Market hours in ET
        market_open = now.replace(hour=9, minute=30, second=0)
        market_close = now.replace(hour=16, minute=0, second=0)
        
        # Adjust for your timezone
        return market_open <= now <= market_close
    
    @st.cache_data(ttl=3600)  # 1 hour cache
    def calculate_signals(self, ticker_data):
        """Calculate all trading signals"""
        signals = {
            'momentum': self._momentum_signal(ticker_data),
            'technical': self._technical_signal(ticker_data),
            'volume': self._volume_signal(ticker_data),
            'pattern': self._pattern_signal(ticker_data)
        }
        
        # Composite scores
        signals['day_score'] = (
            signals['momentum'] * 0.4 +
            signals['volume'] * 0.4 +
            signals['technical'] * 0.2
        )
        
        signals['swing_score'] = (
            signals['technical'] * 0.5 +
            signals['pattern'] * 0.3 +
            signals['momentum'] * 0.2
        )
        
        return signals
    
    def _momentum_signal(self, data):
        """Calculate momentum signal (0-100)"""
        try:
            info = data['info']
            change = info.get('regularMarketChangePercent', 0)
            
            # Scale change to 0-100
            if change > 10:
                return 100
            elif change > 5:
                return 80
            elif change > 2:
                return 60
            elif change > 0:
                return 50
            else:
                return max(0, 50 + change * 5)
        except:
            return 50
    
    def _technical_signal(self, data):
        """Calculate technical signal (0-100)"""
        try:
            hist = data['history']
            if hist.empty or len(hist) < 20:
                return 50
                
            close = hist['Close']
            sma20 = close.rolling(20).mean().iloc[-1]
            current = close.iloc[-1]
            
            # Price above SMA20
            score = 50
            if current > sma20:
                score += 25
                
            # RSI
            rsi = self._calculate_rsi(close)
            if 30 < rsi < 70:
                score += 25
            elif rsi <= 30:  # Oversold
                score += 15
                
            return min(100, score)
        except:
            return 50
    
    def _volume_signal(self, data):
        """Calculate volume signal (0-100)"""
        try:
            info = data['info']
            volume = info.get('regularMarketVolume', 0)
            avg_volume = info.get('averageVolume', 1)
            
            rvol = volume / avg_volume if avg_volume > 0 else 0
            
            if rvol > 3:
                return 100
            elif rvol > 2:
                return 80
            elif rvol > 1.5:
                return 60
            elif rvol > 1:
                return 50
            else:
                return rvol * 50
        except:
            return 50
    
    def _pattern_signal(self, data):
        """Detect patterns (0-100)"""
        try:
            hist = data['history']
            if hist.empty or len(hist) < 20:
                return 50
                
            # Simple breakout detection
            high_20 = hist['High'].rolling(20).max().iloc[-1]
            current = hist['Close'].iloc[-1]
            
            if current >= high_20:
                return 80
            elif current >= high_20 * 0.98:  # Near breakout
                return 60
            else:
                return 40
        except:
            return 50
    
    def _calculate_rsi(self, prices, period=14):
        """Simple RSI calculation"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]
    
    def get_watchlist(self):
        """Get watchlist from file"""
        watchlist_file = Path("watchlist_storage.json")
        if watchlist_file.exists():
            try:
                with open(watchlist_file, 'r') as f:
                    data = json.load(f)
                    return data.get('watchlist', [])
            except:
                return []
        return []
    
    def save_watchlist(self, tickers):
        """Save watchlist to file"""
        watchlist_file = Path("watchlist_storage.json")
        try:
            with open(watchlist_file, 'w') as f:
                json.dump({'watchlist': tickers}, f)
            return True
        except:
            return False
    
    def get_trade_journal(self):
        """Get trade journal entries"""
        journal_file = Path("trade_journal.json")
        if journal_file.exists():
            try:
                with open(journal_file, 'r') as f:
                    return json.load(f)
            except:
                return []
        return []
    
    def add_trade_entry(self, trade):
        """Add entry to trade journal"""
        journal = self.get_trade_journal()
        trade['timestamp'] = datetime.now().isoformat()
        journal.append(trade)
        
        try:
            with open("trade_journal.json", 'w') as f:
                json.dump(journal, f)
            return True
        except:
            return False
    
    def analyze_portfolio_performance(self):
        """Analyze overall trading performance"""
        trades = self.get_trade_journal()
        if not trades:
            return None
            
        df = pd.DataFrame(trades)
        
        # Calculate metrics
        total_trades = len(df)
        if 'profit_loss' in df.columns:
            winning_trades = len(df[df['profit_loss'] > 0])
            win_rate = winning_trades / total_trades if total_trades > 0 else 0
            
            total_pnl = df['profit_loss'].sum()
            avg_win = df[df['profit_loss'] > 0]['profit_loss'].mean() if winning_trades > 0 else 0
            avg_loss = abs(df[df['profit_loss'] < 0]['profit_loss'].mean()) if winning_trades < total_trades else 0
            
            profit_factor = avg_win / avg_loss if avg_loss > 0 else 0
            
            return {
                'total_trades': total_trades,
                'win_rate': win_rate,
                'total_pnl': total_pnl,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'profit_factor': profit_factor,
                'best_trade': df['profit_loss'].max() if 'profit_loss' in df.columns else 0,
                'worst_trade': df['profit_loss'].min() if 'profit_loss' in df.columns else 0
            }
        
        return {
            'total_trades': total_trades,
            'win_rate': 0,
            'total_pnl': 0
        }

# Global instance
data_manager = DataManager()
