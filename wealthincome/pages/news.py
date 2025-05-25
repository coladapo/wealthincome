import streamlit as st
import sys
import os
import pandas as pd
from datetime import datetime
import yfinance as yf
import requests
from bs4 import BeautifulSoup
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pytz
import json
from collections import defaultdict

# --- Start of Path Fix ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)
# --- End of Path Fix ---

# --- Import data_manager ---
data_manager_instance = None 
try:
    from data_manager import data_manager as dm_instance 
    data_manager_instance = dm_instance 
except ImportError as e:
    st.error(f"Could not import data_manager: {e}. Some features might be limited.")
except Exception as e:
    st.error(f"An unexpected error occurred during data_manager import: {e}")
# --- End Imports ---

# --- Page Configuration ---
try:
    st.set_page_config(page_title="🗞️ Market News", layout="wide")
except st.errors.StreamlitAPIException:
    pass

st.title('🗞️ Market News & Sentiment Feed')

# --- Helper function to handle datetime conversion ---
def ensure_datetime(date_value):
    """Convert string dates to datetime objects"""
    if isinstance(date_value, str):
        try:
            # Try ISO format first
            return datetime.fromisoformat(date_value.replace('Z', '+00:00'))
        except:
            try:
                # Try other common formats
                return datetime.strptime(date_value, '%Y-%m-%d %H:%M:%S')
            except:
                return datetime.min
    elif isinstance(date_value, datetime):
        return date_value
    else:
        return datetime.min

# --- OpenAI Configuration ---
try:
    from openai import OpenAI
    openai_api_key = st.secrets.get("OPENAI_API_KEY", None)
    use_ai_sentiment = openai_api_key is not None
    if openai_api_key:
        st.success("✅ AI-Powered Sentiment Analysis Active (OpenAI)")
    else:
        st.warning("⚠️ Using basic sentiment analysis. Add OPENAI_API_KEY to secrets.toml for better accuracy.")
except ImportError:
    use_ai_sentiment = False
    st.info("OpenAI not installed. Using basic sentiment analysis.")

# --- Analytics Management using DataManager ---
def load_analytics():
    """Load analytics from DataManager cache directory"""
    if data_manager_instance:
        analytics_file = data_manager_instance.cache_dir / "sentiment_analytics.json"
        if analytics_file.exists():
            try:
                with open(analytics_file, 'r') as f:
                    saved_data = json.load(f)
                    return {
                        'comparisons': saved_data.get('comparisons', []),
                        'api_calls': saved_data.get('api_calls', 0),
                        'total_tokens': saved_data.get('total_tokens', 0),
                        'cache_hits': saved_data.get('cache_hits', 0),
                        'session_start': saved_data.get('session_start', datetime.now().isoformat())
                    }
            except:
                pass
    return {
        'comparisons': [],
        'api_calls': 0,
        'total_tokens': 0,
        'cache_hits': 0,
        'session_start': datetime.now().isoformat()
    }

def save_analytics(analytics):
    """Save analytics to DataManager cache directory"""
    if data_manager_instance:
        analytics_file = data_manager_instance.cache_dir / "sentiment_analytics.json"
        try:
            with open(analytics_file, 'w') as f:
                json.dump(analytics, f, indent=2)
            return True
        except Exception as e:
            if st.session_state.get('debug_mode', False):
                st.error(f"Failed to save analytics: {e}")
            return False
    return False

# Initialize Session State
if 'sentiment_analytics' not in st.session_state:
    st.session_state.sentiment_analytics = load_analytics()

if 'last_known_openai' not in st.session_state:
    st.session_state.last_known_openai = {
        'api_calls': st.session_state.sentiment_analytics.get('api_calls', 0),
        'total_tokens': st.session_state.sentiment_analytics.get('total_tokens', 0)
    }

if 'session_api_calls' not in st.session_state:
    st.session_state.session_api_calls = 0
    st.session_state.session_tokens = 0

if 'sentiment_cache' not in st.session_state:
    st.session_state.sentiment_cache = {}

if 'append_mode' not in st.session_state:
    st.session_state.append_mode = True

