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
    import openai
    openai_api_key = st.secrets.get("OPENAI_API_KEY", None)
    use_ai_sentiment = openai_api_key is not None
    if openai_api_key:
        openai.api_key = openai_api_key
        st.success("✅ AI-Powered Sentiment Analysis Active (OpenAI)")
    else:
        st.warning("⚠️ Using basic sentiment analysis. Add OPENAI_API_KEY to secrets.toml for better accuracy.")
except ImportError:
    use_ai_sentiment = False
    st.info("OpenAI not installed. Using basic sentiment analysis.")

# --- Helper Functions ---

def get_ai_sentiment(title, summary, ticker):
    """
    Use OpenAI to analyze sentiment specifically for the given ticker
    """
    if not use_ai_sentiment:
        return basic_sentiment_analysis(f"{title} {summary}")
    
    try:
        prompt = f"""
        Analyze this news article's sentiment specifically for {ticker} stock:
        
        Title: {title}
        Summary: {summary[:500]}
        
        Consider:
        1. Does this news directly impact {ticker}?
        2. Is it good or bad for {ticker} shareholders?
        3. Would a trader want to buy, sell, or hold based on this news?
        
        Examples:
        - "Apple faces new competition" = Negative
        - "Apple expands despite warnings" = Positive
        - "Apple stock analysis" = Neutral
        
        Respond with ONLY ONE of these three words: Positive, Negative, or Neutral
        """
        
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a stock sentiment analyzer. You must respond with EXACTLY one word: Positive, Negative, or Neutral. No other text."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=10
        )
        
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
            if debug_mode:
                st.warning(f"Unexpected AI response: '{sentiment_text}' for {ticker}")
            return basic_sentiment_analysis(f"{title} {summary}")
        
        # Log the analysis in debug mode
        if debug_mode:
            st.caption(f"🤖 AI Analysis for {ticker}: '{title[:50]}...' → {sentiment}")
            
        return sentiment, score
            
    except Exception as e:
        if debug_mode:
            st.error(f"AI sentiment analysis failed: {str(e)}")
        return basic_sentiment_analysis(f"{title} {summary}")

def basic_sentiment_analysis(text):
    """
    Current basic sentiment analysis - THIS IS WHAT'S BEING USED NOW
    Problems:
    - Only counts keywords without context
    - "Apple faces challenges" = negative (even if article says they'll overcome them)
    - Doesn't understand sarcasm or nuance
    """
    if not text or not isinstance(text, str):
        return "Neutral", 0.0
    
    text_lower = text.lower()
    
    # Current keyword lists - TOO SIMPLE!
    positive_keywords = ['up', 'gain', 'profit', 'bullish', 'rally', 'strong', 'positive', 'upgrade', 
                        'outperform', 'beat', 'good', 'great', 'excellent', 'record', 'high', 'boom', 
                        'surge', 'growth', 'rise']
    negative_keywords = ['down', 'loss', 'bearish', 'slump', 'weak', 'negative', 'downgrade', 
                        'underperform', 'miss', 'bad', 'terrible', 'poor', 'plunge', 'drop', 'crisis', 
                        'fear', 'fall', 'decline', 'isn\'t worth', 'lost']
    
    positive_score = sum(1 for keyword in positive_keywords if keyword in text_lower)
    negative_score = sum(1 for keyword in negative_keywords if keyword in text_lower)
    
    # This is the problem - just counting words!
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
                            "Price_Data": first_article.get('Price_Data')
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

        # Analyze sentiment - Use AI if available, otherwise basic
        if use_ai_sentiment:
            sentiment_label, sentiment_score = get_ai_sentiment(title, summary, ticker_symbol)
        else:
            combined_text = f"{title} {summary}"
            sentiment_label, sentiment_score = basic_sentiment_analysis(combined_text)

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
                    
                    # Sentiment
                    if sentiment_label == "Positive":
                        st.success(f"↑ {sentiment_label}")
                    elif sentiment_label == "Negative":
                        st.error(f"↓ {sentiment_label}")
                    else:
                        st.info(f"→ {sentiment_label}")
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
            
            # Sentiment score
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
