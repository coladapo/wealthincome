"""
FastAPI REST API — thin layer over the DB.
Both Streamlit and external tools read/control the trader through here.
Starts the reconciler background thread on startup.
"""

import os
import sys
import threading
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env explicitly so the API works regardless of how it was launched
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from backend.db import (
    get_config, set_config, set_config_many, get_cycles, get_last_cycle,
    get_trades, get_errors, get_token_usage, init_db,
    get_ai_decisions, get_ai_decision_detail,
    get_orders_history, get_order_groups,
    get_closed_positions, get_open_positions_lifecycle,
    get_equity_curve, get_daily_summaries, compute_risk_metrics,
)

logger = logging.getLogger("api")


# ─── Lifespan: start/stop reconciler ────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    init_db()
    try:
        from core.alpaca_client import AlpacaClient
        api_key = os.environ.get("ALPACA_API_KEY")
        secret_key = os.environ.get("ALPACA_SECRET_KEY")
        if api_key and secret_key:
            paper = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
            alpaca = AlpacaClient(api_key=api_key, secret_key=secret_key, paper=paper)
            from backend.reconciler import start_reconciler
            start_reconciler(alpaca)
            logger.info("Reconciler started")
        else:
            logger.warning("No Alpaca keys — reconciler not started")
    except Exception as e:
        logger.warning(f"Could not start reconciler: {e}")
    yield
    try:
        from backend.reconciler import stop_reconciler
        stop_reconciler()
    except Exception:
        pass


app = FastAPI(title="WealthIncome Trading API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Models ──────────────────────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    updates: Dict[str, str]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_alpaca_status():
    try:
        from core.alpaca_client import AlpacaClient
        api_key = os.environ.get("ALPACA_API_KEY")
        secret_key = os.environ.get("ALPACA_SECRET_KEY")
        paper = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
        if not api_key or not secret_key:
            return None, None, []
        alpaca = AlpacaClient(api_key=api_key, secret_key=secret_key, paper=paper)
        account = alpaca.get_account()
        positions = alpaca.get_positions()
        clock = alpaca.get_clock()
        return {
            "portfolio_value": account.portfolio_value,
            "cash": account.cash,
            "buying_power": account.buying_power,
            "long_market_value": account.long_market_value,
            "status": account.status,
            "trading_blocked": account.trading_blocked,
            "paper": paper,
        }, {
            "is_open": clock.get("is_open", False),
            "next_open": clock.get("next_open"),
            "next_close": clock.get("next_close"),
        }, [
            {
                "symbol": p.symbol, "qty": p.qty,
                "avg_entry_price": p.avg_entry_price,
                "current_price": p.current_price,
                "market_value": p.market_value,
                "unrealized_pl": p.unrealized_pl,
                "unrealized_plpc": p.unrealized_plpc,
            }
            for p in positions
        ]
    except Exception as e:
        logger.warning(f"Could not fetch Alpaca data: {e}")
        return None, None, []


# ─── Core routes ─────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True}


def _is_trader_running() -> bool:
    """Ground truth: check PID file and whether that process is alive."""
    pid_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trader.pid")
    if not os.path.exists(pid_file):
        return False
    try:
        pid = int(open(pid_file).read().strip())
        os.kill(pid, 0)  # signal 0 = existence check only
        return True
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        return False


def _compute_daily_pnl(current_portfolio_value: float) -> dict:
    """Compute today's P&L using the first equity snapshot of the day as the baseline."""
    try:
        from backend.db import DB_PATH
        import sqlite3
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT portfolio_value FROM equity_snapshots WHERE snapshot_at LIKE ? ORDER BY snapshot_at ASC LIMIT 1",
            (f"{today}%",)
        ).fetchone()
        conn.close()
        if row and row["portfolio_value"]:
            baseline = float(row["portfolio_value"])
            daily_pnl = current_portfolio_value - baseline
            daily_pnl_pct = daily_pnl / baseline * 100 if baseline else 0
            return {"daily_pnl": round(daily_pnl, 2), "daily_pnl_pct": round(daily_pnl_pct, 4)}
    except Exception:
        pass
    return {"daily_pnl": None, "daily_pnl_pct": None}