# Load cached news on startup
if 'news_articles' not in st.session_state and data_manager_instance:
    news_cache_file = data_manager_instance.cache_dir / "news_articles_cache.json"
    if news_cache_file.exists():
        try:
            with open(news_cache_file, 'r') as f:
                cached_data = json.load(f)
                cache_time = ensure_datetime(cached_data.get('timestamp', '2000-01-01'))
                st.session_state['news_articles'] = cached_data.get('articles', [])
                st.session_state['last_fetch_time'] = cache_time
                
                cache_age = datetime.now() - cache_time
                hours = cache_age.total_seconds() / 3600
                
                if hours < 1:
                    minutes = cache_age.total_seconds() / 60
                    st.info(f"📂 Showing {len(st.session_state.get('news_articles', []))} cached articles from {int(minutes)} minutes ago")
                elif hours < 24:
                    st.info(f"📂 Showing {len(st.session_state.get('news_articles', []))} cached articles from {int(hours)} hours ago")
                else:
                    days = hours / 24
                    st.warning(f"📂 Showing {len(st.session_state.get('news_articles', []))} cached articles from {int(days)} days ago - Consider refreshing")
        except Exception as e:
            if st.session_state.get('debug_mode', False):
                st.error(f"Failed to load cached news: {e}")

# Track when articles were last viewed
if 'last_viewed_time' not in st.session_state:
    st.session_state['last_viewed_time'] = datetime.now()

# --- Helper Functions ---
def get_cached_sentiment(title, summary, ticker):
    """Cache wrapper for sentiment analysis"""
    cache_key = f"{ticker}:{hash(title + summary)}"
    return cache_key

def get_ai_sentiment(title, summary, ticker):
    """Use OpenAI to analyze sentiment specifically for the given ticker"""
    if not use_ai_sentiment:
        return basic_sentiment_analysis(f"{title} {summary}")
    
    cache_key = get_cached_sentiment(title, summary, ticker)
    if cache_key in st.session_state.sentiment_cache:
        st.session_state.sentiment_analytics['cache_hits'] += 1
        return st.session_state.sentiment_cache[cache_key]
    
    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_api_key)
        
        prompt = f"""
        Analyze this news article's sentiment specifically for {ticker} stock:
        
        Title: {title}
        Summary: {summary[:500]}
        
        Consider:
        1. Does this news directly impact {ticker}?
        2. Is it good or bad for {ticker} shareholders?
        3. Would a trader want to buy, sell, or hold based on this news?
        
        Respond with ONLY ONE of these three words: Positive, Negative, or Neutral
        """
        
        st.session_state.sentiment_analytics['api_calls'] += 1
        st.session_state.session_api_calls += 1
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a financial sentiment analyzer. Respond with EXACTLY one word: Positive, Negative, or Neutral."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=10
        )
        
        if hasattr(response, 'usage'):
            tokens_used = response.usage.total_tokens
            st.session_state.sentiment_analytics['total_tokens'] += tokens_used
            st.session_state.session_tokens += tokens_used
        
        save_analytics(st.session_state.sentiment_analytics)
        
        sentiment_text = response.choices[0].message.content.strip().lower()
        
        if "positive" in sentiment_text:
            sentiment = "Positive"
            score = 0.7
        elif "negative" in sentiment_text:
            sentiment = "Negative" 
            score = -0.7
        elif "neutral" in sentiment_text:
            sentiment = "Neutral"
            score = 0.0
        else:
            return basic_sentiment_analysis(f"{title} {summary}")
        
        result = (sentiment, score)
        st.session_state.sentiment_cache[cache_key] = result
        
        basic_sentiment, basic_score = basic_sentiment_analysis(f"{title} {summary}")
        if sentiment != basic_sentiment:
            comparison = {
                'ticker': ticker,
                'title': title[:100],
                'ai_sentiment': sentiment,
                'basic_sentiment': basic_sentiment,
                'timestamp': datetime.now().isoformat()
            }
            st.session_state.sentiment_analytics['comparisons'].append(comparison)
            save_analytics(st.session_state.sentiment_analytics)
        
        return result
            
    except Exception as e:
        if st.session_state.get('debug_mode', False):
            st.error(f"AI sentiment analysis failed: {str(e)}")
        return basic_sentiment_analysis(f"{title} {summary}")

