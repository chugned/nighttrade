"""Public, read-only stock market-data clients.

These hit ONLY public market-data endpoints — no auth, no keys, no orders.
They are disabled unless ``runtime.allow_network`` is true, and every call is
wrapped so a degraded API downgrades gracefully to an ``ExchangeError`` rather
than crashing the pipeline.

Two free, key-less providers are supported:

* **yfinance** — Yahoo Finance. Intraday (1-minute) and daily candles plus a
  last-trade quote. The primary source.
* **stooq** — a free CSV endpoint. Last quote + daily candles. A genuine
  second source so the consensus engine has real cross-source dispersion.

Neither provider exposes a public Level-2 order book for equities, so
:meth:`get_orderbook` returns a *synthetic* top-of-book built around the last
trade with an effective-spread proxy — used only for the spread reading, never
as if it were real depth.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..models import OHLCV, ExchangeStatus, OrderBookSnapshot, PriceTick
from ..runtime import get_logger
from .base import ExchangeError, MarketDataClient
from .mock import build_orderbook

_log = get_logger("exchanges.public")

# Effective-spread proxy (bps) for the synthetic equity top-of-book. Liquid US
# large caps trade a fraction of a cent wide; this is a deliberately
# conservative placeholder, not a measured quote.
_SYNTHETIC_SPREAD_BPS = 5.0


def _synthetic_book(symbol: str, exchange: str, price: float) -> OrderBookSnapshot:
    """A synthetic top-of-book around ``price`` (equities have no free L2)."""
    return build_orderbook(
        symbol=symbol,
        mid_price=price,
        exchange=exchange,
        depth=10,
        spread_bps=_SYNTHETIC_SPREAD_BPS,
        base_quantity=max(price, 1.0),
        imbalance=0.0,
        timestamp=datetime.now(timezone.utc),
        jitter=0.0,
    )


class YFinanceClient(MarketDataClient):
    """Yahoo Finance market data via the ``yfinance`` package."""

    name = "yfinance"

    def __init__(
        self, timeout: float = 5.0, max_retries: int = 3, allow_network: bool = False
    ) -> None:
        self._timeout = timeout
        self._max_retries = max(1, max_retries)
        self._allow_network = allow_network

    def _yf(self):
        """Import yfinance lazily so it is only required for online runs."""
        if not self._allow_network:
            raise ExchangeError("yfinance: network disabled (set runtime.allow_network=true)")
        try:
            import yfinance  # noqa: WPS433 - intentional lazy import
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ExchangeError(
                "yfinance not installed — run: pip install nighttrade[online]"
            ) from exc
        return yfinance

    def get_ticker(self, symbol: str) -> PriceTick:
        yf = self._yf()
        try:
            fi = yf.Ticker(symbol).fast_info
            price = fi.get("last_price") or fi.get("previous_close")
            shares = fi.get("last_volume") or fi.get("ten_day_average_volume") or 0.0
        except Exception as exc:  # noqa: BLE001 - any yfinance failure
            raise ExchangeError(f"yfinance: ticker failed for {symbol}: {exc}") from exc
        if not price or price <= 0:
            raise ExchangeError(f"yfinance: no price for {symbol}")
        return PriceTick(
            symbol=symbol,
            exchange=self.name,
            price=float(price),
            timestamp=datetime.now(timezone.utc),
            volume_24h=float(shares) * float(price),  # dollar volume
            status=ExchangeStatus.OK,
        )

    def get_ohlcv(self, symbol: str, limit: int = 200) -> List[OHLCV]:
        yf = self._yf()
        try:
            frame = yf.Ticker(symbol).history(period="5d", interval="1m", auto_adjust=False)
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"yfinance: ohlcv failed for {symbol}: {exc}") from exc
        if frame is None or frame.empty:
            raise ExchangeError(f"yfinance: no candles for {symbol}")
        rows = frame.tail(limit)
        candles: List[OHLCV] = []
        for ts, row in rows.iterrows():
            candles.append(
                OHLCV(
                    symbol=symbol,
                    timestamp=ts.to_pydatetime(),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"] or 0.0),
                )
            )
        return candles

    def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBookSnapshot:
        return _synthetic_book(symbol, self.name, self.get_ticker(symbol).price)


class _HttpClient(MarketDataClient):
    """Shared HTTP plumbing for CSV/REST providers: timeouts, retries, gating."""

    base_url: str = ""

    def __init__(
        self, timeout: float = 5.0, max_retries: int = 3, allow_network: bool = False
    ) -> None:
        self._timeout = timeout
        self._max_retries = max(1, max_retries)
        self._allow_network = allow_network

    def _get_text(self, path: str, params: Dict[str, Any]) -> str:
        if not self._allow_network:
            raise ExchangeError(f"{self.name}: network disabled (set runtime.allow_network=true)")

        @retry(
            retry=retry_if_exception_type((httpx.HTTPError,)),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=0.5, max=4.0),
            reraise=True,
        )
        def _do() -> str:
            url = self.base_url + path
            with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                return resp.text

        try:
            return _do()
        except httpx.HTTPError as exc:
            raise ExchangeError(f"{self.name}: request failed: {exc}") from exc


class StooqClient(_HttpClient):
    """Stooq public CSV market data (last quote + daily candles)."""

    name = "stooq"
    base_url = "https://stooq.com"

    @staticmethod
    def _stooq_symbol(symbol: str) -> str:
        """Stooq tickers are lowercase and suffixed with the market (.us)."""
        sym = symbol.lower()
        return sym if "." in sym else f"{sym}.us"

    def get_ticker(self, symbol: str) -> PriceTick:
        text = self._get_text(
            "/q/l/", {"s": self._stooq_symbol(symbol), "f": "sd2t2ohlcv", "h": "", "e": "csv"}
        )
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            raise ExchangeError(f"stooq: empty response for {symbol}")
        row = rows[0]
        close = row.get("Close", "N/D")
        if close in ("", "N/D", None):
            raise ExchangeError(f"stooq: no price for {symbol}")
        price = float(close)
        volume = float(row.get("Volume") or 0.0)
        return PriceTick(
            symbol=symbol,
            exchange=self.name,
            price=price,
            timestamp=datetime.now(timezone.utc),
            volume_24h=volume * price,
            status=ExchangeStatus.OK,
        )

    def get_ohlcv(self, symbol: str, limit: int = 200) -> List[OHLCV]:
        text = self._get_text("/q/d/l/", {"s": self._stooq_symbol(symbol), "i": "d"})
        rows = list(csv.DictReader(io.StringIO(text)))
        candles: List[OHLCV] = []
        for row in rows[-limit:]:
            try:
                candles.append(
                    OHLCV(
                        symbol=symbol,
                        timestamp=row["Date"],
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=float(row.get("Volume") or 0.0),
                    )
                )
            except (KeyError, ValueError):
                continue
        if not candles:
            raise ExchangeError(f"stooq: no daily candles for {symbol}")
        return candles

    def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBookSnapshot:
        raise ExchangeError("stooq: order book not supported (no public L2 API)")


_REGISTRY = {
    "yfinance": YFinanceClient,
    "stooq": StooqClient,
}


def build_public_client(
    name: str, timeout: float = 5.0, max_retries: int = 3, allow_network: bool = False
) -> MarketDataClient:
    """Factory: construct a public stock-data client by name."""
    cls = _REGISTRY.get(name.lower())
    if cls is None:
        raise ExchangeError(f"unknown public data source: {name}")
    return cls(timeout=timeout, max_retries=max_retries, allow_network=allow_network)
