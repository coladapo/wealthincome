import streamlit as st
import sys
import os
import pandas as pd
from datetime import datetime
import yfinance as yf
import requests
from bs4 import BeautifulSoup
import openai
import json

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
openai_api_key = st.secrets.get("OPENAI_API_KEY", None)
use_ai_sentiment = False

if openai_api_key:
    openai.api_key = openai_api_key
    use_ai_sentiment = True
else:
    st.warning("⚠️ No OpenAI API key found. Using basic sentiment analysis. Add OPENAI_API_KEY to your secrets.toml for AI-powered analysis.")

# --- Helper Functions ---

def get_ai_sentiment(title, summary, ticker):
    """
    Use OpenAI to analyze sentiment specifically for the given ticker
    """
    if not openai_api_key:
        return basic_sentiment_analysis(f"{title} {summary}")
    
    try:
        prompt = f"""
        Analyze this news article's sentiment specifically for {ticker} stock:
        
        Title: {title}
        Summary: {summary}
        
        Provide a JSON response with:
        1. "sentiment": one of "Positive", "Negative", or "Neutral"
        2. "score": a number between -1 (very negative) and 1 (very positive)
        3. "reasoning": a brief explanation (max 50 words)
        4. "impact": "High", "Medium", or "Low" - how much this news impacts {ticker}
        
        Focus on how this news specifically affects {ticker}, not general market sentiment.
        """
        
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a financial analyst providing stock-specific sentiment analysis."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=150
        )
        
        # Parse the response
        result = json.loads(response.choices[0].message.content)
        
        # Store the reasoning for display
        sentiment_label = result.get("sentiment", "Neutral")
        sentiment_score = result.get("score", 0.0)
        reasoning = result.get("reasoning", "")
        impact = result.get("impact", "Medium")
        
        return sentiment_label, sentiment_score, reasoning, impact
        
    except Exception as e:
        st.warning(f"AI sentiment analysis failed for {ticker}: {str(e)}. Using basic analysis.")
        return basic_sentiment_analysis(f"{title} {summary}") + ("", "Medium")

def basic_sentiment_analysis(text):
    """
    Fallback basic sentiment analysis
    """
    if not text or not isinstance(text, str):
        return "Neutral", 0.0
    
    text_lower = text.lower()
    positive_keywords = ['up', 'gain', 'profit', 'bullish', 'rally', 'strong', 'positive', 'upgrade', 
                        'outperform', 'beat', 'good', 'great', 'excellent', 'record', 'high', 'boom', 
                        'surge', 'growth', 'rise']
    negative_keywords = ['down', 'loss', 'bearish', 'slump', 'weak', 'negative', 'downgrade', 
                        'underperform', 'miss', 'bad', 'terrible', 'poor', 'plunge', 'drop', 'crisis', 
                        'fear', 'fall', 'decline']
    
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

    for ticker in tickers_list:
        try:
            # Create a yfinance Ticker object
            stock = yf.Ticker(ticker)
            
            # Get news - this returns a list of dictionaries
            news_data = stock.news
            
            if news_data:
                for article in news_data:
                    # The actual content is nested inside 'content' key
                    content = article.get('content', {})
                    
                    # Extract title and other info from the content
                    title = content.get('title', 'No Title')
                    
                    # Extract link from canonicalUrl or clickThroughUrl
                    link_data = content.get('clickThroughUrl') or content.get('canonicalUrl') or {}
                    link = link_data.get('url', '#')
                    
                    # Handle the date - it's in pubDate field
                    pub_date = content.get('pubDate')
                    if pub_date:
                        try:
                            # Parse ISO format date
                            date_obj = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
                            date_str = date_obj.strftime('%Y-%m-%d %H:%M:%S')
                        except:
                            date_str = pub_date
                            date_obj = datetime.min
                    else:
                        date_str = 'No Date'
                        date_obj = datetime.min
                    
                    # Get provider info
                    provider = content.get('provider', {})
                    source = provider.get('displayName', 'Unknown')
                    
                    # Get summary
                    summary = content.get('summary', content.get('description', ''))
                    # Clean HTML from summary
                    if summary:
                        soup = BeautifulSoup(summary, 'html.parser')
                        summary = soup.get_text()
                    
                    # Standardize the format
                    formatted_article = {
                        'Title': title,
                        'Link': link,
                        'Date': date_str,
                        'Source': source,
                        'Ticker': ticker,
                        'Summary': summary,
                        'Parsed_Date': date_obj
                    }
                    all_news.append(formatted_article)
                    
        except Exception as e:
            st.error(f"Error fetching news for {ticker}: {str(e)}")
            continue
    
    # Sort by date (newest first)
    all_news.sort(key=lambda x: x['Parsed_Date'], reverse=True)
    
    return all_news