def basic_sentiment_analysis(text):
    """Basic sentiment analysis using keyword matching"""
    if not text or not isinstance(text, str):
        return "Neutral", 0.0
    
    text_lower = text.lower()
    
    positive_keywords = ['up', 'gain', 'profit', 'bullish', 'rally', 'strong', 'positive', 'upgrade', 
                        'outperform', 'beat', 'good', 'great', 'excellent', 'record', 'high', 'boom', 
                        'surge', 'growth', 'rise', 'expansion', 'breakthrough', 'innovation']
    negative_keywords = ['down', 'loss', 'bearish', 'slump', 'weak', 'negative', 'downgrade', 
                        'underperform', 'miss', 'bad', 'terrible', 'poor', 'plunge', 'drop', 'crisis', 
                        'fear', 'fall', 'decline', 'lawsuit', 'investigation']
    
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

def fetch_ticker_news_yfinance(tickers_string, append_to_existing=False):
    """Fetches news for a list of tickers using yfinance."""
    if not tickers_string:
        return []
    
    tickers_list = [ticker.strip().upper() for ticker in tickers_string.split(',')]
    all_news = []
    ticker_prices = {}
    
    fetch_timestamp = datetime.now()
    fetch_id = fetch_timestamp.strftime("%Y%m%d_%H%M%S")

    # Fetch current prices for all tickers
    for ticker in tickers_list:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            current_price = info.get('currentPrice') or info.get('regularMarketPrice', 0)
            previous_close = info.get('previousClose') or info.get('regularMarketPreviousClose', 0)
            
            pre_market_price = info.get('preMarketPrice', None)
            post_market_price = info.get('postMarketPrice', None)
            
            regular_volume = info.get('regularMarketVolume', 0)
            avg_volume = info.get('averageVolume', 0)
            volume_ratio = regular_volume / avg_volume if avg_volume > 0 else 0
            
            if current_price and previous_close:
                change_percent = ((current_price - previous_close) / previous_close) * 100
                ticker_prices[ticker] = {
                    'current_price': current_price,
                    'previous_close': previous_close,
                    'change_percent': change_percent,
                    'change_dollar': current_price - previous_close,
                    'pre_market_price': pre_market_price,
                    'post_market_price': post_market_price,
                    'pre_market_change': ((pre_market_price - previous_close) / previous_close * 100) if pre_market_price else None,
                    'post_market_change': ((post_market_price - current_price) / current_price * 100) if post_market_price else None,
                    'volume': regular_volume,
                    'avg_volume': avg_volume,
                    'volume_ratio': volume_ratio,
                    'unusual_volume': volume_ratio > 2.0
                }
        except:
            ticker_prices[ticker] = None

    # Get news_source from select box (it's set in the UI before this function is called)
    news_source = st.session_state.get('news_source', 'Yahoo Finance (Free)')
    debug_mode = st.session_state.get('debug_mode', False)

    # Handle DataManager Enhanced mode
    if news_source == "DataManager Enhanced" and data_manager_instance:
        # Use DataManager's built-in news sentiment feature
        for ticker in tickers_list:
            try:
                dm_news = data_manager_instance.get_latest_news_sentiment(ticker, debug_mode=debug_mode)
                if dm_news:
                    # Convert DataManager format to our format
                    formatted_article = {
                        'Title': dm_news['headline'],
                        'Link': dm_news['link'],
                        'Date': dm_news['date'],
                        'Source': dm_news['source'],
                        'Ticker': ticker,
                        'Summary': '',  # DataManager doesn't provide summary
                        'Parsed_Date': datetime.now(),  # Use current time as approximation
                        'Price_Data': ticker_prices.get(ticker),
                        'Fetch_Time': fetch_timestamp,
                        'Fetch_ID': fetch_id,
                        'Article_ID': f"{ticker}_{hash(dm_news['headline'])}_{dm_news['date']}",
                        'Cached_Sentiment': dm_news['label'],  # Pre-calculated sentiment
                        'Cached_Score': dm_news['score']
                    }
                    all_news.append(formatted_article)
                    
                    if debug_mode:
                        st.caption(f"✅ DataManager fetched news for {ticker}: {dm_news['label']} ({dm_news['score']:.2f})")
            except Exception as e:
                if debug_mode:
                    st.error(f"DataManager fetch failed for {ticker}: {e}")
                # Fall back to regular yfinance will happen below
        
        # If we got news from DataManager, sort and return
        if all_news:
            all_news.sort(key=lambda x: x.get('Parsed_Date', datetime.min), reverse=True)
            return all_news
        # Otherwise fall through to Yahoo Finance
    
    # Regular Yahoo Finance fetching
    for ticker in tickers_list:
        try:
            stock = yf.Ticker(ticker)
            news_data = stock.news
            
            if news_data:
                for article in news_data:
                    content = article.get('content', {})
                    title = content.get('title', 'No Title')
                    
                    link_data = content.get('clickThroughUrl') or content.get('canonicalUrl') or {}
                    link = link_data.get('url', '#')
                    
                    pub_date = content.get('pubDate')
                    if pub_date:
                        try:
                            date_obj = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
                            date_str = date_obj.strftime('%Y-%m-%d %H:%M:%S')
                        except:
                            date_str = pub_date
                            date_obj = datetime.min
                    else:
                        date_str = 'No Date'
                        date_obj = datetime.min
                    
                    provider = content.get('provider', {})
                    source = provider.get('displayName', 'Unknown')
                    
                    summary = content.get('summary', content.get('description', ''))
                    if summary:
                        soup = BeautifulSoup(summary, 'html.parser')
                        summary = soup.get_text()
                    
                    formatted_article = {
                        'Title': title,
                        'Link': link,
                        'Date': date_str,
                        'Source': source,
                        'Ticker': ticker,
                        'Summary': summary,
                        'Parsed_Date': date_obj,
                        'Price_Data': ticker_prices.get(ticker),
                        'Fetch_Time': fetch_timestamp,
                        'Fetch_ID': fetch_id,
                        'Article_ID': f"{ticker}_{hash(title)}_{date_str}"
                    }
                    all_news.append(formatted_article)
                    
        except Exception as e:
            st.error(f"Error fetching news for {ticker}: {str(e)}")
            continue
    
    all_news.sort(key=lambda x: x.get('Parsed_Date', datetime.min), reverse=True)
    return all_news

