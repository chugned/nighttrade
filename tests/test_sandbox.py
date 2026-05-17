"""Sandbox (broker paper-account) execution-layer and credential tests.

These cover the *structural guards* — the paper-URL allowlist, credential
verification, the disabled-by-default posture. The actual paper-account HTTP
calls require live paper keys and are not exercised offline.
"""

from __future__ import annotations

import pytest

from nighttrade.config import SandboxConfig, load_config
from nighttrade.exchanges.credentials import (
    ApiCredentials,
    ApiKeyPermissions,
    LiveAccountError,
    TradePermissionError,
    enforce_key_safety,
    load_sandbox_credentials,
)
from nighttrade.exchanges.sandbox import (
    SandboxExchangeClient,
    SandboxSafetyError,
    _assert_paper_url,
    _PAPER_URLS,
    build_sandbox_client,
)
from nighttrade.paper import PaperBroker, SandboxBroker


# --- credentials -----------------------------------------------------------

def test_no_credentials_returns_none(monkeypatch):
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_API_SECRET", raising=False)
    assert load_sandbox_credentials("alpaca") is None


def test_partial_credentials_raise(monkeypatch):
    from nighttrade.exchanges.credentials import MissingCredentialsError
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "abc")
    monkeypatch.delenv("ALPACA_PAPER_API_SECRET", raising=False)
    with pytest.raises(MissingCredentialsError):
        load_sandbox_credentials("alpaca")


def test_masked_key_hides_secret():
    creds = ApiCredentials("alpaca", "ABCDEFGH1234", "secret")
    assert creds.masked_key().endswith("1234")
    assert "ABCDEFGH" not in creds.masked_key()


def test_read_only_key_accepted():
    enforce_key_safety(ApiKeyPermissions(can_read=True), SandboxConfig())


def test_funding_key_rejected():
    with pytest.raises(LiveAccountError):
        enforce_key_safety(ApiKeyPermissions(can_fund=True), SandboxConfig())


def test_trade_key_rejected_when_read_only_required():
    with pytest.raises(TradePermissionError):
        enforce_key_safety(ApiKeyPermissions(can_trade=True), SandboxConfig())


def test_trade_key_allowed_when_read_only_disabled():
    cfg = SandboxConfig(require_read_only_keys=False)
    # Order scope is fine; a live account is still banned.
    enforce_key_safety(ApiKeyPermissions(can_trade=True), cfg)


def test_live_account_key_rejected():
    with pytest.raises(LiveAccountError):
        enforce_key_safety(ApiKeyPermissions(is_paper=False), SandboxConfig())


# --- paper-URL guard -------------------------------------------------------

def test_all_sandbox_urls_are_paper():
    for url in _PAPER_URLS.values():
        assert "paper" in url
        _assert_paper_url(url)


def test_live_url_rejected():
    for url in ("https://api.alpaca.markets",
                "https://broker-api.alpaca.markets"):
        with pytest.raises(SandboxSafetyError):
            _assert_paper_url(url)


def test_sandbox_client_uses_paper_base_url():
    client = SandboxExchangeClient(
        ApiCredentials("alpaca", "k", "s"), SandboxConfig())
    assert client.base_url == _PAPER_URLS["alpaca"]
    assert "paper" in client.base_url


def test_place_order_requires_verified_trade_key():
    """An unverified client cannot place a paper order."""
    from nighttrade.models import Side
    client = SandboxExchangeClient(
        ApiCredentials("alpaca", "k", "s"), SandboxConfig())
    with pytest.raises(SandboxSafetyError):
        client.place_paper_order("AAPL", Side.BUY, 1.0)


# --- build / broker --------------------------------------------------------

def test_build_sandbox_client_none_when_disabled():
    cfg = load_config(load_dotenv_file=False)
    assert cfg.sandbox.enabled is False
    assert build_sandbox_client(cfg) is None


def test_sandbox_broker_defaults_to_simulated():
    broker = SandboxBroker(PaperBroker(10_000.0))
    assert broker.execution_mode == "simulated"
    assert not broker.is_broker_paper_execution
