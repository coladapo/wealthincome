import streamlit as st
import pandas as pd
from datetime import datetime
import json
import os

# Page config
st.set_page_config(
    page_title="Paper Trading",
    page_icon="📝",
    layout="wide"
)

st.title("📝 Paper Trading")
st.markdown("Practice trading without real money")

# Initialize session state
if 'paper_portfolio' not in st.session_state:
    st.session_state.paper_portfolio = {
        'cash': 100000.0,  # Starting with $100k
        'positions': {},
        'trades': []
    }

# Display portfolio summary
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("💵 Cash Balance", f"${st.session_state.paper_portfolio['cash']:,.2f}")
with col2:
    positions_value = sum(pos['quantity'] * pos['current_price'] 
                         for pos in st.session_state.paper_portfolio['positions'].values())
    st.metric("📊 Positions Value", f"${positions_value:,.2f}")
with col3:
    total_value = st.session_state.paper_portfolio['cash'] + positions_value
    st.metric("💰 Total Portfolio", f"${total_value:,.2f}")

# Trading form
st.markdown("---")
st.subheader("🎯 Place a Trade")

with st.container():
    col1, col2 = st.columns([2, 1])
    
    with col1:
        with st.form("trade_form"):
            tcol1, tcol2, tcol3 = st.columns(3)
            
            with tcol1:
                symbol = st.text_input("Symbol", value="AAPL").upper()
            with tcol2:
                action = st.selectbox("Action", ["BUY", "SELL"])
            with tcol3:
                quantity = st.number_input("Shares", min_value=1, value=10)
            
            # For now, use a manual price input (can integrate with yfinance later)
            price = st.number_input("Price per share", min_value=0.01, value=150.00, step=0.01)
            
            submit_trade = st.form_submit_button("Execute Trade", type="primary", use_container_width=True)
            
            if submit_trade:
                total_cost = quantity * price
                
                if action == "BUY":
                    if total_cost <= st.session_state.paper_portfolio['cash']:
                        # Execute buy
                        st.session_state.paper_portfolio['cash'] -= total_cost
                        
                        if symbol in st.session_state.paper_portfolio['positions']:
                            # Update existing position
                            pos = st.session_state.paper_portfolio['positions'][symbol]
                            new_quantity = pos['quantity'] + quantity
                            new_avg_price = ((pos['quantity'] * pos['avg_price']) + (quantity * price)) / new_quantity
                            
                            st.session_state.paper_portfolio['positions'][symbol] = {
                                'quantity': new_quantity,
                                'avg_price': new_avg_price,
                                'current_price': price
                            }
                        else:
                            # New position
                            st.session_state.paper_portfolio['positions'][symbol] = {
                                'quantity': quantity,
                                'avg_price': price,
                                'current_price': price
                            }
                        
                        # Record trade
                        trade = {
                            'timestamp': datetime.now().isoformat(),
                            'symbol': symbol,
                            'action': action,
                            'quantity': quantity,
                            'price': price,
                            'total': total_cost
                        }
                        st.session_state.paper_portfolio['trades'].append(trade)
                        
                        st.success(f"✅ Bought {quantity} shares of {symbol} at ${price:.2f}")
                        st.rerun()
                    else:
                        st.error(f"❌ Insufficient funds! Need ${total_cost:.2f}, have ${st.session_state.paper_portfolio['cash']:.2f}")
                
                else:  # SELL
                    if symbol in st.session_state.paper_portfolio['positions']:
                        pos = st.session_state.paper_portfolio['positions'][symbol]
                        if pos['quantity'] >= quantity:
                            # Execute sell
                            st.session_state.paper_portfolio['cash'] += total_cost
                            pos['quantity'] -= quantity
                            
                            if pos['quantity'] == 0:
                                del st.session_state.paper_portfolio['positions'][symbol]
                            
                            # Record trade
                            trade = {
                                'timestamp': datetime.now().isoformat(),
                                'symbol': symbol,
                                'action': action,
                                'quantity': quantity,
                                'price': price,
                                'total': total_cost
                            }
                            st.session_state.paper_portfolio['trades'].append(trade)
                            
                            st.success(f"✅ Sold {quantity} shares of {symbol} at ${price:.2f}")
                            st.rerun()
                        else:
                            st.error(f"❌ Insufficient shares! Have {pos['quantity']}, trying to sell {quantity}")
                    else:
                        st.error(f"❌ No position in {symbol}")

# Current positions
if st.session_state.paper_portfolio['positions']:
    st.markdown("---")
    st.subheader("📊 Current Positions")
    
    positions_data = []
    for symbol, pos in st.session_state.paper_portfolio['positions'].items():
        profit_loss = (pos['current_price'] - pos['avg_price']) * pos['quantity']
        profit_loss_pct = ((pos['current_price'] - pos['avg_price']) / pos['avg_price']) * 100
        
        positions_data.append({
            'Symbol': symbol,
            'Shares': pos['quantity'],
            'Avg Cost': f"${pos['avg_price']:.2f}",
            'Current Price': f"${pos['current_price']:.2f}",
            'Market Value': f"${pos['quantity'] * pos['current_price']:.2f}",
            'P&L': f"${profit_loss:+.2f}",
            'P&L %': f"{profit_loss_pct:+.2f}%"
        })
    
    df_positions = pd.DataFrame(positions_data)
    st.dataframe(df_positions, use_container_width=True, hide_index=True)

# Recent trades
if st.session_state.paper_portfolio['trades']:
    st.markdown("---")
    st.subheader("📜 Recent Trades")
    
    # Show last 10 trades
    recent_trades = st.session_state.paper_portfolio['trades'][-10:][::-1]  # Reverse to show newest first
    
    trades_data = []
    for trade in recent_trades:
        trades_data.append({
            'Time': datetime.fromisoformat(trade['timestamp']).strftime('%Y-%m-%d %H:%M'),
            'Symbol': trade['symbol'],
            'Action': trade['action'],
            'Shares': trade['quantity'],
            'Price': f"${trade['price']:.2f}",
            'Total': f"${trade['total']:.2f}"
        })
    
    df_trades = pd.DataFrame(trades_data)
    st.dataframe(df_trades, use_container_width=True, hide_index=True)

# Reset button
st.markdown("---")
col1, col2, col3 = st.columns([1, 1, 2])
with col3:
    if st.button("🔄 Reset Paper Portfolio", type="secondary"):
        st.session_state.paper_portfolio = {
            'cash': 100000.0,
            'positions': {},
            'trades': []
        }
        st.success("Portfolio reset to $100,000")
        st.rerun()