# --- UI Elements ---
st.header("📰 Fetch News")
st.info("📌 **Note:** This news feed uses Yahoo Finance API which provides free, real-time market news.")

# Default tickers
default_tickers = "AAPL,TSLA,GOOGL"
tickers_input = st.text_input("Enter stock tickers (comma-separated):", value=default_tickers, key="news_tickers_input")

# News source selector
news_source = st.selectbox(
    "Select News Source:",
    ["Yahoo Finance (Free)", "DataManager Enhanced", "Finviz (Requires API Key - Not Active)"],
    index=0,
    help="DataManager Enhanced uses caching and may include additional sources."
)
st.session_state['news_source'] = news_source

# Debug mode and analytics
col1, col2 = st.columns(2)
with col1:
    debug_mode = st.checkbox("Debug Mode", value=False, help="Show raw data structure")
    st.session_state['debug_mode'] = debug_mode
with col2:
    show_analytics = st.checkbox("Show AI Analytics", value=False, help="Track AI sentiment performance")

# Show cached news status
if 'news_articles' in st.session_state and st.session_state.get('news_articles'):
    cached_tickers = sorted(list(set(article.get('Ticker', 'Unknown') for article in st.session_state['news_articles'])))
    st.info(f"📊 **Cached News**: {len(st.session_state['news_articles'])} articles from: {', '.join(cached_tickers)}")

# Fetch options
col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    fetch_button = st.button("🔄 Fetch Fresh News", key="fetch_news_button", use_container_width=True)
with col2:
    append_mode = st.checkbox("Append to existing", value=True, help="Add new articles to existing cache instead of replacing")
    st.session_state['append_mode'] = append_mode
with col3:
    if st.button("🗑️ Clear All", key="clear_cache_button"):
        if 'news_articles' in st.session_state:
            del st.session_state['news_articles']
        if 'sentiment_cache' in st.session_state:
            del st.session_state['sentiment_cache']
        st.rerun()

