"""Continuous, time-driven market feed for the observatory.

The observer runs forever, so it needs market data that *advances with the
wall clock* and is identical across restarts. ``LiveMockFeed`` provides that:
price is a pure deterministic function of ``(symbol, absolute-minute)`` — a
blend of sinusoidal cycles plus hash-based noise. Because it is a function of
absolute time, a prediction made at T can be honestly evaluated at T+H by
sampling the feed at T+H, and a crashed-and-restarted observer sees exactly
the same history.

Each watchlist symbol has a distinct *profile* so the dashboard shows a
realistic mix of regimes — calm megacaps, choppy names, volatile high-beta
stocks.

This feed is SIMULATED. No network, no real prices, no orders.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from ..exchanges.mock import build_orderbook
from ..models import OHLCV, OrderBookSnapshot, PriceTick

# Absolute time origin for the minute index.
_EPOCH = datetime(2025, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class SymbolProfile:
    """Deterministic market character for one simulated symbol."""

    base_price: float
    volume_24h_usd: float  # average daily dollar volume
    spread_bps: float
    book_base_qty: float
    book_depth: int
    trend_amp: float  # slow ~1-day swing amplitude (log units)
    cycle_amp: float  # medium ~2h cycle amplitude
    chop_amp: float  # fast ~15min wobble amplitude
    noise: float  # per-minute random amplitude


# A curated set of liquid US large caps + index ETFs, each with a deliberately
# distinct character (calm megacaps -> volatile high-beta names). book_base_qty
# is sized so each synthetic book clears the watchlist liquidity filter.
_PROFILES: Dict[str, SymbolProfile] = {
    "AAPL": SymbolProfile(234.0, 1.2e10, 2.0, 64.0, 20, 0.014, 0.006, 0.0018, 0.0008),
    "MSFT": SymbolProfile(470.0, 9.0e9, 2.0, 32.0, 20, 0.014, 0.006, 0.0018, 0.0008),
    "NVDA": SymbolProfile(175.0, 2.5e10, 3.0, 86.0, 20, 0.030, 0.014, 0.0060, 0.0020),
    "AMZN": SymbolProfile(220.0, 8.0e9, 3.0, 68.0, 20, 0.018, 0.008, 0.0030, 0.0012),
    "META": SymbolProfile(600.0, 9.0e9, 2.5, 25.0, 20, 0.020, 0.010, 0.0040, 0.0014),
    "GOOGL": SymbolProfile(195.0, 7.0e9, 2.5, 77.0, 20, 0.016, 0.007, 0.0028, 0.0011),
    "TSLA": SymbolProfile(340.0, 2.2e10, 4.0, 44.0, 20, 0.040, 0.024, 0.0150, 0.0042),
    "AVGO": SymbolProfile(230.0, 5.0e9, 3.0, 65.0, 20, 0.024, 0.012, 0.0055, 0.0019),
    "JPM": SymbolProfile(285.0, 3.0e9, 3.5, 53.0, 20, 0.014, 0.007, 0.0025, 0.0010),
    "V": SymbolProfile(310.0, 2.4e9, 3.0, 48.0, 20, 0.012, 0.006, 0.0020, 0.0009),
    "WMT": SymbolProfile(95.0, 2.6e9, 3.5, 158.0, 20, 0.010, 0.005, 0.0018, 0.0008),
    "JNJ": SymbolProfile(155.0, 1.8e9, 3.5, 97.0, 20, 0.010, 0.005, 0.0016, 0.0007),
    "XOM": SymbolProfile(115.0, 3.0e9, 4.0, 130.0, 20, 0.016, 0.009, 0.0035, 0.0013),
    "UNH": SymbolProfile(520.0, 2.5e9, 4.0, 29.0, 20, 0.018, 0.010, 0.0045, 0.0016),
    "PG": SymbolProfile(165.0, 1.6e9, 3.5, 91.0, 20, 0.009, 0.004, 0.0014, 0.0006),
    "MA": SymbolProfile(530.0, 2.2e9, 3.0, 28.0, 20, 0.013, 0.006, 0.0022, 0.0009),
    "HD": SymbolProfile(410.0, 2.0e9, 3.5, 37.0, 20, 0.014, 0.007, 0.0026, 0.0010),
    "COST": SymbolProfile(940.0, 2.3e9, 3.5, 16.0, 20, 0.012, 0.006, 0.0020, 0.0008),
    "ORCL": SymbolProfile(185.0, 2.4e9, 3.5, 81.0, 20, 0.020, 0.011, 0.0050, 0.0017),
    "MRK": SymbolProfile(100.0, 2.0e9, 4.0, 150.0, 20, 0.014, 0.007, 0.0028, 0.0011),
    "ABBV": SymbolProfile(195.0, 1.9e9, 4.0, 77.0, 20, 0.012, 0.006, 0.0022, 0.0009),
    "BAC": SymbolProfile(46.0, 2.6e9, 4.5, 326.0, 20, 0.018, 0.010, 0.0040, 0.0014),
    "KO": SymbolProfile(62.0, 1.7e9, 4.0, 242.0, 20, 0.008, 0.004, 0.0013, 0.0006),
    "PEP": SymbolProfile(145.0, 1.6e9, 4.0, 103.0, 20, 0.009, 0.004, 0.0015, 0.0006),
    "CVX": SymbolProfile(155.0, 2.2e9, 4.0, 97.0, 20, 0.016, 0.009, 0.0034, 0.0012),
    "ADBE": SymbolProfile(480.0, 2.4e9, 3.5, 31.0, 20, 0.024, 0.013, 0.0065, 0.0022),
    "CRM": SymbolProfile(320.0, 2.3e9, 3.5, 47.0, 20, 0.024, 0.013, 0.0062, 0.0021),
    "NFLX": SymbolProfile(920.0, 4.0e9, 3.5, 16.0, 20, 0.030, 0.017, 0.0090, 0.0028),
    "AMD": SymbolProfile(130.0, 6.0e9, 4.0, 115.0, 20, 0.036, 0.022, 0.0130, 0.0038),
    "DIS": SymbolProfile(110.0, 2.4e9, 4.0, 136.0, 20, 0.018, 0.010, 0.0042, 0.0015),
    "INTC": SymbolProfile(24.0, 3.5e9, 5.0, 625.0, 20, 0.030, 0.020, 0.0110, 0.0034),
    "QCOM": SymbolProfile(165.0, 2.2e9, 4.0, 91.0, 20, 0.022, 0.012, 0.0055, 0.0019),
    "TXN": SymbolProfile(200.0, 1.9e9, 4.0, 75.0, 20, 0.018, 0.009, 0.0038, 0.0014),
    "SPY": SymbolProfile(600.0, 3.0e10, 1.5, 25.0, 20, 0.010, 0.004, 0.0014, 0.0006),
    "QQQ": SymbolProfile(520.0, 1.8e10, 2.0, 29.0, 20, 0.014, 0.006, 0.0022, 0.0009),
}

_DEFAULT_PROFILE = SymbolProfile(150.0, 1.0e9, 5.0, 100.0, 20, 0.020, 0.012, 0.0060, 0.0020)


def profile_for(symbol: str) -> SymbolProfile:
    return _PROFILES.get(symbol.upper(), _DEFAULT_PROFILE)


def known_symbols() -> List[str]:
    return list(_PROFILES)


def _hash_unit(key: str) -> float:
    """Deterministic value in [-1, 1] from a string key."""
    digest = hashlib.md5(key.encode()).digest()
    raw = int.from_bytes(digest[:8], "big")
    return (raw / float(1 << 64)) * 2.0 - 1.0


def minute_index(when: datetime) -> int:
    """Whole minutes from the fixed epoch to ``when``."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return int((when - _EPOCH).total_seconds() // 60)


