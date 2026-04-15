"""
Alpaca API Client - Live and paper trading via Alpaca Markets
"""

import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

PAPER_BASE_URL = "https://paper-api.alpaca.markets/v2"
LIVE_BASE_URL = "https://api.alpaca.markets/v2"
DATA_BASE_URL = "https://data.alpaca.markets/v2"


class AlpacaOrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class AlpacaOrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class AlpacaTimeInForce(Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


@dataclass
class AlpacaAccount:
    id: str
    cash: float
    portfolio_value: float
    buying_power: float
    equity: float
    long_market_value: float
    short_market_value: float
    daytrade_count: int
    pattern_day_trader: bool
    trading_blocked: bool
    status: str


@dataclass
class AlpacaPosition:
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float
    side: str


@dataclass
class AlpacaOrder:
    id: str
    symbol: str
    side: str
    order_type: str
    qty: float
    filled_qty: float
    status: str
    limit_price: Optional[float]
    stop_price: Optional[float]
    filled_avg_price: Optional[float]
    created_at: str
    filled_at: Optional[str]
    legs: Optional[List[Dict]] = None   # child orders for bracket/oco
    raw: Optional[Dict] = None          # full Alpaca response dict


class AlpacaClient:
    """Alpaca Markets REST API client"""

    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        self.api_key = api_key
        self.secret_key = secret_key
        self.paper = paper
        self.base_url = PAPER_BASE_URL if paper else LIVE_BASE_URL
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: Dict = None) -> Dict:
        url = f"{self.base_url}{path}"
        resp = requests.get(url, headers=self.headers, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: Dict) -> Dict:
        url = f"{self.base_url}{path}"
        resp = requests.post(url, headers=self.headers, json=body, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str) -> bool:
        url = f"{self.base_url}{path}"
        resp = requests.delete(url, headers=self.headers, timeout=10)
        return resp.status_code in (200, 204)

    # ─── Account ────────────────────────────────────────────────────────────

    def get_account(self) -> AlpacaAccount:
        data = self._get("/account")
        return AlpacaAccount(
            id=data["id"],
            cash=float(data["cash"]),
            portfolio_value=float(data["portfolio_value"]),
            buying_power=float(data["buying_power"]),
            equity=float(data["equity"]),
            long_market_value=float(data.get("long_market_value", 0)),
            short_market_value=float(data.get("short_market_value", 0)),
            daytrade_count=int(data.get("daytrade_count", 0)),
            pattern_day_trader=data.get("pattern_day_trader", False),
            trading_blocked=data.get("trading_blocked", False),
            status=data["status"],
        )

    def is_market_open(self) -> bool:
        data = self._get("/clock")
        return data.get("is_open", False)

    def get_clock(self) -> Dict:
        return self._get("/clock")

    # ─── Positions ──────────────────────────────────────────────────────────

    def get_positions(self) -> List[AlpacaPosition]:
        data = self._get("/positions")
        return [self._parse_position(p) for p in data]

    def get_position(self, symbol: str) -> Optional[AlpacaPosition]:
        try:
            data = self._get(f"/positions/{symbol}")
            return self._parse_position(data)
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    def close_position(self, symbol: str) -> Optional[AlpacaOrder]:
        try:
            data = self._delete(f"/positions/{symbol}")
            return data
        except Exception as e:
            logger.error(f"Error closing position {symbol}: {e}")
            return None

    def close_all_positions(self) -> bool:
        try:
            self._delete("/positions")
            return True
        except Exception as e:
            logger.error(f"Error closing all positions: {e}")
            return False

    def _parse_position(self, data: Dict) -> AlpacaPosition:
        return AlpacaPosition(
            symbol=data["symbol"],
            qty=float(data["qty"]),
            avg_entry_price=float(data["avg_entry_price"]),
            current_price=float(data.get("current_price", 0)),
            market_value=float(data.get("market_value", 0)),
            unrealized_pl=float(data.get("unrealized_pl", 0)),
            unrealized_plpc=float(data.get("unrealized_plpc", 0)),
            side=data.get("side", "long"),
        )

    # ─── Orders ─────────────────────────────────────────────────────────────

    def place_market_order(
        self,
        symbol: str,
        qty: float,
        side: AlpacaOrderSide,
        time_in_force: AlpacaTimeInForce = AlpacaTimeInForce.DAY,
    ) -> AlpacaOrder:
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side.value,
            "type": "market",
            "time_in_force": time_in_force.value,
        }
        data = self._post("/orders", body)
        return self._parse_order(data)

    def place_limit_order(
        self,
        symbol: str,
        qty: float,
        side: AlpacaOrderSide,
        limit_price: float,
        time_in_force: AlpacaTimeInForce = AlpacaTimeInForce.DAY,
    ) -> AlpacaOrder:
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side.value,
            "type": "limit",
            "limit_price": str(round(limit_price, 2)),
            "time_in_force": time_in_force.value,
        }
        data = self._post("/orders", body)
        return self._parse_order(data)

    def place_bracket_order(
        self,
        symbol: str,
        qty: float,
        side: AlpacaOrderSide,
        take_profit_price: float,
        stop_loss_price: float,
    ) -> AlpacaOrder:
        """Market order with automatic take profit and stop loss legs"""
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side.value,
            "type": "market",
            "time_in_force": "gtc",
            "order_class": "bracket",
            "take_profit": {"limit_price": str(round(take_profit_price, 2))},
            "stop_loss": {"stop_price": str(round(stop_loss_price, 2))},
        }
        data = self._post("/orders", body)
        return self._parse_order(data)

    def place_trailing_stop_order(
        self,
        symbol: str,
        qty: float,
        trail_percent: float,
    ) -> AlpacaOrder:
        """
        Sell-side trailing stop that trails trail_percent% below the high-water mark.
        Replaces the fixed take-profit leg from bracket orders.
        Alpaca preserves the high-water mark across market sessions (GTC).
        """
        body = {
            "symbol": symbol,
            "qty": str(int(qty)),
            "side": "sell",
            "type": "trailing_stop",
            "trail_percent": str(round(trail_percent, 2)),
            "time_in_force": "gtc",
        }
        data = self._post("/orders", body)
        return self._parse_order(data)

    def cancel_order(self, order_id: str) -> bool:
        return self._delete(f"/orders/{order_id}")

    def cancel_all_orders(self) -> bool:
        return self._delete("/orders")

    def get_orders(self, status: str = "open", limit: int = 50) -> List[AlpacaOrder]:
        data = self._get("/orders", params={"status": status, "limit": limit})
        return [self._parse_order(o) for o in data]

    def get_order(self, order_id: str) -> Optional[AlpacaOrder]:
        try:
            data = self._get(f"/orders/{order_id}")
            return self._parse_order(data)
        except Exception:
            return None

    def get_order_raw(self, order_id: str) -> Optional[Dict]:
        """Return full Alpaca order dict including legs. Used by reconciler."""
        try:
            return self._get(f"/orders/{order_id}")
        except Exception:
            return None

    def _parse_order(self, data: Dict) -> AlpacaOrder:
        return AlpacaOrder(
            id=data["id"],
            symbol=data["symbol"],
            side=data["side"],
            order_type=data["type"],
            qty=float(data.get("qty") or 0),
            filled_qty=float(data.get("filled_qty") or 0),
            status=data["status"],
            limit_price=float(data["limit_price"]) if data.get("limit_price") else None,
            stop_price=float(data["stop_price"]) if data.get("stop_price") else None,
            filled_avg_price=float(data["filled_avg_price"]) if data.get("filled_avg_price") else None,
            created_at=data.get("created_at", ""),
            filled_at=data.get("filled_at"),
            legs=data.get("legs"),
            raw=data,
        )

    # ─── Market Data ────────────────────────────────────────────────────────

    def get_latest_quote(self, symbol: str) -> Optional[Dict]:
        """Get latest bid/ask quote"""
        try:
            url = f"{DATA_BASE_URL}/stocks/{symbol}/quotes/latest"
            resp = requests.get(url, headers=self.headers, timeout=10)
            resp.raise_for_status()
            return resp.json().get("quote")
        except Exception as e:
            logger.error(f"Error getting quote for {symbol}: {e}")
            return None

    def get_latest_trade(self, symbol: str) -> Optional[Dict]:
        """Get latest trade price"""
        try:
            url = f"{DATA_BASE_URL}/stocks/{symbol}/trades/latest"
            resp = requests.get(url, headers=self.headers, timeout=10)
            resp.raise_for_status()
            return resp.json().get("trade")
        except Exception as e:
            logger.error(f"Error getting trade for {symbol}: {e}")
            return None

    def get_bars(
        self,
        symbol: str,
        timeframe: str = "1Day",
        limit: int = 50,
    ) -> List[Dict]:
        """Get OHLCV bars"""
        try:
            url = f"{DATA_BASE_URL}/stocks/{symbol}/bars"
            params = {"timeframe": timeframe, "limit": limit}
            resp = requests.get(url, headers=self.headers, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json().get("bars", [])
        except Exception as e:
            logger.error(f"Error getting bars for {symbol}: {e}")
            return []

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current market price — uses latest trade"""
        trade = self.get_latest_trade(symbol)
        if trade:
            return float(trade.get("p", 0)) or None
        return None
