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
        self.cache_dir = Path("cache")
        self.cache_dir.mkdir(exist_ok=True)
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
    def get_stock_data(_self, tickers, period="1mo"):  # Changed self to _self
        data = {}
        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                hist = stock.history(period=period)
                intraday = None
                if _self.is_market_open():
                    intraday = stock.history(period="1d", interval="5m")
                data[ticker] = {
                    'info': info, 'history': hist, 'intraday': intraday,
                    'last_updated': datetime.now()
                }
            except Exception:
                continue
        return data
    
    def is_market_open(self):
        now_utc = datetime.now(pytz.utc)
        et_tz = pytz.timezone('US/Eastern')
        now_et = now_utc.astimezone(et_tz)

        if now_et.weekday() >= 5: return False
        
        market_open_time = now_et.replace(hour=9, minute=30, second=0, microsecond=0).time()
        market_close_time = now_et.replace(hour=16, minute=0, second=0, microsecond=0).time()
        
        return market_open_time <= now_et.time() <= market_close_time

    def _basic_sentiment_analysis_for_dm(self, text):
        if not text or not isinstance(text, str): return "Neutral", 0.0
        text_lower = text.lower()
        positive_keywords = ['up', 'gain', 'profit', 'bullish', 'rally', 'strong', 'positive', 'upgrade', 'outperform', 'beat', 'good', 'great', 'excellent', 'record', 'high', 'boom', 'surge', 'growth', 'rise']
        negative_keywords = ['down', 'loss', 'bearish', 'slump', 'weak', 'negative', 'downgrade', 'underperform', 'miss', 'bad', 'terrible', 'poor', 'plunge', 'drop', 'crisis', 'fear', 'fall', 'decline', 'isn\'t worth', 'lost']
        positive_score = sum(1 for keyword in positive_keywords if keyword in text_lower)
        negative_score = sum(1 for keyword in negative_keywords if keyword in text_lower)
        total_keywords = positive_score + negative_score
        if total_keywords == 0: return "Neutral", 0.0
        score = (positive_score - negative_score) / total_keywords
        if score > 0.1: return "Positive", score
        elif score < -0.1: return "Negative", score
        else: return "Neutral", score

    def _get_openai_sentiment_for_dm(self, title, summary, ticker, debug_mode=False):
        global OPENAI_AUTH_ERROR_MESSAGE_DM
        if OPENAI_AUTH_ERROR_MESSAGE_DM:
            if debug_mode: st.caption(f"DM Skipping AI for {ticker} due to: {OPENAI_AUTH_ERROR_MESSAGE_DM}")
            return self._basic_sentiment_analysis_for_dm(f"{title} {summary}")

        if not self.use_ai_sentiment_dm or not openai_client_dm:
            if debug_mode: st.caption(f"DM: AI sentiment not used for {ticker} (not configured or client init failed).")
            return self._basic_sentiment_analysis_for_dm(f"{title} {summary}")
        
        try:
            prompt = f"""Analyze this news article's sentiment specifically for {ticker} stock:
            Title: {title}
            Summary: {summary[:500] if summary else title[:500]}
            Consider the direct impact on {ticker}. Is it good, bad, or neutral for {ticker} shareholders?
            Respond with ONLY ONE word: Positive, Negative, or Neutral."""
            
            if debug_mode: st.caption(f"DM 🤖 Attempting OpenAI call for {ticker}: '{title[:30]}...'")

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
            if debug_mode: st.caption(f"DM 🤖 OpenAI Raw Response for {ticker}: '{sentiment_text}'")

            sentiment, score = "Neutral", 0.0
            if "positive" in sentiment_text: sentiment, score = "Positive", 0.7
            elif "negative" in sentiment_text: sentiment, score = "Negative", -0.7
            elif "neutral" in sentiment_text: sentiment, score = "Neutral", 0.0
            else:
                if debug_mode: st.warning(f"DM Unexpected AI word: '{sentiment_text}' for {ticker}.")
                return self._basic_sentiment_analysis_for_dm(f"{title} {summary}")
            return sentiment, score
        except openai.AuthenticationError as auth_err:
            OPENAI_AUTH_ERROR_MESSAGE_DM = f"DM OpenAI AuthError for {ticker}: {auth_err}"
            if debug_mode: st.error(OPENAI_AUTH_ERROR_MESSAGE_DM)
            return self._basic_sentiment_analysis_for_dm(f"{title} {summary}")
        except Exception as e:
            if debug_mode: st.error(f"DM General error in AI sentiment for {ticker}: {e}")
            return self._basic_sentiment_analysis_for_dm(f"{title} {summary}")

    @st.cache_data(ttl=1800)
    def get_latest_news_sentiment(_self, ticker_symbol, debug_mode=False):  # Changed self to _self
        try:
            stock = yf.Ticker(ticker_symbol)
            news_list = stock.news
            if not news_list:
                if debug_mode: st.caption(f"DM: No news found for {ticker_symbol} via yfinance.")
                return None

            latest_article_raw = news_list[0]
            
            title = latest_article_raw.get('title', 'No Title')
            link = latest_article_raw.get('link', '#')
            publisher = latest_article_raw.get('publisher', 'Unknown Source')
            publish_time_unix = latest_article_raw.get('providerPublishTime')
            
            date_str = "N/A"
            if publish_time_unix:
                try:
                    date_obj_utc = datetime.fromtimestamp(publish_time_unix, tz=pytz.utc)
                    date_str = date_obj_utc.strftime('%Y-%m-%d %H:%M')
                except Exception:
                    pass

            summary_for_sentiment = title 

            sentiment_label, sentiment_score = _self._get_openai_sentiment_for_dm(title, summary_for_sentiment, ticker_symbol, debug_mode)
            
            return {
                'label': sentiment_label,
                'score': sentiment_score,
                'headline': title,
                'link': link,
                'source': publisher,
                'date': date_str
            }
        except Exception as e:
            if debug_mode: st.error(f"DM Error in get_latest_news_sentiment for {ticker_symbol}: {e}")
            return None

    @st.cache_data(ttl=3600)
    def calculate_signals(_self, ticker_data):  # Changed self to _self
        signals = {
            'momentum': _self._momentum_signal(ticker_data),
            'technical': _self._technical_signal(ticker_data),
            'volume': _self._volume_signal(ticker_data),
            'pattern': _self._pattern_signal(ticker_data)
        }
        signals['day_score'] = (signals['momentum'] * 0.4 + signals['volume'] * 0.4 + signals['technical'] * 0.2)
        signals['swing_score'] = (signals['technical'] * 0.5 + signals['pattern'] * 0.3 + signals['momentum'] * 0.2)
        return signals
    
    def _momentum_signal(self, data):
        try:
            info = data.get('info', {})
            change = info.get('regularMarketChangePercent', 0)
            if change > 0.10: return 100
            elif change > 0.05: return 80
            elif change > 0.02: return 60
            elif change > 0: return 50
            else: return max(0, 50 + (change * 500))
        except: return 50
    
    def _technical_signal(self, data):
        try:
            hist = data.get('history')
            if hist is None or hist.empty or len(hist) < 20: return 50
            close = hist['Close']
            sma20 = close.rolling(20).mean().iloc[-1]
            current = close.iloc[-1]
            score = 50
            if current > sma20: score += 25
            rsi = self._calculate_rsi(close)
            if rsi is not None:
                 if 30 < rsi < 70: score += 25
                 elif rsi <= 30: score += 15
            return min(100, score)
        except: return 50

    def _volume_signal(self, data):
        try:
            info = data.get('info',{})
            volume = info.get('regularMarketVolume', 0)
            avg_volume = info.get('averageVolume', 1)
            if avg_volume == 0: return 0
            rvol = volume / avg_volume
            if rvol > 3: return 100
            elif rvol > 2: return 80
            elif rvol > 1.5: return 60
            elif rvol > 1: return 50
            else: return max(0, rvol * 50)
        except: return 50

    def _pattern_signal(self, data):
        try:
            hist = data.get('history')
            if hist is None or hist.empty or len(hist) < 20: return 50
            high_20 = hist['High'].rolling(20).max().iloc[-1]
            current = hist['Close'].iloc[-1]
            if current >= high_20: return 80
            elif current >= high_20 * 0.98: return 60
            else: return 40
        except: return 50

    def _calculate_rsi(self, prices, period=14):
        if prices is None or len(prices) < period + 1: return None
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        if loss.iloc[-1] == 0 : return 100 if gain.iloc[-1] > 0 else 50
        rs = gain.iloc[-1] / loss.iloc[-1]
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def get_watchlist(self):
        watchlist_file = self.cache_dir / "watchlist_storage.json"
        if watchlist_file.exists():
            try:
                with open(watchlist_file, 'r') as f: data = json.load(f)
                return data.get('watchlist', [])
            except: return []
        return []

    def save_watchlist(self, tickers):
        watchlist_file = self.cache_dir / "watchlist_storage.json"
        try:
            with open(watchlist_file, 'w') as f: json.dump({'watchlist': tickers}, f)
            return True
        except: return False

    def get_trade_journal(self):
        journal_file = self.cache_dir / "trade_journal.json"
        if journal_file.exists():
            try:
                with open(journal_file, 'r') as f: return json.load(f)
            except: return []
        return []

    def add_trade_entry(self, trade):
        journal = self.get_trade_journal()
        trade['timestamp'] = datetime.now().isoformat()
        journal.append(trade)
        try:
            with open(self.cache_dir / "trade_journal.json", 'w') as f: json.dump(journal, f)
            return True
        except: return False

    def analyze_portfolio_performance(self):
        trades = self.get_trade_journal()
        if not trades: return None
        df = pd.DataFrame(trades)
        if df.empty or 'profit_loss' not in df.columns:
             return {'total_trades': len(df), 'win_rate': 0, 'total_pnl': 0}

        df['profit_loss'] = pd.to_numeric(df['profit_loss'], errors='coerce').fillna(0)
            
        total_trades = len(df)
        winning_trades = len(df[df['profit_loss'] > 0])
        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        total_pnl = df['profit_loss'].sum()
        avg_win = df[df['profit_loss'] > 0]['profit_loss'].mean() if winning_trades > 0 else 0
        avg_loss = abs(df[df['profit_loss'] < 0]['profit_loss'].mean()) if winning_trades < total_trades else 0
        profit_factor = (winning_trades * avg_win) / ( (total_trades - winning_trades) * avg_loss ) if avg_loss > 0 and (total_trades - winning_trades) > 0 else float('inf') if avg_win > 0 else 0
        
        return {
            'total_trades': total_trades, 'win_rate': win_rate, 'total_pnl': total_pnl,
            'avg_win': avg_win, 'avg_loss': avg_loss, 'profit_factor': profit_factor,
            'best_trade': df['profit_loss'].max(), 'worst_trade': df['profit_loss'].min()
        }

# Global instance
data_manager = DataManager()
