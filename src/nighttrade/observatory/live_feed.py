"""Live stock-data feed for the observatory.

``YFinanceFeed`` is a drop-in replacement for :class:`LiveMockFeed` backed by
real Yahoo Finance intraday data. It implements the same four methods the
observer calls — ``candles_at``, ``orderbook_at``, ``tick_at``, ``price_at`` —
so the observatory, dashboard and learning session work unchanged.

It batches one download for the whole universe per refresh window and serves
every symbol from that cache, so a 100–500 name universe costs one network
round-trip per cycle rather than one per symbol.

This feed is READ-ONLY market data. It never places an order.
"""

from __future__ import annotations

import time as _time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..exchanges.mock import build_orderbook
from ..models import OHLCV, OrderBookSnapshot, PriceTick
from ..runtime import get_logger

_log = get_logger("observatory.live_feed")


def _synthetic_book(symbol: str, price: float, when: datetime) -> OrderBookSnapshot:
    """A synthetic top-of-book (equities have no free L2 feed)."""
    base_qty = max(200_000.0 / (price * 6.0), 1.0)
    return build_orderbook(symbol=symbol, mid_price=price, exchange="yfinance",
                           depth=20, spread_bps=5.0, base_quantity=base_qty,
                           imbalance=0.0, timestamp=when, jitter=0.0)


