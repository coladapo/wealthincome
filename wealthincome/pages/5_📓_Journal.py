import sys
import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import json

# --- Start of Path Fix ---
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)
# --- End of Path Fix ---

# Import data_manager
try:
    from data_manager import data_manager
except ImportError:
    st.error("🚨 Failed to import 'data_manager'. Please ensure 'data_manager.py' exists in the root directory.")
    st.stop()

# Page config
try:
    st.set_page_config(page_title="📓 Trade Journal", layout="wide")
except st.errors.StreamlitAPIException:
    pass

st.title('📓 Trade Journal & Performance Tracker')

# Initialize session state
if 'current_trade' not in st.session_state:
    st.session_state.current_trade = {}

# Check if coming from another page with a ticker
if 'journal_ticker' in st.session_state and st.session_state.journal_ticker:
    st.session_state.current_trade['ticker'] = st.session_state.journal_ticker
    del st.session_state.journal_ticker

# Tabs
tab1, tab2, tab3, tab4 = st.tabs(["📝 New Trade", "📊 Trade History", "📈 Analytics", "🎯 Signal Performance"])

with tab1:
    st.header("📝 Log New Trade")
    
    # Trade entry form
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Entry Details")
        
        # Pre-fill ticker if available
        default_ticker = st.session_state.current_trade.get('ticker', '')
        ticker = st.text_input("Ticker Symbol", value=default_ticker, placeholder="AAPL").upper()
        
        # Trade type
        trade_type = st.selectbox("Trade Type", ["Day Trade", "Swing Trade", "Position Trade"])
        
        # Entry details
        entry_date = st.date_input("Entry Date", value=datetime.now())
        entry_time = st.time_input("Entry Time", value=datetime.now().time())
        entry_price = st.number_input("Entry Price", min_value=0.01, step=0.01, format="%.2f")
        shares = st.number_input("Shares", min_value=1, step=1)
        
        # Signal source
        signal_source = st.multiselect(
            "Signal Source(s)",
            ["AI Scanner", "News Sentiment", "Technical Pattern", "Manual Analysis", "Other"]
        )
        
        # Notes
        entry_notes = st.text_area("Entry Notes", placeholder="Why did you enter this trade?")
    
    with col2:
        st.subheader("Exit Details (Optional)")
        
        # Exit details
        is_closed = st.checkbox("Trade Closed?")
        
        if is_closed:
            exit_date = st.date_input("Exit Date", value=datetime.now(), key="exit_date")
            exit_time = st.time_input("Exit Time", value=datetime.now().time(), key="exit_time")
            exit_price = st.number_input("Exit Price", min_value=0.01, step=0.01, format="%.2f", key="exit_price")
            
            # Calculate P&L
            if entry_price > 0 and exit_price > 0:
                pnl = (exit_price - entry_price) * shares
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                
                col_pnl1, col_pnl2 = st.columns(2)
                with col_pnl1:
                    st.metric("P&L", f"${pnl:.2f}", f"{pnl_pct:.2f}%")
                with col_pnl2:
                    st.metric("Result", "WIN" if pnl > 0 else "LOSS", 
                             delta_color="normal" if pnl > 0 else "inverse")
            
            exit_notes = st.text_area("Exit Notes", placeholder="Why did you exit?")
        else:
            exit_date = None
            exit_time = None
            exit_price = None
            pnl = None
            pnl_pct = None
            exit_notes = ""
    
    # Get current analysis if ticker provided
    if ticker and st.button("🔍 Get Current Analysis", use_container_width=True):
        with st.spinner("Analyzing..."):
            analysis = data_manager.get_combined_analysis(ticker)
            
            if 'error' not in analysis:
                st.success("✅ Analysis complete!")
                
                # Display analysis
                with st.expander("📊 Current Analysis", expanded=True):
                    # Scores
                    if 'scores' in analysis:
                        st.subheader("Scores")
                        scores = analysis['scores']
                        
                        if 'technical' in scores:
                            tech = scores['technical']
                            col_s1, col_s2, col_s3 = st.columns(3)
                            with col_s1:
                                st.metric("Day Score", f"{tech.get('day_score', 0):.0f}")
                            with col_s2:
                                st.metric("Swing Score", f"{tech.get('swing_score', 0):.0f}")
                            with col_s3:
                                st.metric("Momentum", f"{tech.get('momentum', 0):.0f}")
                        
                        if 'sentiment' in scores:
                            sent = scores['sentiment']
                            st.metric("News Sentiment", sent['label'], f"Score: {sent['score']:.2f}")
                    
                    # Signals
                    if 'signals' in analysis and analysis['signals']:
                        st.subheader("Active Signals")
                        for signal in analysis['signals']:
                            st.write(f"- **{signal['type']}** ({signal['strength']}): {signal['reason']}")
                    
                    # Recommendations
                    if 'recommendations' in analysis and analysis['recommendations']:
                        st.subheader("Recommendations")
                        for rec in analysis['recommendations']:
                            st.info(rec)
                
                # Store analysis in session state
                st.session_state.current_trade['analysis'] = analysis
            else:
                st.error(f"Analysis failed: {analysis['error']}")
    
  # Save trade button
