"""Canonical demo reproduction test.

PLAN.md mandates a specific scenario. This test pins the platform's output to
those numbers so a regression anywhere in the pipeline is caught immediately.
"""

from __future__ import annotations

import pytest

from nighttrade.config import load_config
from nighttrade.demo import (
    DEMO_MACRO_SCENARIO,
    DEMO_REFERENCE_PRICE,
    build_demo_candles,
    build_demo_orderbook,
)
from nighttrade.models import Action
from nighttrade.pipeline import AnalysisPipeline


@pytest.fixture
def demo_result():
    cfg = load_config(load_dotenv_file=False)
    return AnalysisPipeline(cfg).analyze(
        build_demo_candles(),
        build_demo_orderbook(),
        reference_price=DEMO_REFERENCE_PRICE,
        macro_scenario=DEMO_MACRO_SCENARIO,
    )


def test_demo_action_is_buy(demo_result):
    assert demo_result.decision.action is Action.BUY


def test_demo_confidence_is_moderate(demo_result):
    assert demo_result.decision.confidence == pytest.approx(0.62, abs=0.04)


def test_demo_entry_price(demo_result):
    assert demo_result.decision.entry == pytest.approx(233.53, abs=0.1)


def test_demo_stop_price(demo_result):
    assert demo_result.decision.stop == pytest.approx(233.06, abs=0.1)


def test_demo_target_price(demo_result):
    assert demo_result.decision.target == pytest.approx(235.87, abs=0.1)


def test_demo_levels_consistent(demo_result):
    d = demo_result.decision
    assert d.stop < d.entry < d.target  # BUY geometry
    assert d.risk_reward and d.risk_reward > 1.0


def test_demo_rsi_oversold(demo_result):
    rsi = demo_result.technical.rsi
    assert rsi is not None and rsi < 30


def test_demo_macro_is_bullish(demo_result):
    assert demo_result.macro.confidence == pytest.approx(0.85)
    assert demo_result.macro.score > 0


def test_demo_tape_is_sell_heavy(demo_result):
    # The sharp pullback leaves a sell-heavy intraday tape => negative imbalance.
    assert demo_result.microstructure.imbalance < 0


def test_demo_is_deterministic():
    cfg = load_config(load_dotenv_file=False)
    a = AnalysisPipeline(cfg).analyze(
        build_demo_candles(),
        build_demo_orderbook(),
        reference_price=DEMO_REFERENCE_PRICE,
        macro_scenario=DEMO_MACRO_SCENARIO,
    )
    b = AnalysisPipeline(cfg).analyze(
        build_demo_candles(),
        build_demo_orderbook(),
        reference_price=DEMO_REFERENCE_PRICE,
        macro_scenario=DEMO_MACRO_SCENARIO,
    )
    assert a.decision.confidence == b.decision.confidence
    assert a.decision.entry == b.decision.entry
