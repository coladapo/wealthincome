# 2_🧠_AI_Signals_Enhanced.py
"""
Enhanced AI Signals Page with Confidence Scoring and Trend Detection
Designed by: Maya Aesthetic, Aria Interface, Flow Master Chen
Enhanced by: Full puo AI Studio Team
"""

import streamlit as st
import sys
import os
import pandas as pd
import yfinance as yf
import numpy as np
from datetime import datetime, timedelta
import plotly.graph_objects as go
import plotly.express as px
from concurrent.futures import ThreadPoolExecutor
import time

# --- Path Setup ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# Add wealthincome directory to path
wealthincome_dir = os.path.join(parent_dir, 'wealthincome')
if wealthincome_dir not in sys.path:
    sys.path.append(wealthincome_dir)

# Import our new modules
from wealthincome.confidence_scoring import ConfidenceScorer, create_confidence_visualization, enhance_with_confidence
from wealthincome.trend_detection import TrendDetectionEngine, create_trend_visualization, scan_market_trends
from wealthincome.data_manager import data_manager

# --- Page Configuration ---
st.set_page_config(
    page_title="🧠 AI Signals Pro | wealthincome",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# --- Custom CSS for Beautiful UI ---
st.markdown("""
<style>
    /* Maya Aesthetic's Design System */
    .stApp {
        background: linear-gradient(135deg, #0f0f0f 0%, #1a1a1a 100%);
    }
    
    .confidence-ring {
        width: 200px;
        height: 200px;
        border-radius: 50%;
        position: relative;
        margin: 20px auto;
    }
    
    .trend-card {
        background: rgba(255,255,255,0.05);
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 15px;
        padding: 20px;
        transition: all 0.3s ease;
    }
    
    .trend-card:hover {
        transform: translateY(-5px);
        border-color: #00ff00;
        box-shadow: 0 10px 30px rgba(0,255,0,0.2);
    }
    
    .signal-strength {
        font-size: 48px;
        font-weight: bold;
        text-align: center;
        margin: 20px 0;
    }
    
    .ultra-high { color: #00ff00; text-shadow: 0 0 20px #00ff00; }
    .high { color: #90EE90; }
    .medium { color: #FFD700; }
    .low { color: #FF6B6B; }
    
    /* Tempo Swift's Animations */
    @keyframes pulse {
        0% { transform: scale(1); }
        50% { transform: scale(1.05); }
        100% { transform: scale(1); }
    }
    
    .pulse-animation {
        animation: pulse 2s infinite;
    }
    
    /* Progressive Disclosure */
    .detail-section {
        max-height: 0;
        overflow: hidden;
        transition: max-height 0.3s ease-out;
    }
    
    .detail-section.expanded {
        max-height: 1000px;
    }
</style>
""", unsafe_allow_html=True)

# --- Session State ---
if 'confidence_scorer' not in st.session_state:
    st.session_state.confidence_scorer = ConfidenceScorer()
if 'trend_engine' not in st.session_state:
    st.session_state.trend_engine = TrendDetectionEngine()
if 'selected_trend' not in st.session_state:
    st.session_state.selected_trend = None

# --- Header with Symphony Chen's Orchestration ---
st.markdown("""
# 🧠 AI Signals Pro - Confidence-Driven Trading

<div style='text-align: center; color: #888; margin-bottom: 30px;'>
    <i>Powered by 59 AI Advisors • Institutional-Grade Analysis • Built for Your Success</i>
</div>
""", unsafe_allow_html=True)

# --- Main Layout ---
tab1, tab2, tab3, tab4 = st.tabs([
    "🎯 Signal Scanner", 
    "🔮 Future Trends", 
    "💎 Confidence Analysis",
    "📊 Position Sizing"
])

# --- TAB 1: ENHANCED SIGNAL SCANNER ---
with tab1:
    # Quick filters at the top
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        confidence_filter = st.select_slider(
            "Minimum Confidence",
            options=["All", "Low (30+)", "Medium (50+)", "High (70+)", "Ultra (85+)"],
            value="High (70+)"
        )
    
    with col2:
        trend_filter = st.selectbox(
            "Trend Alignment",
            ["All Trends", "AI Revolution", "Quantum Computing", "Clean Energy", 
             "Biotech", "Cybersecurity", "Fintech", "Metaverse"]
        )
    
    with col3:
        strategy_filter = st.selectbox(
            "Trading Strategy",
            ["Best Overall", "Day Trade", "Swing Trade", "Position Trade"]
        )
    
    with col4:
        scan_button = st.button("🔍 Scan Market", type="primary", use_container_width=True)
    
    # Ticker input section
    st.markdown("---")
    col1, col2 = st.columns([3, 1])
    
    with col1:
        ticker_input = st.text_area(
            "Enter Tickers (comma-separated)",
            value="NVDA, MSFT, GOOGL, IONQ, CRWD",
            height=60,
            help="Enter stock symbols separated by commas"
        )
    
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        analyze_button = st.button("⚡ Analyze", type="primary", use_container_width=True)
    
    # Results Section
    if analyze_button or scan_button:
        with st.spinner("🤖 59 AI Advisors analyzing markets..."):
            # Parse tickers
            tickers = [t.strip().upper() for t in ticker_input.split(',') if t.strip()]
            
            # Progress bar
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            results = []
            for i, ticker in enumerate(tickers):
                status_text.text(f"Analyzing {ticker}... ({i+1}/{len(tickers)})")
                progress_bar.progress((i + 1) / len(tickers))
                
                # Get analysis (using existing analyze_stock_enhanced function)
                # This would integrate with your existing analysis
                analysis = {'ticker': ticker}  # Placeholder
                
                # Add confidence scoring
                confidence = st.session_state.confidence_scorer.calculate_confidence_score(
                    ticker, analysis
                )
                analysis['confidence'] = confidence
                
                results.append(analysis)
                time.sleep(0.1)  # Smooth animation
            
            progress_bar.empty()
            status_text.empty()
            
            # Enhanced results with confidence
            if results:
                # Sort by confidence score
                results = sorted(results, key=lambda x: x['confidence']['total_score'], reverse=True)
                
                # Display top opportunities
                st.markdown("## 🏆 Top Opportunities")
                
                # Create cards for top 3
                cols = st.columns(3)
                for i, result in enumerate(results[:3]):
                    with cols[i]:
                        confidence = result['confidence']
                        score = confidence['total_score']
                        level = confidence['confidence_level']
                        
                        # Determine styling
                        if level == "ULTRA HIGH":
                            border_color = "#00ff00"
                            bg_color = "rgba(0,255,0,0.1)"
                            emoji = "🔥"
                        elif level == "HIGH":
                            border_color = "#90EE90"
                            bg_color = "rgba(144,238,144,0.1)"
                            emoji = "✅"
                        else:
                            border_color = "#FFD700"
                            bg_color = "rgba(255,215,0,0.1)"
                            emoji = "⚡"
                        
                        st.markdown(f"""
                        <div style="
                            background: {bg_color};
                            border: 2px solid {border_color};
                            border-radius: 15px;
                            padding: 20px;
                            text-align: center;
                            height: 300px;
                        ">
                            <h2 style="color: {border_color}; margin: 0;">
                                {emoji} {result['ticker']}
                            </h2>
                            <div class="signal-strength {level.lower().replace(' ', '-')}">
                                {score:.0f}
                            </div>
                            <p style="color: {border_color}; font-weight: bold;">
                                {level} CONFIDENCE
                            </p>
                            <div style="margin-top: 20px; text-align: left;">
                                <small style="color: #aaa;">Key Factors:</small><br>
                                {'<br>'.join([f"• {exp}" for exp in confidence['explanations'][:2]])}
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                
                # Detailed results table
                st.markdown("### 📊 Detailed Analysis")
                
                # Create DataFrame for display
                display_data = []
                for r in results:
                    conf = r['confidence']
                    display_data.append({
                        'Ticker': r['ticker'],
                        'Confidence': f"{conf['total_score']:.0f}",
                        'Level': conf['confidence_level'],
                        'Technical': f"{conf['components']['technical']['score']:.0f}",
                        'Fundamental': f"{conf['components']['fundamental']['score']:.0f}",
                        'Sentiment': f"{conf['components']['sentiment']['score']:.0f}",
                        'Trend': f"{conf['components']['trend']['score']:.0f}",
                        'Top Factor': conf['explanations'][0] if conf['explanations'] else 'N/A',
                        'Action': conf['recommendations'][0] if conf['recommendations'] else 'Hold'
                    })
                
                df = pd.DataFrame(display_data)
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Confidence": st.column_config.NumberColumn(
                            "Confidence",
                            help="Overall confidence score (0-100)",
                            format="%d",
                        ),
                        "Level": st.column_config.TextColumn(
                            "Level",
                            help="Confidence level category",
                        ),
                    }
                )

# --- TAB 2: FUTURE TRENDS ---
with tab2:
    st.markdown("## 🔮 Future Trend Scanner")
    st.markdown("*Quantum Mind's pattern recognition identifies emerging opportunities before they're obvious*")
    
    # Scan for trends
    if st.button("🌐 Scan All Trends", type="primary"):
        with st.spinner("🔍 Analyzing global trends..."):
            trends, trend_df = scan_market_trends()
            st.session_state.trends = trends
            st.session_state.trend_df = trend_df
    
    # Display trends
    if 'trends' in st.session_state:
        create_trend_visualization(st.session_state.trends)
        
        # Trend selector
        st.markdown("### 🎯 Select a Trend for Deep Dive")
        selected_trend = st.selectbox(
            "Choose trend to analyze",
            options=list(st.session_state.trends.keys()),
            format_func=lambda x: x.replace('_', ' ').title()
        )
        
        if selected_trend:
            # Get detailed trend report
            engine = st.session_state.trend_engine
            report = engine.get_trend_report(selected_trend)
            
            if report:
                # Display trend report
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown(f"### 📈 {report['name']}")
                    st.markdown(f"**Category:** {report['category']}")
                    st.markdown(f"**Current Score:** {report['current_score']:.0f}/100")
                    st.markdown(f"**Momentum:** {report['momentum']:.0f}/100")
                    
                    st.markdown("#### 💡 Investment Thesis")
                    st.write(report['investment_thesis'])
                    
                    st.markdown("#### 🎯 Leaders")
                    for leader in report['leaders'][:5]:
                        st.write(f"• **{leader}**")
                
                with col2:
                    st.markdown("#### ⏱️ Timeline")
                    timeline = report['timeline']
                    for period, description in timeline.items():
                        st.write(f"**{period.replace('_', ' ').title()}:** {description}")
                    
                    st.markdown("#### ⚠️ Key Risks")
                    for risk in report['risks']:
                        st.write(f"• {risk}")
                    
                    st.markdown("#### 🔗 Related Trends")
                    for related in report['related_trends']:
                        st.write(f"• {related.replace('_', ' ').title()}")

# --- TAB 3: CONFIDENCE ANALYSIS ---
with tab3:
    st.markdown("## 💎 Deep Confidence Analysis")
    
    # Single stock deep dive
    col1, col2 = st.columns([2, 1])
    
    with col1:
        analysis_ticker = st.text_input(
            "Enter ticker for deep analysis",
            value="NVDA",
            help="Get comprehensive confidence analysis for a single stock"
        )
    
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        deep_analyze = st.button("🔬 Deep Analysis", type="primary", use_container_width=True)
    
    if deep_analyze and analysis_ticker:
        with st.spinner(f"🧠 Running deep analysis on {analysis_ticker}..."):
            # Simulate comprehensive analysis
            # In production, this would call all your analysis functions
            
            # Placeholder analysis data
            analysis = {
                'ticker': analysis_ticker,
                'price': 450.0,
                'technicals': {
                    'rsi': 55,
                    'sma_20': 440,
                    'sma_50': 420,
                    'sma_200': 380,
                    'support': 430,
                    'resistance': 470
                },
                'info': {
                    'forwardPE': 28,
                    'revenueGrowth': 0.35,
                    'profitMargins': 0.25,
                    'targetMeanPrice': 550
                },
                'news_sentiment': {
                    'label': 'Positive',
                    'score': 0.75
                },
                'rvol': 1.8,
                'scores': {
                    'ai_score': 82,
                    'swing_score': 78
                }
            }
            
            # Calculate confidence
            confidence = st.session_state.confidence_scorer.calculate_confidence_score(
                analysis_ticker, analysis
            )
            
            # Display confidence visualization
            create_confidence_visualization(confidence)
            
            # Position sizing recommendations
            st.markdown("### 💰 Position Sizing Recommendations")
            
            account_size = st.number_input(
                "Account Size ($)",
                value=50000,
                step=5000,
                help="Your total trading account size"
            )
            
            risk_percent = st.slider(
                "Base Risk Per Trade (%)",
                min_value=0.5,
                max_value=3.0,
                value=2.0,
                step=0.5,
                help="Your standard risk per trade as percentage of account"
            )
            
            # Get position sizing
            sizing = st.session_state.confidence_scorer.get_position_sizing_recommendation(
                confidence['total_score'],
                account_size,
                risk_percent / 100
            )
            
            # Display sizing
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.metric(
                    "Recommended Risk",
                    f"${sizing['adjusted_risk']:.0f}",
                    f"{sizing['multiplier']:.1f}x base"
                )
            
            with col2:
                st.metric(
                    "Max Position %",
                    f"{sizing['max_position_percent']:.1f}%",
                    help="Maximum percentage of account for this position"
                )
            
            with col3:
                st.metric(
                    "Confidence Level",
                    sizing['confidence_level'],
                    help=sizing['sizing_note']
                )
            
            # Risk management tips
            st.markdown("### 🛡️ Risk Management")
            st.info(f"""
            **Mind Mapper's Psychology Tips:**
            - {sizing['sizing_note']}
            - Set stop loss before entering position
            - Consider scaling in if confidence is medium
            - Review thesis if price moves against you
            - Take partial profits at resistance levels
            """)

# --- TAB 4: POSITION SIZING CALCULATOR ---
with tab4:
    st.markdown("## 📊 Kelly Criterion Position Sizing")
    st.markdown("*Capital Sage's mathematical approach to optimal position sizing*")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### 📈 Trade Setup")
        
        entry_price = st.number_input("Entry Price ($)", value=100.0, step=1.0)
        stop_loss = st.number_input("Stop Loss ($)", value=95.0, step=1.0)
        target_price = st.number_input("Target Price ($)", value=115.0, step=1.0)
        
        win_rate = st.slider(
            "Historical Win Rate (%)",
            min_value=30,
            max_value=80,
            value=55,
            help="Your historical win rate for similar setups"
        )
        
        confidence_score = st.slider(
            "Setup Confidence (0-100)",
            min_value=0,
            max_value=100,
            value=75,
            help="Your confidence in this specific trade"
        )
    
    with col2:
        st.markdown("### 💰 Account Details")
        
        total_capital = st.number_input(
            "Total Account ($)",
            value=50000,
            step=1000
        )
        
        max_risk_percent = st.slider(
            "Maximum Risk per Trade (%)",
            min_value=0.5,
            max_value=5.0,
            value=2.0,
            step=0.5
        )
        
        # Calculate Kelly percentage
        if entry_price > 0 and stop_loss > 0 and target_price > entry_price:
            # Risk and reward
            risk_per_share = entry_price - stop_loss
            reward_per_share = target_price - entry_price
            risk_reward_ratio = reward_per_share / risk_per_share
            
            # Kelly calculation
            win_prob = win_rate / 100
            loss_prob = 1 - win_prob
            
            # Kelly % = (p * b - q) / b
            # where p = win probability, q = loss probability, b = risk/reward ratio
            kelly_percent = (win_prob * risk_reward_ratio - loss_prob) / risk_reward_ratio
            
            # Adjust for confidence
            adjusted_kelly = kelly_percent * (confidence_score / 100)
            
            # Cap at max risk
            final_risk_percent = min(adjusted_kelly * 100, max_risk_percent)
            
            # Calculate position size
            risk_amount = total_capital * (final_risk_percent / 100)
            shares = int(risk_amount / risk_per_share)
            position_size = shares * entry_price
            
            # Display results
            st.markdown("### 🎯 Optimal Position Size")
            
            metric_cols = st.columns(4)
            
            with metric_cols[0]:
                st.metric(
                    "Risk:Reward",
                    f"1:{risk_reward_ratio:.1f}",
                    help="Risk to reward ratio"
                )
            
            with metric_cols[1]:
                st.metric(
                    "Kelly %",
                    f"{kelly_percent*100:.1f}%",
                    help="Raw Kelly percentage"
                )
            
            with metric_cols[2]:
                st.metric(
                    "Adjusted %",
                    f"{final_risk_percent:.1f}%",
                    help="Confidence-adjusted and capped"
                )
            
            with metric_cols[3]:
                st.metric(
                    "Position Size",
                    f"${position_size:,.0f}",
                    f"{shares} shares"
                )
            
            # Visual representation
            st.markdown("### 📊 Position Breakdown")
            
            breakdown_data = {
                'Component': ['Entry Cost', 'Risk Amount', 'Potential Profit', 'Total Exposure'],
                'Amount': [
                    position_size,
                    risk_amount,
                    shares * reward_per_share,
                    position_size
                ],
                'Percentage': [
                    (position_size / total_capital) * 100,
                    final_risk_percent,
                    (shares * reward_per_share / total_capital) * 100,
                    (position_size / total_capital) * 100
                ]
            }
            
            df_breakdown = pd.DataFrame(breakdown_data)
            
            fig = px.bar(
                df_breakdown,
                x='Component',
                y='Amount',
                color='Percentage',
                color_continuous_scale='Viridis',
                title='Position Size Analysis'
            )
            
            fig.update_layout(
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                font=dict(color='white')
            )
            
            st.plotly_chart(fig, use_container_width=True)

# --- Footer ---
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #666; font-size: 12px;'>
    <p>Powered by puo AI studio • 59 World-Class AI Advisors Working for Your Success</p>
    <p>Symphony Chen orchestrating • Maya Aesthetic designing • Code Einstein architecting</p>
    <p>Remember: Great traders manage risk first, profits follow</p>
</div>
""", unsafe_allow_html=True)