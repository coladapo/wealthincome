"""
Paper Trading Analytics Module
Handles portfolio management, trade execution, and performance analytics
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import yfinance as yf


class PaperTradingPortfolio:
    """Manages a paper trading portfolio with position tracking and analytics"""
    
    def __init__(self, starting_capital: float = 100000.0):
        self.starting_capital = starting_capital
        self.cash = starting_capital
        self.positions = {}  # {symbol: {quantity, avg_price, current_price, stop_loss, take_profit}}
        self.trades = []  # List of all executed trades
        self.portfolio_history = []  # Daily portfolio value history
        self.pending_orders = []  # Limit orders waiting to be filled
        
        # Track the portfolio value at start of day
        self._record_portfolio_value()
    
    def execute_trade(self, symbol: str, action: str, quantity: int, price: float,
                     order_type: str = "Market", stop_loss: Optional[float] = None,
                     take_profit: Optional[float] = None) -> Tuple[bool, str]:
        """
        Execute a trade (buy or sell)
        Returns: (success: bool, message: str)
        """
        symbol = symbol.upper()
        total_value = quantity * price
        
        if action == "BUY":
            # Check if we have enough cash
            if total_value > self.cash:
                return False, f"Insufficient funds. Need ${total_value:.2f}, have ${self.cash:.2f}"
            
            # Execute buy order
            self.cash -= total_value
            
            if symbol in self.positions:
                # Update existing position (average in)
                pos = self.positions[symbol]
                new_quantity = pos['quantity'] + quantity
                new_avg_price = ((pos['quantity'] * pos['avg_price']) + (quantity * price)) / new_quantity
                
                self.positions[symbol] = {
                    'quantity': new_quantity,
                    'avg_price': new_avg_price,
                    'current_price': price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit
                }
            else:
                # Create new position
                self.positions[symbol] = {
                    'quantity': quantity,
                    'avg_price': price,
                    'current_price': price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit
                }
            
            # Record trade
            trade = {
                'timestamp': datetime.now().isoformat(),
                'symbol': symbol,
                'action': action,
                'quantity': quantity,
                'price': price,
                'total': total_value,
                'order_type': order_type,
                'status': 'Executed'
            }
            self.trades.append(trade)
            
            return True, f"✅ Bought {quantity} shares of {symbol} at ${price:.2f} (Total: ${total_value:.2f})"
        
        elif action == "SELL":
            # Check if we have the position
            if symbol not in self.positions:
                return False, f"No position in {symbol}"
            
            pos = self.positions[symbol]
            if pos['quantity'] < quantity:
                return False, f"Insufficient shares. Have {pos['quantity']}, trying to sell {quantity}"
            
            # Calculate P&L for this trade
            pnl = (price - pos['avg_price']) * quantity
            pnl_pct = ((price - pos['avg_price']) / pos['avg_price']) * 100
            
            # Execute sell order
            self.cash += total_value
            pos['quantity'] -= quantity
            
            if pos['quantity'] == 0:
                # Close position completely
                del self.positions[symbol]
            
            # Record trade with P&L
            trade = {
                'timestamp': datetime.now().isoformat(),
                'symbol': symbol,
                'action': action,
                'quantity': quantity,
                'price': price,
                'total': total_value,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'order_type': order_type,
                'status': 'Executed'
            }
            self.trades.append(trade)
            
            return True, f"✅ Sold {quantity} shares of {symbol} at ${price:.2f} (P&L: ${pnl:+.2f} / {pnl_pct:+.2f}%)"
        
        return False, "Invalid action. Use BUY or SELL"
    
    def get_positions_value(self) -> float:
        """Calculate total value of all positions"""
        total = 0.0
        for symbol, pos in self.positions.items():
            # Update current price if possible
            try:
                ticker = yf.Ticker(symbol)
                current_price = ticker.history(period="1d")['Close'].iloc[-1]
                pos['current_price'] = current_price
            except:
                pass  # Use last known price
            
            total += pos['quantity'] * pos.get('current_price', pos['avg_price'])
        
        return total
    
    def get_total_value(self) -> float:
        """Get total portfolio value (cash + positions)"""
        return self.cash + self.get_positions_value()
    
    def get_total_return(self) -> float:
        """Calculate total return percentage"""
        total_value = self.get_total_value()
        return ((total_value - self.starting_capital) / self.starting_capital) * 100
    
    def get_win_rate(self) -> float:
        """Calculate win rate from closed trades"""
        sell_trades = [t for t in self.trades if t['action'] == 'SELL' and 'pnl' in t]
        if not sell_trades:
            return 0.0
        
        winning_trades = [t for t in sell_trades if t['pnl'] > 0]
        return (len(winning_trades) / len(sell_trades)) * 100
    
    def get_trade_pnls(self) -> List[float]:
        """Get list of P&L values from closed trades"""
        return [t['pnl'] for t in self.trades if t['action'] == 'SELL' and 'pnl' in t]
    
    def check_stop_loss_take_profit(self):
        """Check if any positions hit stop loss or take profit"""
        positions_to_close = []
        
        for symbol, pos in self.positions.items():
            current_price = pos.get('current_price', pos['avg_price'])
            
            # Check stop loss
            if pos.get('stop_loss') and current_price <= pos['stop_loss']:
                positions_to_close.append((symbol, 'STOP_LOSS', pos['stop_loss']))
            
            # Check take profit
            elif pos.get('take_profit') and current_price >= pos['take_profit']:
                positions_to_close.append((symbol, 'TAKE_PROFIT', pos['take_profit']))
        
        # Execute closes
        for symbol, reason, price in positions_to_close:
            quantity = self.positions[symbol]['quantity']
            success, message = self.execute_trade(symbol, 'SELL', quantity, price)
            if success:
                self.trades[-1]['order_type'] = reason  # Mark the trade type
    
    def _record_portfolio_value(self):
        """Record current portfolio value for history tracking"""
        self.portfolio_history.append({
            'timestamp': datetime.now().isoformat(),
            'total_value': self.get_total_value(),
            'cash': self.cash,
            'positions_value': self.get_positions_value()
        })
    
    def save_to_file(self, filepath: str = "paper_portfolio.json"):
        """Save portfolio state to JSON file"""
        data = {
            'starting_capital': self.starting_capital,
            'cash': self.cash,
            'positions': self.positions,
            'trades': self.trades,
            'portfolio_history': self.portfolio_history
        }
        
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
    
    def load_from_file(self, filepath: str = "paper_portfolio.json"):
        """Load portfolio state from JSON file"""
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            self.starting_capital = data.get('starting_capital', 100000)
            self.cash = data.get('cash', self.starting_capital)
            self.positions = data.get('positions', {})
            self.trades = data.get('trades', [])
            self.portfolio_history = data.get('portfolio_history', [])


def calculate_portfolio_metrics(portfolio: PaperTradingPortfolio) -> Dict:
    """Calculate comprehensive portfolio performance metrics"""
    metrics = {
        'total_trades': len([t for t in portfolio.trades if t['action'] == 'SELL']),
        'win_rate': portfolio.get_win_rate(),
        'total_return': portfolio.get_total_return(),
        'total_value': portfolio.get_total_value()
    }
    
    # Get P&L data
    pnls = portfolio.get_trade_pnls()
    
    if pnls:
        winning_trades = [pnl for pnl in pnls if pnl > 0]
        losing_trades = [pnl for pnl in pnls if pnl < 0]
        
        metrics['avg_win'] = np.mean(winning_trades) if winning_trades else 0
        metrics['avg_loss'] = np.mean(losing_trades) if losing_trades else 0
        metrics['best_trade'] = max(pnls) if pnls else 0
        metrics['worst_trade'] = min(pnls) if pnls else 0
        
        # Profit factor
        gross_profit = sum(winning_trades) if winning_trades else 0
        gross_loss = abs(sum(losing_trades)) if losing_trades else 1
        metrics['profit_factor'] = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Calculate Sharpe ratio (simplified)
        if len(pnls) > 1:
            returns = pd.Series(pnls)
            metrics['sharpe_ratio'] = (returns.mean() / returns.std()) * np.sqrt(252) if returns.std() > 0 else 0
        else:
            metrics['sharpe_ratio'] = 0
        
        # Max drawdown
        if portfolio.portfolio_history:
            values = [h['total_value'] for h in portfolio.portfolio_history]
            peak = values[0]
            max_dd = 0
            
            for value in values:
                if value > peak:
                    peak = value
                dd = ((peak - value) / peak) * 100
                if dd > max_dd:
                    max_dd = dd
            
            metrics['max_drawdown'] = max_dd
        else:
            metrics['max_drawdown'] = 0
    else:
        # Set defaults if no trades
        metrics.update({
            'avg_win': 0,
            'avg_loss': 0,
            'best_trade': 0,
            'worst_trade': 0,
            'profit_factor': 0,
            'sharpe_ratio': 0,
            'max_drawdown': 0
        })
    
    return metrics


def get_performance_chart(portfolio: PaperTradingPortfolio) -> go.Figure:
    """Create portfolio performance chart"""
    if not portfolio.portfolio_history:
        return None
    
    # Prepare data
    timestamps = [datetime.fromisoformat(h['timestamp']) for h in portfolio.portfolio_history]
    values = [h['total_value'] for h in portfolio.portfolio_history]
    
    # Create figure
    fig = go.Figure()
    
    # Add portfolio value line
    fig.add_trace(go.Scatter(
        x=timestamps,
        y=values,
        mode='lines',
        name='Portfolio Value',
        line=dict(color='#1f77b4', width=2)
    ))
    
    # Add starting capital reference line
    fig.add_hline(
        y=portfolio.starting_capital,
        line_dash="dash",
        line_color="gray",
        annotation_text="Starting Capital"
    )
    
    # Add trade markers
    for trade in portfolio.trades:
        trade_time = datetime.fromisoformat(trade['timestamp'])
        # Find closest portfolio value
        closest_idx = min(range(len(timestamps)), 
                         key=lambda i: abs(timestamps[i] - trade_time))
        
        fig.add_trace(go.Scatter(
            x=[trade_time],
            y=[values[closest_idx]],
            mode='markers',
            marker=dict(
                size=8,
                color='green' if trade['action'] == 'BUY' else 'red',
                symbol='triangle-up' if trade['action'] == 'BUY' else 'triangle-down'
            ),
            name=trade['action'],
            showlegend=False,
            hovertext=f"{trade['action']} {trade['quantity']} {trade['symbol']} @ ${trade['price']:.2f}"
        ))
    
    # Update layout
    fig.update_layout(
        title="Portfolio Performance",
        xaxis_title="Date",
        yaxis_title="Portfolio Value ($)",
        hovermode='x unified',
        height=400
    )
    
    return fig


def get_holdings_pie_chart(portfolio: PaperTradingPortfolio) -> go.Figure:
    """Create holdings distribution pie chart"""
    if not portfolio.positions:
        return None
    
    # Calculate holdings
    holdings = []
    for symbol, pos in portfolio.positions.items():
        value = pos['quantity'] * pos.get('current_price', pos['avg_price'])
        holdings.append({'symbol': symbol, 'value': value})
    
    # Add cash
    holdings.append({'symbol': 'Cash', 'value': portfolio.cash})
    
    # Create pie chart
    df = pd.DataFrame(holdings)
    fig = px.pie(
        df,
        values='value',
        names='symbol',
        title='Portfolio Allocation',
        hole=0.4
    )
    
    # Update layout
    fig.update_traces(textposition='inside', textinfo='percent+label')
    fig.update_layout(height=300)
    
    return fig


def analyze_trade_patterns(portfolio: PaperTradingPortfolio) -> Dict:
    """Analyze trading patterns and behaviors"""
    if not portfolio.trades:
        return {}
    
    df_trades = pd.DataFrame(portfolio.trades)
    df_trades['timestamp'] = pd.to_datetime(df_trades['timestamp'])
    
    patterns = {
        'most_traded_symbol': df_trades['symbol'].mode()[0] if not df_trades.empty else None,
        'avg_position_size': df_trades['total'].mean(),
        'trading_frequency': len(df_trades) / max(1, (df_trades['timestamp'].max() - df_trades['timestamp'].min()).days),
        'preferred_action': 'BUY' if (df_trades['action'] == 'BUY').sum() > (df_trades['action'] == 'SELL').sum() else 'SELL'
    }
    
    # Time-based patterns
    df_trades['hour'] = df_trades['timestamp'].dt.hour
    df_trades['day_of_week'] = df_trades['timestamp'].dt.day_name()
    
    patterns['most_active_hour'] = df_trades['hour'].mode()[0] if not df_trades.empty else None
    patterns['most_active_day'] = df_trades['day_of_week'].mode()[0] if not df_trades.empty else None
    
    return patterns


def generate_trade_report(portfolio: PaperTradingPortfolio) -> str:
    """Generate a text report of trading performance"""
    metrics = calculate_portfolio_metrics(portfolio)
    patterns = analyze_trade_patterns(portfolio)
    
    report = f"""
    📊 PAPER TRADING PERFORMANCE REPORT
    Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
    
    💰 PORTFOLIO SUMMARY
    Starting Capital: ${portfolio.starting_capital:,.2f}
    Current Value: ${metrics['total_value']:,.2f}
    Total Return: {metrics['total_return']:+.2f}%
    
    📈 TRADING STATISTICS
    Total Trades: {metrics['total_trades']}
    Win Rate: {metrics['win_rate']:.1f}%
    Average Win: ${metrics['avg_win']:.2f}
    Average Loss: ${metrics['avg_loss']:.2f}
    Best Trade: ${metrics['best_trade']:.2f}
    Worst Trade: ${metrics['worst_trade']:.2f}
    
    📊 RISK METRICS
    Profit Factor: {metrics['profit_factor']:.2f}
    Sharpe Ratio: {metrics['sharpe_ratio']:.2f}
    Max Drawdown: {metrics['max_drawdown']:.1f}%
    
    🎯 TRADING PATTERNS
    Most Traded: {patterns.get('most_traded_symbol', 'N/A')}
    Avg Position Size: ${patterns.get('avg_position_size', 0):.2f}
    Trading Frequency: {patterns.get('trading_frequency', 0):.1f} trades/day
    
    """
    
    return report
