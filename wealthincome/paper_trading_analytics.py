# paper_trading_analytics.py
"""
Advanced analytics module for Paper Trading Pro
Provides institutional-grade performance metrics and analysis
NO SCIPY VERSION - Works without scipy dependency
"""

import pandas as pd
import numpy as np
import json
from datetime import datetime, timedelta

class TradingAnalytics:
    """Professional trading performance analytics"""
    
    def __init__(self, trades_df, initial_capital=100000):
        self.trades = trades_df
        self.initial_capital = initial_capital
        self.closed_trades = trades_df[trades_df['Status'] == 'Closed'].copy()
        
    def calculate_advanced_metrics(self):
        """Calculate institutional-grade metrics"""
        if self.closed_trades.empty:
            return {}
        
        metrics = {
            # Basic metrics
            'total_trades': len(self.closed_trades),
            'winning_trades': len(self.closed_trades[self.closed_trades['PnL_Dollar'] > 0]),
            'losing_trades': len(self.closed_trades[self.closed_trades['PnL_Dollar'] <= 0]),
            
            # Returns analysis
            'total_return': self.calculate_total_return(),
            'cagr': self.calculate_cagr(),
            'sharpe_ratio': self.calculate_sharpe_ratio(),
            'sortino_ratio': self.calculate_sortino_ratio(),
            'calmar_ratio': self.calculate_calmar_ratio(),
            
            # Risk metrics
            'max_drawdown': self.calculate_max_drawdown(),
            'var_95': self.calculate_var(0.95),
            'var_99': self.calculate_var(0.99),
            'kelly_criterion': self.calculate_kelly_criterion(),
            
            # Performance metrics
            'profit_factor': self.calculate_profit_factor(),
            'expectancy': self.calculate_expectancy(),
            'sqn': self.calculate_sqn(),  # System Quality Number
            
            # Statistical analysis
            'edge_ratio': self.calculate_edge_ratio(),
            'monte_carlo_95': self.run_monte_carlo_simulation(confidence=0.95),
            't_statistic': self.calculate_t_statistic_simple(),  # Simplified version
            
            # Behavioral metrics
            'avg_winner_holding': self.calculate_avg_holding_time('Win'),
            'avg_loser_holding': self.calculate_avg_holding_time('Loss'),
            'win_loss_ratio': self.calculate_win_loss_ratio(),
            'largest_winner': self.closed_trades['PnL_Dollar'].max(),
            'largest_loser': self.closed_trades['PnL_Dollar'].min(),
            
            # Time-based analysis
            'best_day': self.find_best_trading_day(),
            'worst_day': self.find_worst_trading_day(),
            'best_hour': self.find_best_trading_hour(),
            'performance_by_month': self.calculate_monthly_returns()
        }
        
        return metrics
    
    def calculate_total_return(self):
        """Calculate total return percentage"""
        total_pnl = self.closed_trades['PnL_Dollar'].sum()
        return (total_pnl / self.initial_capital) * 100
    
    def calculate_cagr(self):
        """Calculate Compound Annual Growth Rate"""
        if self.closed_trades.empty:
            return 0
            
        first_trade = pd.to_datetime(self.closed_trades['Date_Opened'].min())
        last_trade = pd.to_datetime(self.closed_trades['Date_Closed'].max())
        years = (last_trade - first_trade).days / 365.25
        
        if years <= 0:
            return 0
            
        ending_value = self.initial_capital + self.closed_trades['PnL_Dollar'].sum()
        cagr = (pow(ending_value / self.initial_capital, 1 / years) - 1) * 100
        
        return cagr
    
    def calculate_sharpe_ratio(self, risk_free_rate=0.02):
        """Calculate Sharpe Ratio (annualized)"""
        if len(self.closed_trades) < 2:
            return 0
            
        returns = self.closed_trades['PnL_Percent'].values / 100
        
        # Assuming daily returns, annualize
        excess_returns = returns - (risk_free_rate / 252)
        
        if np.std(excess_returns) == 0:
            return 0
            
        sharpe = np.sqrt(252) * (np.mean(excess_returns) / np.std(excess_returns))
        return sharpe
    
    def calculate_sortino_ratio(self, risk_free_rate=0.02):
        """Calculate Sortino Ratio (downside deviation)"""
        if len(self.closed_trades) < 2:
            return 0
            
        returns = self.closed_trades['PnL_Percent'].values / 100
        excess_returns = returns - (risk_free_rate / 252)
        
        # Only consider negative returns for downside deviation
        downside_returns = excess_returns[excess_returns < 0]
        
        if len(downside_returns) == 0 or np.std(downside_returns) == 0:
            return float('inf') if np.mean(excess_returns) > 0 else 0
            
        sortino = np.sqrt(252) * (np.mean(excess_returns) / np.std(downside_returns))
        return sortino
    
    def calculate_calmar_ratio(self):
        """Calculate Calmar Ratio (CAGR / Max Drawdown)"""
        cagr = self.calculate_cagr()
        max_dd = abs(self.calculate_max_drawdown()['max_drawdown_pct'])
        
        if max_dd == 0:
            return float('inf') if cagr > 0 else 0
            
        return cagr / max_dd
    
    def calculate_max_drawdown(self):
        """Calculate maximum drawdown with details"""
        if self.closed_trades.empty:
            return {'max_drawdown': 0, 'max_drawdown_pct': 0, 'recovery_time': 0}
            
        # Calculate cumulative returns
        self.closed_trades = self.closed_trades.sort_values('Date_Closed')
        cumulative_pnl = self.closed_trades['PnL_Dollar'].cumsum()
        cumulative_value = self.initial_capital + cumulative_pnl
        
        # Calculate running maximum
        running_max = cumulative_value.expanding().max()
        
        # Calculate drawdown
        drawdown = cumulative_value - running_max
        drawdown_pct = (drawdown / running_max) * 100
        
        # Find maximum drawdown
        max_dd_idx = drawdown.idxmin()
        max_dd = drawdown.min()
        max_dd_pct = drawdown_pct.min()
        
        # Calculate recovery time (if recovered)
        if max_dd < 0:
            peak_idx = running_max[:max_dd_idx].idxmax()
            recovery_mask = cumulative_value[max_dd_idx:] >= running_max[peak_idx]
            
            if recovery_mask.any():
                recovery_idx = recovery_mask.idxmax()
                recovery_time = (pd.to_datetime(self.closed_trades.loc[recovery_idx, 'Date_Closed']) - 
                               pd.to_datetime(self.closed_trades.loc[max_dd_idx, 'Date_Closed'])).days
            else:
                recovery_time = None  # Still in drawdown
        else:
            recovery_time = 0
            
        return {
            'max_drawdown': max_dd,
            'max_drawdown_pct': max_dd_pct,
            'recovery_time': recovery_time
        }
    
    def calculate_var(self, confidence_level=0.95):
        """Calculate Value at Risk"""
        if self.closed_trades.empty:
            return 0
            
        returns = self.closed_trades['PnL_Dollar'].values
        var = np.percentile(returns, (1 - confidence_level) * 100)
        return var
    
    def calculate_kelly_criterion(self):
        """Calculate optimal position sizing using Kelly Criterion"""
        if self.closed_trades.empty:
            return 0
            
        wins = self.closed_trades[self.closed_trades['PnL_Dollar'] > 0]
        losses = self.closed_trades[self.closed_trades['PnL_Dollar'] <= 0]
        
        if wins.empty or losses.empty:
            return 0
            
        win_rate = len(wins) / len(self.closed_trades)
        avg_win = wins['PnL_Dollar'].mean()
        avg_loss = abs(losses['PnL_Dollar'].mean())
        
        if avg_loss == 0:
            return 1.0  # Maximum position size
            
        # Kelly % = (p * b - q) / b
        # where p = win rate, q = loss rate, b = avg win / avg loss
        b = avg_win / avg_loss
        kelly_pct = (win_rate * b - (1 - win_rate)) / b
        
        # Cap at 25% for safety
        return min(max(kelly_pct, 0), 0.25)
    
    def calculate_profit_factor(self):
        """Calculate profit factor"""
        gross_profits = self.closed_trades[self.closed_trades['PnL_Dollar'] > 0]['PnL_Dollar'].sum()
        gross_losses = abs(self.closed_trades[self.closed_trades['PnL_Dollar'] < 0]['PnL_Dollar'].sum())
        
        if gross_losses == 0:
            return float('inf') if gross_profits > 0 else 0
            
        return gross_profits / gross_losses
    
    def calculate_expectancy(self):
        """Calculate mathematical expectancy per trade"""
        if self.closed_trades.empty:
            return 0
            
        return self.closed_trades['PnL_Dollar'].mean()
    
    def calculate_sqn(self):
        """Calculate System Quality Number (Van Tharp)"""
        if len(self.closed_trades) < 2:
            return 0
            
        expectancy = self.calculate_expectancy()
        std_dev = self.closed_trades['PnL_Dollar'].std()
        
        if std_dev == 0:
            return 0
            
        sqn = np.sqrt(len(self.closed_trades)) * (expectancy / std_dev)
        return sqn
    
    def calculate_edge_ratio(self):
        """Calculate Edge Ratio (average win/loss vs probability)"""
        if self.closed_trades.empty:
            return 0
            
        wins = self.closed_trades[self.closed_trades['PnL_Dollar'] > 0]
        losses = self.closed_trades[self.closed_trades['PnL_Dollar'] <= 0]
        
        if wins.empty or losses.empty:
            return 0
            
        avg_win = wins['PnL_Dollar'].mean()
        avg_loss = abs(losses['PnL_Dollar'].mean())
        win_rate = len(wins) / len(self.closed_trades)
        
        if avg_loss == 0:
            return float('inf')
            
        # Edge = (Average Win × Win%) - (Average Loss × Loss%)
        edge = (avg_win * win_rate) - (avg_loss * (1 - win_rate))
        edge_ratio = edge / avg_loss
        
        return edge_ratio
    
    def run_monte_carlo_simulation(self, num_simulations=10000, confidence=0.95):
        """Run Monte Carlo simulation to project future performance"""
        if self.closed_trades.empty:
            return {'median': 0, 'lower_bound': 0, 'upper_bound': 0}
            
        trade_results = self.closed_trades['PnL_Dollar'].values
        
        final_values = []
        for _ in range(num_simulations):
            # Randomly sample trades with replacement
            simulated_trades = np.random.choice(trade_results, size=len(trade_results), replace=True)
            final_value = self.initial_capital + simulated_trades.sum()
            final_values.append(final_value)
        
        # Calculate confidence intervals
        lower_percentile = (1 - confidence) / 2 * 100
        upper_percentile = (1 + confidence) / 2 * 100
        
        return {
            'median': np.median(final_values),
            'lower_bound': np.percentile(final_values, lower_percentile),
            'upper_bound': np.percentile(final_values, upper_percentile),
            'probability_of_profit': sum(1 for v in final_values if v > self.initial_capital) / num_simulations
        }
    
    def calculate_t_statistic_simple(self):
        """Calculate t-statistic without scipy - simplified version"""
        if len(self.closed_trades) < 30:  # Need sufficient sample size
            return {'t_stat': 0, 'p_value': 1, 'significant': False}
            
        returns = self.closed_trades['PnL_Dollar'].values
        
        # Calculate t-statistic manually
        mean_return = np.mean(returns)
        std_return = np.std(returns, ddof=1)  # Sample standard deviation
        n = len(returns)
        
        if std_return == 0:
            return {'t_stat': 0, 'p_value': 1, 'significant': False}
        
        # t-statistic = (mean - 0) / (std / sqrt(n))
        t_stat = mean_return / (std_return / np.sqrt(n))
        
        # Simplified p-value estimation using normal approximation
        # For large samples (n > 30), t-distribution approximates normal
        # Using 2-tailed test
        z_score = abs(t_stat)
        
        # Approximate p-value using normal CDF
        # This is a rough approximation without scipy
        if z_score > 3.5:
            p_value = 0.0005
        elif z_score > 3:
            p_value = 0.003
        elif z_score > 2.5:
            p_value = 0.012
        elif z_score > 2:
            p_value = 0.046
        elif z_score > 1.96:
            p_value = 0.05
        elif z_score > 1.5:
            p_value = 0.134
        elif z_score > 1:
            p_value = 0.317
        else:
            p_value = 1.0
        
        return {
            't_stat': t_stat,
            'p_value': p_value,
            'significant': p_value < 0.05,
            'confidence': f"{(1 - p_value) * 100:.1f}%"
        }
    
    def calculate_avg_holding_time(self, result_type):
        """Calculate average holding time for wins/losses"""
        filtered = self.closed_trades[self.closed_trades['Result'] == result_type]
        
        if filtered.empty or 'Hold_Time' not in filtered.columns:
            return 0
            
        return filtered['Hold_Time'].mean()
    
    def calculate_win_loss_ratio(self):
        """Calculate win/loss size ratio"""
        wins = self.closed_trades[self.closed_trades['PnL_Dollar'] > 0]
        losses = self.closed_trades[self.closed_trades['PnL_Dollar'] < 0]
        
        if wins.empty or losses.empty:
            return 0
            
        avg_win = wins['PnL_Dollar'].mean()
        avg_loss = abs(losses['PnL_Dollar'].mean())
        
        if avg_loss == 0:
            return float('inf')
            
        return avg_win / avg_loss
    
    def find_best_trading_day(self):
        """Find most profitable day of week"""
        if self.closed_trades.empty:
            return "N/A"
            
        self.closed_trades['DayOfWeek'] = pd.to_datetime(self.closed_trades['Date_Opened']).dt.day_name()
        daily_pnl = self.closed_trades.groupby('DayOfWeek')['PnL_Dollar'].sum()
        
        if daily_pnl.empty:
            return "N/A"
            
        return daily_pnl.idxmax()
    
    def find_worst_trading_day(self):
        """Find least profitable day of week"""
        if self.closed_trades.empty:
            return "N/A"
            
        self.closed_trades['DayOfWeek'] = pd.to_datetime(self.closed_trades['Date_Opened']).dt.day_name()
        daily_pnl = self.closed_trades.groupby('DayOfWeek')['PnL_Dollar'].sum()
        
        if daily_pnl.empty:
            return "N/A"
            
        return daily_pnl.idxmin()
    
    def find_best_trading_hour(self):
        """Find most profitable hour of day"""
        if self.closed_trades.empty:
            return "N/A"
            
        self.closed_trades['Hour'] = pd.to_datetime(self.closed_trades['Date_Opened']).dt.hour
        hourly_pnl = self.closed_trades.groupby('Hour')['PnL_Dollar'].sum()
        
        if hourly_pnl.empty:
            return "N/A"
            
        best_hour = hourly_pnl.idxmax()
        return f"{best_hour}:00-{best_hour+1}:00"
    
    def calculate_monthly_returns(self):
        """Calculate returns by month"""
        if self.closed_trades.empty:
            return {}
            
        self.closed_trades['Month'] = pd.to_datetime(self.closed_trades['Date_Closed']).dt.to_period('M')
        monthly_returns = self.closed_trades.groupby('Month')['PnL_Dollar'].sum()
        
        return monthly_returns.to_dict()
    
    def generate_performance_report(self):
        """Generate comprehensive performance report"""
        metrics = self.calculate_advanced_metrics()
        
        report = f"""
        PROFESSIONAL TRADING PERFORMANCE REPORT
        =====================================
        
        SUMMARY STATISTICS
        ------------------
        Total Trades: {metrics['total_trades']}
        Winning Trades: {metrics['winning_trades']}
        Losing Trades: {metrics['losing_trades']}
        Win Rate: {(metrics['winning_trades'] / metrics['total_trades'] * 100):.1f}%
        
        RETURNS ANALYSIS
        ----------------
        Total Return: {metrics['total_return']:.2f}%
        CAGR: {metrics['cagr']:.2f}%
        Sharpe Ratio: {metrics['sharpe_ratio']:.2f}
        Sortino Ratio: {metrics['sortino_ratio']:.2f}
        Calmar Ratio: {metrics['calmar_ratio']:.2f}
        
        RISK METRICS
        ------------
        Maximum Drawdown: ${metrics['max_drawdown']['max_drawdown']:.2f} ({metrics['max_drawdown']['max_drawdown_pct']:.1f}%)
        95% VaR: ${metrics['var_95']:.2f}
        99% VaR: ${metrics['var_99']:.2f}
        Kelly Criterion: {metrics['kelly_criterion']*100:.1f}%
        
        PERFORMANCE METRICS
        -------------------
        Profit Factor: {metrics['profit_factor']:.2f}
        Expectancy: ${metrics['expectancy']:.2f}
        SQN: {metrics['sqn']:.2f}
        Edge Ratio: {metrics['edge_ratio']:.2f}
        
        STATISTICAL SIGNIFICANCE
        ------------------------
        T-Statistic: {metrics['t_statistic']['t_stat']:.2f}
        Confidence: {metrics['t_statistic']['confidence']}
        Monte Carlo 95% CI: ${metrics['monte_carlo_95']['lower_bound']:.2f} - ${metrics['monte_carlo_95']['upper_bound']:.2f}
        
        BEHAVIORAL ANALYSIS
        -------------------
        Avg Winner Hold Time: {metrics['avg_winner_holding']:.1f} hours
        Avg Loser Hold Time: {metrics['avg_loser_holding']:.1f} hours
        Win/Loss Size Ratio: {metrics['win_loss_ratio']:.2f}
        
        Best Trading Day: {metrics['best_day']}
        Worst Trading Day: {metrics['worst_day']}
        Best Trading Hour: {metrics['best_hour']}
        
        SYSTEM QUALITY
        --------------
        {'⭐⭐⭐⭐⭐ Superb (SQN > 3.0)' if metrics['sqn'] > 3 else 
         '⭐⭐⭐⭐ Excellent (SQN 2.5-3.0)' if metrics['sqn'] > 2.5 else
         '⭐⭐⭐ Good (SQN 2.0-2.5)' if metrics['sqn'] > 2 else
         '⭐⭐ Average (SQN 1.6-2.0)' if metrics['sqn'] > 1.6 else
         '⭐ Below Average (SQN < 1.6)'}
        """
        
        return report
