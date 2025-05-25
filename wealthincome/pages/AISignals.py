import streamlit as st
import sys
import os
import pandas as pd
from datetime import datetime
import yfinance as yf
# import requests # Not directly used for yfinance news
from bs4 import BeautifulSoup
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pytz

# Attempt to import openai
OPENAI_INSTALLED = False
openai_client = None # Will hold the OpenAI client instance
try:
    import openai
    OPENAI_INSTALLED = True
except ImportError:
    st.warning("OpenAI library not found. Please add 'openai>=1.0.0' to your requirements.txt for AI sentiment.")

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
    # Removed caption from here to avoid clutter, will confirm AI status later
except ImportError:
    st.error("Could not import data_manager. Some features might be limited.") # Simplified error
except Exception:
    st.error("Unexpected error importing data_manager.")
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
use_ai_sentiment = False
if OPENAI_INSTALLED:
    openai_api_key_from_secrets = st.secrets.get("OPENAI_API_KEY") # Removed default None to see if it's truly missing
    if openai_api_key_from_secrets:
        try:
            openai_client = openai.OpenAI(api_key=openai_api_key_from_secrets) # Initialize client
            # Quick test to ensure API key is valid (optional, can cause an API call)
            # try:
            #     openai_client.models.list() 
            #     st.success("✅ OpenAI API Key configured and valid. AI Sentiment Active.")
            #     use_ai_sentiment = True
            # except openai.AuthenticationError:
            #     st.error("🚨 OpenAI AuthenticationError: API Key is invalid or expired. Using basic sentiment.")
            # except Exception as e_auth:
            #     st.error(f"🚨 OpenAI API Key test failed: {e_auth}. Using basic sentiment.")
            st.success("✅ OpenAI API Key found in secrets. AI Sentiment will be attempted.")
            use_ai_sentiment = True # Assume key is valid for now, errors will be caught in get_ai_sentiment
        except Exception as e_client:
            st.error(f"🚨 Error initializing OpenAI client: {e_client}. Using basic sentiment.")
    else:
        st.warning("⚠️ OpenAI API Key not found in Streamlit secrets. Using basic sentiment analysis. Add OPENAI_API_KEY to enable.")
else:
    st.info("OpenAI library not installed. Using basic sentiment. Add 'openai>=1.0.0' to requirements.txt.")


# --- Helper Functions ---
def basic_sentiment_analysis(text):
    # (Your existing basic_sentiment_analysis function - no changes needed here)
    if not text or not isinstance(text, str): return "Neutral", 0.0
    text_lower = text.lower()
    positive_keywords = ['up', 'gain', 'profit', 'bullish', 'rally', 'strong', 'positive', 'upgrade', 'outperform', 'beat', 'good', 'great', 'excellent', 'record', 'high', 'boom', 'surge', 'growth', 'rise', 'optimistic', 'improvement']
    negative_keywords = ['down', 'loss', 'bearish', 'slump', 'weak', 'negative', 'downgrade', 'underperform', 'miss', 'bad', 'terrible', 'poor', 'plunge', 'drop', 'crisis', 'fear', 'fall', 'decline', 'warning', 'risk', 'concern']
    positive_score = sum(1 for keyword in positive_keywords if keyword in text_lower)
    negative_score = sum(1 for keyword in negative_keywords if keyword in text_lower)
    total_keywords = positive_score + negative_score
    if total_keywords == 0: return "Neutral", 0.0
    score = (positive_score - negative_score) / total_keywords
    if score > 0.1: return "Positive", score
    elif score < -0.1: return "Negative", score
    else: return "Neutral", score

