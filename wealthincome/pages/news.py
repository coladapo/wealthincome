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
    if data_manager_instance:
        st.caption("DataManager instance successfully imported.")
    else:
        st.warning("DataManager imported as None. Check data_manager.py structure.")
except ImportError as e:
    st.error(f"Could not import data_manager: {e}. Some features might be limited.")
except Exception as e:
    st.error(f"An unexpected error occurred during data_manager import: {e}")
# --- End Imports ---

# --- Page Configuration ---
try:
    st.set_page_config(page_title="🗞️ Market News", layout="wide")
except st.errors.StreamlitAPIException as e:
    if "can only be called once per app" in str(e):
        st.caption("Note: Page config was already set.")
    else:
        raise e

st.title('🗞️ Market News & Sentiment Feed')

# --- OpenAI Configuration ---
# Check if API key is configured
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
                    return json.load(f)
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

# Initialize Session State for Analytics
if 'sentiment_analytics' not in st.session_state:
    st.session_state.sentiment_analytics = load_analytics()
else:
    # Always reload from file to get the latest counts
    loaded_analytics = load_analytics()
    # Merge with current session data
    if loaded_analytics['api_calls'] > st.session_state.sentiment_analytics['api_calls']:
        st.session_state.sentiment_analytics = loaded_analytics

if 'sentiment_cache' not in st.session_state:
    st.session_state.sentiment_cache = {}

# --- Helper Functions ---

@st.cache_data(ttl=3600)  # Cache for 1 hour
def get_cached_sentiment(title, summary, ticker):
    """Cache wrapper for sentiment analysis"""
    cache_key = f"{ticker}:{hash(title + summary)}"
    return cache_key

def get_ai_sentiment(title, summary, ticker):
    """
    Use OpenAI to analyze sentiment specifically for the given ticker
    """
    if not use_ai_sentiment:
        return basic_sentiment_analysis(f"{title} {summary}")
    
    # Check cache first
    cache_key = get_cached_sentiment(title, summary, ticker)
    if cache_key in st.session_state.sentiment_cache:
        st.session_state.sentiment_analytics['cache_hits'] += 1
        return st.session_state.sentiment_cache[cache_key]
    
    try:
        from openai import OpenAI
        
        # Initialize client with API key
        client = OpenAI(api_key=openai_api_key)
        
        # Enhanced prompt with more specific examples
        prompt = f"""
        Analyze this news article's sentiment specifically for {ticker} stock:
        
        Title: {title}
        Summary: {summary[:500]}
        
        Consider:
        1. Does this news directly impact {ticker}?
        2. Is it good or bad for {ticker} shareholders?
        3. Would a trader want to buy, sell, or hold based on this news?
        4. Consider market context - expansion news during recession might still be positive
        5. Regulatory news - approvals are positive, investigations are negative
        6. Competitive landscape - losing market share is negative even if revenue grows
        
        Examples for better accuracy:
        - "{ticker} faces supply chain challenges but secures alternative suppliers" = Neutral (problem + solution)
        - "{ticker} CEO steps down amid scandal" = Negative (leadership instability)
        - "{ticker} beats earnings despite market downturn" = Positive (outperformance)
        - "Analyst maintains hold rating on {ticker}" = Neutral (no change)
        - "{ticker} announces $10B buyback program" = Positive (shareholder value)
        - "Industry regulation may impact {ticker}" = Negative (uncertainty)
        - "{ticker} expands into new markets despite economic headwinds" = Positive (growth)
        
        Respond with ONLY ONE of these three words: Positive, Negative, or Neutral
        """
        
        # Track API call
        st.session_state.sentiment_analytics['api_calls'] += 1
        
        # NEW API FORMAT with token tracking
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a financial sentiment analyzer with deep understanding of market dynamics. You must respond with EXACTLY one word: Positive, Negative, or Neutral. Consider the specific impact on the mentioned ticker, not general market sentiment."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=10
        )
        
        # Track token usage - get actual usage from response
        if hasattr(response, 'usage'):
            tokens_used = response.usage.total_tokens
            st.session_state.sentiment_analytics['total_tokens'] += tokens_used
            if st.session_state.get('debug_mode', False):
                st.caption(f"🔢 This request used {tokens_used} tokens")
        
        # Save analytics immediately after each call
        save_analytics(st.session_state.sentiment_analytics)
        
        # Extract and clean the response
        sentiment_text = response.choices[0].message.content.strip().lower()
        
        # Handle various response formats
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
            # Fallback if AI gives unexpected response
            if st.session_state.get('debug_mode', False):
                st.warning(f"Unexpected AI response: '{sentiment_text}' for {ticker}")
            return basic_sentiment_analysis(f"{title} {summary}")
        
        # Cache the result
        result = (sentiment, score)
        st.session_state.sentiment_cache[cache_key] = result
        
        # Compare with basic sentiment for analytics
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
        
        # Log the analysis in debug mode
        if st.session_state.get('debug_mode', False):
            st.caption(f"🤖 AI Analysis for {ticker}: '{title[:50]}...' → {sentiment}")
            if sentiment != basic_sentiment:
                st.caption(f"📊 Basic would have said: {basic_sentiment}")
            
        return result
            
    except Exception as e:
        if st.session_state.get('debug_mode', False):
            st.error(f"AI sentiment analysis failed: {str(e)}")
        return basic_sentiment_analysis(f"{title} {summary}")