st.markdown("---")
if st.button("💾 Save Trade", type="primary", use_container_width=True):
    if ticker and entry_price > 0 and shares > 0:
        # Prepare trade data with proper types
        trade_data = {
            'ticker': str(ticker),
            'trade_type': str(trade_type),
            'entry_date': entry_date.isoformat(),
            'entry_time': entry_time.isoformat(),
            'entry_price': float(entry_price),
            'shares': int(shares),
            'position_size': float(entry_price * shares),
            'signal_source': list(signal_source),  # Convert to list
            'entry_notes': str(entry_notes),
            'is_closed': bool(is_closed)
        }
        
        # Add exit data if closed
        if is_closed:
            trade_data.update({
                'exit_date': exit_date.isoformat() if exit_date else None,
                'exit_time': exit_time.isoformat() if exit_time else None,
                'exit_price': float(exit_price) if exit_price else None,
                'profit_loss': float(pnl) if pnl is not None else None,
                'profit_loss_pct': float(pnl_pct) if pnl_pct is not None else None,
                'exit_notes': str(exit_notes)
            })
        
        # Add analysis if available
        if 'analysis' in st.session_state.current_trade:
            # Don't include the full analysis object, just key metrics
            analysis = st.session_state.current_trade['analysis']
            trade_data['entry_scores'] = {
                'day_score': analysis.get('scores', {}).get('technical', {}).get('day_score', 0),
                'swing_score': analysis.get('scores', {}).get('technical', {}).get('swing_score', 0),
                'ai_score': analysis.get('scores', {}).get('technical', {}).get('ai_score', 0)
            }
        
        # Debug info
        if st.session_state.get('debug_mode', False):
            st.write("Trade data to save:", trade_data)
        
        # Save using enhanced method
        try:
            if data_manager.add_trade_with_context(trade_data):
                st.success("✅ Trade saved successfully!")
                st.balloons()
                
                # Clear form
                st.session_state.current_trade = {}
                st.rerun()
            else:
                st.error("Failed to save trade. Check the console for details.")
        except Exception as e:
            st.error(f"Error saving trade: {str(e)}")
            if st.session_state.get('debug_mode', False):
                st.exception(e)
    else:
        st.warning("Please fill in all required fields (ticker, entry price, shares)")

