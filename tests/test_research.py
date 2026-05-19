"""Research lab tests — history cache, purged walk-forward, baseline verdict."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random

from nighttrade.exchanges import generate_random_walk
from nighttrade.models import OHLCV
from nighttrade.research import HistoryCache, ResearchLab
from nighttrade.validation import walk_forward_validate

_T0 = datetime(2023, 1, 2, tzinfo=timezone.utc)


def _daily(symbol: str, n: int, seed: int = 1):
    """A synthetic daily OHLCV series — one bar per calendar day."""
    rng = random.Random(seed)
    out = []
    price = 200.0
    for i in range(n):
        close = price * (1.0 + rng.uniform(-0.02, 0.02))
        hi = max(price, close) * 1.004
        lo = min(price, close) * 0.996
        out.append(OHLCV(symbol=symbol, timestamp=_T0 + timedelta(days=i),
                         open=round(price, 2), high=round(hi, 2),
                         low=round(lo, 2), close=round(close, 2),
                         volume=1_000_000.0))
        price = close
    return out


# --- history cache ---------------------------------------------------------

def test_history_cache_round_trip(tmp_path):
    cache = HistoryCache(tmp_path / "h.db")
    candles = _daily("AAPL", 250)
    assert cache.store("AAPL", candles) == 250
    loaded = cache.load("AAPL")
    assert len(loaded) == 250
    assert loaded[0].timestamp < loaded[-1].timestamp  # oldest first
    assert loaded[-1].close == candles[-1].close
    assert cache.cached_symbols() == ["AAPL"]
    cache.close()


def test_history_cache_get_uses_cache_without_download(tmp_path):
    """get() returns cached data and never hits the network when present."""
    cache = HistoryCache(tmp_path / "h.db")
    cache.store("MSFT", _daily("MSFT", 200, seed=2))
    got = cache.get("MSFT")  # cached -> no download
    assert len(got) == 200
    cache.close()


# --- purged walk-forward ---------------------------------------------------

def test_walk_forward_is_purged(config):
    """A non-zero purge shifts every test window later than purge=0 would."""
    candles = generate_random_walk("AAPL", n_bars=1200, start_price=200.0,
                                   drift=0.0002, volatility=0.005, seed=7)
    no_purge = walk_forward_validate(candles, config, purge=0)
    purged = walk_forward_validate(candles, config, purge=60)
    assert no_purge.n_folds > 0 and purged.n_folds > 0
    # With a purge gap, fold 0's test window starts strictly later.
    assert purged.folds[0].test_start > no_purge.folds[0].test_start
    # And the train window never overlaps the test window.
    for fold in purged.folds:
        assert fold.train_end < fold.test_start


# --- research lab ----------------------------------------------------------

def test_research_lab_produces_honest_report(config):
    """Random-walk data has no edge — the lab must say so, not celebrate."""
    candles_by_symbol = {
        sym: generate_random_walk(sym, n_bars=600, start_price=200.0,
                                  drift=0.0, volatility=0.006, seed=s)
        for s, sym in enumerate(["AAA", "BBB", "CCC"])
    }
    report = ResearchLab(config).run(
        list(candles_by_symbol), candles_by_symbol=candles_by_symbol)
    assert len(report.symbols) == 3
    assert report.verdict  # a non-empty verdict string
    # The honest verdict is never an unqualified edge claim.
    assert "EDGE" not in report.verdict or "UNPROVEN" in report.verdict \
        or report.verdict == "NO EDGE"
    # The optimism caveat is always present.
    assert any("optimistic" in n.lower() for n in report.notes)


def test_research_lab_skips_thin_history(config):
    """A symbol with too little history is skipped, not crashed on."""
    report = ResearchLab(config).run(
        ["TINY"], candles_by_symbol={"TINY": _daily("TINY", 40)})
    assert "TINY" in report.skipped
    assert report.verdict == "NO DATA"


# --- Phase 1: ATR stop/target sweep ----------------------------------------

def test_sweep_stops_produces_report(config):
    """The sweep grids the multipliers, picks a best, and validates OOS."""
    candles_by_symbol = {
        sym: generate_random_walk(sym, n_bars=900, start_price=200.0,
                                  drift=0.0003, volatility=0.006, seed=s)
        for s, sym in enumerate(["AAA", "BBB", "CCC"])
    }
    rep = ResearchLab(config).sweep_stops(
        list(candles_by_symbol), candles_by_symbol=candles_by_symbol)
    # 5 stop multipliers x 4 reward:risk ratios.
    assert len(rep.grid) == 20
    assert rep.best in rep.grid
    # The best point is the in-sample return maximum.
    assert rep.best.return_pct == max(p.return_pct for p in rep.grid)
    assert rep.baseline is not None
    assert rep.notes  # always carries the optimism caveat


# --- Phase 4: meta-model evaluation ----------------------------------------

def test_evaluate_meta_produces_report(config):
    """The lab trains the meta-model in-sample and scores it out-of-sample."""
    candles_by_symbol = {
        sym: generate_random_walk(sym, n_bars=900, start_price=200.0,
                                  drift=0.0004, volatility=0.006, seed=s)
        for s, sym in enumerate(["AAA", "BBB", "CCC"])
    }
    rep = ResearchLab(config).evaluate_meta(
        list(candles_by_symbol), candles_by_symbol=candles_by_symbol)
    assert 0.0 <= rep.base_rate <= 1.0
    assert 0.0 <= rep.precision <= 1.0
    assert 0.0 <= rep.coverage <= 1.0
    assert rep.notes  # always carries the optimism caveat
