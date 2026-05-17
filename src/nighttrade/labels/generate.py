"""Label generation — OFFLINE TRAINING ONLY.

⚠️  Labels look into the FUTURE. Every function here uses ``close.shift(-h)``,
which references bars *after* the labeled bar. That is correct and necessary
for supervised training — and it is exactly why labels must NEVER touch the
online inference path.

Two structural safeguards:

1. The last ``horizon`` rows have no future and therefore get a NaN label.
   Training code drops them. The model is never trained on a bar whose
   outcome is unknown.
2. Nothing in ``nighttrade.fusion`` / ``nighttrade.cli`` imports this module for
   live decisions — the leakage test suite asserts that.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def future_return(close: pd.Series, horizon: int) -> pd.Series:
    """Forward return over ``horizon`` bars: ``close[t+h] / close[t] - 1``.

    The trailing ``horizon`` entries are NaN — their future is not yet known.
    """
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    return close.shift(-horizon) / close - 1.0


def directional_label(close: pd.Series, horizon: int) -> pd.Series:
    """Binary direction label: 1 if the forward return is positive else 0.

    Returns a float Series (NaN where the future is unknown) so callers can
    ``dropna()`` before casting to int.
    """
    fwd = future_return(close, horizon)
    label = (fwd > 0).astype(float)
    return label.where(fwd.notna(), np.nan)


def breakout_label(
    close: pd.Series,
    horizon: int,
    threshold: float,
) -> pd.Series:
    """Three-way breakout label, keeping only decisive moves.

    * forward return ``> +threshold``  -> 1 (up breakout)
    * forward return ``< -threshold``  -> 0 (down breakout)
    * otherwise                        -> NaN (ambiguous; dropped in training)

    Filtering out the indecisive middle gives the classifier a cleaner,
    better-separated target — at the cost of fewer samples.
    """
    if threshold <= 0:
        raise ValueError("threshold must be > 0")
    fwd = future_return(close, horizon)
    label = pd.Series(np.nan, index=close.index, dtype=float)
    label[fwd > threshold] = 1.0
    label[fwd < -threshold] = 0.0
    # Where the future is unknown the label must stay NaN regardless.
    label = label.where(fwd.notna(), np.nan)
    return label


def make_labels(
    frame: pd.DataFrame,
    horizon: int,
    threshold: float,
    kind: str = "breakout",
) -> pd.Series:
    """Build the training label Series from an OHLCV frame.

    Args:
        kind: ``"breakout"`` (decisive moves only) or ``"directional"``
            (every bar labeled up/down).
    """
    close = frame["close"]
    if kind == "breakout":
        labels = breakout_label(close, horizon, threshold)
    elif kind == "directional":
        labels = directional_label(close, horizon)
    else:
        raise ValueError(f"unknown label kind: {kind}")
    labels.name = "label"
    return labels
