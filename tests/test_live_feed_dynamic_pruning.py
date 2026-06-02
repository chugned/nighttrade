"""Pin the dynamic-symbol-pruning behaviour of ``YFinanceFeed``.

Symbols that consistently fail to return data (delisted, halted,
missing intraday) get "parked" after ``_PARK_AFTER_FAILS`` consecutive
failures. Parked symbols are skipped for ``_PARK_DURATION_S`` then
re-tried. A small fraction (``_REPROBE_FRACTION``) of parked symbols
gets re-tried every cycle to detect recoveries.

State persists to a JSON file across restarts.
"""

from __future__ import annotations

import json
import sys
import types
import threading
from typing import Dict, List
from unittest.mock import MagicMock

import pandas as pd
import pytest


def _install_fake_yfinance(monkeypatch, fail_symbols=None):
    """Patch yfinance to fail for a configurable set of symbols and
    succeed (return a one-bar frame) for others."""
    fail_symbols = set(fail_symbols or [])
    fake = types.ModuleType("yfinance")

    def fake_download(symbols, **kwargs):
        # Build a MultiIndex frame matching yfinance's group_by='ticker' shape
        syms = list(symbols) if isinstance(symbols, list) else [symbols]
        good = [s for s in syms if s not in fail_symbols]
        if not good:
            return pd.DataFrame()  # all in this chunk failed
        # Build one tiny frame per good symbol, concat
        cols, rows = [], []
        for s in good:
            for c in ("Open", "High", "Low", "Close", "Volume"):
                cols.append((s, c))
                rows.append(100.0 if c != "Volume" else 1000.0)
        # Each "row" needs to be a single-row frame
        idx = pd.DatetimeIndex(["2026-06-02 14:30:00"])
        data = {col: [val] for col, val in zip(cols, rows)}
        return pd.DataFrame(data, index=idx, columns=pd.MultiIndex.from_tuples(cols))

    class _FakeTicker:
        def __init__(self, *_a, **_kw): pass
        def history(self, *_a, **_kw):
            return pd.DataFrame({"Close": []})

    fake.download = fake_download
    fake.Ticker = _FakeTicker
    monkeypatch.setitem(sys.modules, "yfinance", fake)


def test_consistent_failures_park_a_symbol(tmp_path, monkeypatch):
    """Three consecutive failures → symbol is parked."""
    from nighttrade.observatory.live_feed import YFinanceFeed
    feed = YFinanceFeed(
        symbols=["GOOD", "BAD"], refresh_seconds=120.0,
        park_state_path=tmp_path / "park.json",
    )
    _install_fake_yfinance(monkeypatch, fail_symbols={"BAD"})
    for _ in range(YFinanceFeed._PARK_AFTER_FAILS):
        feed._refresh()
    assert "BAD" in feed._parked_until
    assert feed._failure_streak["BAD"] >= YFinanceFeed._PARK_AFTER_FAILS
    assert "GOOD" not in feed._parked_until


def test_parked_symbol_excluded_from_next_fetch(tmp_path, monkeypatch):
    """Once parked, a symbol is excluded from the active fetch list
    (except for the random reprobe sample)."""
    from nighttrade.observatory.live_feed import YFinanceFeed
    syms = [f"S{i:03d}" for i in range(20)]
    feed = YFinanceFeed(
        symbols=syms, refresh_seconds=120.0,
        park_state_path=tmp_path / "park.json",
    )
    # Park 10 symbols by hand
    import time
    for s in syms[:10]:
        feed._parked_until[s] = time.time() + 3600
    active = feed._active_symbols()
    # All 10 unparked symbols + a small reprobe sample (1-2) from parked
    # Expected ~11-12 total (active 10 + 10% × 10 = 1 probe)
    assert 10 <= len(active) <= 12
    # All unparked are present
    for s in syms[10:]:
        assert s in active


def test_recovered_symbol_clears_park_state(tmp_path, monkeypatch):
    """A failed-then-recovered symbol should be un-parked + streak reset."""
    from nighttrade.observatory.live_feed import YFinanceFeed
    feed = YFinanceFeed(
        symbols=["S"], refresh_seconds=120.0,
        park_state_path=tmp_path / "park.json",
    )
    # First fail
    _install_fake_yfinance(monkeypatch, fail_symbols={"S"})
    feed._refresh()
    assert feed._failure_streak.get("S", 0) == 1
    # Then succeed
    _install_fake_yfinance(monkeypatch, fail_symbols=set())
    feed._refresh()
    assert feed._failure_streak.get("S", 0) == 0
    assert "S" not in feed._parked_until


def test_park_state_persists_across_restarts(tmp_path, monkeypatch):
    """A new YFinanceFeed instance reads the parked-symbols JSON
    written by the previous one."""
    from nighttrade.observatory.live_feed import YFinanceFeed
    path = tmp_path / "park.json"
    # First feed parks 'BAD'
    feed1 = YFinanceFeed(symbols=["GOOD", "BAD"], park_state_path=path)
    _install_fake_yfinance(monkeypatch, fail_symbols={"BAD"})
    for _ in range(YFinanceFeed._PARK_AFTER_FAILS):
        feed1._refresh()
    assert "BAD" in feed1._parked_until
    # JSON file should exist with the state
    assert path.exists()
    state = json.loads(path.read_text())
    assert "BAD" in state["parked_until"]
    # Second feed reads the same path → parked state carried over
    feed2 = YFinanceFeed(symbols=["GOOD", "BAD"], park_state_path=path)
    assert "BAD" in feed2._parked_until


def test_park_duration_eventually_expires(tmp_path, monkeypatch):
    """When the park_until time has passed, the symbol is fetched again."""
    from nighttrade.observatory.live_feed import YFinanceFeed
    feed = YFinanceFeed(symbols=["S"], park_state_path=tmp_path / "park.json")
    # Park S in the past (already expired)
    import time
    feed._parked_until["S"] = time.time() - 1.0  # 1 second ago
    active = feed._active_symbols()
    assert "S" in active  # expired park = re-included


def test_reprobe_includes_some_parked_each_cycle(tmp_path, monkeypatch):
    """Some parked symbols get re-tried each cycle for recovery detection."""
    from nighttrade.observatory.live_feed import YFinanceFeed
    syms = [f"S{i:03d}" for i in range(50)]
    feed = YFinanceFeed(symbols=syms, park_state_path=tmp_path / "park.json")
    import time
    park_time = time.time() + 3600
    for s in syms:
        feed._parked_until[s] = park_time

    active = feed._active_symbols()
    # All parked → reprobe sample is 10% × 50 = 5
    n_reprobe = max(1, int(50 * YFinanceFeed._REPROBE_FRACTION))
    assert len(active) == n_reprobe
    assert all(s in syms for s in active)


def test_thresholds_are_conservative():
    """Sanity check on the constants — we don't want a single transient
    blip to park a real symbol forever."""
    from nighttrade.observatory.live_feed import YFinanceFeed
    assert YFinanceFeed._PARK_AFTER_FAILS >= 2, "1-fail-to-park is too aggressive"
    assert YFinanceFeed._PARK_DURATION_S >= 600, "park <10min isn't worth the bookkeeping"
    assert YFinanceFeed._REPROBE_FRACTION > 0, (
        "must reprobe SOMETHING or recoveries never get detected"
    )
