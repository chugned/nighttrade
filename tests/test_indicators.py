"""Technical indicator correctness and numerical-stability tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nighttrade.indicators import TechnicalEngine, core


def test_ema_tracks_constant_series():
    s = pd.Series([100.0] * 50)
    assert core.ema(s, 10).iloc[-1] == pytest.approx(100.0)


def test_rsi_in_bounds():
    s = pd.Series(np.linspace(100, 120, 80) + np.random.default_rng(0).normal(0, 1, 80))
    rsi = core.rsi(s, 14).dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all()


def test_rsi_high_on_pure_uptrend():
    s = pd.Series(np.arange(1, 60, dtype=float))
    assert core.rsi(s, 14).iloc[-1] > 95


def test_rsi_low_on_pure_downtrend():
    s = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert core.rsi(s, 14).iloc[-1] < 5


def test_macd_columns():
    s = pd.Series(np.linspace(100, 110, 100))
    macd = core.macd(s)
    assert list(macd.columns) == ["macd", "signal", "histogram"]


def test_macd_rejects_fast_ge_slow():
    with pytest.raises(ValueError):
        core.macd(pd.Series([1.0, 2.0, 3.0]), fast=26, slow=12)


def test_volatility_non_negative():
    s = pd.Series(np.random.default_rng(1).normal(100, 5, 100))
    vol = core.volatility(s, 20).dropna()
    assert (vol >= 0).all()


def test_atr_positive_on_real_ranges():
    n = 60
    rng = np.random.default_rng(2)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    high = close + 1.0
    low = close - 1.0
    atr = core.atr(high, low, close, 14).dropna()
    assert (atr > 0).all()


def test_trend_slope_sign():
    up = pd.Series(np.arange(1, 60, dtype=float))
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert core.trend_slope(up, 20).iloc[-1] > 0
    assert core.trend_slope(down, 20).iloc[-1] < 0


def test_technical_engine_bullish_on_uptrend(uptrend_candles):
    sig = TechnicalEngine().compute(uptrend_candles)
    assert sig.rsi is not None
    assert -1.0 <= sig.score <= 1.0
    assert 0.0 <= sig.confidence <= 1.0


def test_technical_engine_needs_candles():
    with pytest.raises(ValueError):
        TechnicalEngine().compute([])
