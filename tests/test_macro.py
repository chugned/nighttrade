"""Macro context engine tests."""

from __future__ import annotations

from nighttrade.config import load_config
from nighttrade.macro import MacroEngine, MockMacroAnalyzer, get_scenario
from nighttrade.models import Bias, RiskLevel


def test_explicit_scenario_risk_on(uptrend_candles):
    sig = MockMacroAnalyzer().analyze("AAPL", uptrend_candles, scenario="risk_on")
    assert sig.bias is Bias.BULLISH
    assert sig.confidence == 0.85
    assert sig.regime_label == "risk_on"


def test_explicit_scenario_credit_crisis(uptrend_candles):
    sig = MockMacroAnalyzer().analyze("AAPL", uptrend_candles,
                                      scenario="credit_crisis")
    assert sig.bias is Bias.BEARISH
    assert sig.risk_level is RiskLevel.EXTREME


def test_unknown_scenario_falls_back_to_neutral(uptrend_candles):
    sig = MockMacroAnalyzer().analyze("AAPL", uptrend_candles,
                                      scenario="not_a_real_regime")
    assert sig.regime_label == "neutral"


def test_derived_macro_is_deterministic(uptrend_candles):
    a = MockMacroAnalyzer().analyze("AAPL", uptrend_candles)
    b = MockMacroAnalyzer().analyze("AAPL", uptrend_candles)
    assert a.score == b.score and a.regime_label == b.regime_label


def test_derived_macro_bullish_on_uptrend(uptrend_candles):
    sig = MockMacroAnalyzer().analyze("AAPL", uptrend_candles)
    assert sig.score > 0


def test_macro_engine_uses_mock_by_default(uptrend_candles):
    cfg = load_config(load_dotenv_file=False)
    sig = MacroEngine(cfg).analyze("AAPL", uptrend_candles, scenario="panic")
    assert sig.source == "mock"
    assert sig.bias is Bias.BEARISH


def test_scenario_scores_in_bounds():
    for name in ("risk_on", "panic", "war", "credit_crisis", "fed_dovish",
                 "fed_hawkish", "neutral"):
        sc = get_scenario(name)
        assert -1.0 <= sc.score <= 1.0
        assert 0.0 <= sc.confidence <= 1.0
