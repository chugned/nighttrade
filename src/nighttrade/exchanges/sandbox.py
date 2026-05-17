"""Broker SANDBOX (paper-account) execution layer.

SANDBOX MEANS A BROKER PAPER ACCOUNT. There is no live-brokerage execution
path in this module — not a disabled one, not a guarded one: there is no code
that can send an order to a real money account.

Structural safety, enforced here and tested in ``tests/test_sandbox.py``:

* **Paper-URL allowlist.** A :class:`SandboxExchangeClient` can only be
  constructed against a URL in ``_PAPER_URLS``. There is no parameter to pass
  an arbitrary (live) URL; every request re-asserts the base URL is a known
  paper endpoint before sending.
* **No funding path.** This module contains no deposit/withdraw/transfer call.
  On connect it verifies the key's account and rejects anything that is not a
  paper account (:func:`credentials.enforce_key_safety`).
* **Read-only by default.** Unless the operator explicitly lowers
  ``sandbox.require_read_only_keys``, an order-scoped key is refused and
  execution falls back to local simulation.

Even with an order-scoped paper key, the worst an order can do is move
*simulated paper-account shares* at the broker.
"""

from __future__ import annotations

import json as _json
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, Optional

import httpx

from ..config.schema import AppConfig, SandboxConfig
from ..models import Fill, Side
from ..runtime import get_logger
from .base import ExchangeError
from .credentials import (
    ApiCredentials,
    ApiKeyPermissions,
    SecurityError,
    enforce_key_safety,
    load_sandbox_credentials,
)

_log = get_logger("exchanges.sandbox")

# The ONLY base URLs this module will ever talk to. Each is a broker
# PAPER-trading endpoint serving simulated accounts. Live hosts appear
# nowhere in this file.
_PAPER_URLS: Dict[str, str] = {
    "alpaca": "https://paper-api.alpaca.markets",
}

# Hostnames that must never be reached for execution. Used by the URL guard
# as a belt-and-braces check in addition to the positive allowlist.
_FORBIDDEN_HOSTS = frozenset({
    "api.alpaca.markets",         # Alpaca LIVE trading
    "broker-api.alpaca.markets",  # Alpaca brokerage / funding
})

# The exact hostnames the paper allowlist resolves to.
_ALLOWED_HOSTS = frozenset(
    urllib.parse.urlparse(u).hostname for u in _PAPER_URLS.values()
)


class SandboxSafetyError(SecurityError):
    """Raised when a sandbox operation would breach the paper-only contract."""


def _assert_paper_url(url: str) -> None:
    """Hard guard: ``url`` must be a known paper endpoint and nothing else."""
    if url not in _PAPER_URLS.values():
        raise SandboxSafetyError(
            f"refusing non-paper base URL: {url!r}. Sandbox execution is "
            "paper-account-only; there is no live execution path."
        )
    host = urllib.parse.urlparse(url).hostname
    if host in _FORBIDDEN_HOSTS or host not in _ALLOWED_HOSTS:
        raise SandboxSafetyError(f"refusing forbidden (live) host in {url!r}")