with tab2:
    st.header("📊 Trade History")
    
    # Get trades
    trades = data_manager.get_trade_journal()
    
    if trades:
        # Convert to DataFrame
        df = pd.DataFrame(trades)
        
        # Filter options
        col_f1, col_f2, col_f3 = st.columns(3)
        
        with col_f1:
            filter_ticker = st.selectbox("Filter by Ticker", 
                                        ["All"] + sorted(df['ticker'].unique().tolist()))
        
        with col_f2:
            filter_type = st.selectbox("Filter by Type", 
                                      ["All"] + sorted(df['trade_type'].unique().tolist()))
        
        with col_f3:
            filter_status = st.selectbox("Filter by Status", 
                                        ["All", "Open", "Closed"])
        
        # Apply filters
        filtered_df = df.copy()
        
        if filter_ticker != "All":
            filtered_df = filtered_df[filtered_df['ticker'] == filter_ticker]
        
        if filter_type != "All":
            filtered_df = filtered_df[filtered_df['trade_type'] == filter_type]
        
        if filter_status == "Open":
            filtered_df = filtered_df[filtered_df['is_closed'] == False]
        elif filter_status == "Closed":
            filtered_df = filtered_df[filtered_df['is_closed'] == True]
        
        # Display metrics
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        
        closed_trades = filtered_df[filtered_df['is_closed'] == True]
        
        with col_m1:
            st.metric("Total Trades", len(filtered_df))
        
        with col_m2:
            st.metric("Open Trades", len(filtered_df[filtered_df['is_closed'] == False]))
        
        with col_m3:
            if len(closed_trades) > 0:
                total_pnl = closed_trades['profit_loss'].sum()
                st.metric("Total P&L", f"${total_pnl:.2f}")
            else:
                st.metric("Total P&L", "$0.00")
        
        with col_m4:
            if len(closed_trades) > 0:
                win_rate = len(closed_trades[closed_trades['profit_loss'] > 0]) / len(closed_trades) * 100
                st.metric("Win Rate", f"{win_rate:.1f}%")
            else:
                st.metric("Win Rate", "N/A")
        
        # Display trades table
        st.markdown("---")
        
        # Prepare display columns
        display_cols = ['timestamp', 'ticker', 'trade_type', 'entry_price', 'shares', 
                       'position_size', 'is_closed']
        
        if 'profit_loss' in filtered_df.columns:
            display_cols.extend(['exit_price', 'profit_loss', 'profit_loss_pct'])
        
        # Format DataFrame for display
        display_df = filtered_df[display_cols].copy()
        display_df['timestamp'] = pd.to_datetime(display_df['timestamp']).dt.strftime('%Y-%m-%d %H:%M')
        display_df['entry_price'] = display_df['entry_price'].apply(lambda x: f"${x:.2f}")
        display_df['position_size'] = display_df['position_size'].apply(lambda x: f"${x:.2f}")
        
        if 'exit_price' in display_df.columns:
            display_df['exit_price'] = display_df['exit_price'].apply(
                lambda x: f"${x:.2f}" if pd.notna(x) else "-"
            )
        
        if 'profit_loss' in display_df.columns:
            display_df['profit_loss'] = display_df['profit_loss'].apply(
                lambda x: f"${x:.2f}" if pd.notna(x) else "-"
            )
            display_df['profit_loss_pct'] = display_df['profit_loss_pct'].apply(
                lambda x: f"{x:.2f}%" if pd.notna(x) else "-"
            )
        
        display_df['is_closed'] = display_df['is_closed'].apply(lambda x: "Closed" if x else "Open")
        
        # Rename columns for display
        display_df.columns = ['Date/Time', 'Ticker', 'Type', 'Entry', 'Shares', 
                              'Position Size', 'Status'] + (
                              ['Exit', 'P&L', 'P&L %'] if 'profit_loss' in display_df.columns else []
                              )
        
        st.dataframe(display_df, use_container_width=True, hide_index=True)
        
        # Export functionality
        st.markdown("---")
        if st.button("📥 Export to CSV"):
            csv = filtered_df.to_csv(index=False)
            st.download_button(
                label="Download CSV",
                data=csv,
                file_name=f"trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv"
            )
    else:
        st.info("No trades recorded yet. Start by logging your first trade!")

