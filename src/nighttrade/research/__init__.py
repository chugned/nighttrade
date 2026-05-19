"""Research lab — measure whether the strategy has a real edge.

This package is the *measurement harness* (Phase 0 of the strategy plan): it
downloads years of real daily history, caches it in SQLite, and runs a
backtest + purged walk-forward validation to produce an honest baseline.

Every later strategy change must be proven here, out-of-sample, BEFORE it is
allowed near the live observer. Backtests are optimistic — the lab says so —
and "no edge" is the default, expected verdict.
"""

from __future__ import annotations

from .history import HistoryCache
from .lab import (
    MetaReport,
    ResearchLab,
    ResearchReport,
    SweepPoint,
    SweepReport,
    SymbolResult,
)

__all__ = [
    "HistoryCache",
    "ResearchLab",
    "ResearchReport",
    "SymbolResult",
    "SweepPoint",
    "SweepReport",
    "MetaReport",
]
