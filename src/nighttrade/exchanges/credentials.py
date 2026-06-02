"""Broker credential loading and the account-safety policy.

This module is the gatekeeper for broker API keys. Two rules are absolute and
cannot be configured away:

1. **Paper accounts only, ever.** Credentials are loaded exclusively from
   ``*_PAPER_*`` env variables and are only ever used against a broker's
   *paper-trading* endpoint. A key that resolves to a LIVE brokerage account
   is rejected outright — the platform must never be able to trade real money.
2. **No funding / transfer path.** This package contains no code that deposits,
   withdraws or transfers money. A key whose verified scopes include funding
   access is rejected on connect.

By default keys must also be *read-only* (no order-placement scope) — pure
monitoring. Placing paper orders requires the operator to explicitly lower
``sandbox.require_read_only_keys``; even then, live accounts stay banned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from ..config.schema import SandboxConfig


class SecurityError(RuntimeError):
    """Base class for credential / account security failures."""


class MissingCredentialsError(SecurityError):
    """Raised when expected paper-account credentials are not configured."""


class LiveAccountError(SecurityError):
    """Raised when an API key resolves to a LIVE brokerage account."""


class TradePermissionError(SecurityError):
    """Raised when a key has order scope but read-only keys are required."""


# Environment-variable names per broker. Only PAPER keys are ever read.
_ENV_KEYS = {
    "alpaca": ("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_API_SECRET"),
}


@dataclass(frozen=True)
class ApiKeyPermissions:
    """The verified capabilities of a broker API key.

    Populated by querying the broker after connecting; until then a key's
    scopes are unknown and it must not be trusted for execution.
    """

    can_read: bool = True
    can_trade: bool = False
    can_fund: bool = False
    is_paper: bool = True

    @property
    def is_read_only(self) -> bool:
        return not (self.can_trade or self.can_fund)

    def describe(self) -> str:
        scopes = [
            name
            for name, on in (
                ("read", self.can_read),
                ("trade", self.can_trade),
                ("fund", self.can_fund),
            )
            if on
        ]
        account = "paper" if self.is_paper else "LIVE"
        return f"{account}: " + (", ".join(scopes) or "none")


@dataclass
class ApiCredentials:
    """A loaded API key/secret pair plus (once verified) its permissions."""

    broker: str
    api_key: str
    api_secret: str
    permissions: Optional[ApiKeyPermissions] = field(default=None)

    @property
    def is_verified(self) -> bool:
        return self.permissions is not None

    def masked_key(self) -> str:
        """The key with all but the last 4 chars masked — safe to log."""
        if len(self.api_key) <= 4:
            return "****"
        return "*" * (len(self.api_key) - 4) + self.api_key[-4:]


def load_sandbox_credentials(broker: str) -> Optional[ApiCredentials]:
    """Load paper-account credentials for ``broker`` from the environment.

    Returns ``None`` when no keys are configured (the platform then stays in
    pure paper-simulation mode). Raises only on a partially-configured pair.
    """
    broker = broker.lower().strip()
    if broker not in _ENV_KEYS:
        raise SecurityError(f"unknown sandbox broker: {broker}")

    key_var, secret_var = _ENV_KEYS[broker]
    api_key = os.environ.get(key_var, "").strip()
    api_secret = os.environ.get(secret_var, "").strip()

    if not api_key and not api_secret:
        return None
    if not api_key or not api_secret:
        raise MissingCredentialsError(f"{broker}: both {key_var} and {secret_var} must be set")
    return ApiCredentials(broker=broker, api_key=api_key, api_secret=api_secret)


def enforce_key_safety(
    permissions: ApiKeyPermissions,
    config: SandboxConfig,
) -> None:
    """Apply the account safety policy. Raises on any violation.

    * funding/transfer access -> always rejected
    * a live brokerage account -> always rejected
    * order scope when read-only keys are required -> rejected
    """
    if permissions.can_fund:
        raise LiveAccountError(
            "API key has FUNDING/TRANSFER access — rejected. This platform "
            "must never be able to move money. Use a paper key without it."
        )
    if not permissions.is_paper:
        raise LiveAccountError(
            "API key resolves to a LIVE brokerage account — rejected. Only "
            "broker paper-trading accounts are accepted; there is no live "
            "execution path."
        )
    if config.reject_live_keys is False:  # defense-in-depth
        raise SecurityError("reject_live_keys must be true")
    if config.require_read_only_keys and permissions.can_trade:
        raise TradePermissionError(
            "API key has ORDER scope but sandbox.require_read_only_keys is "
            "true. Either supply a read-only key, or explicitly set "
            "require_read_only_keys: false to allow PAPER order placement."
        )
