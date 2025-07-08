"""
Trading Journal Page - Track and analyze trading performance
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import numpy as np

from ui.components import render_metric_card, render_alert_banner
from ui.navigation import render_page_header

def render_journal():
    """Render trading journal page"""
    
    render_page_header(
        "📔 Trading Journal",
        "Track and analyze your trading decisions and performance",
        actions=[
            {"label": "🔄 Refresh", "key": "refresh_journal", "callback": refresh_journal_data},
            {"label": "📊 Performance Report", "key": "generate_journal_report", "callback": generate_journal_report},
            {"label": "➕ Add Entry", "key": "add_journal_entry", "callback": show_add_entry_form}
        ]
    )
    
    trading_engine = st.session_state.get('trading_engine')
    
    if not trading_engine:
        st.error("Trading engine not initialized")
        return
    
    # Journal Overview
    render_journal_overview(trading_engine)
    
    st.markdown("---")
    
    # Main Content
    col1, col2 = st.columns([2, 1])
    
    with col1:
        render_journal_entries(trading_engine)
    
    with col2:
        render_performance_summary(trading_engine)
    
    st.markdown("---")
    
    # Analysis Section
    render_journal_analysis(trading_engine)

def render_journal_overview(trading_engine):
    """Render trading journal overview metrics"""
    
    st.markdown("### 📊 Journal Overview")
    
    # Get mock journal data
    journal_entries = get_mock_journal_entries()
    
    # Calculate metrics
    total_entries = len(journal_entries)
    winning_trades = len([e for e in journal_entries if e['outcome'] == 'Win'])
    losing_trades = len([e for e in journal_entries if e['outcome'] == 'Loss'])
    win_rate = (winning_trades / total_entries * 100) if total_entries > 0 else 0
    
    avg_win = np.mean([e['pnl'] for e in journal_entries if e['outcome'] == 'Win']) if winning_trades > 0 else 0
    avg_loss = np.mean([e['pnl'] for e in journal_entries if e['outcome'] == 'Loss']) if losing_trades > 0 else 0
    profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        render_metric_card(
            "Total Entries",
            str(total_entries),
            delta="Journal entries",
            icon="📝"
        )
    
    with col2:
        render_metric_card(
            "Win Rate",
            f"{win_rate:.1f}%",
            delta=f"{winning_trades}W / {losing_trades}L",
            icon="🎯"
        )
    
    with col3:
        render_metric_card(
            "Avg Win",
            f"${avg_win:.2f}",
            delta="Per winning trade",
            icon="💚"
        )
    
    with col4:
        render_metric_card(
            "Avg Loss",
            f"${avg_loss:.2f}",
            delta="Per losing trade",
            icon="💔"
        )
    
    with col5:
        render_metric_card(
            "Profit Factor",
            f"{profit_factor:.2f}",
            delta="Win/Loss ratio",
            icon="⚖️"
        )

def render_journal_entries(trading_engine):
    """Render journal entries list and details"""
    
    st.markdown("### 📔 Journal Entries")
    
    # Show add entry form if requested
    if st.session_state.get('show_add_entry_form', False):
        render_add_entry_form()
        return
    
    # Filter options
    col1, col2, col3 = st.columns(3)
    
    with col1:
        date_filter = st.selectbox(
            "Time Period",
            ["All Time", "Last 30 Days", "Last 7 Days", "Today"]
        )
    
    with col2:
        outcome_filter = st.selectbox(
            "Outcome",
            ["All", "Wins", "Losses", "Breakeven"]
        )
    
    with col3:
        strategy_filter = st.selectbox(
            "Strategy",
            ["All", "Momentum", "Mean Reversion", "Breakout", "Swing", "Scalping"]
        )
    
    # Get and display entries
    journal_entries = get_mock_journal_entries()
    
    # Sort by date (newest first)
    journal_entries.sort(key=lambda x: x['date'], reverse=True)
    
    # Display entries
    for i, entry in enumerate(journal_entries[:20]):  # Show last 20 entries
        with st.container():
            # Header
            col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
            
            with col1:
                outcome_emoji = "✅" if entry['outcome'] == 'Win' else "❌" if entry['outcome'] == 'Loss' else "➡️"
                st.markdown(f"**{outcome_emoji} {entry['symbol']} - {entry['strategy']}**")
                st.caption(f"{entry['date'].strftime('%Y-%m-%d %H:%M')} • {entry['side']}")
            
            with col2:
                st.metric("P&L", f"${entry['pnl']:+.2f}")
            
            with col3:
                st.metric("Quantity", f"{entry['quantity']:,}")
            
            with col4:
                st.metric("Entry", f"${entry['entry_price']:.2f}")
            
            # Expandable details
            with st.expander(f"📝 Details - {entry['symbol']}", expanded=False):
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("**Trade Details**")
                    st.write(f"**Entry Price:** ${entry['entry_price']:.2f}")
                    st.write(f"**Exit Price:** ${entry['exit_price']:.2f}")
                    st.write(f"**Quantity:** {entry['quantity']:,} shares")
                    st.write(f"**Duration:** {entry['duration']}")
                    st.write(f"**Strategy:** {entry['strategy']}")
                
                with col2:
                    st.markdown("**Analysis**")
                    st.write(f"**Setup:** {entry['setup']}")
                    st.write(f"**Risk/Reward:** {entry['risk_reward']}")
                    st.write(f"**Emotion:** {entry['emotion']}")
                    st.write(f"**Market Condition:** {entry['market_condition']}")
                
                st.markdown("**Rationale**")
                st.write(entry['rationale'])
                
                st.markdown("**Lessons Learned**")
                st.write(entry['lessons'])
                
                # Edit/Delete buttons
                col1, col2 = st.columns(2)
                with col1:
                    if st.button(f"✏️ Edit", key=f"edit_{i}"):
                        st.session_state[f'edit_entry_{i}'] = True
                        st.rerun()
                
                with col2:
                    if st.button(f"🗑️ Delete", key=f"delete_{i}"):
                        st.warning("Delete functionality would be implemented here")
            
            st.markdown("---")

def render_add_entry_form():
    """Render form to add new journal entry"""
    
    st.markdown("### ➕ Add New Journal Entry")
    
    with st.form("add_journal_entry"):
        col1, col2 = st.columns(2)
        
        with col1:
            symbol = st.text_input("Symbol", placeholder="e.g., AAPL")
            side = st.selectbox("Side", ["Buy", "Sell"])
            quantity = st.number_input("Quantity", min_value=1, value=100)
            entry_price = st.number_input("Entry Price", min_value=0.01, value=100.00, format="%.2f")
            exit_price = st.number_input("Exit Price", min_value=0.01, value=105.00, format="%.2f")
        
        with col2:
            strategy = st.selectbox(
                "Strategy",
                ["Momentum", "Mean Reversion", "Breakout", "Swing", "Scalping", "Other"]
            )
            setup = st.text_input("Setup Description", placeholder="e.g., Bull flag breakout")
            emotion = st.selectbox(
                "Emotional State",
                ["Confident", "Neutral", "Anxious", "Excited", "Fearful", "Greedy"]
            )
            market_condition = st.selectbox(
                "Market Condition",
                ["Trending Up", "Trending Down", "Sideways", "Volatile", "Low Volume"]
            )
            risk_reward = st.text_input("Risk/Reward Ratio", placeholder="e.g., 1:3")
        
        rationale = st.text_area(
            "Trade Rationale",
            placeholder="Why did you enter this trade? What was your thesis?"
        )
        
        lessons = st.text_area(
            "Lessons Learned",
            placeholder="What did you learn from this trade? What would you do differently?"
        )
        
        tags = st.text_input(
            "Tags",
            placeholder="e.g., earnings, technical, fundamental (comma-separated)"
        )
        
        col1, col2 = st.columns(2)
        
        with col1:
            submitted = st.form_submit_button("💾 Save Entry", type="primary")
        
        with col2:
            if st.form_submit_button("❌ Cancel"):
                st.session_state['show_add_entry_form'] = False
                st.rerun()
        
        if submitted:
            # Here you would save to database
            st.success("Journal entry saved successfully!")
            st.session_state['show_add_entry_form'] = False
            st.rerun()

def render_performance_summary(trading_engine):
    """Render performance summary and insights"""
    
    st.markdown("### 📈 Performance Summary")
    
    journal_entries = get_mock_journal_entries()
    
    # Performance metrics
    total_pnl = sum(entry['pnl'] for entry in journal_entries)
    win_rate = len([e for e in journal_entries if e['outcome'] == 'Win']) / len(journal_entries) * 100
    
    st.metric("Total P&L", f"${total_pnl:+,.2f}")
    st.metric("Win Rate", f"{win_rate:.1f}%")
    
    # Strategy performance
    st.markdown("#### 🎯 Strategy Performance")
    
    strategy_performance = {}
    for entry in journal_entries:
        strategy = entry['strategy']
        if strategy not in strategy_performance:
            strategy_performance[strategy] = []
        strategy_performance[strategy].append(entry['pnl'])
    
    strategy_summary = []
    for strategy, pnls in strategy_performance.items():
        strategy_summary.append({
            'Strategy': strategy,
            'Trades': len(pnls),
            'Total P&L': sum(pnls),
            'Avg P&L': np.mean(pnls),
            'Win Rate': len([p for p in pnls if p > 0]) / len(pnls) * 100
        })
    
    df_strategies = pd.DataFrame(strategy_summary)
    df_strategies = df_strategies.sort_values('Total P&L', ascending=False)
    
    for _, row in df_strategies.iterrows():
        with st.container():
            st.write(f"**{row['Strategy']}**")
            st.write(f"P&L: ${row['Total P&L']:+.2f} ({row['Trades']} trades)")
            st.write(f"Win Rate: {row['Win Rate']:.1f}%")
            st.markdown("---")
    
    # Recent insights
    st.markdown("#### 💡 Recent Insights")
    
    insights = [
        "Your momentum strategy has 85% win rate this month",
        "Consider reducing position size on Friday trades",
        "Best performance occurs in first 2 hours of market",
        "Emotional state 'Confident' correlates with better outcomes"
    ]
    
    for insight in insights:
        st.info(f"💡 {insight}")

def render_journal_analysis(trading_engine):
    """Render detailed journal analysis"""
    
    # Performance by Strategy
    with st.expander("📊 Performance by Strategy", expanded=False):
        render_strategy_analysis()
    
    # Emotional Analysis
    with st.expander("😊 Emotional Trading Analysis", expanded=False):
        render_emotional_analysis()
    
    # Time Analysis
    with st.expander("⏰ Time-based Analysis", expanded=False):
        render_time_analysis()
    
    # Lessons Learned
    with st.expander("📚 Lessons & Insights", expanded=False):
        render_lessons_analysis()

def render_strategy_analysis():
    """Render strategy performance analysis"""
    
    st.markdown("#### Strategy Performance Breakdown")
    
    journal_entries = get_mock_journal_entries()
    
    # Group by strategy
    strategy_data = {}
    for entry in journal_entries:
        strategy = entry['strategy']
        if strategy not in strategy_data:
            strategy_data[strategy] = []
        strategy_data[strategy].append(entry)
    
    # Create analysis
    for strategy, entries in strategy_data.items():
        st.markdown(f"**{strategy} Strategy**")
        
        total_trades = len(entries)
        wins = len([e for e in entries if e['outcome'] == 'Win'])
        losses = len([e for e in entries if e['outcome'] == 'Loss'])
        win_rate = wins / total_trades * 100 if total_trades > 0 else 0
        
        total_pnl = sum(e['pnl'] for e in entries)
        avg_win = np.mean([e['pnl'] for e in entries if e['outcome'] == 'Win']) if wins > 0 else 0
        avg_loss = np.mean([e['pnl'] for e in entries if e['outcome'] == 'Loss']) if losses > 0 else 0
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Trades", total_trades)
        
        with col2:
            st.metric("Win Rate", f"{win_rate:.1f}%")
        
        with col3:
            st.metric("Total P&L", f"${total_pnl:+.2f}")
        
        with col4:
            st.metric("Avg Win/Loss", f"${avg_win:.2f} / ${avg_loss:.2f}")
        
        st.markdown("---")

def render_emotional_analysis():
    """Render emotional state analysis"""
    
    st.markdown("#### Trading Performance by Emotional State")
    
    journal_entries = get_mock_journal_entries()
    
    # Group by emotion
    emotion_data = {}
    for entry in journal_entries:
        emotion = entry['emotion']
        if emotion not in emotion_data:
            emotion_data[emotion] = []
        emotion_data[emotion].append(entry['pnl'])
    
    emotions = list(emotion_data.keys())
    avg_pnl = [np.mean(emotion_data[emotion]) for emotion in emotions]
    win_rates = [len([p for p in emotion_data[emotion] if p > 0]) / len(emotion_data[emotion]) * 100 for emotion in emotions]
    
    # Performance by emotion chart
    fig_emotion = go.Figure()
    
    fig_emotion.add_trace(go.Bar(
        x=emotions,
        y=avg_pnl,
        name='Avg P&L',
        marker_color=['green' if x > 0 else 'red' for x in avg_pnl]
    ))
    
    fig_emotion.update_layout(
        title="Average P&L by Emotional State",
        xaxis_title="Emotional State",
        yaxis_title="Average P&L ($)",
        template="plotly_dark",
        height=400
    )
    
    st.plotly_chart(fig_emotion, use_container_width=True, key="emotion_pnl_chart")
    
    # Win rate by emotion
    fig_emotion_wr = go.Figure(data=[
        go.Bar(x=emotions, y=win_rates, marker_color='lightblue')
    ])
    
    fig_emotion_wr.update_layout(
        title="Win Rate by Emotional State",
        xaxis_title="Emotional State",
        yaxis_title="Win Rate (%)",
        template="plotly_dark",
        height=300
    )
    
    st.plotly_chart(fig_emotion_wr, use_container_width=True, key="emotion_winrate_chart")

def render_time_analysis():
    """Render time-based trading analysis"""
    
    st.markdown("#### Time-based Trading Patterns")
    
    # Mock data for time analysis
    hours = list(range(9, 16))  # Market hours
    hourly_pnl = [np.random.uniform(-50, 100) for _ in hours]
    hourly_trades = [np.random.randint(1, 8) for _ in hours]
    
    # Performance by hour
    fig_hourly = go.Figure()
    
    fig_hourly.add_trace(go.Bar(
        x=hours,
        y=hourly_pnl,
        name='P&L by Hour',
        marker_color=['green' if x > 0 else 'red' for x in hourly_pnl]
    ))
    
    fig_hourly.update_layout(
        title="Trading Performance by Hour of Day",
        xaxis_title="Hour (EST)",
        yaxis_title="Average P&L ($)",
        template="plotly_dark",
        height=400
    )
    
    st.plotly_chart(fig_hourly, use_container_width=True, key="hourly_pnl_chart")
    
    # Day of week analysis
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    daily_pnl = [np.random.uniform(-30, 80) for _ in days]
    
    fig_daily = go.Figure(data=[
        go.Bar(
            x=days,
            y=daily_pnl,
            marker_color=['green' if x > 0 else 'red' for x in daily_pnl]
        )
    ])
    
    fig_daily.update_layout(
        title="Trading Performance by Day of Week",
        xaxis_title="Day",
        yaxis_title="Average P&L ($)",
        template="plotly_dark",
        height=300
    )
    
    st.plotly_chart(fig_daily, use_container_width=True, key="daily_pnl_chart")

def render_lessons_analysis():
    """Render lessons learned and insights"""
    
    st.markdown("#### Key Lessons & Insights")
    
    # Common lessons from journal entries
    lessons_summary = [
        "Wait for clear breakout confirmation before entry",
        "Set stop losses immediately after entry",
        "Avoid trading during first 30 minutes of market open",
        "Position sizing is crucial for risk management",
        "Emotional trading leads to poor decisions",
        "Follow the trading plan strictly",
        "Don't chase momentum without proper setup"
    ]
    
    st.markdown("**Most Frequent Lessons:**")
    for i, lesson in enumerate(lessons_summary, 1):
        st.write(f"{i}. {lesson}")
    
    st.markdown("---")
    
    # Improvement areas
    st.markdown("#### Areas for Improvement")
    
    improvement_areas = [
        {"area": "Entry Timing", "priority": "High", "action": "Use more precise entry signals"},
        {"area": "Risk Management", "priority": "High", "action": "Implement consistent position sizing"},
        {"area": "Emotional Control", "priority": "Medium", "action": "Develop pre-trade checklist"},
        {"area": "Exit Strategy", "priority": "Medium", "action": "Define clear profit targets"}
    ]
    
    for area in improvement_areas:
        col1, col2, col3 = st.columns([2, 1, 3])
        
        with col1:
            st.write(f"**{area['area']}**")
        
        with col2:
            priority_color = "🔴" if area['priority'] == "High" else "🟡"
            st.write(f"{priority_color} {area['priority']}")
        
        with col3:
            st.write(area['action'])

def get_mock_journal_entries():
    """Generate mock journal entries for demonstration"""
    
    entries = []
    symbols = ['AAPL', 'GOOGL', 'TSLA', 'MSFT', 'NVDA', 'META', 'AMZN']
    strategies = ['Momentum', 'Mean Reversion', 'Breakout', 'Swing', 'Scalping']
    emotions = ['Confident', 'Neutral', 'Anxious', 'Excited', 'Fearful']
    setups = ['Bull flag', 'Support bounce', 'Breakout', 'Pullback', 'Gap fill']
    
    for i in range(50):  # Generate 50 mock entries
        entry_price = np.random.uniform(50, 300)
        is_win = np.random.random() > 0.4  # 60% win rate
        
        if is_win:
            exit_price = entry_price * np.random.uniform(1.01, 1.08)
            outcome = 'Win'
        else:
            exit_price = entry_price * np.random.uniform(0.92, 0.99)
            outcome = 'Loss'
        
        quantity = np.random.randint(50, 500)
        pnl = (exit_price - entry_price) * quantity
        
        entry = {
            'date': datetime.now() - timedelta(days=np.random.randint(0, 90)),
            'symbol': np.random.choice(symbols),
            'side': 'Buy',  # Simplified for demo
            'strategy': np.random.choice(strategies),
            'quantity': quantity,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'pnl': pnl,
            'outcome': outcome,
            'setup': np.random.choice(setups),
            'emotion': np.random.choice(emotions),
            'risk_reward': f"1:{np.random.randint(2, 5)}",
            'market_condition': 'Trending Up',
            'duration': f"{np.random.randint(1, 480)} minutes",
            'rationale': "Technical analysis indicated strong momentum with volume confirmation.",
            'lessons': "Should have taken partial profits at 50% target level."
        }
        
        entries.append(entry)
    
    return entries

def show_add_entry_form():
    """Show the add entry form"""
    st.session_state['show_add_entry_form'] = True
    st.rerun()

def refresh_journal_data():
    """Refresh journal data"""
    st.cache_data.clear()
    st.success("Journal data refreshed!")
    st.rerun()

def generate_journal_report():
    """Generate comprehensive journal report"""
    st.info("Journal report generation feature would be implemented here")
    # In a real implementation, this would generate a detailed trading performance report