def basic_sentiment_analysis(text):
    """
    Basic sentiment analysis using keyword matching
    """
    if not text or not isinstance(text, str):
        return "Neutral", 0.0
    
    text_lower = text.lower()
    
    positive_keywords = ['up', 'gain', 'profit', 'bullish', 'rally', 'strong', 'positive', 'upgrade', 
                        'outperform', 'beat', 'good', 'great', 'excellent', 'record', 'high', 'boom', 
                        'surge', 'growth', 'rise', 'expansion', 'breakthrough', 'innovation']
    negative_keywords = ['down', 'loss', 'bearish', 'slump', 'weak', 'negative', 'downgrade', 
                        'underperform', 'miss', 'bad', 'terrible', 'poor', 'plunge', 'drop', 'crisis', 
                        'fear', 'fall', 'decline', 'isn\'t worth', 'lost', 'lawsuit', 'investigation']
    
    positive_score = sum(1 for keyword in positive_keywords if keyword in text_lower)
    negative_score = sum(1 for keyword in negative_keywords if keyword in text_lower)
    
    total_keywords = positive_score + negative_score
    if total_keywords == 0:
        return "Neutral", 0.0
    
    score = (positive_score - negative_score) / total_keywords

    if score > 0.1: return "Positive", score
    elif score < -0.1: return "Negative", score
    else: return "Neutral", score

