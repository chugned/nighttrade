"""Cross-sectional ranking — relative-strength stock selection.

Where the rest of the pipeline asks "is this stock going up?", this layer asks
the question that actually drives equity alpha: *"of the whole universe, which
stocks are most attractive **relative to the others** right now?"*

Each stock's factors (momentum, trend quality, mean-reversion, low volatility,
ML score) are z-scored **across the universe**, blended, and ranked. The top
fraction becomes the long basket, the bottom fraction the short/avoid basket.
"""

from __future__ import annotations

from .factors import StockFactors, compute_factors
from .ranker import RankedStock, RankedUniverse, rank_universe

__all__ = [
    "StockFactors",
    "compute_factors",
    "RankedStock",
    "RankedUniverse",
    "rank_universe",
]
