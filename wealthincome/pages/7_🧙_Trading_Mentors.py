import streamlit as st
import pandas as pd
import json
import os
import sys
from datetime import datetime

# --- Start of Path Fix ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)
# --- End of Path Fix ---

# Try to import analytics module
try:
    from paper_trading_analytics import TradingAnalytics
    analytics_available = True
except ImportError:
    analytics_available = False

# Page config
try:
    st.set_page_config(page_title="🧙 Trading Mentors", layout="wide")
except st.errors.StreamlitAPIException:
    pass

# Trading Mentors Database
MENTORS = {
    "Warren Buffett": {
        "style": "Value Investing",
        "emoji": "🏛️",
        "principles": [
            "Be fearful when others are greedy, greedy when others are fearful",
            "Time in the market beats timing the market",
            "Never invest in a business you cannot understand",
            "Risk comes from not knowing what you're doing"
        ],
        "rules": {
            "position_size": "Concentrate on best ideas (5-10% per position)",
            "holding_period": "Years to decades",
            "stop_loss": "Rarely uses stops - focuses on business fundamentals",
            "entry": "Buy wonderful companies at fair prices"
        },
        "red_flags": ["Day trading", "Excessive leverage", "Speculation", "Trading on tips"]
    },
    
    "Paul Tudor Jones": {
        "style": "Macro Trading",
        "emoji": "🌍",
        "principles": [
            "Don't focus on making money, focus on protecting what you have",
            "The most important rule is to play great defense",
            "I believe the very best money is made at the market turns",
            "Losers average losers"
        ],
        "rules": {
            "position_size": "1-2% risk per trade maximum",
            "holding_period": "Days to months",
            "stop_loss": "Always use stops - typically 5-7%",
            "entry": "Trade at inflection points with asymmetric risk/reward"
        },
        "red_flags": ["No stop loss", "Adding to losers", "Ignoring macro trends", "Over-leveraging"]
    },
    
    "Jesse Livermore": {
        "style": "Momentum Trading",
        "emoji": "📈",
        "principles": [
            "The big money is not in buying and selling, but in waiting",
            "Trade only when the market is clearly bullish or bearish",
            "Never average down",
            "Let profits run, cut losses quickly"
        ],
        "rules": {
            "position_size": "Pyramid into winners, never losers",
            "holding_period": "Follow the trend",
            "stop_loss": "Exit when the trend changes",
            "entry": "Buy on breakouts from consolidation"
        },
        "red_flags": ["Fighting the trend", "Averaging down", "No patience", "Emotional trading"]
    },
    
    "George Soros": {
        "style": "Reflexivity Trading",
        "emoji": "🔄",
        "principles": [
            "It's not whether you're right or wrong, but how much you make when right",
            "Markets are always biased in one direction or another",
            "The worse a situation becomes, the less it takes to turn it around",
            "Find the trend whose premise is false, and bet against it"
        ],
        "rules": {
            "position_size": "Go big when conviction is high",
            "holding_period": "Until the thesis plays out",
            "stop_loss": "Exit when thesis is proven wrong",
            "entry": "Enter when market perception diverges from reality"
        },
        "red_flags": ["Small thinking", "Ignoring market psychology", "No thesis", "Following the crowd"]
    },
    
    "Peter Lynch": {
        "style": "Growth at Reasonable Price (GARP)",
        "emoji": "🏪",
        "principles": [
            "Know what you own, and know why you own it",
            "The best stock to buy is the one you already own",
            "Go for a business that any idiot can run",
            "In the long run, it's not just how much you make but how much you keep"
        ],
        "rules": {
            "position_size": "Diversify across 5-10 stocks you understand",
            "holding_period": "3-5 years typically",
            "stop_loss": "Sell when fundamentals deteriorate",
            "entry": "Buy growth at reasonable valuations (PEG < 1)"
        },
        "red_flags": ["Buying hype", "No research", "Ignoring valuation", "Panic selling"]
    },
    
    "Ray Dalio": {
        "style": "Systematic Macro",
        "emoji": "⚖️",
        "principles": [
            "He who lives by the crystal ball will eat shattered glass",
            "Diversification is the Holy Grail of investing",
            "Pain + Reflection = Progress",
            "Truth - or more precisely, an accurate understanding of reality - is the essential foundation"
        ],
        "rules": {
            "position_size": "Risk parity across uncorrelated assets",
            "holding_period": "Based on economic cycles",
            "stop_loss": "Systematic rules, not discretionary",
            "entry": "Enter based on fundamental economic principles"
        },
        "red_flags": ["No systematic approach", "Emotional decisions", "Ignoring correlations", "No learning from mistakes"]
    },
    
    "Stanley Druckenmiller": {
        "style": "Opportunistic Trading",
        "emoji": "🎯",
        "principles": [
            "It's not whether you're right or wrong, it's how much you make when right",
            "The way to build superior long-term returns is through preservation of capital",
            "Put all your eggs in one basket and watch that basket very carefully",
            "Never, ever invest in the present"
        ],
        "rules": {
            "position_size": "Bet big when odds are heavily in favor",
            "holding_period": "Flexible - hours to years",
            "stop_loss": "Exit when wrong, no ego",
            "entry": "Trade with a 3:1 reward/risk minimum"
        },
        "red_flags": ["Stubborn positions", "No flexibility", "Poor risk/reward", "Trading without conviction"]
    },
    
    "Ed Seykota": {
        "style": "Trend Following",
        "emoji": "🌊",
        "principles": [
            "The trend is your friend until the end",
            "Ride winners and cut losers",
            "Risk no more than you can afford to lose",
            "The elements of good trading are: cutting losses, riding winners, and managing risk"
        ],
        "rules": {
            "position_size": "Fixed fractional position sizing",
            "holding_period": "As long as trend persists",
            "stop_loss": "ATR-based trailing stops",
            "entry": "Enter on confirmed trend breaks"
        },
        "red_flags": ["Fighting trends", "No risk management", "Prediction over reaction", "Overtrading"]
    },
    
    "William O'Neil": {
        "style": "CANSLIM Growth",
        "emoji": "📊",
        "principles": [
            "The whole secret to winning in the stock market is to lose the least amount possible",
            "What seems too high usually goes higher",
            "All stocks are bad. There are no good stocks unless they go up",
            "The market will tell you when you're wrong"
        ],
        "rules": {
            "position_size": "Start with 1/2 position, add on proof",
            "holding_period": "8-12 weeks average",
            "stop_loss": "7-8% maximum loss from entry",
            "entry": "Buy at pivot points from sound bases"
        },
        "red_flags": ["Buying on dips", "No sell rules", "Ignoring market direction", "Bottom fishing"]
    },
    
    "Mark Minervini": {
        "style": "Momentum Growth",
        "emoji": "🚀",
        "principles": [
            "Specific entry points are crucial",
            "Risk management is paramount",
            "Trade with the trend",
            "Let the market prove you right or wrong quickly"
        ],
        "rules": {
            "position_size": "Risk 0.5-1% per trade",
            "holding_period": "Weeks to months",
            "stop_loss": "Place stops at logical support",
            "entry": "Buy superperformance stocks at low-risk entry points"
        },
        "red_flags": ["Wide stops", "No trading plan", "Chasing extended moves", "Trading against the trend"]
    }
}