@st.cache_data(ttl=900) # Cache for 15 minutes
def fetch_ticker_news_yfinance(tickers_string):
    """
    Fetches news for a list of tickers using yfinance.
    """
    if not tickers_string:
        return []
    
    tickers_list = [ticker.strip().upper() for ticker in tickers_string.split(',')]
    all_news = []
    ticker_prices = {}

    # Fetch current prices and extended hours data for all tickers
    for ticker in tickers_list:
        try:
            stock = yf.Ticker(ticker)
            # Get current price and daily change
            info = stock.info
            current_price = info.get('currentPrice') or info.get('regularMarketPrice', 0)
            previous_close = info.get('previousClose') or info.get('regularMarketPreviousClose', 0)
            
            # Get pre/post market data
            pre_market_price = info.get('preMarketPrice', None)
            post_market_price = info.get('postMarketPrice', None)
            
            # Get volume data
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
                    'unusual_volume': volume_ratio > 2.0  # Flag if volume is 2x normal
                }
        except:
            ticker_prices[ticker] = None

    for ticker in tickers_list:
        try:
            # Alternative: Use DataManager's news method if available
            if data_manager_instance and hasattr(data_manager_instance, 'get_latest_news_sentiment'):
                try:
                    dm_news = data_manager_instance.get_latest_news_sentiment(ticker, debug_mode=st.session_state.get('debug_mode', False))
                    if dm_news:
                        st.caption(f"📡 Using DataManager news for {ticker}")
                        # Convert DataManager format to our format
                        formatted_article = {
                            'Title': dm_news['headline'],
                            'Link': dm_news['link'],
                            'Date': dm_news['date'],
                            'Source': dm_news['source'],
                            'Ticker': ticker,
                            'Summary': '',  # DataManager doesn't provide summary
                            'Parsed_Date': datetime.now(),  # Approximate
                            'Price_Data': ticker_prices.get(ticker),
                            'DM_Sentiment': dm_news['label'],  # Store DataManager's sentiment
                            'DM_Score': dm_news['score']
                        }
                        all_news.append(formatted_article)
                        continue
                except Exception as e:
                    if st.session_state.get('debug_mode', False):
                        st.warning(f"DataManager news fetch failed for {ticker}: {str(e)}. Using regular yfinance.")
                    # Continue to regular yfinance method below
            
            # Fallback to regular yfinance method
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
                        'Price_Data': ticker_prices.get(ticker)
                    }
                    all_news.append(formatted_article)
                    
        except Exception as e:
            st.error(f"Error fetching news for {ticker}: {str(e)}")
            continue
    
    # Sort by date (newest first)
    all_news.sort(key=lambda x: x.get('Parsed_Date', datetime.min), reverse=True)
    
    return all_news

# --- UI Elements ---
st.header("📰 Fetch News")

# Info box about the news source
st.info("📌 **Note:** This news feed uses Yahoo Finance API which provides free, real-time market news. DataManager integration enabled for enhanced caching.")

# Show cache directory location if DataManager is available
if data_manager_instance:
    with st.expander("📁 Data Storage Info"):
        st.write(f"**Cache Directory**: `{data_manager_instance.cache_dir}`")
        st.write(f"**Analytics File**: `{data_manager_instance.cache_dir / 'sentiment_analytics.json'}`")
        if st.button("View Analytics File Content"):
            analytics_file = data_manager_instance.cache_dir / "sentiment_analytics.json"
            if analytics_file.exists():
                with open(analytics_file, 'r') as f:
                    st.json(json.load(f))
            else:
                st.info("No analytics file found yet. It will be created after first AI analysis.")

default_tickers = "AAPL,TSLA,GOOGL" # Default tickers
tickers_input = st.text_input("Enter stock tickers (comma-separated):", value=default_tickers, key="news_tickers_input")

# Add a news source selector for future expansion
news_source = st.selectbox(
    "Select News Source:",
    ["Yahoo Finance (Free)", "DataManager Enhanced", "Finviz (Requires API Key - Not Active)"],
    index=0,
    help="DataManager Enhanced uses caching and may include additional sources."
)

# Debug mode and analytics on a new line
col1, col2 = st.columns(2)
with col1:
    debug_mode = st.checkbox("Debug Mode", value=False, help="Show raw data structure")
    st.session_state['debug_mode'] = debug_mode
with col2:
    show_analytics = st.checkbox("Show AI Analytics", value=False, help="Track AI sentiment performance")

