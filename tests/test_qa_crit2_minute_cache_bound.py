"""QA-CRIT-2 (nighttrade variant) — YFinanceFeed candle cache is bounded.

In nighttrade the analog of daytrade's _minute_close cache is the
per-symbol candle list. Cap is YFinanceFeed._MAX_CACHED_BARS_PER_SYMBOL.
"""

from nighttrade.observatory.live_feed import YFinanceFeed


def test_max_cached_bars_constant_sensible():
    cap = YFinanceFeed._MAX_CACHED_BARS_PER_SYMBOL
    assert cap >= 240  # at least enough for analysis
    assert cap <= 2000  # not so large it becomes a leak
