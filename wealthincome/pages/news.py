import streamlit as st
import sys
import os
import pandas as pd
from finvizfinance.news import News as FinvizNews 
from datetime import datetime
# Removed 'import inspect' as it's no longer needed for debugging this part

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
def fetch_ticker_news(tickers_string):
    """
    Fetches news for a list of tickers using finvizfinance.
    """
    if not tickers_string:
        return []
    
    tickers_list = [ticker.strip().upper() for ticker in tickers_string.split(',')]
    all_news = []
    fn = FinvizNews() 

    for ticker in tickers_list:
        # This st.write can be commented out if you don't want it in the final UI
        # st.caption(f"Fetching news for {ticker}...") 
        try:
            news_data_for_ticker = fn.get_news(ticker) 
            
            if ticker in news_data_for_ticker and news_data_for_ticker[ticker]:
                for article in news_data_for_ticker[ticker]:
                    article['Ticker'] = ticker 
                    all_news.append(article)
            # else:
                # st.caption(f"No news found for {ticker} via Finviz.") # Can be noisy
        except Exception as e:
            st.error(f"Error fetching news for {ticker}: {e}")
            continue 
    
    def parse_date(date_str):
        try:
            if isinstance(date_str, str):
                for fmt in ("%b-%d-%y %I:%M%p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try: return datetime.strptime(date_str, fmt)
                    except ValueError: continue
                return None
            return date_str
        except Exception: return None

    for article in all_news:
        article['Parsed_Date'] = parse_date(article.get('Date'))
    all_news.sort(key=lambda x: x['Parsed_Date'] if x['Parsed_Date'] else datetime.min, reverse=True)
    
    return all_news


def basic_sentiment_analysis(text):
    if not text or not isinstance(text, str):
        return "Neutral", 0.0
    text_lower = text.lower()
    positive_keywords = ['up', 'gain', 'profit', 'bullish', 'rally', 'strong', 'positive', 'upgrade', 'outperform', 'beat', 'good', 'great', 'excellent', 'record', 'high', 'boom', 'surge']
    negative_keywords = ['down', 'loss', 'bearish', 'slump', 'weak', 'negative', 'downgrade', 'underperform', 'miss', 'bad', 'terrible', 'poor', ' plunge', 'drop', 'crisis', 'fear']
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
default_tickers = "AAPL,TSLA,GOOGL" # Updated default
# You can add logic here to use data_manager_instance.get_watchlist() if available

tickers_input = st.text_input("Enter stock tickers (comma-separated):", value=default_tickers, key="news_tickers_input")

if st.button("Fetch News", key="fetch_news_button"):
    if tickers_input:
        with st.spinner("Fetching news articles..."):
            news_articles = fetch_ticker_news(tickers_input)
        
        if news_articles:
            st.session_state['news_articles'] = news_articles
            st.success(f"Fetched {len(news_articles)} articles for {tickers_input}.")
        else:
            st.info("No news articles found for the given tickers or an error occurred.")
            if 'news_articles' in st.session_state: # Clear if empty fetch
                 st.session_state['news_articles'] = []
    else:
        st.warning("Please enter at least one ticker.")

st.markdown("---")

if 'news_articles' in st.session_state and st.session_state['news_articles']:
    st.header("📊 News Feed")
    
    col1, col2 = st.columns(2)
    with col1:
        unique_tickers_in_news = sorted(list(set(article['Ticker'] for article in st.session_state.news_articles if 'Ticker' in article)))
        selected_ticker_filter = st.selectbox("Filter by Ticker:", ["All"] + unique_tickers_in_news, key="news_ticker_filter")
    with col2:
        selected_sentiment_filter = st.selectbox("Filter by Sentiment:", ["All", "Positive", "Neutral", "Negative"], key="news_sentiment_filter")

    displayed_count = 0
    for article in st.session_state.news_articles:
        if displayed_count >= 50: 
            st.caption("Displaying top 50 most recent articles. Refine filters for more specific results.")
            break

        title = article.get('Title', 'No Title')
        link = article.get('Link', '#')
        date_str = article.get('Date', 'No Date') 
        source = article.get('Source', 'No Source')
        ticker_symbol = article.get('Ticker', 'N/A')

        sentiment_label, sentiment_score = basic_sentiment_analysis(title)

        if selected_ticker_filter != "All" and ticker_symbol != selected_ticker_filter: continue
        if selected_sentiment_filter != "All" and sentiment_label != selected_sentiment_filter: continue
        
        container = st.container(border=True)
        container.subheader(f"[{title}]({link})")
        
        meta_col1, meta_col2, meta_col3 = container.columns(3)
        meta_col1.caption(f"🗓️ {date_str}")
        meta_col2.caption(f"📰 Source: {source}")
        meta_col3.caption(f"💹 Ticker: {ticker_symbol}")

        color = "gray"
        if sentiment_label == "Positive": color = "green"
        elif sentiment_label == "Negative": color = "red"
        container.markdown(f"Sentiment: <span style='color:{color}; font-weight:bold;'>{sentiment_label}</span> (Score: {sentiment_score:.2f})", unsafe_allow_html=True)
        
        container.markdown("---")
        displayed_count +=1

    if displayed_count == 0 and (selected_ticker_filter != "All" or selected_sentiment_filter != "All"):
        st.info("No articles match your current filter criteria.")
elif 'news_articles' in st.session_state and not st.session_state.news_articles :
    st.info("No news articles were found for the specified tickers in the last fetch.")
else:
    st.info("Enter tickers and click 'Fetch News' to see the latest market updates.")

st.markdown("---")
st.markdown("News data sourced from Finviz. Sentiment analysis is basic and for illustrative purposes only.")