# Analytics Dashboard
if show_analytics and use_ai_sentiment:
    with st.expander("📊 AI Sentiment Analytics Dashboard", expanded=True):
        # Add sync status indicator
        col1, col2, col3, col4 = st.columns(4)
        
        analytics = st.session_state.sentiment_analytics
        
        # Check if we need to reload from file
        if st.button("🔄 Refresh Analytics", help="Reload analytics from file"):
            st.session_state.sentiment_analytics = load_analytics()
            analytics = st.session_state.sentiment_analytics
            st.rerun()
        
        with col1:
            st.metric("API Calls", analytics['api_calls'])
            avg_tokens = analytics['total_tokens'] / analytics['api_calls'] if analytics['api_calls'] > 0 else 0
            st.caption(f"Avg tokens/call: {avg_tokens:.0f}")
        
        with col2:
            st.metric("Total Tokens", f"{analytics['total_tokens']:,}")
            cost_estimate = (analytics['total_tokens'] / 1000) * 0.002  # GPT-3.5 pricing
            st.caption(f"Est. cost: ${cost_estimate:.4f}")
        
        with col3:
            st.metric("Cache Hits", analytics['cache_hits'])
            cache_rate = (analytics['cache_hits'] / (analytics['api_calls'] + analytics['cache_hits']) * 100) if (analytics['api_calls'] + analytics['cache_hits']) > 0 else 0
            st.caption(f"Cache rate: {cache_rate:.1f}%")
        
        with col4:
            disagreements = len(analytics['comparisons'])
            st.metric("AI vs Basic Disagreements", disagreements)
            if analytics['api_calls'] > 0:
                st.caption(f"Disagreement rate: {(disagreements/analytics['api_calls']*100):.1f}%")
        
        # Add OpenAI sync check
        st.info(f"💡 **Tip**: Analytics are saved to `{data_manager_instance.cache_dir if data_manager_instance else 'cache'}/sentiment_analytics.json`")
        
        # Manual sync option
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔧 Manual Sync", help="Manually update counts if they don't match OpenAI dashboard"):
                manual_calls = st.number_input("Enter actual API calls from OpenAI:", value=analytics['api_calls'], key="manual_calls")
                manual_tokens = st.number_input("Enter actual tokens from OpenAI:", value=analytics['total_tokens'], key="manual_tokens")
                if st.button("Update Counts"):
                    st.session_state.sentiment_analytics['api_calls'] = manual_calls
                    st.session_state.sentiment_analytics['total_tokens'] = manual_tokens
                    save_analytics(st.session_state.sentiment_analytics)
                    st.success("✅ Counts updated!")
                    st.rerun()
        
        # Session info
        if 'session_start' in analytics:
            st.caption(f"Tracking since: {analytics['session_start']}")
        
        # Show recent disagreements
        if analytics['comparisons']:
            st.subheader("Recent AI vs Basic Sentiment Disagreements")
            recent_comparisons = analytics['comparisons'][-5:]  # Last 5
            for comp in reversed(recent_comparisons):
                col1, col2, col3 = st.columns([3, 1, 1])
                with col1:
                    st.text(f"{comp['ticker']}: {comp['title']}...")
                with col2:
                    if comp['ai_sentiment'] == 'Positive':
                        st.success(f"AI: {comp['ai_sentiment']}")
                    elif comp['ai_sentiment'] == 'Negative':
                        st.error(f"AI: {comp['ai_sentiment']}")
                    else:
                        st.info(f"AI: {comp['ai_sentiment']}")
                with col3:
                    st.caption(f"Basic: {comp['basic_sentiment']}")

# Add test OpenAI button if debug mode is on
if debug_mode and use_ai_sentiment:
    if st.button("Test OpenAI Connection"):
        try:
            from openai import OpenAI
            client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
            
            # Simple test
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": "Say 'API working'"}],
                max_tokens=10
            )
            st.success(f"✅ API Response: {response.choices[0].message.content}")
        except Exception as e:
            st.error(f"❌ Error: {type(e).__name__}: {str(e)}")

