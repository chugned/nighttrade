"""Pin the startup-warm decision (ADR-0007 follow-up: lean overnight RSS).

Booting the live observer into a CLOSED market and eagerly fetching all
503 S&P 500 symbols wasted a minute of network AND pinned ~665 MB RSS for
the whole overnight sleep (Python never returns the allocation to the OS).
``should_warm_now`` decides whether the CLI should do that eager warm:

  * REGULAR session            -> True  (we need data right now)
  * within the pre-open window -> True  (warm so we're ready at the open)
  * otherwise (overnight/wknd) -> False (start lean; the pre-open warm-up
                                         cycle fetches when actually needed)
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from nighttrade.market_hours import should_warm_now

_ET = ZoneInfo("America/New_York")


def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=_ET).astimezone(timezone.utc)


def test_warm_during_regular_session():
    # Tuesday 10:00 ET — market open.
    assert should_warm_now(_utc(2026, 6, 9, 10, 0)) is True


def test_no_warm_overnight():
    # Tuesday 22:00 ET — closed, next open ~11h away.
    assert should_warm_now(_utc(2026, 6, 9, 22, 0)) is False


def test_no_warm_on_weekend():
    # Saturday noon ET.
    assert should_warm_now(_utc(2026, 6, 13, 12, 0)) is False


def test_warm_inside_pre_open_window():
    # Tuesday 09:15 ET — 15 min before the 09:30 open, inside the 30-min window.
    assert should_warm_now(_utc(2026, 6, 9, 9, 15), warmup_minutes=30) is True


def test_no_warm_well_before_open():
    # Tuesday 08:30 ET — 60 min before open, OUTSIDE the 30-min window.
    assert should_warm_now(_utc(2026, 6, 9, 8, 30), warmup_minutes=30) is False


def test_no_warm_on_holiday():
    # 2026-07-03 is a hardcoded US market holiday.
    assert should_warm_now(_utc(2026, 7, 3, 10, 0)) is False
