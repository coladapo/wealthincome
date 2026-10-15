"""In-memory stand-in for core.alpaca_client.AlpacaClient — no network.

Mirrors the behaviour the trader/reconciler rely on, including Alpaca's
client_order_id uniqueness (a repeat key returns the existing order, which is
what AlpacaClient._submit_order does on Alpaca's 422)."""

import itertools
from types import SimpleNamespace

from core.alpaca_client import AlpacaOrder


class FakeBroker:
    paper = True

    def __init__(self, price=100.0, market_open=True):
        self.price = price
        self.market_open = market_open
        self.orders = {}          # id -> AlpacaOrder
        self.by_client_id = {}    # client_order_id -> id
        self.submissions = []     # every order that actually reached the "exchange"
        self.positions = {}       # symbol -> SimpleNamespace position
        self._ids = itertools.count(1)

    # ── submission ───────────────────────────────────────────────────────────
    def _submit(self, symbol, qty, side, order_type, client_order_id, status, filled=True):
        if client_order_id and client_order_id in self.by_client_id:
            return self.orders[self.by_client_id[client_order_id]]
        oid = f"ord-{next(self._ids)}"
        filled_qty = float(qty) if filled else 0.0
        raw = {"id": oid, "symbol": symbol, "side": side, "type": order_type,
               "qty": str(qty), "filled_qty": str(filled_qty), "status": status,
               "filled_avg_price": str(self.price) if filled else None,
               "client_order_id": client_order_id, "created_at": "2026-10-14T14:00:00Z",
               "filled_at": "2026-10-14T14:00:01Z" if filled else None}
        o = AlpacaOrder(id=oid, symbol=symbol, side=side, order_type=order_type, qty=float(qty),
                        filled_qty=filled_qty, status=status, limit_price=None, stop_price=None,
                        filled_avg_price=self.price if filled else None,
                        created_at=raw["created_at"], filled_at=raw["filled_at"], raw=raw)
        self.orders[oid] = o
        if client_order_id:
            self.by_client_id[client_order_id] = oid
        self.submissions.append(o)
        if filled and side == "buy" and order_type != "trailing_stop":
            self.hold(symbol, float(qty))
        return o

    def place_limit_order(self, symbol, qty, side, limit_price, time_in_force=None,
                          enforce_cap=True, client_order_id=None):
        return self._submit(symbol, qty, getattr(side, "value", side), "limit", client_order_id, "filled")

    def place_market_order(self, symbol, qty, side, time_in_force=None, enforce_cap=True,
                           client_order_id=None):
        return self._submit(symbol, qty, getattr(side, "value", side), "market", client_order_id, "filled")

    def place_trailing_stop_order(self, symbol, qty, trail_percent, client_order_id=None):
        return self._submit(symbol, qty, "sell", "trailing_stop", client_order_id, "new", filled=False)

    def cancel_order(self, order_id):
        o = self.orders[order_id]
        o.status = "canceled"
        o.raw["status"] = "canceled"
        return True

    # ── reads ────────────────────────────────────────────────────────────────
    def hold(self, symbol, qty, avg=None):
        self.positions[symbol] = SimpleNamespace(
            symbol=symbol, qty=qty, avg_entry_price=avg or self.price, current_price=self.price,
            market_value=qty * self.price, unrealized_pl=0.0, unrealized_plpc=0.0, side="long")

    def get_positions(self):
        return list(self.positions.values())

    def get_orders(self, status="open", limit=50, symbols=None):
        out = []
        for o in self.orders.values():
            is_open = o.status in ("new", "accepted", "pending_new", "partially_filled", "held")
            if (status == "open" and not is_open) or (status == "closed" and is_open):
                continue
            if symbols and o.symbol not in symbols:
                continue
            out.append(o)
        return out[:limit]

    def get_order_raw(self, order_id):
        o = self.orders.get(order_id)
        return dict(o.raw, status=o.status) if o else None

    def get_order_by_client_id(self, client_order_id):
        oid = self.by_client_id.get(client_order_id)
        return self.orders[oid] if oid else None

    def get_current_price(self, symbol):
        return self.price

    def is_market_open(self):
        return self.market_open

    def trailing_stops(self, symbol=None, open_only=True):
        return [o for o in self.submissions if o.order_type == "trailing_stop"
                and (symbol is None or o.symbol == symbol)
                and (not open_only or o.status == "new")]
