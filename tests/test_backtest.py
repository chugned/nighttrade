"""Backtester and integration / pipeline tests."""

from __future__ import annotations

import pytest

from nighttrade.backtest import Backtester
from nighttrade.exchanges.mock import build_orderbook
from nighttrade.pipeline import AnalysisPipeline


def test_backtest_runs_and_reports(uptrend_backtest):
    m = uptrend_backtest.metrics
    assert m.bars > 0
    assert m.ending_equity >= 0
    assert m.warnings  # always carries the "not reality" caveat


def test_backtest_rejects_short_series(config):
    from nighttrade.exchanges import generate_random_walk
    short = generate_random_walk("AAPL", n_bars=30, seed=1)
    with pytest.raises(ValueError):
        Backtester(config).run(short)


def test_backtest_metrics_are_consistent(uptrend_backtest):
    m = uptrend_backtest.metrics
    assert m.winning_trades + m.losing_trades == m.total_trades
    assert 0.0 <= m.win_rate <= 1.0
    assert 0.0 <= m.exposure_pct <= 1.0
    assert m.max_drawdown_pct >= 0.0


def test_backtest_is_deterministic(config):
    """Two runs over the same data must produce identical results.

    Uses a short dedicated series — determinism does not need a long run.
    """
    from nighttrade.exchanges import generate_random_walk
    series = generate_random_walk("AAPL", n_bars=130, start_price=30_000.0,
                                  drift=0.0008, volatility=0.004, seed=21)
    a = Backtester(config).run(series).metrics
    b = Backtester(config).run(series).metrics
    assert a.ending_equity == b.ending_equity
    assert a.total_trades == b.total_trades


def test_backtest_fees_and_slippage_nonneg(uptrend_backtest):
    m = uptrend_backtest.metrics
    assert m.total_fees >= 0.0
    assert m.total_slippage >= 0.0


@pytest.mark.integration
def test_pipeline_end_to_end(uptrend_candles, config):
    """The full analysis pipeline produces a coherent decision."""
    book = build_orderbook("AAPL", uptrend_candles[-1].close, jitter=0.0)
    result = AnalysisPipeline(config).analyze(
        uptrend_candles, book, reference_price=uptrend_candles[-1].close)
    d = result.decision
    assert d.symbol == "AAPL"
    assert -1.0 <= d.fused_score <= 1.0
    assert 0.0 <= d.confidence <= 1.0
    # Component scores cover all four layers.
    assert set(d.component_scores) == {"technical", "microstructure",
                                       "macro", "ml"}


@pytest.mark.integration
def test_pipeline_decision_levels_consistent(uptrend_candles, config):
    book = build_orderbook("AAPL", uptrend_candles[-1].close, imbalance=0.5,
                           jitter=0.0)
    result = AnalysisPipeline(config).analyze(
        uptrend_candles, book, reference_price=uptrend_candles[-1].close,
        macro_scenario="risk_on")
    d = result.decision
    if d.is_actionable:
        assert d.entry and d.stop and d.target
        assert d.risk_reward and d.risk_reward > 0


@pytest.mark.integration
def test_pipeline_kill_switch_blocks_decision(uptrend_candles, config):
    """A credit-crisis macro scenario must force HOLD."""
    book = build_orderbook("AAPL", uptrend_candles[-1].close, jitter=0.0)
    result = AnalysisPipeline(config).analyze(
        uptrend_candles, book, reference_price=uptrend_candles[-1].close,
        macro_scenario="credit_crisis")
    assert result.kill_switch.active
    assert result.decision.action.value == "hold"