# Load paper trading data
TRADE_LOG_DIR = "data/persistent"
TRADE_LOG = os.path.join(TRADE_LOG_DIR, "paper_trades_pro.csv")
MENTOR_FEEDBACK_LOG = os.path.join(TRADE_LOG_DIR, "mentor_feedback.json")

def load_trades():
    if os.path.exists(TRADE_LOG):
        return pd.read_csv(TRADE_LOG)
    return pd.DataFrame()

def load_mentor_feedback():
    if os.path.exists(MENTOR_FEEDBACK_LOG):
        with open(MENTOR_FEEDBACK_LOG, 'r') as f:
            return json.load(f)
    return {}

def save_mentor_feedback(feedback):
    os.makedirs(TRADE_LOG_DIR, exist_ok=True)
    with open(MENTOR_FEEDBACK_LOG, 'w') as f:
        json.dump(feedback, f, indent=2)

# Analyze trading style
def analyze_trading_style(trades_df):
    """Determine trader's natural style based on their trades"""
    if trades_df.empty:
        return None
    
    closed_trades = trades_df[trades_df['Status'] == 'Closed']
    if closed_trades.empty:
        return None
    
    # Calculate average hold time
    avg_hold_time = closed_trades['Hold_Time'].mean() if 'Hold_Time' in closed_trades else 0
    
    # Determine style based on metrics
    if avg_hold_time < 8:  # Less than 8 hours
        primary_style = "Day Trading"
        matching_mentors = ["Paul Tudor Jones", "Jesse Livermore", "Mark Minervini"]
    elif avg_hold_time < 24 * 7:  # Less than a week
        primary_style = "Swing Trading"
        matching_mentors = ["William O'Neil", "Mark Minervini", "Stanley Druckenmiller"]
    else:
        primary_style = "Position Trading"
        matching_mentors = ["Warren Buffett", "Peter Lynch", "Ray Dalio"]
    
    return {
        "style": primary_style,
        "avg_hold_time": avg_hold_time,
        "matching_mentors": matching_mentors
    }