with tab3:
    st.header("📈 Performance Analytics")
    
    trades = data_manager.get_trade_journal()
    
    if trades:
        df = pd.DataFrame(trades)
        closed_df = df[df['is_closed'] == True]
        
        if len(closed_df) > 0:
            # Performance over time chart
            fig = make_subplots(
                rows=2, cols=2,
                subplot_titles=('Cumulative P&L', 'Win Rate by Month', 
                               'P&L Distribution', 'Trade Type Performance'),
                specs=[[{"type": "scatter"}, {"type": "bar"}],
                       [{"type": "histogram"}, {"type": "bar"}]]
            )
            
            # Cumulative P&L
            closed_df = closed_df.sort_values('timestamp')
            closed_df['cumulative_pnl'] = closed_df['profit_loss'].cumsum()
            
            fig.add_trace(
                go.Scatter(
                    x=pd.to_datetime(closed_df['timestamp']),
                    y=closed_df['cumulative_pnl'],
                    mode='lines+markers',
                    name='Cumulative P&L',
                    line=dict(color='green' if closed_df['cumulative_pnl'].iloc[-1] > 0 else 'red')
                ),
                row=1, col=1
            )
            
            # Win rate by month
            closed_df['month'] = pd.to_datetime(closed_df['timestamp']).dt.to_period('M')
            monthly_stats = closed_df.groupby('month').agg({
                'profit_loss': ['count', lambda x: (x > 0).sum()]
            }).reset_index()
            monthly_stats.columns = ['month', 'total_trades', 'winning_trades']
            monthly_stats['win_rate'] = monthly_stats['winning_trades'] / monthly_stats['total_trades'] * 100
            
            fig.add_trace(
                go.Bar(
                    x=monthly_stats['month'].astype(str),
                    y=monthly_stats['win_rate'],
                    name='Win Rate %',
                    marker_color='lightblue'
                ),
                row=1, col=2
            )
            
            # P&L Distribution
            fig.add_trace(
                go.Histogram(
                    x=closed_df['profit_loss'],
                    nbinsx=20,
                    name='P&L Distribution',
                    marker_color='purple'
                ),
                row=2, col=1
            )
            
            # Trade type performance
            type_stats = closed_df.groupby('trade_type')['profit_loss'].agg(['sum', 'count', 'mean'])
            
            fig.add_trace(
                go.Bar(
                    x=type_stats.index,
                    y=type_stats['sum'],
                    name='Total P&L by Type',
                    marker_color=['green' if x > 0 else 'red' for x in type_stats['sum']]
                ),
                row=2, col=2
            )
            
            fig.update_layout(height=800, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
            
            # Additional metrics
            st.markdown("---")
            st.subheader("📊 Detailed Statistics")
            
            col_s1, col_s2, col_s3, col_s4 = st.columns(4)
            
            with col_s1:
                avg_win = closed_df[closed_df['profit_loss'] > 0]['profit_loss'].mean()
                st.metric("Average Win", f"${avg_win:.2f}" if not pd.isna(avg_win) else "$0.00")
            
            with col_s2:
                avg_loss = abs(closed_df[closed_df['profit_loss'] < 0]['profit_loss'].mean())
                st.metric("Average Loss", f"${avg_loss:.2f}" if not pd.isna(avg_loss) else "$0.00")
            
            with col_s3:
                if not pd.isna(avg_win) and not pd.isna(avg_loss) and avg_loss > 0:
                    profit_factor = avg_win / avg_loss
                    st.metric("Profit Factor", f"{profit_factor:.2f}")
                else:
                    st.metric("Profit Factor", "N/A")
            
            with col_s4:
                max_drawdown = (closed_df['cumulative_pnl'].cummax() - closed_df['cumulative_pnl']).max()
                st.metric("Max Drawdown", f"${max_drawdown:.2f}")
            
            # Best and worst trades
            st.markdown("---")
            col_t1, col_t2 = st.columns(2)
            
            with col_t1:
                st.subheader("🏆 Best Trades")
                best_trades = closed_df.nlargest(5, 'profit_loss')[['ticker', 'profit_loss', 'profit_loss_pct']]
                best_trades['profit_loss'] = best_trades['profit_loss'].apply(lambda x: f"${x:.2f}")
                best_trades['profit_loss_pct'] = best_trades['profit_loss_pct'].apply(lambda x: f"{x:.2f}%")
                st.dataframe(best_trades, use_container_width=True, hide_index=True)
            
            with col_t2:
                st.subheader("😢 Worst Trades")
                worst_trades = closed_df.nsmallest(5, 'profit_loss')[['ticker', 'profit_loss', 'profit_loss_pct']]
                worst_trades['profit_loss'] = worst_trades['profit_loss'].apply(lambda x: f"${x:.2f}")
                worst_trades['profit_loss_pct'] = worst_trades['profit_loss_pct'].apply(lambda x: f"{x:.2f}%")
                st.dataframe(worst_trades, use_container_width=True, hide_index=True)
        else:
            st.info("No closed trades to analyze yet.")
    else:
        st.info("No trades recorded yet.")

with tab4:
    st.header("🎯 Signal Performance Analysis")
    
    # Get performance by signal type
    signal_performance = data_manager.get_trade_performance_by_signal()
    
    if signal_performance:
        # Create performance comparison chart
        fig = go.Figure()
        
        signal_types = list(signal_performance.keys())
        win_rates = [signal_performance[s]['win_rate'] * 100 for s in signal_types]
        avg_pnls = [signal_performance[s]['avg_pnl'] for s in signal_types]
        trade_counts = [signal_performance[s]['trades'] for s in signal_types]
        
        # Win rate bars
        fig.add_trace(go.Bar(
            name='Win Rate %',
            x=signal_types,
            y=win_rates,
            yaxis='y',
            offsetgroup=1
        ))
        
        # Average P&L line
        fig.add_trace(go.Scatter(
            name='Avg P&L',
            x=signal_types,
            y=avg_pnls,
            yaxis='y2',
            mode='lines+markers',
            line=dict(color='red', width=3)
        ))
        
        fig.update_layout(
            title='Signal Performance Comparison',
            yaxis=dict(title='Win Rate %', side='left'),
            yaxis2=dict(title='Average P&L ($)', overlaying='y', side='right'),
            hovermode='x unified',
            height=400
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
        # Detailed signal stats
        st.markdown("---")
        st.subheader("📊 Signal Statistics")
        
        signal_df = pd.DataFrame(signal_performance).T
        signal_df['win_rate'] = signal_df['win_rate'].apply(lambda x: f"{x*100:.1f}%")
        signal_df['avg_pnl'] = signal_df['avg_pnl'].apply(lambda x: f"${x:.2f}")
        signal_df['total_pnl'] = signal_df['total_pnl'].apply(lambda x: f"${x:.2f}")
        
        st.dataframe(signal_df, use_container_width=True)
        
        # Recommendations based on performance
        st.markdown("---")
        st.subheader("💡 Signal Recommendations")
        
        best_signal = max(signal_performance.items(), 
                         key=lambda x: x[1]['win_rate'] * x[1]['avg_pnl'] if x[1]['avg_pnl'] > 0 else 0)
        
        if best_signal[1]['win_rate'] > 0.6 and best_signal[1]['avg_pnl'] > 0:
            st.success(f"**Best Performing Signal:** {best_signal[0]} with {best_signal[1]['win_rate']*100:.1f}% win rate and ${best_signal[1]['avg_pnl']:.2f} avg profit")
        
        # Signal correlation analysis
        if 'entry_signals' in df.columns:
            st.markdown("---")
            st.subheader("🔗 Signal Combinations")
            st.info("Analysis of which signal combinations work best together - Coming soon!")
    else:
        st.info("Trade more using different signals to see performance analysis.")

# Footer with tips
st.markdown("---")
with st.expander("💡 Trading Journal Tips"):
    st.markdown("""
    - **Be Consistent**: Log every trade, win or lose
    - **Be Honest**: Record the real reasons for entry/exit
    - **Review Regularly**: Analyze your performance weekly
    - **Learn from Losses**: Your losses teach you the most
    - **Track Emotions**: Note if emotions affected your decisions
    - **Signal Performance**: Pay attention to which signals work best for you
    """)