# --- UI Elements ---
st.header("📰 Fetch News")

# Info box about the news source
if use_ai_sentiment:
    st.success("✨ **AI-Powered Sentiment Analysis Active** - Using OpenAI for intelligent, context-aware sentiment analysis.")
else:
    st.info("📌 **Note:** Using basic keyword-based sentiment analysis. Add OpenAI API key for smarter analysis.")

default_tickers = "AAPL,TSLA,GOOGL" # Default tickers
tickers_input = st.text_input("Enter stock tickers (comma-separated):", value=default_tickers, key="news_tickers_input")

# Add a news source selector for future expansion
news_source = st.selectbox(
    "Select News Source:",
    ["Yahoo Finance (Free)", "Finviz (Requires API Key - Not Active)", "NewsAPI (Requires API Key - Not Active)"],
    index=0,
    help="Currently only Yahoo Finance is active. Other sources require API keys."
)

# Settings in columns
col1, col2 = st.columns([1, 1])
with col1:
    # Debug mode on a new line
    debug_mode = st.checkbox("Debug Mode", value=False, help="Show raw data structure")
with col2:
    # AI sentiment toggle (only if API key is available)
    if openai_api_key:
        use_ai = st.checkbox("Use AI Sentiment", value=True, help="Use OpenAI for intelligent sentiment analysis")
    else:
        use_ai = False

