"""Strategy decision gates.

Each gate is a filter on live entries, driven by the strategy's *own measured
evidence*:

* :class:`RegimeGate` (Phase 2) — block regimes measured below break-even.
* :class:`CalibrationGate` (Phase 3) — gate on the isotonic-calibrated
  probability, not raw confidence.
* :class:`MetaGate` (Phase 4) — gate on a secondary model's P(win).

A gate with too little evidence stays inactive and lets trades through, so it
gathers data before it judges — self-correcting by design.
"""

from __future__ import annotations

from .calibration import CalibrationGate, ConfidenceCalibrator
from .decision import GateDecision
from .meta import MetaGate, MetaModel, build_meta_dataset
from .regime import RegimeGate, break_even_win_rate

__all__ = [
    "GateDecision",
    "RegimeGate",
    "break_even_win_rate",
    "ConfidenceCalibrator",
    "CalibrationGate",
    "MetaModel",
    "MetaGate",
    "build_meta_dataset",
]