# Generate mentor feedback
def get_mentor_feedback(mentor_name, trades_df, recent_trades=5):
    """Generate specific feedback from a mentor based on recent trades"""
    mentor = MENTORS[mentor_name]
    feedback = []
    
    if trades_df.empty:
        return ["Start trading to receive personalized mentor feedback!"]
    
    recent = trades_df.tail(recent_trades)
    
    # Check for red flags
    for _, trade in recent.iterrows():
        # Check stop loss usage
        if mentor_name in ["Paul Tudor Jones", "William O'Neil"] and pd.isna(trade.get('Stop_Loss')):
            feedback.append(f"❌ {mentor_name} says: 'No stop loss on {trade['Ticker']}? That's playing with fire!'")
        
        # Check position sizing
        if trade.get('Risk_Amount', 0) > trade.get('Position_Size', 1) * 0.02 and mentor_name == "Mark Minervini":
            feedback.append(f"⚠️ {mentor_name} warns: 'Your risk on {trade['Ticker']} exceeds 2% - reduce position size!'")
        
        # Check hold time
        if mentor_name == "Warren Buffett" and trade.get('Hold_Time', 0) < 24:
            feedback.append(f"🤔 {mentor_name} notes: 'You sold {trade['Ticker']} after {trade.get('Hold_Time', 0):.1f} hours? I prefer decades!'")
    
    # Add positive feedback
    winning_trades = recent[recent['PnL_Dollar'] > 0] if 'PnL_Dollar' in recent else pd.DataFrame()
    if len(winning_trades) > len(recent) * 0.6:
        feedback.append(f"✅ {mentor_name} approves: 'Good win rate recently. {mentor['principles'][0]}'")
    
    # Style-specific advice
    if mentor['style'] == "Momentum Trading" and len(recent) > 0:
        feedback.append(f"💡 {mentor_name} tip: 'Remember - {mentor['principles'][3]}'")
    
    return feedback if feedback else [f"{mentor_name}: 'Keep trading and I'll provide specific feedback.'"]

# Main UI
st.title("🧙 Trading Mentors - Learn from the Legends")

# Load data
trades_df = load_trades()
mentor_feedback = load_mentor_feedback()

# Top section - Trading style analysis
if not trades_df.empty:
    style_analysis = analyze_trading_style(trades_df)
    if style_analysis:
        st.info(f"📊 Based on your trading history, your style appears to be **{style_analysis['style']}** "
                f"(avg hold: {style_analysis['avg_hold_time']:.1f} hours). "
                f"Consider learning from: {', '.join(style_analysis['matching_mentors'])}")

# Main tabs
tab1, tab2, tab3, tab4 = st.tabs(["👥 Meet the Mentors", "📝 Mentor Feedback", "📊 Style Comparison", "🎓 Trading Wisdom"])

with tab1:
    st.header("👥 Meet Your Trading Mentors")
    
    # Mentor selection
    selected_mentor = st.selectbox(
        "Choose a mentor to learn from:",
        list(MENTORS.keys()),
        format_func=lambda x: f"{MENTORS[x]['emoji']} {x} - {MENTORS[x]['style']}"
    )
    
    if selected_mentor:
        mentor = MENTORS[selected_mentor]
        
        col1, col2 = st.columns([1, 2])
        
        with col1:
            st.markdown(f"# {mentor['emoji']}")
            st.subheader(selected_mentor)
            st.caption(f"Style: {mentor['style']}")
        
        with col2:
            st.markdown("### Core Principles")
            for principle in mentor['principles']:
                st.write(f"• {principle}")
        
        # Trading rules
        st.markdown("### 📋 Trading Rules")
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Position Size", mentor['rules']['position_size'])
        with col2:
            st.metric("Holding Period", mentor['rules']['holding_period'])
        with col3:
            st.metric("Stop Loss", mentor['rules']['stop_loss'])
        with col4:
            st.metric("Entry Strategy", mentor['rules']['entry'])
        
        # Red flags
        st.markdown("### 🚩 Red Flags to Avoid")
        cols = st.columns(len(mentor['red_flags']))
        for i, flag in enumerate(mentor['red_flags']):
            with cols[i]:
                st.error(flag)
        
        # Get personalized feedback
        if st.button(f"Get {selected_mentor}'s Feedback on Your Trading"):
            feedback = get_mentor_feedback(selected_mentor, trades_df)
            
            st.markdown("### 💬 Personal Feedback")
            for item in feedback:
                st.write(item)
            
            # Save feedback
            if selected_mentor not in mentor_feedback:
                mentor_feedback[selected_mentor] = []
            
            mentor_feedback[selected_mentor].append({
                "date": datetime.now().isoformat(),
                "feedback": feedback
            })
            save_mentor_feedback(mentor_feedback)