if st.button("Fetch News", key="fetch_news_button"):
    if tickers_input:
        with st.spinner("Fetching news articles..."):
            if news_source in ["Yahoo Finance (Free)", "DataManager Enhanced"]:
                news_articles = fetch_ticker_news_yfinance(tickers_input)
                
                # Debug mode: show comprehensive information
                if debug_mode and news_articles and len(news_articles) > 0:
                    with st.expander("🔍 Debug Information", expanded=True):
                        st.write("### First Article Raw Data Structure:")
                        first_article = news_articles[0]
                        
                        # Show raw article data
                        debug_data = {
                            "Title": first_article.get('Title'),
                            "Date": first_article.get('Date'),
                            "Parsed_Date": str(first_article.get('Parsed_Date')),
                            "Source": first_article.get('Source'),
                            "Ticker": first_article.get('Ticker'),
                            "Link": first_article.get('Link'),
                            "Summary_Length": len(first_article.get('Summary', '')),
                            "Price_Data": first_article.get('Price_Data'),
                            "DM_Sentiment": first_article.get('DM_Sentiment', 'N/A'),
                            "DM_Score": first_article.get('DM_Score', 'N/A')
                        }
                        st.json(debug_data)
                        
                        # Show fetching statistics
                        st.write("### Fetching Statistics:")
                        tickers = [t.strip().upper() for t in tickers_input.split(',')]
                        ticker_counts = {}
                        for article in news_articles:
                            ticker = article.get('Ticker', 'Unknown')
                            ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1
                        
                        col1, col2 = st.columns(2)
                        with col1:
                            st.write("**Articles per Ticker:**")
                            for ticker, count in ticker_counts.items():
                                st.write(f"- {ticker}: {count} articles")
                        
                        with col2:
                            st.write("**Price Data Status:**")
                            price_success = sum(1 for a in news_articles if a.get('Price_Data') is not None)
                            st.write(f"- Success: {price_success}/{len(news_articles)}")
                            st.write(f"- Failed: {len(news_articles) - price_success}/{len(news_articles)}")
                        
                        # Show date range of news
                        st.write("### News Date Range:")
                        dates = [a['Parsed_Date'] for a in news_articles if a.get('Parsed_Date') and a['Parsed_Date'] != datetime.min]
                        if dates:
                            oldest = min(dates)
                            newest = max(dates)
                            st.write(f"- Oldest: {oldest}")
                            st.write(f"- Newest: {newest}")
                            st.write(f"- Span: {(newest - oldest).days} days")
            else:
                st.warning(f"{news_source} is not currently active. Please use Yahoo Finance or DataManager Enhanced.")
                news_articles = []
        
        if news_articles:
            st.session_state['news_articles'] = news_articles
            st.success(f"✅ Fetched {len(news_articles)} articles for {tickers_input}.")
        else:
            st.warning("No news articles found for the given tickers. Try different tickers or check back later.")
            if 'news_articles' in st.session_state:
                st.session_state['news_articles'] = []
    else:
        st.warning("Please enter at least one ticker.")

st.markdown("---")

