import streamlit as st
import sys
import os
import pandas as pd
from datetime import datetime
import yfinance as yf
import requests
from bs4 import BeautifulSoup
import plotly.graph_objects as go

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
    ticker_prices = {}

    # Fetch current prices for all tickers
    for ticker in tickers_list:
        try:
            stock = yf.Ticker(ticker)
            # Get current price and daily change
            info = stock.info
            current_price = info.get('currentPrice') or info.get('regularMarketPrice', 0)
            previous_close = info.get('previousClose') or info.get('regularMarketPreviousClose', 0)
            
            if current_price and previous_close:
                change_percent = ((current_price - previous_close) / previous_close) * 100
                ticker_prices[ticker] = {
                    'current_price': current_price,
                    'previous_close': previous_close,
                    'change_percent': change_percent,
                    'change_dollar': current_price - previous_close
                }
        except:
            ticker_prices[ticker] = None

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
                        'Parsed_Date': date_obj,
                        'Price_Data': ticker_prices.get(ticker)
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
            
            # Price and metadata row
            meta_col1, meta_col2, meta_col3, meta_col4, price_col = st.columns([2, 2, 1, 1, 2])
            
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
            
            # Price information
            price_data = article.get('Price_Data')
            if price_data:
                price = price_data['current_price']
                change_pct = price_data['change_percent']
                change_dollar = price_data['change_dollar']
                
                if change_pct >= 0:
                    price_col.metric(
                        label="Price",
                        value=f"${price:.2f}",
                        delta=f"{change_pct:.2f}% (${change_dollar:.2f})",
                        delta_color="normal"
                    )
                else:
                    price_col.metric(
                        label="Price",
                        value=f"${price:.2f}",
                        delta=f"{change_pct:.2f}% (${change_dollar:.2f})",
                        delta_color="normal"
                    )
            else:
                price_col.caption("Price data unavailable")
            
            # Summary if available
            if summary:
                st.caption(summary[:200] + "..." if len(summary) > 200 else summary)
            
            # News age and potential impact
            if 'Parsed_Date' in article and article['Parsed_Date'] != datetime.min:
                news_age = datetime.now() - article['Parsed_Date'].replace(tzinfo=None)
                hours_old = news_age.total_seconds() / 3600
                
                if hours_old < 1:
                    st.caption(f"🔥 **Fresh news** - Published {int(hours_old * 60)} minutes ago")
                elif hours_old < 6:
                    st.caption(f"📍 **Recent** - Published {hours_old:.1f} hours ago")
                elif hours_old < 24:
                    st.caption(f"📅 Published {hours_old:.0f} hours ago")
                else:
                    days_old = hours_old / 24
                    st.caption(f"📅 Published {days_old:.0f} days ago")
            
            # Mini price chart (last 5 days)
            with st.expander("📊 View Price Movement"):
                try:
                    ticker_obj = yf.Ticker(ticker_symbol)
                    hist = ticker_obj.history(period="5d", interval="1h")
                    
                    if not hist.empty:
                        import plotly.graph_objects as go
                        
                        fig = go.Figure()
                        fig.add_trace(go.Candlestick(
                            x=hist.index,
                            open=hist['Open'],
                            high=hist['High'],
                            low=hist['Low'],
                            close=hist['Close'],
                            name='Price'
                        ))
                        
                        # Add a vertical line at news publication time
                        if 'Parsed_Date' in article and article['Parsed_Date'] != datetime.min:
                            fig.add_vline(
                                x=article['Parsed_Date'], 
                                line_dash="dash", 
                                line_color="yellow",
                                annotation_text="News Published"
                            )
                        
                        fig.update_layout(
                            title=f"{ticker_symbol} - 5 Day Price Movement",
                            yaxis_title="Price ($)",
                            xaxis_title="Date",
                            height=300,
                            showlegend=False,
                            template="plotly_dark"
                        )
                        
                        st.plotly_chart(fig, use_container_width=True)
                        
                        # Price statistics
                        col1, col2, col3 = st.columns(3)
                        col1.metric("5-Day High", f"${hist['High'].max():.2f}")
                        col2.metric("5-Day Low", f"${hist['Low'].min():.2f}")
                        col3.metric("Avg Volume", f"{hist['Volume'].mean():,.0f}")
                        
                except Exception as e:
                    st.error(f"Could not load price chart: {str(e)}")
            
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