if st.button("Fetch News", key="fetch_news_button"):
    if tickers_input:
        with st.spinner("Fetching news articles..."):
            if news_source == "Yahoo Finance (Free)":
                news_articles = fetch_ticker_news_yfinance(tickers_input)
                
                # Analyze sentiment for each article
                if news_articles and use_ai and use_ai_sentiment:
                    with st.spinner("Analyzing sentiment with AI..."):
                        for article in news_articles:
                            sentiment_result = get_ai_sentiment(
                                article['Title'], 
                                article['Summary'], 
                                article['Ticker']
                            )
                            if len(sentiment_result) == 4:
                                article['Sentiment'], article['Sentiment_Score'], article['Reasoning'], article['Impact'] = sentiment_result
                            else:
                                article['Sentiment'], article['Sentiment_Score'] = sentiment_result[:2]
                                article['Reasoning'] = ""
                                article['Impact'] = "Medium"
                else:
                    # Use basic sentiment analysis
                    for article in news_articles:
                        article['Sentiment'], article['Sentiment_Score'] = basic_sentiment_analysis(
                            f"{article['Title']} {article['Summary']}"
                        )
                        article['Reasoning'] = ""
                        article['Impact'] = "Medium"
                
                # Debug mode: show first article structure
                if debug_mode and news_articles and len(news_articles) > 0:
                    st.write("🔍 Debug Info - First Article Structure:")
                    st.json(news_articles[0])
            else:
                st.warning(f"{news_source} is not currently active. Please use Yahoo Finance.")
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
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Articles", total_articles, f"from {unique_tickers} ticker(s)")
    with col2:
        positive_count = sum(1 for article in st.session_state['news_articles'] if article.get('Sentiment') == 'Positive')
        st.metric("Positive Sentiment", positive_count, f"{positive_count/total_articles*100:.1f}%")
    with col3:
        negative_count = sum(1 for article in st.session_state['news_articles'] if article.get('Sentiment') == 'Negative')
        st.metric("Negative Sentiment", negative_count, f"{negative_count/total_articles*100:.1f}%")
    
    st.markdown("---")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        unique_tickers_in_news = sorted(list(set(article['Ticker'] for article in st.session_state.news_articles if 'Ticker' in article)))
        selected_ticker_filter = st.selectbox("Filter by Ticker:", ["All"] + unique_tickers_in_news, key="news_ticker_filter")
    with col2:
        selected_sentiment_filter = st.selectbox("Filter by Sentiment:", ["All", "Positive", "Neutral", "Negative"], key="news_sentiment_filter")
    with col3:
        articles_to_show = st.slider("Articles to display:", min_value=10, max_value=100, value=30, step=10)

    displayed_count = 0
    for article in st.session_state.news_articles:
        if displayed_count >= articles_to_show:
            st.caption(f"Showing {articles_to_show} articles. Adjust slider to see more.")
            break

        title = article.get('Title', 'No Title')
        link = article.get('Link', '#')
        date_str = article.get('Date', 'No Date')
        source = article.get('Source', 'No Source')
        ticker_symbol = article.get('Ticker', 'N/A')
        summary = article.get('Summary', '')
        
        # Get sentiment data
        sentiment_label = article.get('Sentiment', 'Neutral')
        sentiment_score = article.get('Sentiment_Score', 0.0)
        reasoning = article.get('Reasoning', '')
        impact = article.get('Impact', 'Medium')

        if selected_ticker_filter != "All" and ticker_symbol != selected_ticker_filter: continue
        if selected_sentiment_filter != "All" and sentiment_label != selected_sentiment_filter: continue
        
        with st.container(border=True):
            # Title with link
            st.markdown(f"### [{title}]({link})")
            
            # Metadata row
            meta_col1, meta_col2, meta_col3, meta_col4 = st.columns([2, 2, 1, 1])
            meta_col1.caption(f"🗓️ {date_str}")
            meta_col2.caption(f"📰 {source}")
            meta_col3.caption(f"💹 {ticker_symbol}")
            
            # Sentiment indicator with impact
            if sentiment_label == "Positive":
                meta_col4.success(f"↑ {sentiment_label}")
            elif sentiment_label == "Negative":
                meta_col4.error(f"↓ {sentiment_label}")
            else:
                meta_col4.info(f"→ {sentiment_label}")
            
            # Summary if available
            if summary:
                st.caption(summary[:200] + "..." if len(summary) > 200 else summary)
            
            # AI Analysis (if available)
            if reasoning and use_ai_sentiment:
                st.info(f"🤖 **AI Analysis**: {reasoning} | **Impact**: {impact}")
            
            # Sentiment score (smaller, less prominent)
            st.caption(f"Sentiment Score: {sentiment_score:.2f}")
            
        displayed_count += 1

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
    st.markdown("**Data Source:** Yahoo Finance API")
    if use_ai_sentiment:
        st.caption("Sentiment analysis powered by OpenAI GPT-3.5")
    else:
        st.caption("Sentiment analysis is basic and for illustrative purposes only.")
with col2:
    if st.button("Clear News Feed", key="clear_news"):
        if 'news_articles' in st.session_state:
            del st.session_state['news_articles']
        st.rerun()def advanced_sentiment_analysis(title, summary, ticker):
    """
    More nuanced sentiment analysis that considers context
    """
    if not title or not isinstance(title, str):
        return "Neutral", 0.0
    
    # Combine title (weighted more) and summary
    text = f"{title} {title} {summary}".lower()  # Title counted twice for more weight
    
    # More comprehensive keyword lists with weights
    strong_positive = ['surge', 'soar', 'rally', 'breakthrough', 'record high', 'beat expectations', 
                      'upgrade', 'outperform', 'strong buy', 'bullish', 'boom', 'accelerate', 'jackpot']
    moderate_positive = ['rise', 'gain', 'up', 'increase', 'positive', 'growth', 'expand', 'improve',
                        'beat', 'exceed', 'favorable', 'optimistic', 'recovery', 'rebound']
    
    strong_negative = ['crash', 'plunge', 'collapse', 'crisis', 'bankruptcy', 'fraud', 'scandal',
                      'plummet', 'disaster', 'lawsuit', 'investigation', 'recall', 'shutdown', 'layoffs']
    moderate_negative = ['fall', 'drop', 'decline', 'decrease', 'loss', 'down', 'cut', 'reduce',
                        'miss', 'disappointing', 'concern', 'risk', 'threat', 'challenge', 'weak']
    
    # Context modifiers that can change sentiment
    negation_words = ['not', 'no', 'despite', 'but', 'however', 'although']
    
    # Calculate weighted scores
    score = 0
    
    # Strong signals worth more
    score += sum(2 for word in strong_positive if word in text)
    score += sum(1 for word in moderate_positive if word in text)
    score -= sum(2 for word in strong_negative if word in text)
    score -= sum(1 for word in moderate_negative if word in text)
    
    # Check for negations near positive/negative words
    for negation in negation_words:
        if negation in text:
            score *= 0.7  # Reduce confidence when negations are present
    
    # Normalize score to -1 to 1 range
    max_possible = len(text.split()) / 5  # Rough normalization
    if max_possible > 0:
        normalized_score = maximport streamlit as st
