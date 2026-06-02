"""Feature engineering pipeline.

THE CRITICAL INVARIANT
----------------------
There is exactly **one** feature computation function — :func:`compute_features`.
The offline training path and the online inference path both call it. They
cannot drift apart, because there is nothing to drift: training computes
features over the whole history; inference computes features over the history
available so far and takes the last row. Same code, same columns, same order.

Every feature at bar ``t`` is causal — a function of bars ``<= t`` only — so a
feature value never changes when future bars later arrive. The leakage tests
assert exactly that.
"""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from ..config.schema import FeatureConfig, IndicatorConfig
from ..indicators import core
from ..indicators.frame import ohlcv_to_frame
from ..models import OHLCV


def compute_features(
    frame: pd.DataFrame,
    feature_config: FeatureConfig | None = None,
    indicator_config: IndicatorConfig | None = None,
) -> pd.DataFrame:
    """Compute the full feature matrix from an OHLCV DataFrame.

    Args:
        frame: time-indexed OHLCV DataFrame (see ``ohlcv_to_frame``).

    Returns:
        A DataFrame of features aligned to ``frame.index``. Warmup rows where
        an indicator is undefined contain NaN; callers decide whether to drop
        them (training) or just read the final row (inference).
    """
    fcfg = feature_config or FeatureConfig()
    icfg = indicator_config or IndicatorConfig()

    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    volume = frame["volume"]
    feats: dict[str, pd.Series] = {}

    # --- Multi-horizon returns ---
    for w in fcfg.return_windows:
        feats[f"ret_{w}"] = core.returns(close, w)
    feats["logret_1"] = core.log_returns(close, 1)

    # --- Distributional shape of recent 1-bar returns ---
    ret1 = core.returns(close, 1)
    feats["roll_std"] = core.rolling_std(ret1, fcfg.rolling_std_window)
    feats["roll_skew"] = core.rolling_skew(ret1, fcfg.skew_kurtosis_window)
    feats["roll_kurt"] = core.rolling_kurtosis(ret1, fcfg.skew_kurtosis_window)

    # --- Trend / momentum / oscillators ---
    feats["rsi"] = core.rsi(close, icfg.rsi_period)
    macd_df = core.macd(close, icfg.ema_fast, icfg.ema_slow, icfg.macd_signal)
    feats["macd"] = macd_df["macd"]
    feats["macd_signal"] = macd_df["signal"]
    feats["macd_hist"] = macd_df["histogram"]

    ema_fast = core.ema(close, icfg.ema_fast)
    ema_slow = core.ema(close, icfg.ema_slow)
    feats["ema_gap"] = (ema_fast - ema_slow) / ema_slow.replace(0.0, np.nan)
    feats["momentum"] = core.momentum(close, icfg.momentum_window)
    feats["trend_slope"] = core.trend_slope(close, icfg.trend_window)
    feats["volatility"] = core.volatility(close, icfg.volatility_window)

    # --- Candle geometry ---
    rng = high - low
    feats["range_pct"] = rng / close.replace(0.0, np.nan)
    body = (close - frame["open"]).abs()
    feats["body_to_range"] = body / rng.replace(0.0, np.nan)
    feats["close_to_high"] = (high - close) / rng.replace(0.0, np.nan)

    # --- Volume ---
    vol_mean = volume.rolling(fcfg.rolling_std_window, min_periods=2).mean()
    vol_std = volume.rolling(fcfg.rolling_std_window, min_periods=2).std()
    feats["volume_z"] = (volume - vol_mean) / vol_std.replace(0.0, np.nan)
    feats["volume_chg"] = volume.pct_change()

    out = pd.DataFrame(feats, index=frame.index)
    # Replace +/-inf (from rare divide-by-zero) with NaN so they are handled
    # like any other warmup gap rather than poisoning the model.
    return out.replace([np.inf, -np.inf], np.nan)


def feature_columns(
    feature_config: FeatureConfig | None = None,
) -> List[str]:
    """The canonical ordered feature-column list (used to lock train/infer)."""
    fcfg = feature_config or FeatureConfig()
    cols = [f"ret_{w}" for w in fcfg.return_windows]
    cols += [
        "logret_1",
        "roll_std",
        "roll_skew",
        "roll_kurt",
        "rsi",
        "macd",
        "macd_signal",
        "macd_hist",
        "ema_gap",
        "momentum",
        "trend_slope",
        "volatility",
        "range_pct",
        "body_to_range",
        "close_to_high",
        "volume_z",
        "volume_chg",
    ]
    return cols


class FeaturePipeline:
    """Stateless wrapper exposing the shared feature logic for both paths."""

    def __init__(
        self,
        feature_config: FeatureConfig | None = None,
        indicator_config: IndicatorConfig | None = None,
    ) -> None:
        self.feature_config = feature_config or FeatureConfig()
        self.indicator_config = indicator_config or IndicatorConfig()

    @property
    def columns(self) -> List[str]:
        return feature_columns(self.feature_config)

    def transform(self, candles: List[OHLCV]) -> pd.DataFrame:
        """OFFLINE path: features for every bar (used to build training sets)."""
        frame = ohlcv_to_frame(candles)
        feats = compute_features(frame, self.feature_config, self.indicator_config)
        return feats[self.columns]

    def transform_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Same as :meth:`transform` but from an already-built OHLCV frame."""
        feats = compute_features(frame, self.feature_config, self.indicator_config)
        return feats[self.columns]

    def latest(self, candles: List[OHLCV]) -> pd.Series:
        """ONLINE path: the feature row for the most recent bar.

        This is the exact last row of :meth:`transform` — by construction,
        not by convention.
        """
        feats = self.transform(candles)
        if feats.empty:
            raise ValueError("not enough data to compute features")
        return feats.iloc[-1]
