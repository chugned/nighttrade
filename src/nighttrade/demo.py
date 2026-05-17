"""The canonical demo scenario.

PLAN.md specifies a fixed scenario the platform must reproduce: a calm market
with AAPL near $234, a bullish (risk-on) macro backdrop, an oversold RSI from
a sharp pullback, and a sell-heavy intraday tape — resolving to a BUY at
moderate confidence.

This module builds that scenario deterministically as real market data
(candles + a synthetic top-of-book) so the *actual pipeline* — not a hard-coded
answer — produces the decision. It is a "buy-the-dip" setup: a long, calm
uptrend, a sharp multi-bar pullback (which drives RSI down and leaves a
sell-heavy tape), then a small bounce off the low.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from .models import OHLCV, OrderBookSnapshot
from .exchanges.mock import build_orderbook

# The canonical scenario constants from PLAN.md.
DEMO_SYMBOL = "AAPL"
DEMO_REFERENCE_PRICE = 234.00
DEMO_MACRO_SCENARIO = "risk_on"
# Effective-spread proxy (bps) for the synthetic demo top-of-book.
DEMO_SPREAD_BPS = 4.0

_BAR = timedelta(minutes=1)
_WICK = 0.00018  # tiny wicks keep ATR low so the volatility floor binds


def _build_closes() -> List[float]:
    """Construct the deterministic close-price path for the demo.

    Segments (per-bar simple returns):
      * uptrend  — long, calm advance
      * pullback — sharp enough to push RSI(14) into oversold territory
      * bounce   — a small recovery off the low
    """
    returns: List[float] = []
    returns += [0.00100] * 150          # calm uptrend
    returns += [-0.00160] * 24          # sharp pullback -> RSI oversold (~25)
    returns += [0.00060] * 5            # small bounce off the low

    closes = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1.0 + r))
    # Rescale the whole path so the final close is exactly the reference price.
    scale = DEMO_REFERENCE_PRICE / closes[-1]
    return [c * scale for c in closes]


def build_demo_candles() -> List[OHLCV]:
    """Return the deterministic OHLCV series for the demo scenario."""
    closes = _build_closes()
    n = len(closes)
    end_time = datetime(2026, 5, 15, 20, 0, tzinfo=timezone.utc)  # a Fri close
    start_time = end_time - _BAR * (n - 1)

    candles: List[OHLCV] = []
    prev_close = closes[0]
    for i, close in enumerate(closes):
        open_ = prev_close
        hi = max(open_, close) * (1.0 + _WICK)
        lo = min(open_, close) * (1.0 - _WICK)
        candles.append(OHLCV(
            symbol=DEMO_SYMBOL,
            timestamp=start_time + _BAR * i,
            open=round(open_, 2),
            high=round(hi, 2),
            low=round(lo, 2),
            close=round(close, 2),
            volume=1000.0,
        ))
        prev_close = close
    return candles


def build_demo_orderbook() -> OrderBookSnapshot:
    """Return the deterministic synthetic top-of-book for the demo scenario.

    Equities have no free Level-2 feed, so this synthetic book exists only to
    supply the effective-spread reading; the microstructure layer derives its
    directional view from the intraday tape (the demo candles).
    """
    return build_orderbook(
        symbol=DEMO_SYMBOL,
        mid_price=DEMO_REFERENCE_PRICE,
        exchange="demo",
        depth=10,
        spread_bps=DEMO_SPREAD_BPS,
        base_quantity=DEMO_REFERENCE_PRICE,
        imbalance=0.0,
        jitter=0.0,  # exact, smooth book for a reproducible scenario
    )