if 'news_articles' in st.session_state and st.session_state['news_articles']:
    st.header("📊 News Feed")
    
    # Add a summary box
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
        sort_by = st.selectbox("Sort by:", ["Newest First", "Oldest First", "Most Positive", "Most Negative", "High Volume First"], key="news_sort")
    
    with col4:
        articles_to_show = st.slider("Articles to display:", min_value=10, max_value=100, value=30, step=10)
    
    # Apply sorting
    articles_to_display = st.session_state.news_articles.copy()
    
    if sort_by == "Newest First":
        articles_to_display.sort(key=lambda x: x.get('Parsed_Date', datetime.min), reverse=True)
    elif sort_by == "Oldest First":
        articles_to_display.sort(key=lambda x: x.get('Parsed_Date', datetime.min))
    elif sort_by == "Most Positive":
        # First add sentiment scores if not present
        for article in articles_to_display:
            if 'Sentiment_Score' not in article:
                _, score = basic_sentiment_analysis(f"{article.get('Title', '')} {article.get('Summary', '')}")
                article['Sentiment_Score'] = score
        articles_to_display.sort(key=lambda x: x.get('Sentiment_Score', 0), reverse=True)
    elif sort_by == "Most Negative":
        for article in articles_to_display:
            if 'Sentiment_Score' not in article:
                _, score = basic_sentiment_analysis(f"{article.get('Title', '')} {article.get('Summary', '')}")
                article['Sentiment_Score'] = score
        articles_to_display.sort(key=lambda x: x.get('Sentiment_Score', 0))
    elif sort_by == "High Volume First":
        articles_to_display.sort(key=lambda x: x.get('Price_Data', {}).get('volume_ratio', 0) if x.get('Price_Data') else 0, reverse=True)

    displayed_count = 0
    sentiment_distribution = {'Positive': 0, 'Negative': 0, 'Neutral': 0}
    
    for article in articles_to_display:
        if displayed_count >= articles_to_show:
            st.caption(f"Showing {articles_to_show} articles. Adjust slider to see more.")
            break

        title = article.get('Title', 'No Title')
        link = article.get('Link', '#')
        date_str = article.get('Date', 'No Date')
        source = article.get('Source', 'No Source')
        ticker_symbol = article.get('Ticker', 'N/A')
        summary = article.get('Summary', '')

        # Check if DataManager already provided sentiment
        if 'DM_Sentiment' in article and article['DM_Sentiment']:
            sentiment_label = article['DM_Sentiment']
            sentiment_score = article.get('DM_Score', 0)
        else:
            # Analyze sentiment - Use AI if available, otherwise basic
            if use_ai_sentiment:
                sentiment_label, sentiment_score = get_ai_sentiment(title, summary, ticker_symbol)
            else:
                combined_text = f"{title} {summary}"
                sentiment_label, sentiment_score = basic_sentiment_analysis(combined_text)
        
        # Track sentiment distribution
        sentiment_distribution[sentiment_label] += 1

        if selected_ticker_filter != "All" and ticker_symbol != selected_ticker_filter: continue
        if selected_sentiment_filter != "All" and sentiment_label != selected_sentiment_filter: continue
        
        with st.container(border=True):
            # Title with link
            st.markdown(f"### [{title}]({link})")
            
            # OPPORTUNITY WINDOW indicator
            if 'Parsed_Date' in article and article['Parsed_Date'] != datetime.min:
                try:
                    article_date = article['Parsed_Date'].replace(tzinfo=None) if article['Parsed_Date'].tzinfo else article['Parsed_Date']
                    current_time = datetime.now()
                    news_age = current_time - article_date
                    minutes_old = news_age.total_seconds() / 60
                    
                    if minutes_old < 15:
                        st.success("🟢 **OPPORTUNITY WINDOW OPEN** - News is fresh! Potential trading opportunity.")
                    elif minutes_old < 30:
                        st.warning("🟡 **OPPORTUNITY CLOSING** - News is 15-30 min old. Move fast if trading.")
                    elif minutes_old < 60:
                        st.info("🔵 **LIKELY PRICED IN** - News is 30-60 min old. Check volume for confirmation.")
                    else:
                        st.error("🔴 **OPPORTUNITY PASSED** - News is over 1 hour old. Already priced in.")
                except:
                    pass
            
            # Price and metadata row with extended hours
            meta_col1, meta_col2, meta_col3 = st.columns([1, 1, 1])
            
            with meta_col1:
                st.caption(f"🗓️ {date_str}")
                st.caption(f"📰 {source}")
                st.caption(f"💹 {ticker_symbol}")
            
            # Price and volume information
            price_data = article.get('Price_Data')
            if price_data:
                with meta_col2:
                    price = price_data['current_price']
                    change_pct = price_data['change_percent']
                    change_dollar = price_data['change_dollar']
                    
                    # Regular market hours
                    if change_pct >= 0:
                        st.metric(
                            label="Regular Hours",
                            value=f"${price:.2f}",
                            delta=f"{change_pct:.2f}% (${change_dollar:.2f})"
                        )
                    else:
                        st.metric(
                            label="Regular Hours",
                            value=f"${price:.2f}",
                            delta=f"{change_pct:.2f}% (${change_dollar:.2f})"
                        )
                    
                    # Pre/Post market data
                    if price_data['pre_market_price']:
                        st.caption(f"🌅 Pre-Market: ${price_data['pre_market_price']:.2f} ({price_data['pre_market_change']:+.2f}%)")
                    if price_data['post_market_price']:
                        st.caption(f"🌙 Post-Market: ${price_data['post_market_price']:.2f} ({price_data['post_market_change']:+.2f}%)")
                
                with meta_col3:
                    # Volume analysis
                    if price_data['unusual_volume']:
                        st.error("🔥 UNUSUAL VOLUME")
                        st.caption(f"Volume: {price_data['volume']:,.0f}")
                        st.caption(f"Avg: {price_data['avg_volume']:,.0f}")
                        st.caption(f"Ratio: {price_data['volume_ratio']:.1f}x")
                    else:
                        st.info("Normal Volume")
                        st.caption(f"Ratio: {price_data['volume_ratio']:.1f}x avg")
                    
                    # Sentiment with comparison
                    if sentiment_label == "Positive":
                        st.success(f"↑ {sentiment_label}")
                    elif sentiment_label == "Negative":
                        st.error(f"↓ {sentiment_label}")
                    else:
                        st.info(f"→ {sentiment_label}")
                    
                    # Show confidence indicator
                    if use_ai_sentiment and abs(sentiment_score) > 0.5:
                        st.caption("🎯 High confidence")
                    elif use_ai_sentiment:
                        st.caption("⚖️ Moderate confidence")
            else:
                with meta_col2:
                    st.caption("Price data unavailable")
                with meta_col3:
                    st.caption("Volume data unavailable")
            
            # Summary if available
            if summary:
                st.caption(summary[:200] + "..." if len(summary) > 200 else summary)
            
            # Enhanced price chart with volume
            with st.expander("📊 View Price Action & Volume"):
                try:
                    ticker_obj = yf.Ticker(ticker_symbol)
                    # Get more granular data for news trading
                    hist = ticker_obj.history(period="2d", interval="5m")
                    
                    if not hist.empty:
                        # Create subplots for price and volume
                        from plotly.subplots import make_subplots
                        
                        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                          vertical_spacing=0.03,
                                          row_heights=[0.7, 0.3],
                                          subplot_titles=('Price Action', 'Volume'))
                        
                        # Add candlestick chart
                        fig.add_trace(go.Candlestick(
                            x=hist.index,
                            open=hist['Open'],
                            high=hist['High'],
                            low=hist['Low'],
                            close=hist['Close'],
                            name='Price'
                        ), row=1, col=1)
                        
                        # Add volume bars
                        colors = ['red' if hist['Close'].iloc[i] < hist['Open'].iloc[i] else 'green' 
                                 for i in range(len(hist))]
                        fig.add_trace(go.Bar(
                            x=hist.index,
                            y=hist['Volume'],
                            name='Volume',
                            marker_color=colors
                        ), row=2, col=1)
                        
                        # Add average volume line
                        avg_vol = hist['Volume'].rolling(window=20).mean()
                        fig.add_trace(go.Scatter(
                            x=hist.index,
                            y=avg_vol,
                            name='Avg Volume',
                            line=dict(color='yellow', width=2, dash='dash')
                        ), row=2, col=1)
                        
                        # Add news publication line
                        if 'Parsed_Date' in article and article['Parsed_Date'] != datetime.min:
                            try:
                                news_time = article['Parsed_Date']
                                if news_time.tzinfo is None:
                                    if hist.index[0].tzinfo is not None:
                                        news_time = pytz.UTC.localize(news_time)
                                else:
                                    if hist.index[0].tzinfo is None:
                                        news_time = news_time.replace(tzinfo=None)
                                
                                if hist.index[0] <= news_time <= hist.index[-1]:
                                    fig.add_vline(
                                        x=news_time, 
                                        line_dash="dash", 
                                        line_color="yellow",
                                        annotation_text="News Published",
                                        row=1, col=1
                                    )
                                    fig.add_vline(
                                        x=news_time,
                                        line_dash="dash",
                                        line_color="yellow",
                                        row=2, col=1
                                    )
                            except:
                                pass
                        
                        fig.update_layout(
                            title=f"{ticker_symbol} - 2 Day Price Action (5-min candles)",
                            height=500,
                            showlegend=False,
                            template="plotly_dark"
                        )
                        fig.update_xaxes(title_text="Time", row=2, col=1)
                        fig.update_yaxes(title_text="Price ($)", row=1, col=1)
                        fig.update_yaxes(title_text="Volume", row=2, col=1)
                        
                        st.plotly_chart(fig, use_container_width=True, key=f"chart_{ticker_symbol}_{article.get('Date', '')}_{displayed_count}")
                        
                        # Trading statistics
                        col1, col2, col3, col4 = st.columns(4)
                        
                        # Calculate key levels
                        recent_high = hist['High'].tail(20).max()
                        recent_low = hist['Low'].tail(20).min()
                        current_price = hist['Close'].iloc[-1]
                        
                        col1.metric("Current", f"${current_price:.2f}")
                        col2.metric("Recent High", f"${recent_high:.2f}", 
                                   delta=f"{((current_price - recent_high) / recent_high * 100):.2f}%")
                        col3.metric("Recent Low", f"${recent_low:.2f}",
                                   delta=f"{((current_price - recent_low) / recent_low * 100):.2f}%")
                        
                        # Volume spike detection
                        recent_avg_vol = hist['Volume'].tail(20).mean()
                        last_vol = hist['Volume'].iloc[-1]
                        vol_spike = last_vol / recent_avg_vol if recent_avg_vol > 0 else 0
                        
                        if vol_spike > 3:
                            col4.error(f"🚨 Volume Spike: {vol_spike:.1f}x")
                        elif vol_spike > 2:
                            col4.warning(f"⚠️ High Volume: {vol_spike:.1f}x")
                        else:
                            col4.info(f"Normal Vol: {vol_spike:.1f}x")
                        
                except Exception as e:
                    st.error(f"Could not load price chart: {str(e)}")
            
            # Sentiment score and analysis quality
            col1, col2 = st.columns(2)
            with col1:
                st.caption(f"Sentiment Score: {sentiment_score:.2f}")
            with col2:
                if 'DM_Sentiment' in article:
                    st.caption("📡 DataManager Sentiment")
                elif use_ai_sentiment:
                    st.caption("🤖 AI-Powered Analysis")
                else:
                    st.caption("📊 Basic Keyword Analysis")
            
        displayed_count += 1

    # Show sentiment distribution summary
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
        
        # Market sentiment indicator
        if positive_pct > 60:
            st.success("🚀 Overall Market Sentiment: BULLISH")
        elif negative_pct > 60:
            st.error("📉 Overall Market Sentiment: BEARISH")
        else:
            st.info("⚖️ Overall Market Sentiment: MIXED")

    if displayed_count == 0 and (selected_ticker_filter != "All" or selected_sentiment_filter != "All"):
        st.info("No articles match your current filter criteria. Try adjusting the filters.")
        
