"""Triple-barrier labelling (de Prado) — the basis of meta-labelling.

For each bar, simulate a long entered there with an ATR-width stop, an
ATR-width target, and a time-out barrier (`risk.time_stop_bars`). The
**meta-label** is:

* ``1`` — the position would hit the TARGET before the stop (a good trade);
* ``0`` — it hits the stop first, or times out without reaching the target.

A secondary model is then trained to predict that label — that is
meta-labelling: not "which way will price go" but "is *this* trade worth
taking". Labels need future bars, so the trailing rows are left ``NaN``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config.schema import AppConfig
from ..indicators import core

_ATR_PERIOD = 14


def triple_barrier_labels(frame: pd.DataFrame, config: AppConfig) -> pd.Series:
    """Return a 0/1 meta-label per bar (``NaN`` where the future is unknown)."""
    fusion = config.fusion
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    n = len(close)
    atr = core.atr(frame["high"], frame["low"], frame["close"],
                   _ATR_PERIOD).to_numpy(dtype=float)
    time_stop = config.risk.time_stop_bars or 20

    labels = np.full(n, np.nan)
    for i in range(n):
        price, vol = close[i], atr[i]
        if not np.isfinite(vol) or price <= 0 or i + 1 >= n:
            continue
        # ATR-based volatility unit — the same geometry the fusion engine uses.
        frac = min(max(vol / price, fusion.min_volatility_fraction),
                   fusion.max_volatility_fraction)
        unit = price * frac
        stop = price - fusion.stop_vol_mult * unit
        target = price + fusion.target_vol_mult * unit

        end = min(i + time_stop, n - 1)
        label = None
        for j in range(i + 1, end + 1):
            if low[j] <= stop:           # stop touched first (tie -> stop)
                label = 0
                break
            if high[j] >= target:        # target touched first
                label = 1
                break
        if label is None:
            # No barrier hit. Genuine time-out iff the full window existed;
            # otherwise the outcome is unknown — leave it NaN.
            if end >= i + time_stop:
                label = 0
            else:
                continue
        labels[i] = label

    return pd.Series(labels, index=frame.index, name="meta_label")