class LiveMockFeed:
    """A deterministic, restart-safe simulated market feed.

    It is a synthetic 24/7 market — a pure function of absolute time — so it
    deliberately does NOT respect market hours; the observer predicts and
    evaluates against it around the clock.
    """

    respects_market_hours = False

    def price_at(self, symbol: str, when: datetime) -> float:
        """The (deterministic) simulated price of ``symbol`` at ``when``."""
        return self._price_at_minute(symbol, minute_index(when))

    def _price_at_minute(self, symbol: str, m: int) -> float:
        p = profile_for(symbol)
        phase = _hash_unit(f"{symbol}:phase") * math.pi
        # Layered sinusoids: slow trend + medium cycle + fast chop.
        log_offset = (
            p.trend_amp * math.sin(2 * math.pi * m / 1440.0 + phase)
            + p.cycle_amp * math.sin(2 * math.pi * m / 137.0 + 2 * phase)
            + p.chop_amp * math.sin(2 * math.pi * m / 17.0 + 3 * phase)
            + p.noise * _hash_unit(f"{symbol}:{m}")
        )
        return p.base_price * math.exp(log_offset)

    def candles_at(self, symbol: str, as_of: datetime, n_bars: int = 300) -> List[OHLCV]:
        """The ``n_bars`` 1-minute candles ending at ``as_of``."""
        end_m = minute_index(as_of)
        candles: List[OHLCV] = []
        for m in range(end_m - n_bars + 1, end_m + 1):
            close = self._price_at_minute(symbol, m)
            open_ = self._price_at_minute(symbol, m - 1)
            wick = abs(_hash_unit(f"{symbol}:wick:{m}")) * 0.0008
            hi = max(open_, close) * (1.0 + wick)
            lo = min(open_, close) * (1.0 - wick)
            vol = 800.0 + abs(_hash_unit(f"{symbol}:vol:{m}")) * 600.0
            ts = _EPOCH + timedelta(minutes=m)
            candles.append(
                OHLCV(
                    symbol=symbol,
                    timestamp=ts,
                    open=round(open_, 4),
                    high=round(hi, 4),
                    low=round(lo, 4),
                    close=round(close, 4),
                    volume=round(vol, 4),
                )
            )
        return candles

    def orderbook_at(self, symbol: str, as_of: datetime) -> OrderBookSnapshot:
        """A synthetic top-of-book for ``symbol`` at ``as_of``."""
        p = profile_for(symbol)
        m = minute_index(as_of)
        price = self._price_at_minute(symbol, m)
        # Imbalance drifts deterministically with time.
        imbalance = 0.35 * _hash_unit(f"{symbol}:imb:{m // 3}")
        return build_orderbook(
            symbol=symbol,
            mid_price=price,
            exchange="observatory",
            depth=p.book_depth,
            spread_bps=p.spread_bps,
            base_quantity=p.book_base_qty,
            imbalance=imbalance,
            timestamp=_EPOCH + timedelta(minutes=m),
            jitter=0.0,
        )

    def tick_at(self, symbol: str, as_of: datetime) -> PriceTick:
        p = profile_for(symbol)
        m = minute_index(as_of)
        return PriceTick(
            symbol=symbol,
            exchange="observatory",
            price=self._price_at_minute(symbol, m),
            timestamp=_EPOCH + timedelta(minutes=m),
            volume_24h=p.volume_24h_usd,
        )
