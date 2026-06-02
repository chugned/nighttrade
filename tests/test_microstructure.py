"""Stock (tape-based) microstructure analysis tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nighttrade.exchanges.mock import build_orderbook
from nighttrade.microstructure import (
    MicrostructureEngine,
    detect_halt,
    order_flow_imbalance,
    relative_volume,
    session_vwap,
)
from nighttrade.models import OHLCV, Bias

_T0 = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)


def _candle(i: int, low: float, high: float, close: float, volume: float = 1000.0) -> OHLCV:
    return OHLCV(
        symbol="AAPL",
        timestamp=_T0 + timedelta(minutes=i),
        open=round((low + high) / 2, 2),
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _series(close_pos: float, n: int = 60, volume: float = 1000.0):
    """A candle series where every bar closes at ``close_pos`` of its range."""
    out = []
    base = 100.0
    for i in range(n):
        low, high = base - 1.0, base + 1.0
        close = low + close_pos * (high - low)
        out.append(_candle(i, low, high, round(close, 2), volume))
        base = close
    return out


def test_order_flow_imbalance_buy_heavy():
    candles = _series(close_pos=0.95)  # closes near the high
    assert order_flow_imbalance(candles, 20) > 0.5


def test_order_flow_imbalance_sell_heavy():
    candles = _series(close_pos=0.05)  # closes near the low
    assert order_flow_imbalance(candles, 20) < -0.5


def test_session_vwap_inside_range():
    candles = _series(close_pos=0.5)
    vwap = session_vwap(candles, 60)
    lows = [c.low for c in candles]
    highs = [c.high for c in candles]
    assert min(lows) <= vwap <= max(highs)


def test_relative_volume_detects_spike():
    candles = _series(close_pos=0.5, n=40)
    spike = candles[:-1] + [_candle(40, 99.0, 101.0, 100.0, volume=5000.0)]
    assert relative_volume(spike, 30) > 2.0


def test_detect_halt_on_frozen_tape():
    frozen = [
        OHLCV(
            symbol="AAPL",
            timestamp=_T0 + timedelta(minutes=i),
            open=50.0,
            high=50.0,
            low=50.0,
            close=50.0,
            volume=0.0,
        )
        for i in range(5)
    ]
    assert detect_halt(frozen) is True
    assert detect_halt(_series(close_pos=0.5)) is False


def test_microstructure_bullish_on_buy_heavy_tape():
    book = build_orderbook("AAPL", 100.0, jitter=0.0)
    sig = MicrostructureEngine().compute(book, _series(close_pos=0.92))
    assert sig.bias is Bias.BULLISH
    assert sig.score > 0


def test_microstructure_bearish_on_sell_heavy_tape():
    book = build_orderbook("AAPL", 100.0, jitter=0.0)
    sig = MicrostructureEngine().compute(book, _series(close_pos=0.08))
    assert sig.bias is Bias.BEARISH
    assert sig.score < 0


def test_microstructure_thin_liquidity_on_low_rvol():
    """A tape whose latest bar has near-zero volume flags thin liquidity."""
    candles = _series(close_pos=0.5, n=40)
    quiet = candles[:-1] + [_candle(40, 99.0, 101.0, 100.0, volume=1.0)]
    sig = MicrostructureEngine().compute(build_orderbook("AAPL", 100.0, jitter=0.0), quiet)
    assert sig.thin_liquidity is True


def test_microstructure_no_tape_is_neutral():
    sig = MicrostructureEngine().compute(build_orderbook("AAPL", 100.0))
    assert sig.bias is Bias.NEUTRAL
    assert sig.confidence < 0.5


def test_microstructure_score_in_bounds(flat_candles):
    book = build_orderbook("AAPL", 230.0, jitter=0.0)
    sig = MicrostructureEngine().compute(book, flat_candles)
    assert -1.0 <= sig.score <= 1.0
    assert 0.0 <= sig.confidence <= 1.0