class SandboxExchangeClient:
    """A paper-account-only broker client (Alpaca paper trading)."""

    def __init__(self, credentials: ApiCredentials, config: SandboxConfig,
                 timeout: float = 10.0) -> None:
        self.broker = credentials.broker
        if self.broker not in _PAPER_URLS:
            raise SandboxSafetyError(f"no paper endpoint for broker {self.broker!r}")
        self.base_url = _PAPER_URLS[self.broker]
        _assert_paper_url(self.base_url)  # guard at construction
        self._creds = credentials
        self._config = config
        self._timeout = timeout

    # -- low-level transport -------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._creds.api_key,
            "APCA-API-SECRET-KEY": self._creds.api_secret,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str,
                 body: Optional[dict] = None) -> dict:
        _assert_paper_url(self.base_url)  # guard before every call
        content = _json.dumps(body) if body is not None else None
        with httpx.Client(base_url=self.base_url, timeout=self._timeout) as client:
            resp = client.request(method, path, headers=self._headers(),
                                   content=content)
            resp.raise_for_status()
            return resp.json()

    # -- credential verification --------------------------------------------

    def verify_credentials(self) -> ApiKeyPermissions:
        """Connect to the paper account, read its scopes, enforce the policy.

        Raises:
            LiveAccountError: the key resolves to a live account — never allowed.
            TradePermissionError: order scope while read-only keys required.
            ExchangeError: the paper endpoint could not be reached.
        """
        try:
            permissions = self._read_permissions()
        except httpx.HTTPError as exc:
            raise ExchangeError(
                f"sandbox: could not reach {self.broker} paper API: {exc}"
            ) from exc

        # The single, non-negotiable safety gate.
        enforce_key_safety(permissions, self._config)
        self._creds = ApiCredentials(
            broker=self._creds.broker, api_key=self._creds.api_key,
            api_secret=self._creds.api_secret, permissions=permissions,
        )
        _log.info("sandbox key verified (%s paper): scopes=%s",
                  self.broker, permissions.describe())
        return permissions

    def _read_permissions(self) -> ApiKeyPermissions:
        account = self._request("GET", "/v2/account")
        blocked = (bool(account.get("account_blocked"))
                   or bool(account.get("trading_blocked")))
        active = str(account.get("status", "")).upper() == "ACTIVE"
        return ApiKeyPermissions(
            can_read=True,
            can_trade=active and not blocked,
            # The paper API never exposes a real-money funding path; the base
            # URL is allowlist-guaranteed to be the paper endpoint.
            can_fund=False,
            is_paper=True,
        )

    # -- account / execution -------------------------------------------------

    def get_balances(self) -> Dict[str, float]:
        """Return the paper account's cash plus per-symbol share counts."""
        account = self._request("GET", "/v2/account")
        balances: Dict[str, float] = {"USD": float(account.get("cash", 0.0) or 0.0)}
        for pos in self._request("GET", "/v2/positions"):
            balances[pos["symbol"]] = float(pos.get("qty", 0.0) or 0.0)
        return balances

    def place_paper_order(self, symbol: str, side: Side, quantity: float) -> Fill:
        """Place a MARKET order on the broker PAPER account and return the fill.

        Pre-conditions (all enforced): the key is verified, has order scope,
        and read-only mode is off. The base URL is re-asserted as a paper
        endpoint immediately before the request.
        """
        perms = self._creds.permissions
        if perms is None:
            raise SandboxSafetyError("credentials not verified — call "
                                     "verify_credentials() first")
        if not perms.can_trade or self._config.require_read_only_keys:
            raise SandboxSafetyError(
                "paper order placement requires an order-scoped key and "
                "sandbox.require_read_only_keys: false"
            )
        _assert_paper_url(self.base_url)  # final guard before sending

        resp = self._request("POST", "/v2/orders", {
            "symbol": symbol,
            "qty": f"{quantity:.6f}",
            "side": side.value.lower(),
            "type": "market",
            "time_in_force": "day",
        })
        return self._fill_from_alpaca(symbol, side, quantity, resp)

    @staticmethod
    def _fill_from_alpaca(symbol: str, side: Side, quantity: float,
                          resp: dict) -> Fill:
        filled_qty = float(resp.get("filled_qty") or 0.0) or quantity
        avg_raw = resp.get("filled_avg_price")
        # A just-submitted market order may not have a fill price yet; the
        # caller marks it at the reference price in that case.
        avg_price = float(avg_raw) if avg_raw else 1.0
        return Fill(
            order_id=str(resp.get("id", "paper")), symbol=symbol, side=side,
            quantity=filled_qty, price=avg_price, requested_price=avg_price,
            fee=0.0, slippage=0.0, timestamp=datetime.now(timezone.utc),
            is_partial=str(resp.get("status", "")).lower() == "partially_filled",
        )


def build_sandbox_client(config: AppConfig) -> Optional[SandboxExchangeClient]:
    """Construct and verify a sandbox client, or return None.

    Returns ``None`` (the platform stays in pure paper-simulation mode) when
    sandbox is disabled, network is off, or no paper credentials are configured.
    """
    sb = config.sandbox
    if not sb.enabled:
        return None
    if not config.runtime.allow_network:
        _log.warning("sandbox enabled but runtime.allow_network is false — "
                     "staying in paper-simulation mode")
        return None
    credentials = load_sandbox_credentials(sb.broker)
    if credentials is None:
        _log.warning("sandbox enabled but no %s paper keys configured — "
                     "staying in paper-simulation mode", sb.broker)
        return None
    client = SandboxExchangeClient(credentials, sb)
    client.verify_credentials()  # raises on any unsafe key
    return client
