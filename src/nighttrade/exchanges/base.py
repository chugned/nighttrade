"""Exchange client interface — the read-only market-data contract.

Every client (mock or public) implements the same three reads. There is
deliberately **no** ``place_order`` method anywhere in this package: market
data flows in, orders never flow out.
"""

from __future__ import annotations

import abc
from typing import List

from ..models import OHLCV, OrderBookSnapshot, PriceTick


class MarketDataClient(abc.ABC):
    """Abstract read-only market-data source.

    Implementations must be side-effect free with respect to the outside
    world beyond optionally fetching public data.
    """

    #: Short lowercase identifier, e.g. "yfinance", "mock".
    name: str = "abstract"

    @abc.abstractmethod
    def get_ticker(self, symbol: str) -> PriceTick:
        """Return the latest price tick for ``symbol``."""

    @abc.abstractmethod
    def get_ohlcv(self, symbol: str, limit: int = 200) -> List[OHLCV]:
        """Return up to ``limit`` recent candles, oldest first."""

    @abc.abstractmethod
    def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBookSnapshot:
        """Return an L2 orderbook snapshot with up to ``depth`` levels/side."""

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<{type(self).__name__} name={self.name!r}>"


class ExchangeError(RuntimeError):
    """Raised when a market-data source fails or returns unusable data."""
