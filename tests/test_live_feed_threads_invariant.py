"""Regression tests: yfinance MUST be called sequentially.

Why this matters (see docs/adr/0005):
``yfinance.download(threads=True)`` spawns one OS thread per ticker.
For the 503-name S&P 500 universe this hits macOS' per-user thread
limit (``ulimit -u`` ≈ 2784) whenever the host is otherwise busy and
yields ``RuntimeError: can't start new thread`` at startup.

We tried ``threads=8`` as a compromise — it still crashed under
modest concurrent thread pressure (2386/2784 user threads in use).
The only safe value is ``threads=False`` (sequential).

This test file pins that invariant TWO ways:

1. **Behavioural** — mock yfinance, call ``_refresh``, assert the
   actual runtime kwargs say ``threads=False``.
2. **Source-level** — grep the source file for ``threads=`` and
   assert the only kwarg value is ``False``. Brittleness here is the
   feature: any edit near the kwarg trips the test and forces the
   editor to read the ADR before changing it.

Both should fire if the next refactor accidentally re-enables
parallel fetches.
"""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Behavioural — mock yfinance, observe what kwargs _refresh actually passes
# ---------------------------------------------------------------------------

def _install_fake_yfinance(monkeypatch, captured_kwargs):
    """Patch ``yfinance`` in ``sys.modules`` so the in-method ``import yfinance
    as yf`` resolves to our fake, and any call to ``download`` records its
    kwargs (and returns a plausible empty DataFrame so _refresh continues)."""

    fake = types.ModuleType("yfinance")

    def fake_download(*args, **kwargs):
        captured_kwargs["download"] = kwargs
        # Return an empty DataFrame so _frame_to_candles drops everything and
        # _refresh handles the empty-cache path gracefully.
        return pd.DataFrame()

    class _FakeTicker:
        def __init__(self, *_args, **_kwargs):
            pass

        def history(self, *_args, **_kwargs):
            # EUR/USD lookup — return an empty frame so fx falls back to 1.0
            return pd.DataFrame({"Close": []})

    fake.download = fake_download
    fake.Ticker = _FakeTicker
    monkeypatch.setitem(sys.modules, "yfinance", fake)


def test_refresh_calls_yfinance_download_with_threads_false(monkeypatch):
    """The runtime call site MUST pass threads=False — anything else risks
    re-introducing the NT-CRASH thread-budget outage."""
    from nighttrade.observatory.live_feed import YFinanceFeed

    captured: dict = {}
    _install_fake_yfinance(monkeypatch, captured)

    feed = YFinanceFeed(symbols=["AAPL", "MSFT", "GOOG"], refresh_seconds=120.0)
    feed._refresh()

    assert "download" in captured, "yf.download was never called"
    kwargs = captured["download"]
    assert "threads" in kwargs, (
        "yf.download must be called with explicit threads=False; "
        "leaving it implicit defaults to threads=True (one OS thread per "
        "ticker, hits macOS ulimit -u). See docs/adr/0005."
    )
    assert kwargs["threads"] is False, (
        f"yf.download was called with threads={kwargs['threads']!r}; "
        "must be exactly False (sequential fetch). Any positive int "
        "spawns that many concurrent threads — under modest host thread "
        "pressure even threads=8 has been observed to crash the bot. "
        "See docs/adr/0005-yfinance-threads-cap.md."
    )


# ---------------------------------------------------------------------------
# Source-level invariant — defends against refactors that change the call
# site in a way the behavioural test happens not to exercise (e.g. moving
# the download into a helper, adding a second download call elsewhere).
# ---------------------------------------------------------------------------

_LIVE_FEED_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "nighttrade" / "observatory" / "live_feed.py"
)


def _extract_threads_kwargs(source: str) -> list[str]:
    """Return every literal value passed to a ``threads=`` kwarg.

    Comments are stripped first so the explanatory comment on the
    threads=False line (which mentions ``threads=True`` and
    ``threads=8`` in prose) doesn't confuse the parser.
    """
    no_comments = re.sub(r"#.*$", "", source, flags=re.MULTILINE)
    return re.findall(r"\bthreads\s*=\s*([A-Za-z0-9_]+)", no_comments)


def test_live_feed_source_contains_no_positive_threads_kwarg():
    """Every threads= kwarg in live_feed.py source must be ``False`` (or 0).
    Anything else is a regression of the NT-CRASH fix."""
    src = _LIVE_FEED_PATH.read_text()
    values = _extract_threads_kwargs(src)
    assert values, (
        "Expected at least one threads= kwarg in live_feed.py — if the "
        "yf.download call has moved, update this test to find the new "
        "location and re-pin the invariant."
    )
    bad = [v for v in values if v not in ("False", "0")]
    assert not bad, (
        f"live_feed.py has a threads= kwarg with value(s) {bad!r}. "
        "Only False or 0 is permitted — see docs/adr/0005 for why."
    )


def test_extractor_helper_strips_comments():
    """Sanity-check the comment-stripping in _extract_threads_kwargs.
    Comments quoting ``threads=True`` and ``threads=8`` as prose must
    NOT be reported as kwargs."""
    sample = """
        # This is a comment mentioning threads=True and threads=8 in prose.
        data = something(threads=False)
    """
    assert _extract_threads_kwargs(sample) == ["False"]