import sys
import os
import pandas as pd
from datetime import datetime
import yfinance as yf
import requests
from bs4 import BeautifulSoup

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

# --- Helper Functions ---

@st.cache_data(ttl=900) # Cache for 15 minutes
def fetch_ticker_news_yfinance(tickers_string):
    """
    Fetches news for a list of tickers using yfinance.
    """
    if not tickers_string:
        return []
    
    tickers_list = [ticker.strip().upper() for ticker in tickers_string.split(',')]
    all_news = []

    for ticker in tickers_list:
        try:
            # Create a yfinance Ticker object
            stock = yf.Ticker(ticker)
            
            # Get news - this returns a list of dictionaries
            news_data = stock.news
            
            if news_data:
                for article in news_data:
                    # The actual content is nested inside 'content' key
                    content = article.get('content', {})
                    
                    # Extract title and other info from the content
                    title = content.get('title', 'No Title')
                    
                    # Extract link from canonicalUrl or clickThroughUrl
                    link_data = content.get('clickThroughUrl') or content.get('canonicalUrl') or {}
                    link = link_data.get('url', '#')
                    
                    # Handle the date - it's in pubDate field
                    pub_date = content.get('pubDate')
                    if pub_date:
                        try:
                            # Parse ISO format date
                            date_obj = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
                            date_str = date_obj.strftime('%Y-%m-%d %H:%M:%S')
                        except:
                            date_str = pub_date
                            date_obj = datetime.min
                    else:
                        date_str = 'No Date'
                        date_obj = datetime.min
                    
                    # Get provider info
                    provider = content.get('provider', {})
                    source = provider.get('displayName', 'Unknown')
                    
                    # Get summary
                    summary = content.get('summary', content.get('description', ''))
                    # Clean HTML from summary
                    if summary:
                        soup = BeautifulSoup(summary, 'html.parser')
                        summary = soup.get_text()
                    
                    # Standardize the format
                    formatted_article = {
                        'Title': title,
                        'Link': link,
                        'Date': date_str,
                        'Source': source,
                        'Ticker': ticker,
                        'Summary': summary,
                        'Parsed_Date': date_obj
                    }
                    all_news.append(formatted_article)
                    
        except Exception as e:
            st.error(f"Error fetching news for {ticker}: {str(e)}")
            continue
    
    # Sort by date (newest first)
    all_news.sort(key=lambda x: x['Parsed_Date'], reverse=True)
    
    return all_news


def basic_sentiment_analysis(text):
    if not text or not isinstance(text, str):
        return "Neutral", 0.0
    text_lower = text.lower()
    positive_keywords = ['up', 'gain', 'profit', 'bullish', 'rally', 'strong', 'positive', 'upgrade', 'outperform', 'beat', 'good', 'great', 'excellent', 'record', 'high', 'boom', 'surge', 'growth', 'rise']
    negative_keywords = ['down', 'loss', 'bearish', 'slump', 'weak', 'negative', 'downgrade', 'underperform', 'miss', 'bad', 'terrible', 'poor', 'plunge', 'drop', 'crisis', 'fear', 'fall', 'decline']
    
    positive_score = sum(1 for keyword in positive_keywords if keyword in text_lower)
    negative_score = sum(1 for keyword in negative_keywords if keyword in text_lower)
    
    # Basic normalization to -1 to 1 range (approximately)
    total_keywords = positive_score + negative_score
    if total_keywords == 0:
        return "Neutral", 0.0
    
    score = (positive_score - negative_score) / total_keywords

    if score > 0.1: return "Positive", score
    elif score < -0.1: return "Negative", score
    else: return "Neutral", score

# --- UI Elements ---
st.header("📰 Fetch News")

