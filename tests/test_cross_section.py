"""Cross-sectional factor / ranking tests."""

from __future__ import annotations

import pytest

from nighttrade.config.schema import CrossSectionConfig
from nighttrade.cross_section import compute_factors, rank_universe
from nighttrade.exchanges import generate_random_walk

_CFG = CrossSectionConfig()


def _stock(symbol: str, drift: float, seed: int):
    return generate_random_walk(symbol, n_bars=220, start_price=200.0,
                                drift=drift, volatility=0.003, seed=seed)


def _universe():
    """A spread of stocks from strongly down-trending to strongly up-trending."""
    spec = {"DOWN2": -0.0020, "DOWN1": -0.0010, "FLATA": 0.0,
            "FLATB": 0.0002, "UP1": 0.0010, "UP2": 0.0020,
            "UP3": 0.0028, "DOWN3": -0.0028, "MIDA": 0.0006, "MIDB": -0.0006}
    factors = []
    for i, (sym, drift) in enumerate(spec.items()):
        snap = compute_factors(sym, _stock(sym, drift, i + 1), _CFG)
        assert snap is not None
        factors.append(snap)
    return factors


def test_compute_factors_short_series_returns_none():
    candles = generate_random_walk("AAPL", n_bars=20, seed=1)
    assert compute_factors("AAPL", candles, _CFG) is None


def test_compute_factors_momentum_sign():
    up = compute_factors("AAPL", _stock("AAPL", 0.0025, 1), _CFG)
    down = compute_factors("MSFT", _stock("MSFT", -0.0025, 2), _CFG)
    assert up.momentum > 0 > down.momentum
    assert up.trend > down.trend  # smoother up-trend scores higher


def test_compute_factors_ml_passthrough():
    snap = compute_factors("AAPL", _stock("AAPL", 0.001, 1), _CFG, ml_score=0.4)
    assert snap.ml == pytest.approx(0.4)
    snap_none = compute_factors("AAPL", _stock("AAPL", 0.001, 1), _CFG)
    assert snap_none.ml is None


def test_rank_universe_orders_by_strength():
    ranked = rank_universe(_universe(), _CFG)
    rank_of = {s.symbol: s.rank for s in ranked.stocks}
    # The strongest up-trend must rank ahead of the strongest down-trend.
    assert rank_of["UP3"] < rank_of["DOWN3"]
    assert rank_of["UP2"] < rank_of["DOWN2"]


def test_rank_universe_is_sorted_and_bounded():
    ranked = rank_universe(_universe(), _CFG)
    composites = [s.composite for s in ranked.stocks]
    assert composites == sorted(composites, reverse=True)
    assert [s.rank for s in ranked.stocks] == list(range(1, len(ranked.stocks) + 1))
    for s in ranked.stocks:
        assert 0.0 <= s.percentile <= 1.0
    assert ranked.stocks[0].percentile == 1.0


def test_rank_universe_baskets():
    ranked = rank_universe(_universe(), _CFG)  # 10 stocks, 0.10 fractions
    assert len(ranked.long_basket) == 1
    assert len(ranked.short_basket) == 1
    assert ranked.stocks[0].basket == "LONG"
    assert ranked.stocks[-1].basket == "SHORT"
    # The long and short baskets never overlap.
    assert not set(ranked.long_basket) & set(ranked.short_basket)


def test_rank_universe_weights_renormalized_without_ml():
    ranked = rank_universe(_universe(), _CFG)
    assert "ml" not in ranked.weights  # no ML scores supplied
    assert sum(ranked.weights.values()) == pytest.approx(1.0)


def test_rank_universe_uses_ml_when_present():
    factors = []
    for i, sym in enumerate(["A", "B", "C", "D", "E"]):
        factors.append(compute_factors(sym, _stock(sym, 0.001 * i, i + 1),
                                       _CFG, ml_score=0.1 * i))
    ranked = rank_universe(factors, _CFG)
    assert "ml" in ranked.weights
    assert sum(ranked.weights.values()) == pytest.approx(1.0)


def test_rank_universe_liquidity_gate_excludes_thin():
    cfg = CrossSectionConfig(min_dollar_volume=1e18)  # nothing can clear this
    with pytest.raises(ValueError, match="liquidity gate"):
        rank_universe(_universe(), cfg)


def test_rank_universe_is_deterministic():
    a = rank_universe(_universe(), _CFG)
    b = rank_universe(_universe(), _CFG)
    assert [s.symbol for s in a.stocks] == [s.symbol for s in b.stocks]
    assert [s.composite for s in a.stocks] == [s.composite for s in b.stocks]
