"""Machine-learning infrastructure — datasets, models, prediction signals."""

from __future__ import annotations

from .dataset import Dataset, build_dataset
from .model import PredictiveModel, TrainResult, build_estimator

__all__ = [
    "Dataset",
    "build_dataset",
    "PredictiveModel",
    "TrainResult",
    "build_estimator",
]
