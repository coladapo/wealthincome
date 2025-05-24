import streamlit as st
import sys
import os
import pandas as pd
from finvizfinance.news import News as FinvizNews # Renamed to avoid conflict if you have a class News
from datetime import datetime

# --- Start of Path Fix ---
# Get the absolute path of the directory containing the current script
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the absolute path of the parent directory (project root)
parent_dir = os.path.dirname(current_dir)

# Add the parent directory to the Python system path if it's not already there
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
        # This case should ideally not happen if data_manager.py defines the instance correctly
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
    Args:
        tickers_string (str): A comma-separated string of tickers.
    Returns:
        list: A list of news articles (dictionaries).
    """
    if not tickers_string:
        return []
    
    tickers_list = [ticker.strip().upper() for ticker in tickers_string.split(',')]
    all_news = []
    fn = FinvizNews() # Initialize once

    for ticker in tickers_list:
        st.write(f"Fetching news for {ticker}...")
        try:
            # The get_news() method in finvizfinance returns a dictionary where keys are tickers
            # and values are lists of news articles (each article is a dictionary).
            news_data_for_ticker = fn.get_news(ticker) # This gets all news pages for the ticker
            
            if ticker in news_data_for_ticker and news_data_for_ticker[ticker]:
                for article in news_data_for_ticker[ticker]:
                    article['Ticker'] = ticker # Add ticker to each article for context
                    all_news.append(article)
            else:
                st.caption(f"No news found for {ticker} via Finviz.")
        except Exception as e:
            st.error(f"Error fetching news for {ticker}: {e}")
            continue # Move to the next ticker
    
    # Sort news by date (assuming 'Date' is a string that can be converted)
    # Finviz news usually comes with datetime objects or parsable date strings.
    # Let's try to parse and sort.
    def parse_date(date_str):
        try:
            # Finviz format example: "Mar-05-24 08:00AM" or just a date like "2024-03-05"
            # This needs to be robust. For now, let's assume a common format if it's a string.
            # If it's already a datetime object, this won't be needed.
            if isinstance(date_str, str):
                # Try a few common formats
                for fmt in ("%b-%d-%y %I:%M%p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(date_str, fmt)
                    except ValueError:
                        continue
                return None # Could not parse
            return date_str # Assume it's already a datetime object
        except Exception:
            return None

    for article in all_news:
        article['Parsed_Date'] = parse_date(article.get('Date'))

    # Sort by Parsed_Date, most recent first, putting None dates at the end
    all_news.sort(key=lambda x: x['Parsed_Date'] if x['Parsed_Date'] else datetime.min, reverse=True)
    
    return all_news


def basic_sentiment_analysis(text):
    """
    Very basic sentiment analysis. Placeholder for a more sophisticated model.
    """
    if not text or not isinstance(text, str):
        return "Neutral", 0.0

    text_lower = text.lower()
    positive_keywords = ['up', 'gain', 'profit', 'bullish', 'rally', 'strong', 'positive', 'upgrade', 'outperform', 'beat', 'good', 'great', 'excellent']
    negative_keywords = ['down', 'loss', 'bearish', 'slump', 'weak', 'negative', 'downgrade', 'underperform', 'miss', 'bad', 'terrible', 'poor']

    positive_score = sum(1 for keyword in positive_keywords if keyword in text_lower)
    negative_score = sum(1 for keyword in negative_keywords if keyword in text_lower)

    if positive_score > negative_score:
        return "Positive", positive_score / (positive_score + negative_score + 1e-6) # Normalize
    elif negative_score > positive_score:
        return "Negative", -negative_score / (positive_score + negative_score + 1e-6) # Normalize
    else:
        return "Neutral", 0.0

# --- UI Elements ---

st.header("📰 Fetch News")
default_tickers = "AAPL,TSLA,MSFT"
if data_manager_instance:
    # Example: try to get watchlist if available
    # watchlist = data_manager_instance.get_watchlist() # Assuming this method exists
    # if watchlist:
    #     default_tickers = ",".join(watchlist[:3]) # Use first 3 from watchlist
    pass # Keep default for now

tickers_input = st.text_input("Enter stock tickers (comma-separated, e.g., AAPL,TSLA,MSFT):", value=default_tickers, key="news_tickers_input")

if st.button("Fetch News", key="fetch_news_button"):
    if tickers_input:
        with st.spinner("Fetching news articles... this might take a moment."):
            news_articles = fetch_ticker_news(tickers_input)
        
        if news_articles:
            st.session_state['news_articles'] = news_articles
            st.success(f"Fetched {len(news_articles)} articles for {tickers_input}.")
        else:
            st.info("No news articles found for the given tickers or an error occurred.")
            st.session_state['news_articles'] = [] # Clear previous results
    else:
        st.warning("Please enter at least one ticker.")

st.markdown("---")

# Display News Articles
if 'news_articles' in st.session_state and st.session_state['news_articles']:
    st.header("📊 News Feed")
    
    # Filters
    col1, col2 = st.columns(2)
    with col1:
        # Create a list of unique tickers from the fetched news for the filter
        unique_tickers_in_news = sorted(list(set(article['Ticker'] for article in st.session_state.news_articles if 'Ticker' in article)))
        selected_ticker_filter = st.selectbox("Filter by Ticker:", ["All"] + unique_tickers_in_news, key="news_ticker_filter")
    with col2:
        selected_sentiment_filter = st.selectbox("Filter by Sentiment:", ["All", "Positive", "Neutral", "Negative"], key="news_sentiment_filter")

    displayed_count = 0
    for article in st.session_state.news_articles:
        if displayed_count >= 50: # Limit displayed articles for performance
            st.caption("Displaying top 50 most recent articles. Refine filters for more specific results.")
            break

        title = article.get('Title', 'No Title')
        link = article.get('Link', '#')
        date_str = article.get('Date', 'No Date') # This is the original date string from Finviz
        source = article.get('Source', 'No Source')
        ticker_symbol = article.get('Ticker', 'N/A')

        sentiment_label, sentiment_score = basic_sentiment_analysis(title)

        # Apply filters
        if selected_ticker_filter != "All" and ticker_symbol != selected_ticker_filter:
            continue
        if selected_sentiment_filter != "All" and sentiment_label != selected_sentiment_filter:
            continue
        
        # Display logic
        container = st.container(border=True)
        container.subheader(f"[{title}]({link})")
        
        meta_col1, meta_col2, meta_col3 = container.columns(3)
        meta_col1.caption(f"🗓️ {date_str}")
        meta_col2.caption(f"📰 Source: {source}")
        meta_col3.caption(f"💹 Ticker: {ticker_symbol}")

        # Sentiment display
        if sentiment_label == "Positive":
            container.markdown(f"Sentiment: <span style='color:green; font-weight:bold;'>{sentiment_label}</span> (Score: {sentiment_score:.2f})", unsafe_allow_html=True)
        elif sentiment_label == "Negative":
            container.markdown(f"Sentiment: <span style='color:red; font-weight:bold;'>{sentiment_label}</span> (Score: {sentiment_score:.2f})", unsafe_allow_html=True)
        else:
            container.markdown(f"Sentiment: <span style='color:gray;'>{sentiment_label}</span>", unsafe_allow_html=True)
        
        # Add a small separator
        container.markdown("---")
        displayed_count +=1

    if displayed_count == 0:
        st.info("No articles match your current filter criteria.")


elif 'news_articles' in st.session_state and not st.session_state.news_articles:
    # This case handles when fetch was clicked but returned no results
    st.info("No news articles were found for the specified tickers in the last fetch.")
else:
    st.info("Enter tickers and click 'Fetch News' to see the latest market updates.")

# Footer
st.markdown("---")
st.markdown("News data sourced from Finviz. Sentiment analysis is basic and for illustrative purposes only.")
