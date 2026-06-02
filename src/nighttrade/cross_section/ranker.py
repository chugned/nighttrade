"""The cross-sectional ranker.

Takes the raw per-stock factor vectors for a whole universe and turns them
into a *relative* ranking: each factor is winsorized and z-scored **across the
universe**, blended with configured weights, and the stocks are ordered and
split into long / short baskets.

Z-scoring across the universe is the whole point — it answers "how does this
stock's momentum compare to every other stock's momentum *right now*", which
is what equity selection actually trades on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

from ..config.schema import CrossSectionConfig
from .factors import StockFactors

# Factor names in blend order. ``ml`` is included only when every stock has a
# model score (i.e. an ML model was loaded).
_BASE_FACTORS = ("momentum", "trend", "reversion", "low_vol")
_LONG, _NEUTRAL, _SHORT = "LONG", "NEUTRAL", "SHORT"


@dataclass(frozen=True)
class RankedStock:
    """One stock's place in the cross-sectional ranking."""

    symbol: str
    rank: int  # 1 = most attractive
    percentile: float  # 1.0 = top of the universe
    composite: float  # blended weighted z-score
    composite_z: float  # composite, re-standardized across universe
    factor_z: Dict[str, float]  # per-factor cross-sectional z-scores
    basket: str  # LONG / NEUTRAL / SHORT
    price: float


@dataclass(frozen=True)
class RankedUniverse:
    """The full ranked universe for one cross-sectional snapshot."""

    timestamp: datetime
    stocks: List[RankedStock]  # ordered best -> worst
    weights: Dict[str, float]  # renormalized weights actually used
    excluded: List[str] = field(default_factory=list)  # dropped by liquidity gate

    @property
    def long_basket(self) -> List[str]:
        return [s.symbol for s in self.stocks if s.basket == _LONG]

    @property
    def short_basket(self) -> List[str]:
        return [s.symbol for s in self.stocks if s.basket == _SHORT]

    def top(self, n: int) -> List[RankedStock]:
        return self.stocks[:n]

    def bottom(self, n: int) -> List[RankedStock]:
        return self.stocks[-n:] if n else []


def _winsorize(values: np.ndarray, limit: float) -> np.ndarray:
    """Clip ``values`` to the [limit, 1-limit] quantile range."""
    if limit <= 0 or values.size < 3:
        return values
    lo = float(np.quantile(values, limit))
    hi = float(np.quantile(values, 1.0 - limit))
    return np.clip(values, lo, hi)


def _zscore(values: np.ndarray) -> np.ndarray:
    """Standardize to mean 0 / std 1. A zero-variance factor becomes zeros."""
    std = float(values.std())
    if std == 0.0:
        return np.zeros_like(values)
    return (values - float(values.mean())) / std


def rank_universe(
    factors: List[StockFactors],
    config: CrossSectionConfig,
    timestamp: Optional[datetime] = None,
) -> RankedUniverse:
    """Rank ``factors`` into a :class:`RankedUniverse`.

    Raises:
        ValueError: when no stock clears the liquidity gate.
    """
    timestamp = timestamp or datetime.now(timezone.utc)

    kept = [f for f in factors if f.dollar_volume >= config.min_dollar_volume]
    excluded = sorted(f.symbol for f in factors if f.dollar_volume < config.min_dollar_volume)
    if not kept:
        raise ValueError("no stocks cleared the liquidity gate — cannot rank")

    # Which factors are in play this cycle. ML only if every stock has a score.
    names = list(_BASE_FACTORS)
    if all(f.ml is not None for f in kept):
        names.append("ml")

    # Winsorize + z-score every factor across the universe.
    factor_z: Dict[str, np.ndarray] = {}
    for name in names:
        raw = np.array([getattr(f, name) or 0.0 for f in kept], dtype=float)
        factor_z[name] = _zscore(_winsorize(raw, config.winsorize_limit))

    # Renormalize the configured weights over the factors actually in play.
    w = config.weights
    raw_weights = {n: getattr(w, n) for n in names}
    total = sum(raw_weights.values()) or 1.0
    weights = {n: raw_weights[n] / total for n in names}

    # Blend, then re-standardize the composite for a comparable headline score.
    composite = np.zeros(len(kept), dtype=float)
    for name in names:
        composite += weights[name] * factor_z[name]
    composite_z = _zscore(composite)

    order = np.argsort(composite)[::-1]  # best (highest) first
    n = len(kept)
    n_long = max(1, round(n * config.long_fraction))
    n_short = round(n * config.short_fraction)
    n_short = min(n_short, max(0, n - n_long))  # never overlap the long basket

    ranked: List[RankedStock] = []
    for position, idx in enumerate(order):
        if position < n_long:
            basket = _LONG
        elif position >= n - n_short and n_short > 0:
            basket = _SHORT
        else:
            basket = _NEUTRAL
        ranked.append(
            RankedStock(
                symbol=kept[idx].symbol,
                rank=position + 1,
                percentile=1.0 if n == 1 else 1.0 - position / (n - 1),
                composite=float(composite[idx]),
                composite_z=float(composite_z[idx]),
                factor_z={name: float(factor_z[name][idx]) for name in names},
                basket=basket,
                price=kept[idx].price,
            )
        )

    return RankedUniverse(timestamp=timestamp, stocks=ranked, weights=weights, excluded=excluded)
