import streamlit as st
import sys
import os
import pandas as pd
import yfinance as yf
import numpy as np
from datetime import datetime, timedelta
import ta
import requests
from concurrent.futures import ThreadPoolExecutor
import json

# --- Start of Path Fix ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)
# --- End of Path Fix ---

# --- Import data_manager ---
try:
    # We'll assume data_manager is now imported and you'll integrate it later.
    # For now, the app uses direct yfinance calls as per your original structure.
    # If you have a data_manager.py and want to use it, you can uncomment these:
    # import data_manager as dm
    # from data_manager import data_manager
    st.caption("Data manager import section present (currently commented out).") # Placeholder
except ImportError as e:
    st.error(f"Could not import data_manager: {e}. Falling back to direct yfinance calls.")
except Exception as e:
    st.error(f"An unexpected error occurred during data_manager import: {e}")
# --- End Imports ---


# --- START OF YOUR ORIGINAL TRADING DASHBOARD CODE ---

# Page config
try:
    st.set_page_config(page_title="🧠 AI Multi-Strategy Screener", layout="wide")
except st.errors.StreamlitAPIException as e:
    if "can only be called once per app" in str(e):
        st.caption("Note: Page config was already set.")
    else:
        raise e

st.title("🧠 AI Multi-Strategy Stock Screener")

# Initialize session state
if 'screener_results' not in st.session_state:
    st.session_state.screener_results = [] # Initialize as list
if 'trade_signals' not in st.session_state:
    st.session_state.trade_signals = []

# Sidebar for configuration
with st.sidebar:
    st.header("⚙️ Screener Settings")
    trade_type = st.selectbox(
        "Trading Strategy",
        ["🎯 All Signals", "⚡ Day Trade", "📊 Swing Trade", "💎 Position Trade"],
        key="trade_type_selector"
    )
    data_source = st.selectbox(
        "Data Source",
        ["📊 Manual Tickers", "🔥 Top Movers", "📈 Trending Stocks"],
        key="data_source_selector"
    )
    st.markdown("---")
    st.subheader("🎛️ Advanced Filters")
    min_price = st.number_input("Min Price ($)", value=1.0, step=0.5, min_value=0.0, key="min_price_input")
    max_price = st.number_input("Max Price ($)", value=500.0, step=10.0, min_value=0.0, key="max_price_input")
    min_volume_m = st.number_input("Min Volume (M)", value=1.0, step=0.5, min_value=0.0, key="min_volume_input")
    min_volume = min_volume_m * 1_000_000
    use_options_flow = st.checkbox("Include Options Flow", value=False, key="options_flow_checkbox")
    use_news_sentiment = st.checkbox("Include News Sentiment", value=False, key="news_sentiment_checkbox")

# Main content area
tab1, tab2, tab3, tab4 = st.tabs(["📊 Scanner", "📈 Signals", "💼 Portfolio", "📚 Education"])

# Helper functions
def calculate_technical_indicators(ticker_symbol, period="1mo"):
    try:
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period=period, auto_adjust=True) # auto_adjust=True is common
        if hist.empty: return None
        current_price = hist['Close'].iloc[-1] if not hist['Close'].empty else 0

        tech_data = {'price': current_price}
        tech_data['sma_20'] = ta.trend.sma_indicator(hist['Close'], window=20).iloc[-1] if len(hist['Close']) >= 20 else None
        tech_data['sma_50'] = ta.trend.sma_indicator(hist['Close'], window=50).iloc[-1] if len(hist['Close']) >= 50 else None
        tech_data['rsi'] = ta.momentum.RSIIndicator(hist['Close'], window=14).rsi().iloc[-1] if len(hist['Close']) >= 15 else None # RSI typically needs more data
        
        if len(hist['Close']) >= 26: # MACD needs enough data
            macd_obj = ta.trend.MACD(hist['Close'])
            tech_data['macd'] = macd_obj.macd().iloc[-1]
            tech_data['macd_signal'] = macd_obj.macd_signal().iloc[-1]
        else:
            tech_data['macd'], tech_data['macd_signal'] = None, None

        if len(hist['Close']) >= 20: # Bollinger Bands
            bb_obj = ta.volatility.BollingerBands(hist['Close'])
            tech_data['bb_upper'] = bb_obj.bollinger_hband().iloc[-1]
            tech_data['bb_lower'] = bb_obj.bollinger_lband().iloc[-1]
            tech_data['support'] = hist['Low'].rolling(window=20).min().iloc[-1]
            tech_data['resistance'] = hist['High'].rolling(window=20).max().iloc[-1]
            volume_sma_last = hist['Volume'].rolling(window=20).mean().iloc[-1]
            tech_data['volume_trend'] = (hist['Volume'].iloc[-1] / volume_sma_last) if volume_sma_last and volume_sma_last > 0 else 0
        else:
            tech_data['bb_upper'], tech_data['bb_lower'], tech_data['support'], tech_data['resistance'], tech_data['volume_trend'] = None, None, current_price * 0.95, current_price * 1.05, 0
        
        return tech_data
    except Exception as e:
        # st.caption(f"TA error for {ticker_symbol}: {e}")
        return None

