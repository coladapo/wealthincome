"""Fail-closed behavior of the manual-order concentration cap."""

import pytest

from core.alpaca_client import (
    AlpacaClient,
    AlpacaOrderSide,
    ConcentrationCapError,
    MAX_SINGLE_POSITION_PCT,
)


class _Account:
    portfolio_value = 100_000.0


class _Position:
    market_value = 5_000.0


@pytest.fixture()
def client(monkeypatch):
    c = AlpacaClient("test-key", "test-secret", paper=True)
    monkeypatch.setattr(c, "get_account", lambda: _Account())
    monkeypatch.setattr(c, "get_position", lambda s: None)
    monkeypatch.setattr(c, "get_current_price", lambda s: 100.0)
    return c


def test_buy_within_cap_passes(client):
    # 8% of $100k = $8,000 → 79 shares @ $100 is fine
    client._enforce_concentration_cap("TEST", qty=79, side=AlpacaOrderSide.BUY)


def test_buy_over_cap_blocked(client):
    with pytest.raises(ConcentrationCapError):
        client._enforce_concentration_cap("TEST", qty=81, side=AlpacaOrderSide.BUY)


def test_existing_exposure_stacks(client, monkeypatch):
    monkeypatch.setattr(client, "get_position", lambda s: _Position())
    # $5k existing + $4k new = 9% > 8% cap
    with pytest.raises(ConcentrationCapError):
        client._enforce_concentration_cap("TEST", qty=40, side=AlpacaOrderSide.BUY)


def test_sells_never_capped(client):
    client._enforce_concentration_cap("TEST", qty=10_000, side=AlpacaOrderSide.SELL)


def test_fails_closed_when_unpriceable(client, monkeypatch):
    monkeypatch.setattr(client, "get_current_price", lambda s: None)
    with pytest.raises(ConcentrationCapError):
        client._enforce_concentration_cap("TEST", qty=1, side=AlpacaOrderSide.BUY)


def test_fails_closed_when_account_unreadable(client, monkeypatch):
    class _BrokenAccount:
        portfolio_value = 0

    monkeypatch.setattr(client, "get_account", lambda: _BrokenAccount())
    with pytest.raises(ConcentrationCapError):
        client._enforce_concentration_cap("TEST", qty=1, side=AlpacaOrderSide.BUY)
