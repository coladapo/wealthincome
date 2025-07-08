"""
Trading Engine - Paper trading and portfolio management
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import uuid

logger = logging.getLogger(__name__)

class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"

class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"

class OrderStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

@dataclass
class Position:
    symbol: str
    quantity: float
    avg_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price
    
    @property
    def cost_basis(self) -> float:
        return self.quantity * self.avg_price
    
    @property
    def total_pnl(self) -> float:
        return self.unrealized_pnl + self.realized_pnl

@dataclass
class Order:
    id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    filled_price: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    
    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED

@dataclass
class Portfolio:
    cash: float = 100000.0
    positions: Dict[str, Position] = field(default_factory=dict)
    orders: List[Order] = field(default_factory=list)
    transaction_history: List[Dict] = field(default_factory=list)
    
    @property
    def total_value(self) -> float:
        return self.cash + sum(pos.market_value for pos in self.positions.values())
    
    @property
    def buying_power(self) -> float:
        return self.cash  # Simplified for paper trading
    
    @property
    def total_pnl(self) -> float:
        return sum(pos.total_pnl for pos in self.positions.values())

class TradingEngine:
    """Paper trading engine for simulating trades"""
    
    def __init__(self, initial_cash: float = 100000.0):
        self.portfolio = Portfolio(cash=initial_cash)
        self.data_manager = None
        self.config = None
        
    def set_data_manager(self, data_manager):
        """Set the data manager for market data"""
        self.data_manager = data_manager
        
    def set_config(self, config):
        """Set configuration"""
        self.config = config
    
    def place_order(self, symbol: str, side: OrderSide, quantity: float, 
                   order_type: OrderType = OrderType.MARKET, 
                   price: Optional[float] = None,
                   stop_price: Optional[float] = None) -> str:
        """Place a trading order"""
        
        order_id = str(uuid.uuid4())
        
        order = Order(
            id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price
        )
        
        # Validate order
        if not self._validate_order(order):
            order.status = OrderStatus.REJECTED
            logger.warning(f"Order rejected: {order_id}")
            return order_id
        
        self.portfolio.orders.append(order)
        
        # For paper trading, execute market orders immediately
        if order_type == OrderType.MARKET:
            self._execute_order(order)
        
        logger.info(f"Order placed: {order_id} - {side.value} {quantity} {symbol}")
        return order_id
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order"""
        
        for order in self.portfolio.orders:
            if order.id == order_id and order.status == OrderStatus.PENDING:
                order.status = OrderStatus.CANCELLED
                logger.info(f"Order cancelled: {order_id}")
                return True
        
        return False
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID"""
        
        for order in self.portfolio.orders:
            if order.id == order_id:
                return order
        
        return None
    
    def get_orders(self, symbol: Optional[str] = None, 
                  status: Optional[OrderStatus] = None) -> List[Order]:
        """Get orders with optional filtering"""
        
        orders = self.portfolio.orders
        
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        
        if status:
            orders = [o for o in orders if o.status == status]
        
        return sorted(orders, key=lambda x: x.created_at, reverse=True)
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for a symbol"""
        return self.portfolio.positions.get(symbol)
    
    def get_positions(self) -> Dict[str, Position]:
        """Get all positions"""
        return self.portfolio.positions.copy()
    
    def update_market_prices(self, price_data: Dict[str, float]):
        """Update current market prices for positions"""
        
        for symbol, position in self.portfolio.positions.items():
            if symbol in price_data:
                position.current_price = price_data[symbol]
                position.unrealized_pnl = (position.current_price - position.avg_price) * position.quantity
                position.updated_at = datetime.now()
    
    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get portfolio summary"""
        
        total_value = self.portfolio.total_value
        total_pnl = self.portfolio.total_pnl
        
        return {
            'cash': self.portfolio.cash,
            'total_value': total_value,
            'buying_power': self.portfolio.buying_power,
            'total_pnl': total_pnl,
            'total_return_pct': (total_pnl / (total_value - total_pnl)) * 100 if total_value > total_pnl else 0,
            'positions_count': len(self.portfolio.positions),
            'active_orders': len([o for o in self.portfolio.orders if o.status == OrderStatus.PENDING])
        }
    
    def get_transaction_history(self, days: int = 30) -> List[Dict]:
        """Get transaction history"""
        
        cutoff_date = datetime.now() - timedelta(days=days)
        
        return [
            tx for tx in self.portfolio.transaction_history
            if tx.get('timestamp', datetime.min) >= cutoff_date
        ]
    
    def _validate_order(self, order: Order) -> bool:
        """Validate order before execution"""
        
        # Check buying power for buy orders
        if order.side == OrderSide.BUY:
            estimated_cost = order.quantity * (order.price or self._get_current_price(order.symbol))
            if estimated_cost > self.portfolio.cash:
                logger.warning(f"Insufficient buying power for order {order.id}")
                return False
        
        # Check position availability for sell orders
        elif order.side == OrderSide.SELL:
            position = self.portfolio.positions.get(order.symbol)
            if not position or position.quantity < order.quantity:
                logger.warning(f"Insufficient position for sell order {order.id}")
                return False
        
        return True
    
    def _execute_order(self, order: Order):
        """Execute a validated order"""
        
        current_price = self._get_current_price(order.symbol)
        
        if not current_price:
            order.status = OrderStatus.REJECTED
            logger.error(f"Could not get price for {order.symbol}")
            return
        
        # Simulate execution price (could add slippage here)
        execution_price = current_price
        
        if order.side == OrderSide.BUY:
            self._execute_buy_order(order, execution_price)
        else:
            self._execute_sell_order(order, execution_price)
        
        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.filled_price = execution_price
        order.filled_at = datetime.now()
        
        # Record transaction
        self._record_transaction(order, execution_price)
        
        logger.info(f"Order executed: {order.id} at ${execution_price:.2f}")
    
    def _execute_buy_order(self, order: Order, price: float):
        """Execute buy order"""
        
        total_cost = order.quantity * price
        
        # Update cash
        self.portfolio.cash -= total_cost
        
        # Update or create position
        if order.symbol in self.portfolio.positions:
            position = self.portfolio.positions[order.symbol]
            total_quantity = position.quantity + order.quantity
            total_cost_basis = (position.quantity * position.avg_price) + (order.quantity * price)
            position.avg_price = total_cost_basis / total_quantity
            position.quantity = total_quantity
            position.current_price = price
            position.updated_at = datetime.now()
        else:
            self.portfolio.positions[order.symbol] = Position(
                symbol=order.symbol,
                quantity=order.quantity,
                avg_price=price,
                current_price=price
            )
    
    def _execute_sell_order(self, order: Order, price: float):
        """Execute sell order"""
        
        position = self.portfolio.positions[order.symbol]
        
        # Calculate realized P&L
        realized_pnl = (price - position.avg_price) * order.quantity
        position.realized_pnl += realized_pnl
        
        # Update cash
        total_proceeds = order.quantity * price
        self.portfolio.cash += total_proceeds
        
        # Update position
        position.quantity -= order.quantity
        position.current_price = price
        position.updated_at = datetime.now()
        
        # Remove position if fully sold
        if position.quantity <= 0:
            del self.portfolio.positions[order.symbol]
    
    def _get_current_price(self, symbol: str) -> Optional[float]:
        """Get current market price for symbol"""
        
        if not self.data_manager:
            # Return mock price for testing
            return 100.0
        
        try:
            stock_data = self.data_manager.get_stock_data([symbol])
            if symbol in stock_data and stock_data[symbol]:
                return stock_data[symbol].get('info', {}).get('regularMarketPrice', 100.0)
        except Exception as e:
            logger.error(f"Error getting price for {symbol}: {e}")
        
        return None
    
    def _record_transaction(self, order: Order, price: float):
        """Record transaction in history"""
        
        transaction = {
            'id': order.id,
            'timestamp': datetime.now(),
            'symbol': order.symbol,
            'side': order.side.value,
            'quantity': order.quantity,
            'price': price,
            'total': order.quantity * price,
            'type': order.order_type.value
        }
        
        self.portfolio.transaction_history.append(transaction)
    
    def reset_portfolio(self, initial_cash: float = 100000.0):
        """Reset portfolio to initial state"""
        
        self.portfolio = Portfolio(cash=initial_cash)
        logger.info("Portfolio reset to initial state")
    
    def export_portfolio_data(self) -> Dict[str, Any]:
        """Export portfolio data for persistence"""
        
        return {
            'cash': self.portfolio.cash,
            'positions': {
                symbol: {
                    'symbol': pos.symbol,
                    'quantity': pos.quantity,
                    'avg_price': pos.avg_price,
                    'current_price': pos.current_price,
                    'unrealized_pnl': pos.unrealized_pnl,
                    'realized_pnl': pos.realized_pnl,
                    'created_at': pos.created_at.isoformat(),
                    'updated_at': pos.updated_at.isoformat()
                }
                for symbol, pos in self.portfolio.positions.items()
            },
            'transaction_history': [
                {
                    **tx,
                    'timestamp': tx['timestamp'].isoformat() if isinstance(tx['timestamp'], datetime) else tx['timestamp']
                }
                for tx in self.portfolio.transaction_history
            ]
        }