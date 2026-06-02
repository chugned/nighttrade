"""Per-stock factor extraction for the cross-sectional ranker.

A *factor* is one number per stock describing one dimension of attractiveness.
Factors are computed in raw, comparable units here; the ranker is what makes
them *relative* by z-scoring each factor across the whole universe.

The five factors:

* **momentum**  — trailing return (winners keep winning)
* **trend**     — signed R² of log-price (smooth trends beat choppy ones)
* **reversion** — RSI distance from 50 (oversold = positive)
* **low_vol**   — negative realized volatility (the low-volatility anomaly)
* **ml**        — the model's intelligent score, if a model is loaded
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from ..config.schema import CrossSectionConfig
from ..indicators import core
from ..models import OHLCV


@dataclass(frozen=True)
class StockFactors:
    """The raw factor values for one stock (pre-z-score)."""

    symbol: str
    price: float
    dollar_volume: float
    momentum: float
    trend: float
    reversion: float
    low_vol: float
    ml: Optional[float] = None


def _finite(value: float, default: float = 0.0) -> float:
    return value if math.isfinite(value) else default


def compute_factors(
    symbol: str,
    candles: List[OHLCV],
    config: CrossSectionConfig,
    ml_score: Optional[float] = None,
) -> Optional[StockFactors]:
    """Compute the raw factor vector for ``symbol``.

    Returns ``None`` when there is not enough history to compute the factors
    — that stock simply does not enter the cross-section this cycle.
    """
    need = (
        max(
            config.momentum_lookback,
            config.trend_window,
            config.volatility_window,
            config.reversion_rsi_period,
        )
        + 2
    )
    if len(candles) < need:
        return None

    closes = np.array([c.close for c in candles], dtype=float)
    volumes = np.array([c.volume for c in candles], dtype=float)
    price = float(closes[-1])
    if price <= 0:
        return None

    # Momentum — trailing simple return over the lookback.
    past = closes[-1 - config.momentum_lookback]
    momentum = _finite(price / past - 1.0) if past > 0 else 0.0

    # Trend quality — slope-signed R² of a linear fit to log price.
    window = closes[-config.trend_window :]
    log_w = np.log(window)
    x = np.arange(len(log_w), dtype=float)
    slope = float(np.polyfit(x, log_w, 1)[0])
    if log_w.std() > 0:
        r = float(np.corrcoef(x, log_w)[0, 1])
        trend = math.copysign(r * r, slope)
    else:
        trend = 0.0

    # Mean-reversion — RSI distance from the neutral 50 line (oversold => +).
    rsi_series = core.rsi(pd.Series(closes), config.reversion_rsi_period).dropna()
    rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0
    reversion = _finite((50.0 - rsi) / 50.0)

    # Low volatility — negative realized vol of recent log returns.
    log_returns = np.diff(np.log(closes))[-config.volatility_window :]
    realized_vol = float(np.std(log_returns)) if log_returns.size else 0.0
    low_vol = -_finite(realized_vol)

    # Dollar volume over the provided window — a liquidity gate, not a factor.
    dollar_volume = float(np.sum(volumes)) * price

    return StockFactors(
        symbol=symbol,
        price=price,
        dollar_volume=dollar_volume,
        momentum=momentum,
        trend=trend,
        reversion=reversion,
        low_vol=low_vol,
        ml=None if ml_score is None else _finite(ml_score),
    )