class YFinanceFeed:
    """A live, real-data market feed backed by Yahoo Finance (``yfinance``)."""

    #: Real data only exists during/after a trading session — the observer
    #: gates prediction-making on the market clock when the feed is live.
    respects_market_hours = True

    def __init__(self, symbols: List[str], refresh_seconds: float = 120.0,
                 period: str = "2d") -> None:
        if not symbols:
            raise ValueError("YFinanceFeed needs at least one symbol")
        self._symbols = [s.upper() for s in symbols]
        self._refresh_seconds = refresh_seconds
        self._period = period
        self._cache: Dict[str, List[OHLCV]] = {}
        self._fetched_monotonic: Optional[float] = None
        # US stocks trade in USD; the platform is euro-denominated, so every
        # price is converted to EUR by this factor (EUR per 1 USD).
        self._eur_per_usd: float = 1.0

    # -- universe ------------------------------------------------------------

    def available_symbols(self) -> List[str]:
        """Symbols that returned usable data on the last refresh."""
        self._ensure_fresh()
        return sorted(self._cache)

    def refresh_now(self) -> None:
        """Force an immediate batch download."""
        self._refresh()

    # -- internal ------------------------------------------------------------

    def _ensure_fresh(self) -> None:
        now = _time.monotonic()
        if (self._fetched_monotonic is not None and self._cache
                and now - self._fetched_monotonic < self._refresh_seconds):
            return
        self._refresh()

    def _refresh(self) -> None:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "yfinance not installed — run: pip install nighttrade[online]"
            ) from exc

        self._eur_per_usd = self._fetch_eur_per_usd()
        _log.info("fetching live intraday data for %d symbols (EUR/USD x%.4f)",
                  len(self._symbols), self._eur_per_usd)
        data = yf.download(self._symbols, period=self._period, interval="1m",
                           group_by="ticker", threads=True, progress=False,
                           auto_adjust=False)
        cache: Dict[str, List[OHLCV]] = {}
        for sym in self._symbols:
            try:
                frame = data[sym] if len(self._symbols) > 1 else data
            except (KeyError, TypeError):
                continue
            candles = self._frame_to_candles(sym, frame)
            if len(candles) >= 30:  # need enough tape for the analysis layers
                cache[sym] = candles
        if cache:
            self._cache = cache
            self._fetched_monotonic = _time.monotonic()
            _log.info("live feed cached %d/%d symbols",
                      len(cache), len(self._symbols))
        else:
            _log.warning("live feed refresh returned no usable data")

    @staticmethod
    def _fetch_eur_per_usd() -> float:
        """EUR per 1 USD from Yahoo. Falls back to 1.0 (no conversion) on failure."""
        try:
            import yfinance as yf

            closes = yf.Ticker("EURUSD=X").history(period="5d")["Close"].dropna()
            if not closes.empty:
                eurusd = float(closes.iloc[-1])
                if eurusd > 0:
                    return 1.0 / eurusd
        except Exception as exc:  # noqa: BLE001
            _log.warning("EUR/USD rate unavailable (%s) — prices stay in USD", exc)
        return 1.0

    def _frame_to_candles(self, symbol: str, frame) -> List[OHLCV]:
        """Convert a yfinance frame to OHLCV, with USD prices scaled to EUR."""
        candles: List[OHLCV] = []
        if frame is None or getattr(frame, "empty", True):
            return candles
        fx = self._eur_per_usd
        for ts, row in frame.dropna().iterrows():
            o, h, l, c = (row.get("Open"), row.get("High"),
                          row.get("Low"), row.get("Close"))
            if None in (o, h, l, c) or min(o, h, l, c) <= 0:
                continue
            try:
                candles.append(OHLCV(
                    symbol=symbol, timestamp=ts.to_pydatetime(),
                    open=float(o) * fx, high=float(h) * fx,
                    low=float(l) * fx, close=float(c) * fx,
                    volume=float(row.get("Volume") or 0.0)))
            except (ValueError, TypeError):
                continue
        return candles

    def _candles(self, symbol: str) -> List[OHLCV]:
        self._ensure_fresh()
        sym = symbol.upper()
        if sym not in self._cache:
            # A symbol outside the batch universe (e.g. a stale prediction from
            # a previous run) — fetch it on demand so it can't crash a cycle.
            self._cache[sym] = self._fetch_one(sym)
        return self._cache.get(sym, [])

    def _fetch_one(self, symbol: str) -> List[OHLCV]:
        """Best-effort single-symbol fetch for a symbol outside the universe."""
        try:
            import yfinance as yf

            frame = yf.Ticker(symbol).history(period=self._period,
                                              interval="1m", auto_adjust=False)
        except Exception as exc:  # noqa: BLE001
            _log.warning("live feed could not fetch %s: %s", symbol, exc)
            return []
        return self._frame_to_candles(symbol, frame)

    # -- feed interface ------------------------------------------------------

    def candles_at(self, symbol: str, as_of: datetime,
                   n_bars: int = 300) -> List[OHLCV]:
        """Real 1-minute candles for ``symbol`` up to ``as_of``."""
        candles = [c for c in self._candles(symbol) if c.timestamp <= as_of]
        if not candles:
            candles = self._candles(symbol)  # as_of before the window — use all
        return candles[-n_bars:]

    def price_at(self, symbol: str, when: datetime) -> float:
        """The close of the 1-minute bar nearest ``when``."""
        candles = self._candles(symbol)
        if not candles:
            raise ValueError(f"no live data for {symbol}")
        at_or_before = [c for c in candles if c.timestamp <= when]
        chosen = at_or_before[-1] if at_or_before else candles[0]
        return chosen.close

    def orderbook_at(self, symbol: str, as_of: datetime) -> OrderBookSnapshot:
        candles = self.candles_at(symbol, as_of, n_bars=1)
        price = candles[-1].close if candles else self.price_at(symbol, as_of)
        return _synthetic_book(symbol, price, candles[-1].timestamp
                               if candles else as_of)

    def tick_at(self, symbol: str, as_of: datetime) -> PriceTick:
        candles = self.candles_at(symbol, as_of, n_bars=390)
        last = candles[-1]
        # Daily dollar volume = today's traded shares x price.
        dollar_volume = sum(c.volume for c in candles) * last.close
        return PriceTick(symbol=symbol, exchange="yfinance", price=last.close,
                         timestamp=last.timestamp, volume_24h=dollar_volume)