@app.get("/status")
def status():
    cfg = get_config()
    last_cycle = get_last_cycle()
    account, clock, positions = _get_alpaca_status()
    running = _is_trader_running()
    # Keep DB flag in sync with reality
    if not running and cfg.get("trader_running", "false") == "true":
        set_config("trader_running", "false")

    # Enrich account with daily P&L computed from equity snapshots
    if account and account.get("portfolio_value"):
        pnl_data = _compute_daily_pnl(account["portfolio_value"])
        account["daily_pnl"] = pnl_data["daily_pnl"]
        account["daily_pnl_pct"] = pnl_data["daily_pnl_pct"]

    return {
        "trader_running": running,
        "account": account,
        "clock": clock,
        "positions": positions,
        "last_cycle": last_cycle,
        "config": cfg,
    }


@app.get("/cycles")
def cycles(limit: int = 20):
    return {"cycles": get_cycles(limit=limit)}


@app.get("/trades")
def trades(limit: int = 50, symbol: str = None):
    return {"trades": get_trades(limit=limit, symbol=symbol)}


@app.get("/errors")
def errors(limit: int = 20):
    return {"errors": get_errors(limit=limit)}


@app.get("/config")
def config():
    return get_config()


@app.post("/config")
def update_config(body: ConfigUpdate):
    allowed_keys = {
        "max_position_pct", "max_open_positions", "daily_loss_limit_pct",
        "confidence_threshold", "poll_interval", "trade_only_market_hours",
        "watchlist", "trader_running",
    }
    bad_keys = set(body.updates.keys()) - allowed_keys
    if bad_keys:
        raise HTTPException(status_code=400, detail=f"Unknown config keys: {bad_keys}")
    set_config_many(body.updates)
    return {"ok": True, "updated": list(body.updates.keys())}


@app.post("/start")
def start_trader():
    import subprocess
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "start_trader.sh")
    try:
        result = subprocess.run(["bash", script], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"start_trader.sh failed: {result.stderr[:300]}")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="start_trader.sh timed out")
    set_config("trader_running", "true")
    return {"ok": True, "trader_running": True}