def get_intraday_momentum(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        intraday = ticker.history(period="1d", interval="5m") # Using 5m for more reliability
        if intraday.empty or len(intraday) < 2: return None

        open_price = intraday['Open'].iloc[0]
        current_price = intraday['Close'].iloc[-1]
        high, low = intraday['High'].max(), intraday['Low'].min()
        
        price_position = (current_price - low) / (high - low) if (high - low) != 0 else 0.5
        avg_volume = intraday['Volume'].mean()
        current_volume = intraday['Volume'].iloc[-1] # Last interval's volume
        volume_surge = current_volume / avg_volume if avg_volume and avg_volume > 0 else 1
        price_change = ((current_price - open_price) / open_price) * 100 if open_price != 0 else 0
        
        return {'intraday_change': price_change, 'price_position': price_position,
                'volume_surge': volume_surge, 'day_high': high, 'day_low': low, 'range': high - low}
    except Exception as e:
        # st.caption(f"Intraday error for {ticker_symbol}: {e}")
        return None

def calculate_ai_scores(ticker_data):
    scores = {'day_trade': 0, 'swing_trade': 0, 'position_trade': 0, 'overall': 0}
    if not ticker_data: return scores

    tech = ticker_data.get('technicals')
    intra = ticker_data.get('intraday')
    fund = ticker_data.get('fundamentals')

    if intra and tech:
        day_score = 0
        day_score += intra.get('intraday_change', 0) * 3
        day_score += intra.get('volume_surge', 0) * 15
        day_score += intra.get('price_position', 0) * 20
        day_score += ticker_data.get('short_pct', 0) * 2
        scores['day_trade'] = round(day_score, 2)

    if tech:
        swing_score = 0
        if tech.get('rsi') is not None:
            if 30 < tech['rsi'] < 70: swing_score += 10
            if tech['rsi'] < 30: swing_score += 20
        if all(tech.get(k) is not None for k in ['price', 'sma_20', 'sma_50']) and \
           tech['price'] > tech['sma_20'] > tech['sma_50']: swing_score += 25
        if all(tech.get(k) is not None for k in ['macd', 'macd_signal']) and \
           tech['macd'] > tech['macd_signal']: swing_score += 15
        if all(tech.get(k) is not None for k in ['price', 'support']) and \
           tech['price'] > 0 and abs(tech['price'] - tech['support']) / tech['price'] < 0.02: swing_score += 20
        scores['swing_trade'] = round(swing_score, 2)

    if fund and tech:
        position_score = 0
        if fund.get('pe_ratio') is not None and 0 < fund['pe_ratio'] < 25: position_score += 20
        if fund.get('peg_ratio') is not None and fund['peg_ratio'] < 1.5: position_score += 25
        if fund.get('revenue_growth') is not None and fund['revenue_growth'] > 0.15: position_score += 20
        if all(tech.get(k) is not None for k in ['price', 'sma_50']) and \
           tech['price'] > tech['sma_50']: position_score += 15
        scores['position_trade'] = round(position_score, 2)
    
    scores['overall'] = round((scores.get('day_trade',0) + scores.get('swing_trade',0) + scores.get('position_trade',0)) / 3, 2)
    return scores

def analyze_stock(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
        if not info or info.get('regularMarketPrice') is None and info.get('currentPrice') is None:
            return None

        data = {'ticker': ticker_symbol,
                'price': info.get('regularMarketPrice', info.get('currentPrice', 0)),
                'change': info.get('regularMarketChangePercent', 0) * 100,
                'volume': info.get('regularMarketVolume', 0),
                'avg_volume': info.get('averageDailyVolume10Day', info.get('averageVolume', 1)),
                'market_cap': info.get('marketCap', 0),
                'short_pct': info.get('shortPercentOfFloat', 0) * 100 if info.get('shortPercentOfFloat') else 0}
        data['rvol'] = (data['volume'] / data['avg_volume']) if data['avg_volume'] and data['avg_volume'] > 0 else 0
        
        data['fundamentals'] = {'pe_ratio': info.get('trailingPE'), 'peg_ratio': info.get('pegRatio'),
                                'revenue_growth': info.get('revenueGrowth'), 'profit_margins': info.get('profitMargins'),
                                'debt_to_equity': info.get('debtToEquity')}
        data['technicals'] = calculate_technical_indicators(ticker_symbol)
        data['intraday'] = get_intraday_momentum(ticker_symbol)
        
        if data['technicals'] is None or data['intraday'] is None:
             data['scores'] = {'day_trade': 0, 'swing_trade': 0, 'position_trade': 0, 'overall': 0}
        else:
            data['scores'] = calculate_ai_scores(data)

        signals = []
        if data['scores'].get('day_trade', 0) > 60: signals.append("⚡ DAY")
        if data['scores'].get('swing_trade', 0) > 70: signals.append("📊 SWING")
        if data['scores'].get('position_trade', 0) > 75: signals.append("💎 POSITION")
        data['signals'] = signals
        return data
    except Exception as e:
        st.error(f"Error analyzing {ticker_symbol}: {str(e)}")
        return None

with tab1:
    st.header("🔍 Stock Scanner")
    if data_source == "📊 Manual Tickers":
        default_tickers = "NVDA,TSLA,AAPL,AMD,MSFT,META,GOOGL,AMZN,NFLX,PLTR"
        ticker_input = st.text_area("Enter tickers (comma-separated):", value=default_tickers, height=100, key="ticker_text_area")
        tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
    elif data_source == "🔥 Top Movers":
        tickers = ["NVDA", "TSLA", "AMD", "SOFI", "PLTR", "RIVN", "LCID", "NIO", "MARA", "RIOT"] # Sample
        st.info(f"Scanning top {len(tickers)} movers (sample data)...")
    else: 
        tickers = ["NVDA", "TSLA", "AAPL", "SPY", "QQQ", "AMD", "MSFT", "META", "GOOGL", "AMZN"] # Sample
        st.info(f"Scanning {len(tickers)} trending stocks (sample data)...")

    if st.button("🚀 Run Analysis", type="primary", key="run_analysis_button_main"):
        if not tickers:
            st.warning("Please enter some tickers or select a valid data source.")
        else:
            progress_bar = st.progress(0, text="Initializing Analysis...")
            results_list = [] # Use a different name to avoid conflict
            total_tickers = len(tickers)

            with ThreadPoolExecutor(max_workers=min(10, total_tickers if total_tickers > 0 else 1)) as executor:
                future_to_ticker = {executor.submit(analyze_stock, ticker): ticker for ticker in tickers}
                for i, future in enumerate(future_to_ticker):
                    ticker_name = future_to_ticker[future]
                    progress_text = f"Analyzing {ticker_name} ({i+1}/{total_tickers})..."
                    progress_bar.progress(min(1.0, (i + 1) / total_tickers), text=progress_text)
                    try:
                        result = future.result()
                        if result: results_list.append(result)
                    except Exception as e: st.error(f"Analysis failed for {ticker_name}: {e}")
            
            progress_bar.empty()
            st.session_state.screener_results = results_list

            if results_list:
                summary_data = []
                for r_val in results_list:
                    if not (r_val and isinstance(r_val.get('price'), (int, float)) and \
                            isinstance(r_val.get('volume'), (int, float)) and \
                            r_val.get('technicals') and r_val.get('scores')):
                        continue

                    price, volume, scores = r_val['price'], r_val['volume'], r_val['scores']
                    if not (min_price <= price <= max_price and volume >= min_volume): continue
                    
                    passes_trade_type_filter = True
                    if trade_type != "🎯 All Signals":
                        if trade_type == "⚡ Day Trade" and scores.get('day_trade', 0) < 60: passes_trade_type_filter = False
                        elif trade_type == "📊 Swing Trade" and scores.get('swing_trade', 0) < 70: passes_trade_type_filter = False
                        elif trade_type == "💎 Position Trade" and scores.get('position_trade', 0) < 75: passes_trade_type_filter = False
                    if not passes_trade_type_filter: continue

                    summary_data.append({'Ticker': r_val.get('ticker', 'N/A'), 'Price': f"${price:.2f}",
                                         '% Change': f"{r_val.get('change', 0):.2f}%", 'RVOL': f"{r_val.get('rvol', 0):.2f}",
                                         'Signals': ' '.join(r_val.get('signals', [])), 'Day Score': scores.get('day_trade', 0),
                                         'Swing Score': scores.get('swing_trade', 0), 'Position Score': scores.get('position_trade', 0),
                                         'AI Score': scores.get('overall', 0)})
                if summary_data:
                    df = pd.DataFrame(summary_data).sort_values('AI Score', ascending=False)
                    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
                    col_m1.metric("Stocks Matched", len(df))
                    col_m2.metric("Total Buy Signals", len([d for d in summary_data if d['Signals']]))
                    avg_ai_score = df['AI Score'].mean() if not df.empty else 0
                    col_m3.metric("Avg AI Score", f"{avg_ai_score:.1f}")
                    top_pick_ticker = df.iloc[0]['Ticker'] if not df.empty else "N/A"
                    col_m4.metric("Top Pick", top_pick_ticker)
                    st.dataframe(df.style.background_gradient(subset=['AI Score'], cmap='RdYlGn'), use_container_width=True)
                    st.session_state.trade_signals = summary_data
                else: st.warning("No stocks matched your filter criteria after analysis.")
            else: st.info("No analysis results. Check if tickers were provided and if analysis functions ran correctly.")

with tab2:
    st.header("📈 Trading Signals")
    if st.session_state.get('trade_signals'):
        for signal_idx, signal_data in enumerate(st.session_state.trade_signals[:5]):
            ticker_key = signal_data.get('Ticker', f"unknown_ticker_{signal_idx}")
            with st.expander(f"{signal_data.get('Ticker','N/A')} - {signal_data.get('Signals','No Signals')}"):
                full_data = next((r for r in st.session_state.get('screener_results', []) if r and r.get('ticker') == signal_data.get('Ticker')), None)
                if full_data and full_data.get('technicals'):
                    tech = full_data['technicals']
                    col_d1, col_d2 = st.columns(2)
                    with col_d1:
                        st.subheader("📊 Technical Analysis")
                        st.write(f"**Price:** ${tech.get('price', 0):.2f}")
                        st.write(f"**RSI:** {tech.get('rsi', 'N/A') if tech.get('rsi') is None else f'{tech.get(
                        target = tech.get('resistance')

                        if all(isinstance(val, (int, float)) for val in [entry, stop, target]) and entry > stop and target > entry:
                            risk_reward = (target - entry) / (entry - stop) if (entry - stop) != 0 else 0
                            st.write(f"**Entry:** ${entry:.2f}")
                            st.write(f"**Stop Loss:** ${stop:.2f} (-{((entry-stop)/entry*100):.1f}%)" if entry != 0 else "N/A")
                            st.write(f"**Target:** ${target:.2f} (+{((target-entry)/entry*100):.1f}%)" if entry != 0 else "N/A")
                            st.write(f"**Risk/Reward:** 1:{risk_reward:.1f}")
                        else: st.warning("Cannot calculate trade setup (invalid/missing S/R/Price).")
                    with col_d2:
                        st.subheader("💰 Position Sizing")
                        account_size = st.number_input("Account Size ($)", value=10000, step=1000, min_value=0, key=f"acc_{ticker_key}_{signal_idx}_tab2")
                        risk_pct = st.slider("Risk per trade (%)", 1, 5, 2, key=f"risk_{ticker_key}_{signal_idx}_tab2")
                        if all(isinstance(val, (int, float)) for val in [entry, stop]) and entry > stop:
                            risk_amount = account_size * (risk_pct / 100)
                            stop_distance = entry - stop
                            shares = int(risk_amount / stop_distance) if stop_distance > 0 else 0
                            position_size = shares * entry
                            st.write(f"**Risk Amount:** ${risk_amount:.2f}")
                            st.write(f"**Shares to Buy:** {shares}")
                            st.write(f"**Position Size:** ${position_size:.2f}")
                            st.write(f"**% of Account:** {(position_size/account_size*100):.1f}%" if account_size > 0 else "N/A")
                        else: st.warning("Cannot calculate position size (invalid entry/stop).")
                else: st.warning(f"Could not retrieve full technical data for {signal_data.get('Ticker','N/A')}.")
    else: st.info("Run the scanner first to see detailed signals.")

with tab3:
    st.header("💼 Portfolio Tracker")
    st.info("Portfolio tracking coming soon! This will track your positions, P&L, and performance metrics.")
with tab4:
    st.header("📚 Trading Education")
    with st.expander("⚡ Day Trading Strategy"): st.markdown("...")
    with st.expander("📊 Swing Trading Strategy"): st.markdown("...")
    with st.expander("💎 Position Trading Strategy"): st.markdown("...")

st.markdown("---")
st.markdown("🚨 **Disclaimer:** This tool is for educational purposes. Always do your own research and manage risk appropriately.")