# Info box about the news source
st.info("📌 **Note:** This news feed uses Yahoo Finance API which provides free, real-time market news. For more comprehensive news coverage, consider premium news services.")

default_tickers = "AAPL,TSLA,GOOGL" # Default tickers
tickers_input = st.text_input("Enter stock tickers (comma-separated):", value=default_tickers, key="news_tickers_input")

# Add a news source selector for future expansion
news_source = st.selectbox(
    "Select News Source:",
    ["Yahoo Finance (Free)", "Finviz (Requires API Key - Not Active)", "NewsAPI (Requires API Key - Not Active)"],
    index=0,
    help="Currently only Yahoo Finance is active. Other sources require API keys."
)

# Debug mode on a new line
debug_mode = st.checkbox("Debug Mode", value=False, help="Show raw data structure")

if st.button("Fetch News", key="fetch_news_button"):
    if tickers_input:
        with st.spinner("Fetching news articles..."):
            if news_source == "Yahoo Finance (Free)":
                news_articles = fetch_ticker_news_yfinance(tickers_input)
                
                # Debug mode: show first article structure
                if debug_mode and news_articles and len(news_articles) > 0:
                    st.write("🔍 Debug Info - First Article Structure:")
                    first_article = news_articles[0]
                    if 'Original' in first_article:
                        st.json(first_article['Original'])
                    else:
                        st.json(first_article)
            else:
                st.warning(f"{news_source} is not currently active. Please use Yahoo Finance.")
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
    
    col1, col2, col3 = st.columns(3)
    with col1:
        unique_tickers_in_news = sorted(list(set(article['Ticker'] for article in st.session_state.news_articles if 'Ticker' in article)))
        selected_ticker_filter = st.selectbox("Filter by Ticker:", ["All"] + unique_tickers_in_news, key="news_ticker_filter")
    with col2:
        selected_sentiment_filter = st.selectbox("Filter by Sentiment:", ["All", "Positive", "Neutral", "Negative"], key="news_sentiment_filter")
    with col3:
        articles_to_show = st.slider("Articles to display:", min_value=10, max_value=100, value=30, step=10)

    displayed_count = 0
    for article in st.session_state.news_articles:
        if displayed_count >= articles_to_show:
            st.caption(f"Showing {articles_to_show} articles. Adjust slider to see more.")
            break

        title = article.get('Title', 'No Title')
        link = article.get('Link', '#')
        date_str = article.get('Date', 'No Date')
        source = article.get('Source', 'No Source')
        ticker_symbol = article.get('Ticker', 'N/A')
        summary = article.get('Summary', '')

        # Analyze sentiment on both title and summary
        combined_text = f"{title} {summary}"
        sentiment_label, sentiment_score = basic_sentiment_analysis(combined_text)

        if selected_ticker_filter != "All" and ticker_symbol != selected_ticker_filter: continue
        if selected_sentiment_filter != "All" and sentiment_label != selected_sentiment_filter: continue
        
        with st.container(border=True):
            # Title with link
            st.markdown(f"### [{title}]({link})")
            
            # Metadata row
            meta_col1, meta_col2, meta_col3, meta_col4 = st.columns([2, 2, 1, 1])
            meta_col1.caption(f"🗓️ {date_str}")
            meta_col2.caption(f"📰 {source}")
            meta_col3.caption(f"💹 {ticker_symbol}")
            
            # Sentiment indicator
            if sentiment_label == "Positive":
                meta_col4.success(f"↑ {sentiment_label}")
            elif sentiment_label == "Negative":
                meta_col4.error(f"↓ {sentiment_label}")
            else:
                meta_col4.info(f"→ {sentiment_label}")
            
            # Summary if available
            if summary:
                st.caption(summary[:200] + "..." if len(summary) > 200 else summary)
            
            # Sentiment score (smaller, less prominent)
            st.caption(f"Sentiment Score: {sentiment_score:.2f}")
            
        displayed_count += 1

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
    st.markdown("**Data Source:** Yahoo Finance API")
    st.caption("Sentiment analysis is basic and for illustrative purposes only.")
with col2:
    if st.button("Clear News Feed", key="clear_news"):
        if 'news_articles' in st.session_state:
            del st.session_state['news_articles']
        st.rerun()
