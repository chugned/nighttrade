"""US equity market hours / session calendar.

Stocks, unlike 24/7 crypto, trade in defined sessions. This module answers
"what session is it?" in US Eastern time with a weekday + holiday model:

* ``CLOSED``      — overnight, weekends, holidays
* ``PRE_MARKET``  — 04:00–09:30 ET
* ``REGULAR``     — 09:30–16:00 ET (the only session this platform paper-trades)
* ``POST_MARKET`` — 16:00–20:00 ET

It is a *research / observation clock*, not a settlement calendar: it models
full-day holidays but deliberately ignores rare early-close half-days. Every
function accepts an optional ``when`` (defaults to now, UTC) so it is fully
deterministic and testable.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

# Session boundaries in Eastern wall-clock time.
_PRE_OPEN = time(4, 0)
_REGULAR_OPEN = time(9, 30)
_REGULAR_CLOSE = time(16, 0)
_POST_CLOSE = time(20, 0)

# Full-day US market holidays (2024–2027). Early-close half-days are ignored
# on purpose — this is an observation clock, not a clearing calendar.
_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2025
        date(2025, 1, 1),
        date(2025, 1, 20),
        date(2025, 2, 17),
        date(2025, 4, 18),
        date(2025, 5, 26),
        date(2025, 6, 19),
        date(2025, 7, 4),
        date(2025, 9, 1),
        date(2025, 11, 27),
        date(2025, 12, 25),
        # 2026
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
        # 2027
        date(2027, 1, 1),
        date(2027, 1, 18),
        date(2027, 2, 15),
        date(2027, 3, 26),
        date(2027, 5, 31),
        date(2027, 6, 18),
        date(2027, 7, 5),
        date(2027, 9, 6),
        date(2027, 11, 25),
        date(2027, 12, 24),
    }
)


class MarketSession(str, Enum):
    """A US equity trading session."""

    CLOSED = "closed"
    PRE_MARKET = "pre_market"
    REGULAR = "regular"
    POST_MARKET = "post_market"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)

    @property
    def is_tradeable(self) -> bool:
        """Whether this platform will paper-trade in this session.

        Paper trades are placed only during the REGULAR session — pre/post
        liquidity is too thin for the execution model to be meaningful.
        """
        return self is MarketSession.REGULAR


def _to_et(when: Optional[datetime]) -> datetime:
    """Coerce ``when`` (default: now) to a timezone-aware US/Eastern datetime."""
    if when is None:
        when = datetime.now(timezone.utc)
    elif when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(_ET)


def is_trading_day(when: Optional[datetime] = None) -> bool:
    """True if ``when`` falls on a weekday that is not a market holiday."""
    et = _to_et(when)
    return et.weekday() < 5 and et.date() not in _HOLIDAYS


def session_at(when: Optional[datetime] = None) -> MarketSession:
    """Return the :class:`MarketSession` in effect at ``when``."""
    et = _to_et(when)
    if not is_trading_day(et):
        return MarketSession.CLOSED
    t = et.time()
    if t < _PRE_OPEN or t >= _POST_CLOSE:
        return MarketSession.CLOSED
    if t < _REGULAR_OPEN:
        return MarketSession.PRE_MARKET
    if t < _REGULAR_CLOSE:
        return MarketSession.REGULAR
    return MarketSession.POST_MARKET


def is_market_open(when: Optional[datetime] = None) -> bool:
    """True during the REGULAR session — the only session this platform trades."""
    return session_at(when) is MarketSession.REGULAR


def next_market_open(when: Optional[datetime] = None) -> datetime:
    """Return the next REGULAR-session open at/after ``when`` (UTC)."""
    et = _to_et(when)
    for _ in range(0, 10):  # at most ~10 days ahead covers any holiday run
        candidate = et.replace(hour=9, minute=30, second=0, microsecond=0)
        if is_trading_day(candidate) and et <= candidate:
            return candidate.astimezone(timezone.utc)
        et = (et + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    raise RuntimeError("no market open found within 10 days")  # pragma: no cover


def describe(when: Optional[datetime] = None) -> str:
    """A short human-readable description of the current market state."""
    et = _to_et(when)
    session = session_at(et)
    stamp = et.strftime("%Y-%m-%d %H:%M %Z")
    if session is MarketSession.REGULAR:
        return f"REGULAR session open ({stamp})"
    if session is MarketSession.CLOSED:
        nxt = next_market_open(et).astimezone(_ET)
        return f"market CLOSED ({stamp}) — next open " f"{nxt.strftime('%Y-%m-%d %H:%M %Z')}"
    return f"{session.value.upper().replace('_', '-')} session ({stamp})"
