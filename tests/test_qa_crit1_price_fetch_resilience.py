"""QA-CRIT-1 (nighttrade variant) — feed.price_at failure must not crash cycle.

Before the fix, nighttrade observer._equity() and _manage_positions()
called feed.price_at() with no try/except. A yfinance hiccup raised
out, killed the cycle, silently skipped stop-loss enforcement for
every other open position.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from nighttrade.observatory.observer import Observer
from nighttrade.config.schema import AppConfig, WatchlistConfig


class _FlakyFeed:
    def __init__(self, prices, fail_symbol=None):
        self._prices = prices
        self._fail = fail_symbol
        self.calls = []

    def price_at(self, symbol, when):
        self.calls.append(symbol)
        if symbol == self._fail:
            raise RuntimeError(f'simulated yfinance 503 for {symbol}')
        return self._prices.get(symbol, 100.0)


def _obs():
    with patch('nighttrade.observatory.observer.ObservatoryDB'):
        return Observer(AppConfig(), WatchlistConfig())


def test_equity_handles_price_at_failure_for_one_position():
    obs = _obs()
    obs._open = {
        'AAPL': {'entry': 200.0, 'qty': 1.0, 'stop': 195.0, 'target': 205.0,
                 'opened_cycle': 1, 'trade_id': 1},
        'MSFT': {'entry': 400.0, 'qty': 1.0, 'stop': 395.0, 'target': 410.0,
                 'opened_cycle': 1, 'trade_id': 2},
    }
    obs.feed = _FlakyFeed(prices={'MSFT': 405.0}, fail_symbol='AAPL')
    obs.db = MagicMock()
    obs.db.total_realised_pnl.return_value = 0.0
    obs.db.closed_paper_trades.return_value = []
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    eq = obs._equity(now)
    # AAPL falls back to entry (0 contribution); MSFT contributes +5
    assert eq == pytest.approx(1000.0 + 5.0)


def test_manage_positions_skips_failed_symbol_but_processes_others():
    obs = _obs()
    obs._open = {
        'AAPL': {'entry': 200.0, 'qty': 1.0, 'stop': 195.0, 'target': 205.0,
                 'opened_cycle': 1, 'trade_id': 1},
        'MSFT': {'entry': 400.0, 'qty': 1.0, 'stop': 395.0, 'target': 410.0,
                 'opened_cycle': 1, 'trade_id': 2},
    }
    # MSFT hits target; AAPL price_at fails
    obs.feed = _FlakyFeed(prices={'MSFT': 410.0}, fail_symbol='AAPL')
    obs.db = MagicMock()
    obs._risk = MagicMock()
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    closed = obs._manage_positions(now)
    assert closed == 1
    # AAPL kept open; MSFT closed
    assert 'AAPL' in obs._open
    assert 'MSFT' not in obs._open


def test_manage_positions_all_symbols_failing_returns_zero():
    obs = _obs()
    obs._open = {
        'AAPL': {'entry': 200.0, 'qty': 1.0, 'stop': 195.0, 'target': 205.0,
                 'opened_cycle': 1, 'trade_id': 1},
    }
    obs.feed = _FlakyFeed(prices={}, fail_symbol='AAPL')
    obs.db = MagicMock()
    obs._risk = MagicMock()
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    closed = obs._manage_positions(now)
    assert closed == 0
    assert 'AAPL' in obs._open
