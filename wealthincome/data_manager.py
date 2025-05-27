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
import pytz
import numpy as np

# Attempt to import openai
OPENAI_INSTALLED_DM = False
openai_client_dm = None
OPENAI_AUTH_ERROR_MESSAGE_DM = None

try:
    import openai
    OPENAI_INSTALLED_DM = True
except ImportError:
    pass

class DataManager:
    """Manages all data operations across the platform"""
    
    def __init__(self):
        self.cache_dir = Path("data/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.use_ai_sentiment_dm = False

        if OPENAI_INSTALLED_DM:
            openai_api_key_from_secrets = st.secrets.get("OPENAI_API_KEY")
            if openai_api_key_from_secrets:
                try:
                    global openai_client_dm
                    openai_client_dm = openai.OpenAI(api_key=openai_api_key_from_secrets)
                    self.use_ai_sentiment_dm = True
                except openai.AuthenticationError as auth_err:
                    global OPENAI_AUTH_ERROR_MESSAGE_DM
                    OPENAI_AUTH_ERROR_MESSAGE_DM = f"DM OpenAI AuthError: {auth_err}. Basic sentiment will be used."
                except Exception as e_client_init:
                    pass

    @st.cache_data(ttl=300)
    def get_stock_data(_self, tickers, period="1mo"):
        """Fetch stock data for given tickers"""
        data = {}
        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                hist = stock.history(period=period)
                intraday = None
                if _self.is_market_open():
                    try:
                        intraday = stock.history(period="1d", interval="5m")
                    except:
                        intraday = None
                data[ticker] = {
                    'info': info, 
                    'history': hist, 
                    'intraday': intraday,
                    'last_updated': datetime.now()
                }
            except Exception as e:
                print(f"Error fetching data for {ticker}: {e}")
                continue
        return data
    
    def is_market_open(self):
        """Check if US market is open"""
        now_utc = datetime.now(pytz.utc)
        et_tz = pytz.timezone('US/Eastern')
        now_et = now_utc.astimezone(et_tz)

        if now_et.weekday() >= 5: 
            return False
        
        market_open_time = now_et.replace(hour=9, minute=30, second=0, microsecond=0).time()
        market_close_time = now_et.replace(hour=16, minute=0, second=0, microsecond=0).time()
        
        return market_open_time <= now_et.time() <= market_close_time

    def _basic_sentiment_analysis_for_dm(self, text):
        """Basic sentiment analysis using keyword matching"""
        if not text or not isinstance(text, str): 
            return "Neutral", 0.0
        text_lower = text.lower()
        positive_keywords = ['up', 'gain', 'profit', 'bullish', 'rally', 'strong', 'positive', 'upgrade', 'outperform', 'beat', 'good', 'great', 'excellent', 'record', 'high', 'boom', 'surge', 'growth', 'rise']
        negative_keywords = ['down', 'loss', 'bearish', 'slump', 'weak', 'negative', 'downgrade', 'underperform', 'miss', 'bad', 'terrible', 'poor', 'plunge', 'drop', 'crisis', 'fear', 'fall', 'decline', 'lawsuit', 'investigation']
        positive_score = sum(1 for keyword in positive_keywords if keyword in text_lower)
        negative_score = sum(1 for keyword in negative_keywords if keyword in text_lower)
        total_keywords = positive_score + negative_score
        if total_keywords == 0: 
            return "Neutral", 0.0
        score = (positive_score - negative_score) / total_keywords
        if score > 0.1: 
            return "Positive", score
        elif score < -0.1: 
            return "Negative", score
        else: 
            return "Neutral", score

    def _get_openai_sentiment_for_dm(self, title, summary, ticker, debug_mode=False):
        """Get AI sentiment analysis using OpenAI"""
        global OPENAI_AUTH_ERROR_MESSAGE_DM
        if OPENAI_AUTH_ERROR_MESSAGE_DM:
            if debug_mode: 
                st.caption(f"DM Skipping AI for {ticker} due to: {OPENAI_AUTH_ERROR_MESSAGE_DM}")
            return self._basic_sentiment_analysis_for_dm(f"{title} {summary}")

        if not self.use_ai_sentiment_dm or not openai_client_dm:
            if debug_mode: 
                st.caption(f"DM: AI sentiment not used for {ticker} (not configured or client init failed).")
            return self._basic_sentiment_analysis_for_dm(f"{title} {summary}")
        
        try:
            prompt = f"""Analyze this news article's sentiment specifically for {ticker} stock:
            Title: {title}
            Summary: {summary[:500] if summary else title[:500]}
            Consider the direct impact on {ticker}. Is it good, bad, or neutral for {ticker} shareholders?
            Respond with ONLY ONE word: Positive, Negative, or Neutral."""
            
            if debug_mode: 
                st.caption(f"DM 🤖 Attempting OpenAI call for {ticker}: '{title[:30]}...'")

            response = openai_client_dm.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a stock sentiment analyzer. Respond with EXACTLY one word: Positive, Negative, or Neutral."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=10
            )
            sentiment_text = response.choices[0].message.content.strip().lower()
            if debug_mode: 
                st.caption(f"DM 🤖 OpenAI Raw Response for {ticker}: '{sentiment_text}'")

            sentiment, score = "Neutral", 0.0
            if "positive" in sentiment_text: 
                sentiment, score = "Positive", 0.7
            elif "negative" in sentiment_text: 
                sentiment, score = "Negative", -0.7
            elif "neutral" in sentiment_text: 
                sentiment, score = "Neutral", 0.0
            else:
                if debug_mode: 
                    st.warning(f"DM Unexpected AI word: '{sentiment_text}' for {ticker}.")
                return self._basic_sentiment_analysis_for_dm(f"{title} {summary}")
            return sentiment, score
        except openai.AuthenticationError as auth_err:
            OPENAI_AUTH_ERROR_MESSAGE_DM = f"DM OpenAI AuthError for {ticker}: {auth_err}"
            if debug_mode: 
                st.error(OPENAI_AUTH_ERROR_MESSAGE_DM)
            return self._basic_sentiment_analysis_for_dm(f"{title} {summary}")
        except Exception as e:
            if debug_mode: 
                st.error(f"DM General error in AI sentiment for {ticker}: {e}")
            return self._basic_sentiment_analysis_for_dm(f"{title} {summary}")

    @st.cache_data(ttl=1800)
    def get_latest_news_sentiment(_self, ticker_symbol, debug_mode=False):
        """Get latest news and sentiment for a ticker"""
        try:
            stock = yf.Ticker(ticker_symbol)
            news_list = stock.news
            if not news_list:
                if debug_mode: 
                    st.caption(f"DM: No news found for {ticker_symbol} via yfinance.")
                return None

            latest_article_raw = news_list[0]
            
            # Handle different news data structures
            if 'content' in latest_article_raw:
                content = latest_article_raw['content']
                title = content.get('title', latest_article_raw.get('title', 'No Title'))
                link_data = content.get('clickThroughUrl') or content.get('canonicalUrl') or {}
                link = link_data.get('url', latest_article_raw.get('link', '#'))
                provider = content.get('provider', {})
                publisher = provider.get('displayName', latest_article_raw.get('publisher', 'Unknown Source'))
                publish_time_unix = content.get('pubDate')
                # Try to parse publish time
                if publish_time_unix and isinstance(publish_time_unix, str):
                    try:
                        date_obj = datetime.fromisoformat(publish_time_unix.replace('Z', '+00:00'))
                        publish_time_unix = date_obj.timestamp()
                    except:
                        publish_time_unix = latest_article_raw.get('providerPublishTime')
                else:
                    publish_time_unix = latest_article_raw.get('providerPublishTime')
            else:
                title = latest_article_raw.get('title', 'No Title')
                link = latest_article_raw.get('link', '#')
                publisher = latest_article_raw.get('publisher', 'Unknown Source')
                publish_time_unix = latest_article_raw.get('providerPublishTime')
            
            date_str = "N/A"
            if publish_time_unix:
                try:
                    if isinstance(publish_time_unix, (int, float)):
                        date_obj_utc = datetime.fromtimestamp(publish_time_unix, tz=pytz.utc)
                    else:
                        date_obj_utc = datetime.now(pytz.utc)
                    date_str = date_obj_utc.strftime('%Y-%m-%d %H:%M')
                except Exception:
                    pass

            summary_for_sentiment = title 

            sentiment_label, sentiment_score = _self._get_openai_sentiment_for_dm(
                title, summary_for_sentiment, ticker_symbol, debug_mode
            )
            
            return {
                'label': sentiment_label,
                'score': sentiment_score,
                'headline': title,
                'link': link,
                'source': publisher,
                'date': date_str
            }
        except Exception as e:
            if debug_mode: 
                st.error(f"DM Error in get_latest_news_sentiment for {ticker_symbol}: {e}")
            return None

    @st.cache_data(ttl=3600)
    def calculate_signals(_self, ticker_data):
        """Calculate trading signals from ticker data"""
        signals = {
            'momentum': _self._momentum_signal(ticker_data),
            'technical': _self._technical_signal(ticker_data),
            'volume': _self._volume_signal(ticker_data),
            'pattern': _self._pattern_signal(ticker_data)
        }
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
        """Calculate momentum signal"""
        try:
            info = data.get('info', {})
            change = info.get('regularMarketChangePercent', 0)
            if change > 0.10: 
                return 100
            elif change > 0.05: 
                return 80
            elif change > 0.02: 
                return 60
            elif change > 0: 
                return 50
            else: 
                return max(0, 50 + (change * 500))
        except: 
            return 50
    
    def _technical_signal(self, data):
        """Calculate technical signal"""
        try:
            hist = data.get('history')
            if hist is None or hist.empty or len(hist) < 20: 
                return 50
            close = hist['Close']
            sma20 = close.rolling(20).mean().iloc[-1]
            current = close.iloc[-1]
            score = 50
            if current > sma20: 
                score += 25
            rsi = self._calculate_rsi(close)
            if rsi is not None:
                if 30 < rsi < 70: 
                    score += 25
                elif rsi <= 30: 
                    score += 15
            return min(100, score)
        except: 
            return 50

    def _volume_signal(self, data):
        """Calculate volume signal"""
        try:
            info = data.get('info', {})
            volume = info.get('regularMarketVolume', 0)
            avg_volume = info.get('averageVolume', 1)
            if avg_volume == 0: 
                return 0
            rvol = volume / avg_volume
            if rvol > 3: 
                return 100
            elif rvol > 2: 
                return 80
            elif rvol > 1.5: 
                return 60
            elif rvol > 1: 
                return 50
            else: 
                return max(0, rvol * 50)
        except: 
            return 50

    def _pattern_signal(self, data):
        """Calculate pattern signal"""
        try:
            hist = data.get('history')
            if hist is None or hist.empty or len(hist) < 20: 
                return 50
            high_20 = hist['High'].rolling(20).max().iloc[-1]
            current = hist['Close'].iloc[-1]
            if current >= high_20: 
                return 80
            elif current >= high_20 * 0.98: 
                return 60
            else: 
                return 40
        except: 
            return 50

    def _calculate_rsi(self, prices, period=14):
        """Calculate RSI indicator"""
        if prices is None or len(prices) < period + 1: 
            return None
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        if loss.iloc[-1] == 0: 
            return 100 if gain.iloc[-1] > 0 else 50
        rs = gain.iloc[-1] / loss.iloc[-1]
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def calculate_indicators(self, hist_data):
        """Calculate all technical indicators for a stock"""
        indicators = {}
        
        if hist_data is None or hist_data.empty:
            return indicators
        
        try:
            # Price
            indicators['current_price'] = hist_data['Close'].iloc[-1]
            
            # Moving averages
            if len(hist_data) >= 20:
                indicators['sma_20'] = hist_data['Close'].rolling(20).mean().iloc[-1]
            if len(hist_data) >= 50:
                indicators['sma_50'] = hist_data['Close'].rolling(50).mean().iloc[-1]
            if len(hist_data) >= 200:
                indicators['sma_200'] = hist_data['Close'].rolling(200).mean().iloc[-1]
            
            # RSI
            indicators['rsi'] = self._calculate_rsi(hist_data['Close'])
            
            # MACD
            if len(hist_data) >= 26:
                exp1 = hist_data['Close'].ewm(span=12, adjust=False).mean()
                exp2 = hist_data['Close'].ewm(span=26, adjust=False).mean()
                indicators['macd'] = (exp1 - exp2).iloc[-1]
                indicators['macd_signal'] = (exp1 - exp2).ewm(span=9, adjust=False).mean().iloc[-1]
            
            # Bollinger Bands
            if len(hist_data) >= 20:
                sma = hist_data['Close'].rolling(20).mean()
                std = hist_data['Close'].rolling(20).std()
                indicators['bb_upper'] = (sma + (std * 2)).iloc[-1]
                indicators['bb_lower'] = (sma - (std * 2)).iloc[-1]
                indicators['bb_middle'] = sma.iloc[-1]
            
            # Support and Resistance
            if len(hist_data) >= 20:
                indicators['support'] = hist_data['Low'].rolling(20).min().iloc[-1]
                indicators['resistance'] = hist_data['High'].rolling(20).max().iloc[-1]
            
            # Volume
            indicators['volume'] = hist_data['Volume'].iloc[-1]
            if len(hist_data) >= 20:
                indicators['volume_sma'] = hist_data['Volume'].rolling(20).mean().iloc[-1]
                indicators['volume_ratio'] = indicators['volume'] / indicators['volume_sma'] if indicators['volume_sma'] > 0 else 0
            
        except Exception as e:
            print(f"Error calculating indicators: {e}")
        
        return indicators

    def find_patterns(self, hist_data):
        """Find chart patterns in historical data"""
        patterns = {}
        
        if hist_data is None or hist_data.empty or len(hist_data) < 20:
            return patterns
        
        try:
            # Get recent highs and lows
            recent_high = hist_data['High'].rolling(20).max().iloc[-1]
            recent_low = hist_data['Low'].rolling(20).min().iloc[-1]
            current_price = hist_data['Close'].iloc[-1]
            
            # Breakout pattern
            if current_price >= recent_high * 0.99:
                patterns['breakout'] = {
                    'type': 'Resistance Breakout',
                    'strength': 'Strong' if current_price > recent_high else 'Pending',
                    'target': recent_high * 1.05
                }
            
            # Support bounce
            if current_price <= recent_low * 1.01:
                patterns['support_bounce'] = {
                    'type': 'Support Test',
                    'strength': 'Strong' if current_price > recent_low else 'Weak',
                    'target': recent_low * 0.95
                }
            
            # Trend detection
            sma_20 = hist_data['Close'].rolling(20).mean().iloc[-1]
            sma_50 = hist_data['Close'].rolling(50).mean().iloc[-1] if len(hist_data) >= 50 else None
            
            if sma_50:
                if current_price > sma_20 > sma_50:
                    patterns['trend'] = {
                        'type': 'Uptrend',
                        'strength': 'Strong',
                        'description': 'Price above 20 & 50 SMA'
                    }
                elif current_price < sma_20 < sma_50:
                    patterns['trend'] = {
                        'type': 'Downtrend',
                        'strength': 'Strong',
                        'description': 'Price below 20 & 50 SMA'
                    }
            
        except Exception as e:
            print(f"Error finding patterns: {e}")
        
        return patterns

    def get_watchlist(self):
        """Get saved watchlist"""
        watchlist_file = self.cache_dir / "watchlist_storage.json"
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
        watchlist_file = self.cache_dir / "watchlist_storage.json"
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(watchlist_file, 'w') as f: 
                json.dump({'watchlist': tickers}, f)
            return True
        except Exception as e:
            print(f"Error saving watchlist: {e}")
            return False

    def get_trade_journal(self):
        """Get trade journal entries"""
        journal_file = self.cache_dir / "trade_journal.json"
        if journal_file.exists():
            try:
                with open(journal_file, 'r') as f: 
                    return json.load(f)
            except: 
                return []
        return []

    def add_trade_entry(self, trade):
        """Add a trade to the journal"""
        journal = self.get_trade_journal()
        trade['timestamp'] = datetime.now().isoformat()
        journal.append(trade)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self.cache_dir / "trade_journal.json", 'w') as f: 
                json.dump(journal, f)
            return True
        except: 
            return False

    def add_trade_with_context(self, trade_data):
        """Add trade with additional context"""
        # Add timestamp if not present
        if 'timestamp' not in trade_data:
            trade_data['timestamp'] = datetime.now().isoformat()
        
        # Add trade to journal
        return self.add_trade_entry(trade_data)

    def get_trade_performance_by_signal(self):
        """Analyze trade performance by signal type"""
        trades = self.get_trade_journal()
        if not trades:
            return {}
        
        # Filter for closed trades
        closed_trades = [t for t in trades if t.get('is_closed', False)]
        if not closed_trades:
            return {}
        
        # Group by signal source
        signal_performance = {}
        
        for trade in closed_trades:
            signal_sources = trade.get('signal_source', ['Manual'])
            profit_loss = trade.get('profit_loss', 0)
            
            for signal in signal_sources:
                if signal not in signal_performance:
                    signal_performance[signal] = {
                        'trades': 0,
                        'wins': 0,
                        'total_pnl': 0,
                        'win_rate': 0,
                        'avg_pnl': 0
                    }
                
                signal_performance[signal]['trades'] += 1
                signal_performance[signal]['total_pnl'] += profit_loss
                if profit_loss > 0:
                    signal_performance[signal]['wins'] += 1
        
        # Calculate rates
        for signal, stats in signal_performance.items():
            if stats['trades'] > 0:
                stats['win_rate'] = stats['wins'] / stats['trades']
                stats['avg_pnl'] = stats['total_pnl'] / stats['trades']
        
        return signal_performance

    def get_combined_analysis(self, ticker):
        """Get comprehensive analysis for a ticker"""
        try:
            # Get stock data
            stock_data = self.get_stock_data([ticker], period="1mo")
            if not stock_data or ticker not in stock_data:
                return {'error': 'Failed to fetch stock data'}
            
            ticker_data = stock_data[ticker]
            
            # Get indicators
            indicators = self.calculate_indicators(ticker_data.get('history'))
            
            # Get patterns
            patterns = self.find_patterns(ticker_data.get('history'))
            
            # Get signals
            signals = self.calculate_signals(ticker_data)
            
            # Get news sentiment
            news_sentiment = self.get_latest_news_sentiment(ticker)
            
            # Combine all analysis
            analysis = {
                'ticker': ticker,
                'price': indicators.get('current_price', 0),
                'indicators': indicators,
                'patterns': patterns,
                'scores': {
                    'technical': signals,
                    'sentiment': news_sentiment if news_sentiment else {'label': 'N/A', 'score': 0}
                },
                'signals': [],
                'recommendations': []
            }
            
            # Generate signals based on analysis
            if signals.get('day_score', 0) > 70:
                analysis['signals'].append({
                    'type': 'Day Trade',
                    'strength': 'Strong',
                    'reason': f"High momentum score: {signals['day_score']:.0f}"
                })
            
            if signals.get('swing_score', 0) > 70:
                analysis['signals'].append({
                    'type': 'Swing Trade',
                    'strength': 'Strong',
                    'reason': f"Good technical setup: {signals['swing_score']:.0f}"
                })
            
            # Add recommendations
            if indicators.get('rsi') and indicators['rsi'] < 30:
                analysis['recommendations'].append("RSI oversold - potential bounce opportunity")
            
            if patterns.get('breakout'):
                analysis['recommendations'].append(f"Breakout pattern detected - target: ${patterns['breakout']['target']:.2f}")
            
            return analysis
            
        except Exception as e:
            return {'error': str(e)}

    def analyze_portfolio_performance(self):
        """Analyze overall portfolio performance"""
        trades = self.get_trade_journal()
        if not trades: 
            return None
            
        df = pd.DataFrame(trades)
        if df.empty or 'profit_loss' not in df.columns:
            return {
                'total_trades': len(df), 
                'win_rate': 0, 
                'total_pnl': 0
            }

        # Filter for closed trades
        closed_trades = df[df.get('is_closed', False) == True]
        if closed_trades.empty:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'total_pnl': 0
            }
            
        closed_trades['profit_loss'] = pd.to_numeric(closed_trades['profit_loss'], errors='coerce').fillna(0)
            
        total_trades = len(closed_trades)
        winning_trades = len(closed_trades[closed_trades['profit_loss'] > 0])
        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        total_pnl = closed_trades['profit_loss'].sum()
        avg_win = closed_trades[closed_trades['profit_loss'] > 0]['profit_loss'].mean() if winning_trades > 0 else 0
        avg_loss = abs(closed_trades[closed_trades['profit_loss'] < 0]['profit_loss'].mean()) if winning_trades < total_trades else 0
        
        profit_factor = 0
        if avg_loss > 0 and (total_trades - winning_trades) > 0:
            profit_factor = (winning_trades * avg_win) / ((total_trades - winning_trades) * avg_loss)
        elif avg_win > 0:
            profit_factor = float('inf')
        
        return {
            'total_trades': total_trades, 
            'win_rate': win_rate, 
            'total_pnl': total_pnl,
            'avg_win': avg_win, 
            'avg_loss': avg_loss, 
            'profit_factor': profit_factor,
            'best_trade': closed_trades['profit_loss'].max() if not closed_trades.empty else 0, 
            'worst_trade': closed_trades['profit_loss'].min() if not closed_trades.empty else 0
        }

# Global instance
data_manager = DataManager()
