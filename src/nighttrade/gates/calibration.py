"""Phase 3 — confidence calibration gate.

A live run revealed problem #3: the strategy was overconfident — its stated
confidence ran well above its realized accuracy. This gate fixes that.

A :class:`ConfidenceCalibrator` fits an isotonic-regression map from stated
confidence to the *empirically-true* win probability, learned from the bot's
own evaluated prediction history. The :class:`CalibrationGate` then admits an
entry only when its **calibrated** probability clears a floor — never the raw,
optimistic number.

Until ``min_samples`` evaluated predictions exist the calibrator is the
identity map: it does not pretend to know better than the raw signal before
it has the evidence to.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .decision import GateDecision


class ConfidenceCalibrator:
    """Maps stated confidence -> empirically-true win probability."""

    def __init__(self, min_samples: int = 40) -> None:
        self.min_samples = max(2, min_samples)
        self.samples = 0
        self._iso = None  # a fitted IsotonicRegression, or None (identity)

    def fit(self, confidences: Sequence[float], outcomes: Sequence[int]) -> ConfidenceCalibrator:
        """Fit from parallel (stated confidence, was-correct 0/1) sequences."""
        conf = np.asarray(list(confidences), dtype=float)
        corr = np.asarray(list(outcomes), dtype=float)
        self.samples = int(conf.size)
        # Too little evidence, or only one outcome class -> stay the identity.
        if conf.size < self.min_samples or np.unique(corr).size < 2:
            self._iso = None
            return self
        from sklearn.isotonic import IsotonicRegression

        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso.fit(conf, corr)
        self._iso = iso
        return self

    @property
    def is_fitted(self) -> bool:
        return self._iso is not None

    def calibrate(self, confidence: float) -> float:
        """Map a stated confidence to a calibrated win probability."""
        if self._iso is None:
            return float(confidence)  # identity until there is evidence
        return float(self._iso.predict([float(confidence)])[0])


class CalibrationGate:
    """Blocks entries whose CALIBRATED win probability is below the floor."""

    def __init__(self, calibrator: ConfidenceCalibrator, floor: float) -> None:
        self.calibrator = calibrator
        self.floor = floor

    def evaluate(self, confidence: float) -> GateDecision:
        """Decide whether an entry at ``confidence`` may proceed."""
        calibrated = self.calibrator.calibrate(confidence)
        if not self.calibrator.is_fitted:
            return GateDecision(
                True,
                f"calibration inactive ({self.calibrator.samples} evaluated "
                f"prediction(s)) — stated confidence {confidence:.0%}",
            )
        if calibrated < self.floor:
            return GateDecision(
                False,
                f"calibrated win probability {calibrated:.0%} "
                f"(stated {confidence:.0%}) is below the floor "
                f"{self.floor:.0%} — entry blocked",
            )
        return GateDecision(
            True,
            f"calibrated win probability {calibrated:.0%} "
            f"(stated {confidence:.0%}) clears the floor {self.floor:.0%}",
        )
