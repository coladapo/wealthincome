"""Read-only Alpaca Market Data access for research (minute bars + the exchange calendar).

Reuses the header-auth pattern of core/alpaca_client.py (APCA-API-KEY-ID /
APCA-API-SECRET-KEY) without importing the trading client: nothing here can place
or touch an order. Credentials come from ALPACA_API_KEY / ALPACA_SECRET_KEY.

Feed: "sip" (full-market consolidated tape). The free plan serves SIP history
older than ~15 minutes, so callers only ask for finished sessions.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable, Dict, List

import requests

log = logging.getLogger("research.alpaca_data")

DATA_BASE_URL = "https://data.alpaca.markets/v2"
PAPER_BASE_URL = "https://paper-api.alpaca.markets/v2"
PAGE_LIMIT = 10000
RETRY_STATUS = (429, 500, 502, 503, 504)


class AlpacaDataError(RuntimeError):
    pass


def _headers() -> Dict[str, str]:
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise AlpacaDataError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set in the environment")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _get(url: str, params: Dict, retries: int = 5, backoff: float = 2.0,
         sleep: Callable[[float], None] = time.sleep, session=None) -> Dict:
    http = session or requests
    for attempt in range(retries + 1):
        wait = backoff * (2 ** attempt)
        try:
            resp = http.get(url, headers=_headers(), params=params, timeout=60)
        except requests.RequestException as e:
            err = f"network: {e.__class__.__name__}"
        else:
            if resp.status_code == 200:
                return resp.json()
            err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code not in RETRY_STATUS:
                raise AlpacaDataError(err)
            if resp.status_code == 429:
                try:
                    wait = max(wait, float(resp.headers.get("X-Ratelimit-Reset", 0)) - time.time() + 1)
                except ValueError:
                    pass
                wait = min(wait, 65.0)
        if attempt == retries:
            raise AlpacaDataError(err)
        log.warning("alpaca data error (%s); retry %d in %.0fs", err, attempt + 1, wait)
        sleep(wait)
    raise AlpacaDataError("unreachable")


def fetch_bars(symbols: List[str], start: str, end: str, timeframe: str = "1Min",
               feed: str = "sip", adjustment: str = "raw",
               sleep: Callable[[float], None] = time.sleep, session=None) -> Dict[str, List[Dict]]:
    """All bars for `symbols` with start <= t <= end (RFC-3339 UTC), following page tokens.

    Returns {symbol: [{"t","o","h","l","c","v","n","vw"}, ...]} in time order. Raises
    AlpacaDataError if any page ultimately fails, so a caller never marks a partially
    fetched window as loaded.
    """
    out: Dict[str, List[Dict]] = {}
    params = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start, "end": end,
              "limit": PAGE_LIMIT, "feed": feed, "adjustment": adjustment, "sort": "asc"}
    while True:
        j = _get(f"{DATA_BASE_URL}/stocks/bars", params, sleep=sleep, session=session)
        for s, rows in (j.get("bars") or {}).items():
            out.setdefault(s, []).extend(rows)
        token = j.get("next_page_token")
        if not token:
            return out
        params["page_token"] = token


def fetch_calendar(start: str, end: str, sleep: Callable[[float], None] = time.sleep,
                   session=None) -> List[Dict]:
    """Exchange calendar [{"date","open","close"}] (ET wall clock "HH:MM"); read-only endpoint."""
    j = _get(f"{PAPER_BASE_URL}/calendar", {"start": start, "end": end}, sleep=sleep, session=session)
    return [{"date": d["date"], "open": d["open"], "close": d["close"]} for d in j]
