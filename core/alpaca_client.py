"""
Alpaca API Client - Live and paper trading via Alpaca Markets
"""

import logging
import time
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

PAPER_BASE_URL = "https://paper-api.alpaca.markets/v2"
LIVE_BASE_URL = "https://api.alpaca.markets/v2"
DATA_BASE_URL = "https://data.alpaca.markets/v2"

# Hard concentration cap — single source of truth in core/risk_limits.py,
# shared with the autonomous trader (unified 2026-06-11; see bug history:
# the 2026-04-15 CAT trade put ~45% of the account into one position).
from core.risk_limits import MAX_SINGLE_POSITION_PCT


class ConcentrationCapError(Exception):
    """Raised when a buy order would push a single position past the concentration cap."""


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

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        """HTTP request with exponential backoff for transient SSL/network errors."""
        last_exc = None
        for attempt in range(3):
            try:
                resp = requests.request(method, url, headers=self.headers, timeout=15, **kwargs)
                resp.raise_for_status()
                return resp
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
                last_exc = e
                if attempt < 2:
                    time.sleep(2 ** attempt)  # 1s, 2s
                    logger.warning(f"Alpaca {method} {url} retry {attempt + 1}/3 after: {e}")
            except requests.exceptions.HTTPError as e:
                # Surface Alpaca's rejection reason (e.g. insufficient qty,
                # wash trade) — the status line alone is undiagnosable.
                body = ""
                if e.response is not None:
                    body = e.response.text[:300]
                    if method == "GET" and e.response.status_code == 404:
                        raise  # "not found" lookups are expected answers, not errors
                logger.error(f"Alpaca {method} {url} → HTTP {e.response.status_code if e.response is not None else '?'}: {body}")
                raise
        raise last_exc

    def _get(self, path: str, params: Dict = None) -> Dict:
        url = f"{self.base_url}{path}"
        resp = self._request_with_retry("GET", url, params=params)
        return resp.json()

    def _post(self, path: str, body: Dict) -> Dict:
        url = f"{self.base_url}{path}"
        resp = self._request_with_retry("POST", url, json=body)
        return resp.json()

    def _delete(self, path: str) -> bool:
        url = f"{self.base_url}{path}"
        try:
            resp = self._request_with_retry("DELETE", url)
            return resp.status_code in (200, 204)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (200, 204):
                return True
            return False

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

    def get_account_raw(self) -> Dict:
        """Full /account dict (includes last_equity, created_at)."""
        return self._get("/account")

    def get_account_activities(
        self,
        activity_types: Optional[str] = None,
        page_size: int = 100,
        page_token: Optional[str] = None,
        direction: str = "asc",
    ) -> List[Dict]:
        """One page of account activities (read-only). activity_types e.g. 'FILL'
        or 'FILL,DIV,FEE'; None = every type."""
        params = {"page_size": page_size, "direction": direction}
        if activity_types:
            params["activity_types"] = activity_types
        if page_token:
            params["page_token"] = page_token
        return self._get("/account/activities", params=params)

    def iter_account_activities(self, activity_types: Optional[str] = None,
                                page_size: int = 100) -> List[Dict]:
        """Every activity for the account's full history, oldest first.
        Paginates with the last row's id as the next page_token."""
        out: List[Dict] = []
        token = None
        while True:
            page = self.get_account_activities(activity_types, page_size, token, "asc")
            if not page:
                break
            out.extend(page)
            if len(page) < page_size:
                break
            token = page[-1]["id"]
        return out

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

    def _enforce_concentration_cap(self, symbol: str, qty: float, side: AlpacaOrderSide,
                                   price: Optional[float] = None) -> None:
        """Block a BUY whose resulting position would exceed MAX_SINGLE_POSITION_PCT
        of portfolio value. Sells are never capped. Raises ConcentrationCapError.

        Counts existing exposure to the same symbol so repeated small buys can't
        stack past the cap. Fails closed: if price or account can't be read, the
        order is refused rather than waved through."""
        if side != AlpacaOrderSide.BUY:
            return

        if price is None:
            price = self.get_current_price(symbol)
        if not price or price <= 0:
            raise ConcentrationCapError(
                f"Cannot price-check {symbol} before order — refusing buy (fail-closed)."
            )

        account = self.get_account()
        portfolio_value = account.portfolio_value
        if not portfolio_value or portfolio_value <= 0:
            raise ConcentrationCapError(
                "Cannot read portfolio value — refusing buy (fail-closed)."
            )

        existing_value = 0.0
        existing = self.get_position(symbol)
        if existing is not None:
            existing_value = abs(float(getattr(existing, "market_value", 0) or 0))

        new_value = qty * price
        resulting_pct = (existing_value + new_value) / portfolio_value
        if resulting_pct > MAX_SINGLE_POSITION_PCT:
            max_dollars = portfolio_value * MAX_SINGLE_POSITION_PCT
            raise ConcentrationCapError(
                f"Order blocked: {symbol} would be {resulting_pct * 100:.1f}% of portfolio "
                f"(cap {MAX_SINGLE_POSITION_PCT * 100:.0f}%, max ${max_dollars:,.0f}). "
                f"Existing ${existing_value:,.0f} + new ${new_value:,.0f} on "
                f"${portfolio_value:,.0f} portfolio."
            )

    def place_market_order(
        self,
        symbol: str,
        qty: float,
        side: AlpacaOrderSide,
        time_in_force: AlpacaTimeInForce = AlpacaTimeInForce.DAY,
        enforce_cap: bool = True,
        client_order_id: Optional[str] = None,
    ) -> AlpacaOrder:
        # enforce_cap defaults True so manual paths (dashboard form, /order endpoint)
        # are protected automatically. The autonomous traders do their own sizing
        # against their own (larger) cap and pass enforce_cap=False.
        if enforce_cap:
            self._enforce_concentration_cap(symbol, qty, side)
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side.value,
            "type": "market",
            "time_in_force": time_in_force.value,
        }
        return self._submit_order(body, client_order_id)

    def place_limit_order(
        self,
        symbol: str,
        qty: float,
        side: AlpacaOrderSide,
        limit_price: float,
        time_in_force: AlpacaTimeInForce = AlpacaTimeInForce.DAY,
        enforce_cap: bool = True,
        client_order_id: Optional[str] = None,
    ) -> AlpacaOrder:
        if enforce_cap:
            self._enforce_concentration_cap(symbol, qty, side, price=limit_price)
        body = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side.value,
            "type": "limit",
            "limit_price": str(round(limit_price, 2)),
            "time_in_force": time_in_force.value,
        }
        return self._submit_order(body, client_order_id)

    def place_bracket_order(
        self,
        symbol: str,
        qty: float,
        side: AlpacaOrderSide,
        take_profit_price: float,
        stop_loss_price: float,
        client_order_id: Optional[str] = None,
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
        return self._submit_order(body, client_order_id)

    def place_trailing_stop_order(
        self,
        symbol: str,
        qty: float,
        trail_percent: float,
        client_order_id: Optional[str] = None,
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
        return self._submit_order(body, client_order_id)

    def _submit_order(self, body: Dict, client_order_id: Optional[str] = None) -> AlpacaOrder:
        """POST an order, idempotently when a client_order_id is given.

        _request_with_retry re-sends the POST after a connection/SSL error, and
        that error can arrive AFTER Alpaca accepted the order — without a key
        the retry is a second, real order. With a key Alpaca rejects the repeat
        (422 "client_order_id must be unique") and we return the order that
        already exists instead of raising."""
        if client_order_id:
            body = dict(body, client_order_id=client_order_id)
        try:
            data = self._post("/orders", body)
        except requests.exceptions.HTTPError as e:
            resp = e.response
            if (client_order_id and resp is not None and resp.status_code == 422
                    and "client_order_id" in (resp.text or "")):
                existing = self.get_order_by_client_id(client_order_id)
                if existing is not None:
                    logger.warning(
                        f"Duplicate submit suppressed: client_order_id {client_order_id} "
                        f"already exists as order {existing.id}"
                    )
                    return existing
            raise
        return self._parse_order(data)

    def get_order_by_client_id(self, client_order_id: str) -> Optional[AlpacaOrder]:
        """Look up an order by our idempotency key. None if Alpaca has no such order."""
        try:
            data = self._get("/orders:by_client_order_id",
                             params={"client_order_id": client_order_id})
            return self._parse_order(data)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise

    def cancel_order(self, order_id: str) -> bool:
        return self._delete(f"/orders/{order_id}")

    def cancel_all_orders(self) -> bool:
        return self._delete("/orders")

    def get_orders(self, status: str = "open", limit: int = 50,
                   symbols: Optional[List[str]] = None) -> List[AlpacaOrder]:
        params = {"status": status, "limit": limit}
        if symbols:
            params["symbols"] = ",".join(symbols)
        data = self._get("/orders", params=params)
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

    def _data_get(self, path: str, params: Dict = None) -> Dict:
        """GET against the data API endpoint with retry."""
        url = f"{DATA_BASE_URL}{path}"
        resp = self._request_with_retry("GET", url, params=params)
        return resp.json()

    def get_latest_quote(self, symbol: str) -> Optional[Dict]:
        """Get latest bid/ask quote"""
        try:
            return self._data_get(f"/stocks/{symbol}/quotes/latest").get("quote")
        except Exception as e:
            logger.error(f"Error getting quote for {symbol}: {e}")
            return None

    def get_latest_trade(self, symbol: str) -> Optional[Dict]:
        """Get latest trade price"""
        try:
            return self._data_get(f"/stocks/{symbol}/trades/latest").get("trade")
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
            return self._data_get(f"/stocks/{symbol}/bars", params={"timeframe": timeframe, "limit": limit}).get("bars", [])
        except Exception as e:
            logger.error(f"Error getting bars for {symbol}: {e}")
            return []

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current market price — uses latest trade"""
        trade = self.get_latest_trade(symbol)
        if trade:
            return float(trade.get("p", 0)) or None
        return None