def get_ai_sentiment(title, summary, ticker, current_debug_mode):
    if not use_ai_sentiment or not openai_client: # Check if client was initialized
        return basic_sentiment_analysis(f"{title} {summary}")
    
    try:
        prompt = f"""Analyze this news article's sentiment specifically for {ticker} stock:
        Title: {title}
        Summary: {summary[:500]}
        Consider the direct impact on {ticker}. Is it good, bad, or neutral for {ticker} shareholders?
        Respond with ONLY ONE word: Positive, Negative, or Neutral."""
        
        if current_debug_mode:
            st.caption(f"🤖 Sending to OpenAI for {ticker}: '{title[:30]}...'")
            # st.text_area("OpenAI Prompt:", prompt, height=150) # Uncomment to see full prompt in debug

        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a stock sentiment analyzer. Respond with EXACTLY one word: Positive, Negative, or Neutral."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=10
        )
        
        sentiment_text = response.choices[0].message.content.strip().lower()
        
        if current_debug_mode:
            st.caption(f"🤖 OpenAI Raw Response for {ticker}: '{sentiment_text}'")

        sentiment, score = "Neutral", 0.0
        if "positive" in sentiment_text: sentiment, score = "Positive", 0.7
        elif "negative" in sentiment_text: sentiment, score = "Negative", -0.7
        elif "neutral" in sentiment_text: sentiment, score = "Neutral", 0.0
        else:
            if current_debug_mode: st.warning(f"Unexpected AI word: '{sentiment_text}' for {ticker}. Defaulting to basic.")
            return basic_sentiment_analysis(f"{title} {summary}")
        
        return sentiment, score
            
    except openai.APIError as e_api: # Catch specific OpenAI errors
        error_message = f"OpenAI API Error for {ticker}: {type(e_api).__name__} - {e_api}"
        if hasattr(e_api, 'message'): error_message += f" | Message: {e_api.message}"
        if hasattr(e_api, 'code'): error_message += f" | Code: {e_api.code}"
        if hasattr(e_api, 'param'): error_message += f" | Param: {e_api.param}"
        if current_debug_mode: st.error(error_message)
        else: st.caption(f"AI sentiment analysis for {ticker} failed. Using basic.")
        return basic_sentiment_analysis(f"{title} {summary}")
    except Exception as e_general: # Catch any other exceptions
        if current_debug_mode: st.error(f"General error in AI sentiment for {ticker}: {type(e_general).__name__} - {str(e_general)}")
        else: st.caption(f"AI sentiment analysis for {ticker} failed. Using basic.")
        return basic_sentiment_analysis(f"{title} {summary}")

@st.cache_data(ttl=900)
def fetch_ticker_news_yfinance(tickers_string):
    # (Your existing fetch_ticker_news_yfinance function - no changes needed here, assuming it's working)
    if not tickers_string: return []
    tickers_list = [ticker.strip().upper() for ticker in tickers_string.split(',')]
    all_news = []
    ticker_prices_cache = {}
    for ticker_symbol_for_price in tickers_list:
        if ticker_symbol_for_price not in ticker_prices_cache:
            try:
                stock_info_obj = yf.Ticker(ticker_symbol_for_price)
                info = stock_info_obj.info
                current_price = info.get('currentPrice') or info.get('regularMarketPrice', 0)
                previous_close = info.get('previousClose') or info.get('regularMarketPreviousClose', 0)
                pre_market_price = info.get('preMarketPrice')
                post_market_price = info.get('postMarketPrice')
                regular_volume = info.get('regularMarketVolume', 0)
                avg_volume = info.get('averageVolume', 1) 
                volume_ratio = regular_volume / avg_volume if avg_volume > 0 else 0
                if current_price and previous_close:
                    change_percent = ((current_price - previous_close) / previous_close) * 100
                    ticker_prices_cache[ticker_symbol_for_price] = {
                        'current_price': current_price, 'previous_close': previous_close,
                        'change_percent': change_percent, 'change_dollar': current_price - previous_close,
                        'pre_market_price': pre_market_price, 'post_market_price': post_market_price,
                        'pre_market_change': ((pre_market_price - previous_close) / previous_close * 100) if pre_market_price and previous_close else None,
                        'post_market_change': ((post_market_price - current_price) / current_price * 100) if post_market_price and current_price else None,
                        'volume': regular_volume, 'avg_volume': avg_volume, 'volume_ratio': volume_ratio,
                        'unusual_volume': volume_ratio > 2.0 
                    }
            except Exception: ticker_prices_cache[ticker_symbol_for_price] = None
    for ticker in tickers_list:
        try:
            stock = yf.Ticker(ticker)
            news_data = stock.news
            if news_data:
                for article in news_data:
                    title = article.get('title', 'No Title')
                    link = article.get('link', '#')
                    pub_time_unix = article.get('providerPublishTime')
                    date_obj, date_str = datetime.min, 'No Date'
                    if pub_time_unix:
                        try: 
                            date_obj = datetime.fromtimestamp(pub_time_unix, tz=pytz.UTC)
                            date_str = date_obj.strftime('%Y-%m-%d %H:%M:%S %Z')
                        except: pass # Keep default if parsing fails
                    source = article.get('publisher', 'Unknown Source')
                    summary_for_sentiment = title 
                    formatted_article = {
                        'Title': title, 'Link': link, 'Date': date_str, 'Source': source,
                        'Ticker': ticker, 'Summary': summary_for_sentiment, 
                        'Parsed_Date': date_obj, 'Price_Data': ticker_prices_cache.get(ticker)
                    }
                    all_news.append(formatted_article)
        except Exception as e: st.error(f"News fetch error ({ticker}): {str(e)}")
    all_news.sort(key=lambda x: x.get('Parsed_Date', datetime.min), reverse=True)
    return all_news