with tab2:
    st.header("📝 Your Mentor Feedback History")
    
    if mentor_feedback:
        for mentor_name, sessions in mentor_feedback.items():
            with st.expander(f"{MENTORS[mentor_name]['emoji']} {mentor_name} ({len(sessions)} sessions)"):
                for session in sessions[-5:]:  # Show last 5 sessions
                    st.caption(f"Date: {session['date'][:10]}")
                    for feedback_item in session['feedback']:
                        st.write(feedback_item)
                    st.markdown("---")
    else:
        st.info("No feedback yet. Select a mentor and request feedback to get started!")
    
    # Performance comparison with mentor styles
    if not trades_df.empty and analytics_available:
        st.markdown("### 📊 Your Performance vs Mentor Expectations")
        
        analytics = TradingAnalytics(trades_df)
        metrics = analytics.calculate_advanced_metrics()
        
        # Create comparison table
        comparison_data = []
        
        if metrics['total_trades'] > 0:
            your_win_rate = metrics['winning_trades'] / metrics['total_trades'] * 100
            your_avg_hold = trades_df[trades_df['Status'] == 'Closed']['Hold_Time'].mean() if 'Hold_Time' in trades_df else 0
            
            comparison_data.append({
                "Mentor": "Your Stats",
                "Expected Win Rate": f"{your_win_rate:.1f}%",
                "Expected Hold Time": f"{your_avg_hold:.1f} hours",
                "Risk per Trade": f"{trades_df['Risk_Amount'].mean() / 100000 * 100:.1f}%" if 'Risk_Amount' in trades_df else "N/A"
            })
            
            # Add mentor benchmarks
            comparison_data.extend([
                {
                    "Mentor": "Day Traders (Jones, Livermore)",
                    "Expected Win Rate": "45-55%",
                    "Expected Hold Time": "0.5-6 hours",
                    "Risk per Trade": "0.5-2%"
                },
                {
                    "Mentor": "Swing Traders (O'Neil, Minervini)",
                    "Expected Win Rate": "50-65%",
                    "Expected Hold Time": "2-10 days",
                    "Risk per Trade": "1-2%"
                },
                {
                    "Mentor": "Position Traders (Buffett, Lynch)",
                    "Expected Win Rate": "60-80%",
                    "Expected Hold Time": "Months-Years",
                    "Risk per Trade": "5-10%"
                }
            ])
            
            comparison_df = pd.DataFrame(comparison_data)
            st.dataframe(comparison_df, use_container_width=True, hide_index=True)

