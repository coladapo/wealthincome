import streamlit as st
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
            
            # Get news
            news_data = stock.news
            
            if news_data:
                for article in news_data:
                    # Standardize the format
                    formatted_article = {
                        'Title': article.get('title', 'No Title'),
                        'Link': article.get('link', '#'),
                        'Date': datetime.fromtimestamp(article.get('providerPublishTime', 0)).strftime('%Y-%m-%d %H:%M:%S') if article.get('providerPublishTime') else 'No Date',
                        'Source': article.get('publisher', 'Unknown'),
                        'Ticker': ticker,
                        'Summary': article.get('summary', ''),
                        'Parsed_Date': datetime.fromtimestamp(article.get('providerPublishTime', 0)) if article.get('providerPublishTime') else datetime.min
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

if st.button("Fetch News", key="fetch_news_button"):
    if tickers_input:
        with st.spinner("Fetching news articles..."):
            if news_source == "Yahoo Finance (Free)":
                news_articles = fetch_ticker_news_yfinance(tickers_input)
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
