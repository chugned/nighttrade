"""Loss-limit tracking — the daily and weekly circuit breakers.

A loss limit is a behavioural safeguard: once the period's drawdown breaches
the threshold, no new entries are allowed until the next period. It cannot
prevent a loss already taken — it stops the bleeding from compounding.
"""

from __future__ import annotations

from datetime import datetime
from typing import Tuple


class _PeriodLossTracker:
    """Tracks drawdown against a per-period equity baseline.

    Subclasses define what a "period" is (a calendar day, an ISO week, ...).
    When the period rolls over, the baseline resets to the current equity.
    """

    def __init__(self, starting_equity: float, max_loss_pct: float) -> None:
        if starting_equity <= 0:
            raise ValueError("starting_equity must be > 0")
        self._max_loss_pct = max_loss_pct
        self._period_key: object = None
        self._period_start_equity = starting_equity

    def _key(self, timestamp: datetime) -> object:  # pragma: no cover - abstract
        raise NotImplementedError

    def observe(self, timestamp: datetime, equity: float) -> None:
        """Record equity, rolling the period baseline if the period changed."""
        key = self._key(timestamp)
        if self._period_key is None or key != self._period_key:
            self._period_key = key
            self._period_start_equity = equity

    def loss_pct(self, equity: float) -> float:
        """Current period's loss as a positive fraction (0 if flat or up)."""
        if self._period_start_equity <= 0:
            return 0.0
        change = (equity - self._period_start_equity) / self._period_start_equity
        return max(0.0, -change)

    def is_breached(self, equity: float) -> bool:
        """True once the period's loss meets or exceeds the limit."""
        return self.loss_pct(equity) >= self._max_loss_pct

    @property
    def period_start_equity(self) -> float:
        return self._period_start_equity


class DailyLossTracker(_PeriodLossTracker):
    """Intraday drawdown against the day's opening equity."""

    def _key(self, timestamp: datetime) -> object:
        return timestamp.date()

    # Backwards-compatible alias.
    @property
    def day_start_equity(self) -> float:
        return self._period_start_equity


class WeeklyLossTracker(_PeriodLossTracker):
    """Rolling weekly drawdown, keyed on the ISO (year, week) pair."""

    def _key(self, timestamp: datetime) -> Tuple[int, int]:
        iso = timestamp.isocalendar()
        return (iso[0], iso[1])
