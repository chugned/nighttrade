"""Exchange infrastructure — read-only stock market data, consensus, failover.

There is no order-entry code anywhere in this package. Data comes in; orders
never go out.
"""

from __future__ import annotations

from .base import ExchangeError, MarketDataClient
from .consensus import compute_consensus
from .credentials import (
    ApiCredentials,
    ApiKeyPermissions,
    LiveAccountError,
    SecurityError,
    load_sandbox_credentials,
)
from .mock import MockExchange, build_orderbook, generate_random_walk
from .provider import MarketDataProvider, build_provider
from .public import StooqClient, YFinanceClient, build_public_client
from .sandbox import (
    SandboxExchangeClient,
    SandboxSafetyError,
    build_sandbox_client,
)

__all__ = [
    "MarketDataClient",
    "ExchangeError",
    "MockExchange",
    "generate_random_walk",
    "build_orderbook",
    "compute_consensus",
    "MarketDataProvider",
    "build_provider",
    "YFinanceClient",
    "StooqClient",
    "build_public_client",
    # credentials & sandbox
    "ApiCredentials",
    "ApiKeyPermissions",
    "SecurityError",
    "LiveAccountError",
    "load_sandbox_credentials",
    "SandboxExchangeClient",
    "SandboxSafetyError",
    "build_sandbox_client",
]