# --- UI Elements ---
# (Keep your existing UI section, but ensure `debug_mode_checkbox` is defined before the fetch button logic)

st.header("📰 Fetch News")
st.info("📌 **Note:** News from Yahoo Finance. AI sentiment via OpenAI (if API key is configured).")

default_tickers = "AAPL,TSLA,GOOGL"
tickers_input = st.text_input("Enter stock tickers (comma-separated):", value=default_tickers, key="news_tickers_input")

debug_mode_checkbox = st.checkbox("Enable Debug Mode for AI Sentiment", value=False, help="Show detailed AI analysis steps and errors.", key="news_debug_mode_checkbox")

if st.button("Fetch News", key="fetch_news_button_main"):
    if tickers_input:
        with st.spinner("Fetching news articles..."):
            news_articles = fetch_ticker_news_yfinance(tickers_input) 
        
        if news_articles:
            st.session_state['news_articles'] = news_articles
            st.success(f"✅ Fetched {len(news_articles)} articles for {tickers_input}.")
            # Debug for first article structure (if debug mode is on)
            if debug_mode_checkbox and news_articles:
                 with st.expander("🔍 Debug: First Fetched Article Structure", expanded=False):
                    st.json(news_articles[0])
        else:
            st.warning("No news articles found for the given tickers.")
            if 'news_articles' in st.session_state: st.session_state['news_articles'] = []
    else:
        st.warning("Please enter at least one ticker.")

st.markdown("---")