elif 'news_articles' in st.session_state and not st.session_state.news_articles:
    st.info("No news articles were found for the specified tickers in the last fetch.")
else:
    st.info("👆 Enter tickers and click 'Fetch News' to see the latest market updates.")

st.markdown("---")

# Footer with additional options
col1, col2 = st.columns(2)
with col1:
    st.markdown("**Data Source:** Yahoo Finance API + DataManager")
    st.caption("Sentiment analysis powered by " + ("OpenAI GPT-3.5" if use_ai_sentiment else "basic keyword matching"))
    if data_manager_instance:
        st.caption(f"Cache location: `{data_manager_instance.cache_dir}`")
with col2:
    if st.button("Clear News Feed", key="clear_news"):
        if 'news_articles' in st.session_state:
            del st.session_state['news_articles']
        if 'sentiment_cache' in st.session_state:
            del st.session_state['sentiment_cache']
        # Don't clear analytics - keep persistent tracking
        st.rerun()

# Export analytics option
if use_ai_sentiment and st.session_state.sentiment_analytics['api_calls'] > 0:
    if st.button("Export Analytics Report", key="export_analytics"):
        analytics_data = {
            'timestamp': datetime.now().isoformat(),
            'api_calls': st.session_state.sentiment_analytics['api_calls'],
            'total_tokens': st.session_state.sentiment_analytics['total_tokens'],
            'cache_hits': st.session_state.sentiment_analytics['cache_hits'],
            'comparisons': st.session_state.sentiment_analytics['comparisons'],
            'cache_directory': str(data_manager_instance.cache_dir) if data_manager_instance else 'N/A'
        }
        
        st.download_button(
            label="Download Analytics JSON",
            data=json.dumps(analytics_data, indent=2, default=str),
            file_name=f"sentiment_analytics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            mime="application/json"
        )