@app.post("/stop")
def stop_trader():
    import subprocess
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "stop_trader.sh")
    try:
        result = subprocess.run(["bash", script], capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            logger.warning(f"stop_trader.sh returned non-zero: {result.stderr[:300]}")
    except subprocess.TimeoutExpired:
        logger.warning("stop_trader.sh timed out")
    set_config("trader_running", "false")
    return {"ok": True, "trader_running": False}


_trigger_lock = threading.Lock()
_trigger_running = False


@app.post("/trigger")
def trigger_cycle():
    global _trigger_running
    if not _trigger_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Cycle already running")

    _trigger_running = True

    def _run():
        global _trigger_running
        try:
            from backend.trader import run_cycle, get_alpaca
            alpaca = get_alpaca()
            run_cycle(alpaca)
        except Exception as e:
            logger.error(f"Triggered cycle error: {e}", exc_info=True)
        finally:
            _trigger_running = False
            _trigger_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": "Cycle started — poll /cycles for results"}


@app.get("/trigger/status")
def trigger_status():
    return {"running": _trigger_running}


# ─── Analytics routes ────────────────────────────────────────────────────────

@app.get("/usage")
def usage(days: int = 1):
    return get_token_usage(days=days)


@app.get("/equity-curve")
def equity_curve(days: int = 90):
    return {"snapshots": get_equity_curve(days=days)}


@app.get("/performance")
def performance(days: int = 252):
    return compute_risk_metrics(days=days)


@app.get("/pnl/daily")
def pnl_daily(days: int = 30):
    return {"days": get_daily_summaries(days=days)}


# ─── Position history ────────────────────────────────────────────────────────

@app.get("/positions/history")
def position_history(limit: int = 100, symbol: str = None):
    return {"positions": get_closed_positions(limit=limit, symbol=symbol)}


@app.get("/positions/open")
def positions_open():
    return {"positions": get_open_positions_lifecycle()}


# ─── Direct order placement (manual, bypasses Claude cycle) ─────────────────

class DirectOrderRequest(BaseModel):
    symbol: str
    side: str          # "buy" or "sell"
    qty: float
    order_type: str = "market"
    limit_price: float = None

@app.post("/order")
def place_direct_order(req: DirectOrderRequest):
    """Place a market or limit order directly on Alpaca — no Claude cycle."""
    from core.alpaca_client import AlpacaClient, AlpacaOrderSide
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    paper = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
    if not api_key or not secret_key:
        raise HTTPException(status_code=500, detail="Alpaca credentials not configured")

    alpaca = AlpacaClient(api_key=api_key, secret_key=secret_key, paper=paper)
    side = AlpacaOrderSide.BUY if req.side.lower() == "buy" else AlpacaOrderSide.SELL

    try:
        if req.order_type == "limit" and req.limit_price:
            order = alpaca.place_limit_order(symbol=req.symbol.upper(), qty=req.qty, side=side, limit_price=req.limit_price)
        else:
            order = alpaca.place_market_order(symbol=req.symbol.upper(), qty=req.qty, side=side)

        if not order:
            raise HTTPException(status_code=500, detail="Order returned None from Alpaca")

        logger.info(f"Direct order placed: {req.side.upper()} {req.qty} {req.symbol} — alpaca_id={order.id}")
        return {
            "ok": True,
            "order_id": order.id,
            "symbol": req.symbol.upper(),
            "side": req.side,
            "qty": req.qty,
            "status": order.status,
        }
    except Exception as e:
        logger.error(f"Direct order failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/orders/cancel-all")
def cancel_all_open_orders():
    """Cancel all open orders on Alpaca."""
    from core.alpaca_client import AlpacaClient
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    paper = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
    alpaca = AlpacaClient(api_key=api_key, secret_key=secret_key, paper=paper)
    try:
        open_orders = alpaca.get_orders(status="open", limit=200)
        cancelled = []
        for o in open_orders:
            try:
                alpaca._delete(f"/orders/{o.id}")
                cancelled.append(o.id)
            except Exception as e:
                logger.warning(f"Could not cancel order {o.id}: {e}")
        return {"ok": True, "cancelled": len(cancelled), "order_ids": cancelled}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Order tracking ──────────────────────────────────────────────────────────

@app.get("/orders")
def orders_history(limit: int = 100, status: str = None, symbol: str = None):
    return {"orders": get_orders_history(limit=limit, status=status, symbol=symbol)}


@app.get("/order-groups")
def order_groups(limit: int = 50):
    return {"groups": get_order_groups(limit=limit)}


# ─── AI decision tracking ────────────────────────────────────────────────────

@app.get("/decisions")
def decisions(limit: int = 20, cycle_id: int = None):
    return {"decisions": get_ai_decisions(limit=limit, cycle_id=cycle_id)}


@app.get("/decisions/{decision_id}")
def decision_detail(decision_id: int):
    d = get_ai_decision_detail(decision_id)
    if not d:
        raise HTTPException(status_code=404, detail="Decision not found")
    return d


# ─── Entry point ─────────────────────────────────────────────────────────────

def _setup_logging():
    os.makedirs("logs", exist_ok=True)
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s")
        fh = logging.FileHandler("logs/trader.log")
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(fh)
        root.addHandler(sh)
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)


if __name__ == "__main__":
    import uvicorn
    _setup_logging()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
