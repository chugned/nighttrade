"""Pin the contract of ``YFinanceFeed._refresh`` parallel-chunked fetch.

The original ``threads=True`` spawned one OS thread per ticker (503
for S&P 500) and crashed the bot under host thread pressure (ADR-0005).
The fix was ``threads=False`` — bulletproof but ~110s sequential.

The new approach: WE own a ThreadPoolExecutor with a hard cap (8
workers), each calling ``yf.download(chunk, threads=False)`` on a
sub-batch. Single-thread per worker × N workers = bounded thread
count regardless of how the universe grows.

These tests pin: the worker cap, the chunking math, that
``threads=False`` is still used inside each worker, and that the
merged result covers every symbol that the chunks fetched.
"""

from __future__ import annotations

import sys
import time
import types
import threading
from typing import Dict, List

import pandas as pd
import pytest


def _install_fake_yfinance(monkeypatch, captured):
    """Patch yfinance so we observe each chunk's call args without network."""
    fake = types.ModuleType("yfinance")

    def fake_download(symbols, **kwargs):
        captured.setdefault("calls", []).append({
            "symbols": list(symbols) if isinstance(symbols, list) else [symbols],
            "kwargs": kwargs,
            "thread_id": threading.get_ident(),
        })
        return pd.DataFrame()

    class _FakeTicker:
        def __init__(self, *_a, **_kw): pass
        def history(self, *_a, **_kw):
            return pd.DataFrame({"Close": []})

    fake.download = fake_download
    fake.Ticker = _FakeTicker
    monkeypatch.setitem(sys.modules, "yfinance", fake)


def test_max_workers_capped_at_eight():
    """ADR-0005 anti-pattern rule: never unbounded parallel fetch.
    Hard cap at 8 — pinned by this test."""
    from nighttrade.observatory.live_feed import YFinanceFeed
    assert YFinanceFeed._MAX_FETCH_WORKERS <= 8


def test_each_chunk_passes_threads_false(monkeypatch):
    """Every chunk MUST be threads=False — otherwise we're double-
    parallelising (our pool × yfinance's per-ticker threads)."""
    from nighttrade.observatory.live_feed import YFinanceFeed
    captured: dict = {}
    _install_fake_yfinance(monkeypatch, captured)
    syms = [f"S{i:03d}" for i in range(20)]
    feed = YFinanceFeed(symbols=syms, refresh_seconds=120.0)
    feed._refresh()
    assert "calls" in captured
    for c in captured["calls"]:
        assert c["kwargs"].get("threads") is False, (
            f"chunk fetched with threads={c['kwargs'].get('threads')!r}; "
            f"must be False per ADR-0005."
        )


def test_chunks_cover_every_symbol_exactly_once(monkeypatch):
    from nighttrade.observatory.live_feed import YFinanceFeed
    captured: dict = {}
    _install_fake_yfinance(monkeypatch, captured)
    syms = [f"S{i:03d}" for i in range(50)]
    feed = YFinanceFeed(symbols=syms, refresh_seconds=120.0)
    feed._refresh()
    fetched = []
    for c in captured["calls"]:
        fetched.extend(c["symbols"])
    assert sorted(fetched) == sorted(syms)


def test_chunk_count_respects_worker_cap(monkeypatch):
    """503 → exactly 8 chunks (one per worker, ~63 syms each)."""
    from nighttrade.observatory.live_feed import YFinanceFeed
    captured: dict = {}
    _install_fake_yfinance(monkeypatch, captured)
    syms = [f"S{i:04d}" for i in range(503)]
    feed = YFinanceFeed(symbols=syms, refresh_seconds=120.0)
    feed._refresh()
    n_chunks = len(captured.get("calls", []))
    assert n_chunks == YFinanceFeed._MAX_FETCH_WORKERS


def test_small_universe_does_not_overshoot_worker_count(monkeypatch):
    """3 symbols → at most 3 chunks (not 8 with empty chunks)."""
    from nighttrade.observatory.live_feed import YFinanceFeed
    captured: dict = {}
    _install_fake_yfinance(monkeypatch, captured)
    syms = ["A", "B", "C"]
    feed = YFinanceFeed(symbols=syms, refresh_seconds=120.0)
    feed._refresh()
    assert len(captured.get("calls", [])) <= len(syms)


def test_chunks_run_concurrently_not_serially(monkeypatch):
    """Barrier-synchronised proof of parallelism. 8 chunks × 50ms each
    should complete in ~100ms (parallel), not 400ms+ (serial)."""
    from nighttrade.observatory.live_feed import YFinanceFeed

    fake = types.ModuleType("yfinance")
    barrier = threading.Barrier(YFinanceFeed._MAX_FETCH_WORKERS)
    captured = []

    def fake_download(symbols, **kwargs):
        captured.append(threading.get_ident())
        try:
            barrier.wait(timeout=2.0)
        except threading.BrokenBarrierError:
            pass
        time.sleep(0.05)
        return pd.DataFrame()

    class _FT:
        def __init__(self, *_a, **_kw): pass
        def history(self, *_a, **_kw): return pd.DataFrame({"Close": []})

    fake.download = fake_download
    fake.Ticker = _FT
    monkeypatch.setitem(sys.modules, "yfinance", fake)

    syms = [f"S{i:03d}" for i in range(64)]
    feed = YFinanceFeed(symbols=syms, refresh_seconds=120.0)
    t0 = time.monotonic()
    feed._refresh()
    elapsed = time.monotonic() - t0

    assert elapsed < 0.30, (
        f"refresh took {elapsed:.3f}s — not parallel (serial baseline > 0.4s)"
    )
    assert len(set(captured)) == YFinanceFeed._MAX_FETCH_WORKERS