# Fetch news
if fetch_button:
    if tickers_input:
        with st.spinner("Fetching news articles..."):
            if news_source in ["Yahoo Finance (Free)", "DataManager Enhanced"]:
                news_articles = fetch_ticker_news_yfinance(tickers_input, append_to_existing=append_mode)
                
                # Handle append mode
                if append_mode and 'news_articles' in st.session_state and st.session_state.get('news_articles'):
                    existing_articles = st.session_state['news_articles']
                    # Create a set of existing article IDs to avoid duplicates
                    existing_ids = set()
                    for article in existing_articles:
                        if article.get('Article_ID'):
                            existing_ids.add(article['Article_ID'])
                    
                    new_articles_to_add = []
                    for article in news_articles:
                        if article.get('Article_ID') and article['Article_ID'] not in existing_ids:
                            new_articles_to_add.append(article)
                    
                    # Combine articles
                    all_articles = existing_articles + new_articles_to_add
                    
                    # Sort by date - handle both datetime objects and strings
                    def get_sort_date(article):
                        return ensure_datetime(article.get('Parsed_Date', datetime.min))
                    
                    all_articles.sort(key=get_sort_date, reverse=True)
                    
                    st.success(f"✅ Added {len(new_articles_to_add)} new articles to existing {len(existing_articles)} articles")
                    news_articles = all_articles
                else:
                    st.success(f"✅ Fetched {len(news_articles)} articles for {tickers_input}")
                
                if news_articles:
                    st.session_state['news_articles'] = news_articles
                    st.session_state['last_fetch_time'] = datetime.now()
                    
                    # Save to cache
                    if data_manager_instance:
                        news_cache_file = data_manager_instance.cache_dir / "news_articles_cache.json"
                        try:
                            cache_data = {
                                'timestamp': datetime.now().isoformat(),
                                'articles': news_articles
                            }
                            with open(news_cache_file, 'w') as f:
                                json.dump(cache_data, f, default=str)
                        except Exception as e:
                            if debug_mode:
                                st.error(f"Failed to cache news: {e}")
                else:
                    st.warning("No news articles found for the given tickers.")
            else:
                st.warning(f"{news_source} is not currently active.")
    else:
        st.warning("Please enter at least one ticker.")

st.markdown("---")