with tab3:
    st.header("📊 Trading Style Comparison")
    
    # Create a comparison matrix
    st.subheader("Which mentor's style fits you best?")
    
    style_matrix = []
    for mentor_name, mentor_info in MENTORS.items():
        style_matrix.append({
            "Mentor": f"{mentor_info['emoji']} {mentor_name}",
            "Style": mentor_info['style'],
            "Best For": "Short-term gains" if "Day" in mentor_info['style'] or "Momentum" in mentor_info['style'] 
                       else "Long-term wealth" if "Value" in mentor_info['style'] 
                       else "Balanced approach",
            "Risk Level": "High" if mentor_name in ["Jesse Livermore", "George Soros"] 
                         else "Low" if mentor_name in ["Warren Buffett", "Ray Dalio"] 
                         else "Medium",
            "Time Commitment": "High (Daily)" if "Day" in mentor_info['style'] 
                              else "Low (Weekly)" if "Value" in mentor_info['style'] 
                              else "Medium",
            "Complexity": "High" if mentor_name in ["George Soros", "Ray Dalio"] 
                         else "Low" if mentor_name in ["Peter Lynch", "Warren Buffett"] 
                         else "Medium"
        })
    
    style_df = pd.DataFrame(style_matrix)
    st.dataframe(style_df, use_container_width=True, hide_index=True)
    
    # Style quiz
    st.markdown("### 🎯 Find Your Ideal Mentor")
    
    col1, col2 = st.columns(2)
    
    with col1:
        time_preference = st.radio(
            "How much time can you dedicate to trading?",
            ["Several hours daily", "A few hours weekly", "Monthly check-ins"]
        )
        
        risk_tolerance = st.radio(
            "What's your risk tolerance?",
            ["High - I can handle 20%+ swings", "Medium - 10% moves are my limit", "Low - Preserve capital first"]
        )
    
    with col2:
        goal = st.radio(
            "What's your primary goal?",
            ["Quick profits", "Steady growth", "Long-term wealth"]
        )
        
        experience = st.radio(
            "What's your experience level?",
            ["Beginner", "Intermediate", "Advanced"]
        )
    
    if st.button("Find My Ideal Mentor"):
        # Simple matching logic
        ideal_mentors = []
        
        if time_preference == "Several hours daily":
            ideal_mentors.extend(["Paul Tudor Jones", "Jesse Livermore", "Mark Minervini"])
        elif time_preference == "Monthly check-ins":
            ideal_mentors.extend(["Warren Buffett", "Peter Lynch"])
        
        if risk_tolerance == "Low - Preserve capital first":
            ideal_mentors.extend(["Warren Buffett", "Ray Dalio"])
        elif risk_tolerance == "High - I can handle 20%+ swings":
            ideal_mentors.extend(["George Soros", "Stanley Druckenmiller"])
        
        # Count occurrences
        mentor_scores = {}
        for mentor in ideal_mentors:
            mentor_scores[mentor] = mentor_scores.get(mentor, 0) + 1
        
        if mentor_scores:
            best_mentor = max(mentor_scores, key=mentor_scores.get)
            st.success(f"🎯 Your ideal mentor is: **{best_mentor}**")
            st.info(f"Style: {MENTORS[best_mentor]['style']}")
            st.write("Key principle to start with:")
            st.quote(MENTORS[best_mentor]['principles'][0])

with tab4:
    st.header("🎓 Trading Wisdom Library")
    
    # Categorized wisdom
    categories = {
        "Risk Management": [],
        "Entry Strategy": [],
        "Exit Strategy": [],
        "Psychology": [],
        "Position Sizing": []
    }
    
    # Populate categories
    for mentor_name, mentor_info in MENTORS.items():
        for i, principle in enumerate(mentor_info['principles']):
            if any(word in principle.lower() for word in ['risk', 'protect', 'lose']):
                categories["Risk Management"].append((mentor_name, principle))
            elif any(word in principle.lower() for word in ['buy', 'enter', 'entry']):
                categories["Entry Strategy"].append((mentor_name, principle))
            elif any(word in principle.lower() for word in ['sell', 'exit', 'cut']):
                categories["Exit Strategy"].append((mentor_name, principle))
            elif any(word in principle.lower() for word in ['fear', 'greed', 'emotion', 'patient']):
                categories["Psychology"].append((mentor_name, principle))
            else:
                categories["Position Sizing"].append((mentor_name, principle))
    
    # Display wisdom by category
    selected_category = st.selectbox("Choose a topic:", list(categories.keys()))
    
    if selected_category:
        st.subheader(f"📚 {selected_category} Wisdom")
        
        for mentor, principle in categories[selected_category]:
            col1, col2 = st.columns([1, 4])
            with col1:
                st.write(f"{MENTORS[mentor]['emoji']} **{mentor}**")
            with col2:
                st.quote(principle)
        
    # Daily wisdom
    st.markdown("### 💡 Today's Trading Wisdom")
    
    # Simple rotation based on day
    import random
    random.seed(datetime.now().day)
    all_principles = []
    for mentor_name, mentor_info in MENTORS.items():
        for principle in mentor_info['principles']:
            all_principles.append((mentor_name, principle))
    
    daily_wisdom = random.choice(all_principles)
    st.info(f"{MENTORS[daily_wisdom[0]]['emoji']} **{daily_wisdom[0]}** says:")
    st.quote(daily_wisdom[1])

# Footer
st.markdown("---")
st.caption("🧙 Remember: Learn from the masters, but develop your own style that fits your personality and life situation.")
