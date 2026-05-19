"""Strategy decision gate tests — Phase 2 regime gate."""

from __future__ import annotations

import pytest

from nighttrade.gates import (
    CalibrationGate,
    ConfidenceCalibrator,
    RegimeGate,
    break_even_win_rate,
)
from nighttrade.observatory.prediction_tracker import (
    GroupAccuracy,
    PredictionMemory,
)


def test_break_even_win_rate():
    """Break-even win rate = 1 / (1 + reward:risk)."""
    assert break_even_win_rate(1.0) == pytest.approx(0.50)
    assert break_even_win_rate(3.0) == pytest.approx(0.25)
    assert break_even_win_rate(1.5) == pytest.approx(0.40)


def _memory(condition: str, samples: int, correct: int) -> PredictionMemory:
    group = GroupAccuracy(label=condition, samples=samples, correct=correct,
                          mean_confidence=0.6)
    return PredictionMemory(by_condition={condition: group})


def test_regime_gate_passes_low_sample_regime():
    """Too few evaluated predictions — the gate stays inactive."""
    gate = RegimeGate(reward_risk=1.5, min_samples=20)
    decision = gate.evaluate("CHOPPY", _memory("CHOPPY", samples=5, correct=1))
    assert decision.allowed  # only 5 samples — gathering evidence
    assert "gathering evidence" in decision.reason


def test_regime_gate_blocks_below_break_even():
    """Enough samples and accuracy below break-even — entries blocked."""
    gate = RegimeGate(reward_risk=1.5, min_samples=20)  # break-even 40%
    # 50 evaluated, 15 correct = 30% accuracy < 40% break-even.
    decision = gate.evaluate("CHOPPY", _memory("CHOPPY", samples=50, correct=15))
    assert not decision.allowed
    assert "blocked" in decision.reason


def test_regime_gate_allows_above_break_even():
    """Enough samples and accuracy above break-even — entries allowed."""
    gate = RegimeGate(reward_risk=1.5, min_samples=20)
    # 50 evaluated, 32 correct = 64% accuracy > 40% break-even.
    decision = gate.evaluate("CALM", _memory("CALM", samples=50, correct=32))
    assert decision.allowed


def test_regime_gate_unknown_regime_passes():
    """A regime with no measured history is allowed through to gather data."""
    gate = RegimeGate(reward_risk=1.5)
    decision = gate.evaluate("MYSTERY", PredictionMemory())
    assert decision.allowed


def test_regime_gate_reward_risk_changes_break_even():
    """A higher reward:risk lowers the break-even bar, so it blocks less."""
    weak = _memory("CHOPPY", samples=50, correct=18)  # 36% accuracy
    # At 1.5:1 (break-even 40%) 36% is blocked …
    assert not RegimeGate(1.5, min_samples=20).evaluate("CHOPPY", weak).allowed
    # … but at 5:1 (break-even ~17%) the same 36% clears it.
    assert RegimeGate(5.0, min_samples=20).evaluate("CHOPPY", weak).allowed


# --- Phase 3: confidence calibration gate ----------------------------------

def _overconfident_history(n_per: int = 60):
    """Stated confidence sits monotonically above realized accuracy."""
    conf: list = []
    correct: list = []
    # stated 0.6 -> truly 40% right; stated 0.9 -> truly 60% right.
    for stated, true_rate in ((0.6, 0.4), (0.9, 0.6)):
        wins = int(n_per * true_rate)
        conf += [stated] * n_per
        correct += [1] * wins + [0] * (n_per - wins)
    return conf, correct


def test_calibrator_is_identity_below_min_samples():
    """With too little evidence the calibrator is the identity map."""
    cal = ConfidenceCalibrator(min_samples=40)
    cal.fit([0.8, 0.6], [1, 0])  # only 2 samples
    assert not cal.is_fitted
    assert cal.calibrate(0.85) == 0.85


def test_calibrator_shrinks_overconfidence():
    """A fitted calibrator maps inflated confidence down toward true odds."""
    cal = ConfidenceCalibrator(min_samples=40)
    cal.fit(*_overconfident_history())
    assert cal.is_fitted
    assert cal.calibrate(0.9) < 0.9                       # 90% stated, ~60% true
    assert cal.calibrate(0.9) > cal.calibrate(0.6)        # still monotone


def test_calibration_gate_blocks_below_floor():
    """The gate uses the calibrated probability, not the raw confidence."""
    cal = ConfidenceCalibrator(min_samples=40)
    cal.fit(*_overconfident_history())
    gate = CalibrationGate(cal, floor=0.55)
    assert not gate.evaluate(0.6).allowed  # calibrates to ~0.40 — blocked
    assert gate.evaluate(0.9).allowed      # calibrates to ~0.60 — allowed


def test_calibration_gate_inactive_until_fitted():
    """Before there is evidence the gate passes every entry through."""
    cal = ConfidenceCalibrator(min_samples=40)
    cal.fit([0.7], [1])  # too few
    gate = CalibrationGate(cal, floor=0.9)
    assert gate.evaluate(0.3).allowed


# --- Phase 4: meta-labelling -----------------------------------------------

def test_triple_barrier_labels_are_binary(config):
    """Every resolved triple-barrier label is 0 or 1; trailing bars are NaN."""
    from nighttrade.exchanges import generate_random_walk
    from nighttrade.indicators.frame import ohlcv_to_frame
    from nighttrade.labels import triple_barrier_labels

    candles = generate_random_walk("AAPL", n_bars=300, start_price=200.0,
                                   drift=0.001, volatility=0.005, seed=4)
    labels = triple_barrier_labels(ohlcv_to_frame(candles), config)
    resolved = labels.dropna()
    assert len(resolved) > 50
    assert set(resolved.unique()).issubset({0.0, 1.0})


def test_meta_dataset_and_model(config):
    """build_meta_dataset aligns features with labels; the model scores [0,1]."""
    from nighttrade.exchanges import generate_random_walk
    from nighttrade.gates import MetaModel, build_meta_dataset

    candles = generate_random_walk("AAPL", n_bars=600, start_price=200.0,
                                   drift=0.0006, volatility=0.006, seed=5)
    X, y, names = build_meta_dataset(candles, config)
    assert len(X) == len(y) > 50 and len(names) > 0
    model = MetaModel(seed=1).fit(X, y)
    if model.is_trained:  # needs both label classes present
        prob = model.probability(candles, config)
        assert 0.0 <= prob <= 1.0


def test_meta_gate_inactive_when_untrained(config):
    """An untrained meta-model leaves the gate inactive — entries pass."""
    from nighttrade.exchanges import generate_random_walk
    from nighttrade.gates import MetaGate, MetaModel

    candles = generate_random_walk("AAPL", n_bars=200, start_price=200.0, seed=6)
    gate = MetaGate(MetaModel(), min_probability=0.6)
    decision = gate.evaluate(candles, config)
    assert decision.allowed and "untrained" in decision.reason


def test_meta_model_save_load_round_trip(config, tmp_path):
    """A trained meta-model survives a save/load round-trip."""
    from nighttrade.exchanges import generate_random_walk
    from nighttrade.gates import MetaModel, build_meta_dataset

    candles = generate_random_walk("AAPL", n_bars=600, start_price=200.0,
                                   drift=0.0006, volatility=0.006, seed=5)
    X, y, _ = build_meta_dataset(candles, config)
    model = MetaModel(seed=1).fit(X, y)
    if not model.is_trained:
        return  # single-class data — nothing to round-trip
    path = model.save(tmp_path / "meta.pkl")
    reloaded = MetaModel.load(path)
    assert reloaded.is_trained
    assert reloaded.version == model.version