# Display news feed
if 'news_articles' in st.session_state and st.session_state['news_articles']:
    st.header("📊 News Feed")
    
    total_articles = len(st.session_state['news_articles'])
    unique_tickers = len(set(article['Ticker'] for article in st.session_state['news_articles']))
    st.metric("Total Articles", total_articles, f"from {unique_tickers} ticker(s)")
    
    # Sorting and filtering options
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        unique_tickers_in_news = sorted(list(set(article['Ticker'] for article in st.session_state.news_articles if 'Ticker' in article)))
        selected_ticker_filter = st.selectbox("Filter by Ticker:", ["All"] + unique_tickers_in_news, key="news_ticker_filter")
    
    with col2:
        selected_sentiment_filter = st.selectbox("Filter by Sentiment:", ["All", "Positive", "Neutral", "Negative"], key="news_sentiment_filter")
    
    with col3:
        sort_by = st.selectbox("Sort by:", ["Fetch Time (Recent First)", "Newest First", "Oldest First", "Most Positive", "Most Negative"], key="news_sort", index=0)
    
    with col4:
        articles_to_show = st.slider("Articles to display:", min_value=10, max_value=100, value=30, step=10)
    
    # Apply sorting
    articles_to_display = st.session_state.news_articles.copy()
    
    if sort_by == "Fetch Time (Recent First)":
        # Sort by fetch time - most recently fetched first (green at top, yellow middle, gray/red at bottom)
        def get_fetch_time_for_sort(article):
            fetch_time = article.get('Fetch_Time', datetime.min)
            return ensure_datetime(fetch_time)
        
        articles_to_display.sort(key=get_fetch_time_for_sort, reverse=True)
    elif sort_by == "Newest First":
        articles_to_display.sort(key=lambda x: ensure_datetime(x.get('Parsed_Date', datetime.min)), reverse=True)
    elif sort_by == "Oldest First":
        articles_to_display.sort(key=lambda x: ensure_datetime(x.get('Parsed_Date', datetime.min)))
    elif sort_by in ["Most Positive", "Most Negative"]:
        for article in articles_to_display:
            if 'Sentiment_Score' not in article:
                _, score = basic_sentiment_analysis(f"{article.get('Title', '')} {article.get('Summary', '')}")
                article['Sentiment_Score'] = score
        
        if sort_by == "Most Positive":
            articles_to_display.sort(key=lambda x: x.get('Sentiment_Score', 0), reverse=True)
        else:
            articles_to_display.sort(key=lambda x: x.get('Sentiment_Score', 0))

    # Display articles
    displayed_count = 0
    sentiment_distribution = {'Positive': 0, 'Negative': 0, 'Neutral': 0}
    
    for article in articles_to_display:
        if displayed_count >= articles_to_show:
            break

        title = article.get('Title', 'No Title')
        link = article.get('Link', '#')
        date_str = article.get('Date', 'No Date')
        source = article.get('Source', 'No Source')
        ticker_symbol = article.get('Ticker', 'N/A')
        summary = article.get('Summary', '')

        # Analyze sentiment
        if 'Cached_Sentiment' in article and article['Cached_Sentiment']:
            sentiment_label = article['Cached_Sentiment']
            sentiment_score = article.get('Cached_Score', 0)
        else:
            if use_ai_sentiment:
                sentiment_label, sentiment_score = get_ai_sentiment(title, summary, ticker_symbol)
            else:
                sentiment_label, sentiment_score = basic_sentiment_analysis(f"{title} {summary}")
            
            article['Cached_Sentiment'] = sentiment_label
            article['Cached_Score'] = sentiment_score
        
        sentiment_distribution[sentiment_label] += 1

        # Apply filters
        if selected_ticker_filter != "All" and ticker_symbol != selected_ticker_filter:
            continue
        if selected_sentiment_filter != "All" and sentiment_label != selected_sentiment_filter:
            continue
        
        # Display article
        with st.container(border=True):
            # Header with ticker and fetch time
            col_ticker, col_fetch = st.columns([3, 1])
            with col_ticker:
                st.markdown(f"**{ticker_symbol}** | {source}")
            with col_fetch:
                if 'Fetch_Time' in article:
                    try:
                        fetch_time = ensure_datetime(article['Fetch_Time'])
                        
                        if fetch_time != datetime.min:
                            fetch_age = datetime.now() - fetch_time
                            hours = fetch_age.total_seconds() / 3600
                            
                            if fetch_age.total_seconds() < 300:  # Less than 5 minutes
                                st.caption("🟢 Just fetched")
                            elif hours < 1:  # Less than 1 hour
                                st.caption(f"🟡 {int(fetch_age.total_seconds() / 60)}m ago")
                            elif hours < 24:  # Less than 24 hours
                                st.caption(f"🟠 {int(hours)}h ago")
                            else:  # More than 24 hours
                                days = int(hours / 24)
                                st.caption(f"🔴 {days}d ago")
                    except:
                        st.caption("⚪ Fetched")
            
            # Title
            st.markdown(f"### [{title}]({link})")
            
            # Metadata
            meta_col1, meta_col2, meta_col3 = st.columns([1, 1, 1])
            
            with meta_col1:
                st.caption(f"🗓️ {date_str}")
                st.caption(f"💹 {ticker_symbol}")
            
            # Price data
            price_data = article.get('Price_Data')
            if price_data:
                with meta_col2:
                    price = price_data['current_price']
                    change_pct = price_data['change_percent']
                    change_dollar = price_data['change_dollar']
                    
                    st.metric(
                        label="Price",
                        value=f"${price:.2f}",
                        delta=f"{change_pct:.2f}% (${change_dollar:.2f})"
                    )
                
                with meta_col3:
                    if sentiment_label == "Positive":
                        st.success(f"↑ {sentiment_label}")
                    elif sentiment_label == "Negative":
                        st.error(f"↓ {sentiment_label}")
                    else:
                        st.info(f"→ {sentiment_label}")
                    
                    st.caption(f"Score: {sentiment_score:.2f}")
            else:
                with meta_col2:
                    st.caption("Price data unavailable")
                with meta_col3:
                    if sentiment_label == "Positive":
                        st.success(f"↑ {sentiment_label}")
                    elif sentiment_label == "Negative":
                        st.error(f"↓ {sentiment_label}")
                    else:
                        st.info(f"→ {sentiment_label}")
            
            # Summary
            if summary:
                st.caption(summary[:200] + "..." if len(summary) > 200 else summary)
            
        displayed_count += 1

    # Sentiment distribution
    if displayed_count > 0:
        st.markdown("---")
        st.subheader("📊 Sentiment Distribution")
        col1, col2, col3 = st.columns(3)
        
        total_analyzed = sum(sentiment_distribution.values())
        with col1:
            positive_pct = (sentiment_distribution['Positive'] / total_analyzed * 100) if total_analyzed > 0 else 0
            st.metric("Positive", f"{sentiment_distribution['Positive']} ({positive_pct:.1f}%)")
        with col2:
            neutral_pct = (sentiment_distribution['Neutral'] / total_analyzed * 100) if total_analyzed > 0 else 0
            st.metric("Neutral", f"{sentiment_distribution['Neutral']} ({neutral_pct:.1f}%)")
        with col3:
            negative_pct = (sentiment_distribution['Negative'] / total_analyzed * 100) if total_analyzed > 0 else 0
            st.metric("Negative", f"{sentiment_distribution['Negative']} ({negative_pct:.1f}%)")

    if displayed_count == 0 and (selected_ticker_filter != "All" or selected_sentiment_filter != "All"):
        st.info("No articles match your current filter criteria. Try adjusting the filters.")

    # Show AI Analytics if enabled
    if show_analytics and use_ai_sentiment:
        st.markdown("---")
        st.subheader("🤖 AI Sentiment Analytics")
        
        col1, col2, col3, col4 = st.columns(4)
        
        # Session stats
        with col1:
            st.metric("Session API Calls", st.session_state.session_api_calls)
        with col2:
            st.metric("Session Tokens", st.session_state.session_tokens)
        with col3:
            total_calls = st.session_state.sentiment_analytics.get('api_calls', 0)
            st.metric("Total API Calls", total_calls)
        with col4:
            cache_hits = st.session_state.sentiment_analytics.get('cache_hits', 0)
            st.metric("Cache Hits", cache_hits)
        
        # Cost estimation
        if st.session_state.sentiment_analytics.get('total_tokens', 0) > 0:
            total_tokens = st.session_state.sentiment_analytics.get('total_tokens', 0)
            # GPT-3.5-turbo pricing (approximate)
            cost_per_1k_tokens = 0.002  # $0.002 per 1K tokens
            estimated_cost = (total_tokens / 1000) * cost_per_1k_tokens
            st.info(f"💰 Estimated Total Cost: ${estimated_cost:.4f} ({total_tokens:,} tokens)")
        
        # Comparison insights
        comparisons = st.session_state.sentiment_analytics.get('comparisons', [])
        if comparisons:
            st.subheader("🔍 AI vs Basic Sentiment Comparisons")
            recent_comparisons = comparisons[-5:]  # Show last 5
            
            for comp in recent_comparisons:
                with st.expander(f"{comp['ticker']} - {comp['title'][:50]}..."):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**AI Sentiment:** {comp['ai_sentiment']}")
                    with col2:
                        st.write(f"**Basic Sentiment:** {comp['basic_sentiment']}")
                    st.caption(f"Time: {comp['timestamp']}")
        
else:
    st.info("👆 Enter tickers and click 'Fetch Fresh News' to get started.")

st.markdown("---")

# Footer
col1, col2 = st.columns(2)
with col1:
    st.markdown("**Data Source:** Yahoo Finance API")
    st.caption("Sentiment analysis powered by " + ("OpenAI GPT-3.5" if use_ai_sentiment else "basic keyword matching"))
with col2:
    if st.button("Clear News Feed", key="clear_news_footer"):
        if 'news_articles' in st.session_state:
            del st.session_state['news_articles']
        if 'sentiment_cache' in st.session_state:
            del st.session_state['sentiment_cache']
        st.rerun()