# News Display Loop (ensure debug_mode_checkbox is passed to get_ai_sentiment)
if 'news_articles' in st.session_state and st.session_state['news_articles']:
    st.header("📊 News Feed")
    # (Keep the rest of your news display loop, making sure to pass 
    #  `debug_mode_checkbox` to `get_ai_sentiment` when it's called for each article)
    # Example of calling get_ai_sentiment within the loop:
    # label, score = get_ai_sentiment(title, summary, ticker_symbol, debug_mode_checkbox)

    total_articles = len(st.session_state['news_articles'])
    unique_tickers_count = len(set(article['Ticker'] for article in st.session_state['news_articles']))
    st.metric("Total Articles Fetched", total_articles, f"from {unique_tickers_count} ticker(s)")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        unique_tickers_in_news = sorted(list(set(article['Ticker'] for article in st.session_state.news_articles if 'Ticker' in article)))
        selected_ticker_filter = st.selectbox("Filter by Ticker:", ["All"] + unique_tickers_in_news, key="news_ticker_filter")
    with col2:
        selected_sentiment_filter = st.selectbox("Filter by Sentiment:", ["All", "Positive", "Neutral", "Negative"], key="news_sentiment_filter")
    with col3:
        sort_by = st.selectbox("Sort by:", ["Newest First", "Oldest First", "Most Positive", "Most Negative", "High Volume First"], key="news_sort")
    with col4:
        articles_to_show = st.slider("Articles to display:", min_value=5, max_value=50, value=10, step=5, key="news_article_slider")
    
    articles_to_display_intermediate = st.session_state.news_articles.copy()
    
    # Pre-calculate sentiment for sorting if needed
    # Pass the state of the debug_mode_checkbox to get_ai_sentiment
    current_debug_state = debug_mode_checkbox 
    if sort_by in ["Most Positive", "Most Negative"]:
        for article in articles_to_display_intermediate:
            if 'Sentiment_Label' not in article or 'Sentiment_Score' not in article:
                title = article.get('Title', '')
                summary = article.get('Summary', '')
                ticker_symbol = article.get('Ticker', 'N/A')
                label, score = get_ai_sentiment(title, summary, ticker_symbol, current_debug_state)
                article['Sentiment_Label'] = label
                article['Sentiment_Score'] = score

    if sort_by == "Newest First": articles_to_display_intermediate.sort(key=lambda x: x.get('Parsed_Date', datetime.min), reverse=True)
    elif sort_by == "Oldest First": articles_to_display_intermediate.sort(key=lambda x: x.get('Parsed_Date', datetime.min))
    elif sort_by == "Most Positive": articles_to_display_intermediate.sort(key=lambda x: x.get('Sentiment_Score', -1.0), reverse=True)
    elif sort_by == "Most Negative": articles_to_display_intermediate.sort(key=lambda x: x.get('Sentiment_Score', 1.0))
    elif sort_by == "High Volume First": articles_to_display_intermediate.sort(key=lambda x: x.get('Price_Data', {}).get('volume_ratio', 0) if x.get('Price_Data') else 0, reverse=True)

    final_display_list = []
    for article in articles_to_display_intermediate:
        if 'Sentiment_Label' not in article: # Ensure sentiment is calculated for all articles before filtering
            title = article.get('Title', '')
            summary = article.get('Summary', '')
            ticker_symbol = article.get('Ticker', 'N/A')
            label, score = get_ai_sentiment(title, summary, ticker_symbol, current_debug_state)
            article['Sentiment_Label'] = label
            article['Sentiment_Score'] = score

        if selected_ticker_filter != "All" and article.get('Ticker') != selected_ticker_filter: continue
        if selected_sentiment_filter != "All" and article.get('Sentiment_Label') != selected_sentiment_filter: continue
        final_display_list.append(article)

    displayed_count = 0
    for article_idx, article in enumerate(final_display_list):
        if displayed_count >= articles_to_show:
            st.caption(f"Showing {articles_to_show} articles. Adjust slider or filters to see more.")
            break
        
        title = article.get('Title', 'No Title')
        link = article.get('Link', '#')
        date_str = article.get('Date', 'No Date')
        source = article.get('Source', 'No Source')
        ticker_symbol = article.get('Ticker', 'N/A')
        summary = article.get('Summary', '')
        sentiment_label = article.get('Sentiment_Label', 'Neutral')
        sentiment_score = article.get('Sentiment_Score', 0.0)

        with st.container(border=True, key=f"news_article_main_{ticker_symbol}_{article_idx}"): # Unique key
            st.markdown(f"### [{title}]({link})")
            if article.get('Parsed_Date') and article['Parsed_Date'] != datetime.min:
                try:
                    article_date_utc = article['Parsed_Date']
                    current_time_utc = datetime.now(pytz.UTC)
                    news_age = current_time_utc - article_date_utc
                    minutes_old = news_age.total_seconds() / 60
                    if minutes_old < 15: st.success("🟢 **FRESH NEWS!** (<15 min)")
                    elif minutes_old < 30: st.warning("🟡 News 15-30 min old.")
                    elif minutes_old < 60: st.info("🔵 News 30-60 min old.")
                except: pass
            
            meta_col1, meta_col2, meta_col3 = st.columns([1.2, 1.5, 1])
            with meta_col1:
                st.caption(f"🗓️ {date_str}")
                st.caption(f"📰 {source} | 💹 {ticker_symbol}")
            
            price_data = article.get('Price_Data')
            if price_data:
                with meta_col2:
                    price = price_data['current_price']
                    change_pct = price_data['change_percent']
                    delta_val = f"{price_data['change_dollar']:.2f} ({change_pct:.2f}%)"
                    st.metric(label="Mkt Price", value=f"${price:.2f}", delta=delta_val)
                    if price_data.get('pre_market_price'): st.caption(f"Pre: ${price_data['pre_market_price']:.2f} ({price_data.get('pre_market_change', 0):+.2f}%)")
                    if price_data.get('post_market_price'): st.caption(f"Post: ${price_data['post_market_price']:.2f} ({price_data.get('post_market_change', 0):+.2f}%)")
                with meta_col3:
                    vol_ratio = price_data.get('volume_ratio',0)
                    vol_status_text = "Normal"
                    if vol_ratio > 2: vol_status_text = "🔥 Unusual"
                    elif vol_ratio > 1.5: vol_status_text = "⚠️ High"
                    st.metric(label=f"{vol_status_text} Vol", value=f"{vol_ratio:.1f}x Avg", help=f"Actual: {price_data.get('volume',0):,}, Avg: {price_data.get('avg_volume',0):,}")
            else:
                with meta_col2: st.caption("Price data unavailable.")
            
            sentiment_color = "gray"
            if sentiment_label == "Positive": sentiment_color = "green"
            elif sentiment_label == "Negative": sentiment_color = "red"
            
            if price_data: st.markdown(f"Sentiment: <b style='color:{sentiment_color};'>{sentiment_label}</b> (Score: {sentiment_score:.2f})", unsafe_allow_html=True)
            else:
                 with meta_col3:
                    st.markdown(f"Sentiment: <b style='color:{sentiment_color};'>{sentiment_label}</b>", unsafe_allow_html=True)
                    st.caption(f"(Score: {sentiment_score:.2f})")

            if summary and summary != title:
                with st.expander("Read summary (from title)", expanded=False): st.caption(summary)
            
            if price_data:
                with st.expander("📊 View Price Action & Volume Chart (5m, 2d)"):
                    try:
                        ticker_obj_chart = yf.Ticker(ticker_symbol)
                        hist_chart = ticker_obj_chart.history(period="2d", interval="5m")
                        if not hist_chart.empty:
                            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3], subplot_titles=('Price Action (5m)', 'Volume'))
                            fig.add_trace(go.Candlestick(x=hist_chart.index, open=hist_chart['Open'], high=hist_chart['High'], low=hist_chart['Low'], close=hist_chart['Close'], name='Price'), row=1, col=1)
                            bar_colors = ['red' if hist_chart['Close'].iloc[i] < hist_chart['Open'].iloc[i] else 'green' for i in range(len(hist_chart))]
                            fig.add_trace(go.Bar(x=hist_chart.index, y=hist_chart['Volume'], name='Volume', marker_color=bar_colors), row=2, col=1)
                            news_time_utc = article.get('Parsed_Date')
                            if news_time_utc and news_time_utc != datetime.min:
                                chart_index_tz = hist_chart.index.tz
                                news_time_for_chart = news_time_utc.astimezone(chart_index_tz) if chart_index_tz else news_time_utc.replace(tzinfo=None)
                                if hist_chart.index[0] <= news_time_for_chart <= hist_chart.index[-1]:
                                    fig.add_vline(x=news_time_for_chart, line_dash="dash", line_color="yellow", annotation_text="News", row=1, col=1)
                                    fig.add_vline(x=news_time_for_chart, line_dash="dash", line_color="yellow", row=2, col=1)
                            fig.update_layout(title_text=None, height=400, showlegend=False, template="plotly_dark", margin=dict(l=20, r=20, t=30, b=20))
                            fig.update_xaxes(rangeslider_visible=False)
                            st.plotly_chart(fig, use_container_width=True, key=f"chart_main_{ticker_symbol}_{article_idx}") # Unique key
                        else: st.caption("Not enough data for 5-min chart.")
                    except Exception as e_chart:
                        if debug_mode_checkbox: st.error(f"Chart error: {e_chart}")
                        else: st.caption("Could not load price chart.")
        displayed_count += 1

    if displayed_count == 0 and (selected_ticker_filter != "All" or selected_sentiment_filter != "All"):
        st.info("No articles match your current filter criteria.")
elif 'news_articles' in st.session_state and not st.session_state.news_articles :
    st.info("No news articles were found for the specified tickers in the last fetch.")
else:
    st.info("👆 Enter tickers and click 'Fetch News' to see the latest market updates.")

st.markdown("---")
col_foot1, col_foot2 = st.columns(2)
with col_foot1:
    st.markdown("**Data Source:** Yahoo Finance API. AI Sentiment via OpenAI (if configured).")
with col_foot2:
    if st.button("Clear News Feed", key="clear_news_button_main"): # Unique key
        if 'news_articles' in st.session_state:
            del st.session_state['news_articles']
        st.rerun()

