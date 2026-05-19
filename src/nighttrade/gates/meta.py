"""Phase 4 — meta-labelling: the secondary model and its gate.

The primary pipeline answers "which way?". The meta-model answers the
question that actually fixes a no-edge strategy (problem #1): *"is THIS trade
worth taking?"*.

A gradient-boosting classifier is trained on triple-barrier-labelled history
to predict P(a long here hits target before stop) from the same technical /
market features the rest of the platform uses. The :class:`MetaGate` then
admits an entry only when that probability clears a floor — fewer trades, but
higher precision.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

from ..config.schema import AppConfig
from ..features import FeaturePipeline
from ..indicators.frame import ohlcv_to_frame
from ..labels import triple_barrier_labels
from ..ml.model import build_estimator
from ..models import OHLCV, ModelKind
from .decision import GateDecision

# Minimum labelled rows before the meta-model will train at all.
_MIN_TRAIN_ROWS = 50


def build_meta_dataset(
    candles: List[OHLCV], config: AppConfig,
) -> "Tuple[pd.DataFrame, pd.Series, List[str]]":
    """Assemble features + triple-barrier meta-labels, aligned and NaN-free."""
    frame = ohlcv_to_frame(candles)
    pipeline = FeaturePipeline(config.features, config.indicators)
    features = pipeline.transform_frame(frame)
    labels = triple_barrier_labels(frame, config)
    joined = features.join(labels, how="inner").dropna()
    columns = list(pipeline.columns)
    return joined[columns].copy(), joined["meta_label"].astype(int), columns


class MetaModel:
    """A secondary classifier — P(a long hits target before stop)."""

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self._estimator = None
        self.feature_names: List[str] = []
        self.samples = 0
        self.version = "untrained"

    @property
    def is_trained(self) -> bool:
        return self._estimator is not None

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "MetaModel":
        """Train the gradient-boosting meta-classifier on (features, labels)."""
        if len(y) < _MIN_TRAIN_ROWS or y.nunique() < 2:
            return self  # too little evidence / single class — stay untrained
        estimator = build_estimator(ModelKind.GRADIENT_BOOSTING, self.seed)
        estimator.fit(X.to_numpy(dtype=float), y.to_numpy(dtype=int))
        self._estimator = estimator
        self.feature_names = list(X.columns)
        self.samples = int(len(y))
        self.version = f"meta-gb-n{self.samples}"
        return self

    def fit_from_candles(self, candle_series: List[List[OHLCV]],
                         config: AppConfig) -> "MetaModel":
        """Pool triple-barrier datasets from many symbols, then fit."""
        x_parts: List[pd.DataFrame] = []
        y_parts: List[pd.Series] = []
        for candles in candle_series:
            try:
                features, labels, _ = build_meta_dataset(candles, config)
            except Exception:  # noqa: BLE001 - skip a bad symbol
                continue
            if len(labels) >= 20:
                x_parts.append(features)
                y_parts.append(labels)
        if not x_parts:
            return self
        return self.fit(pd.concat(x_parts, ignore_index=True),
                        pd.concat(y_parts, ignore_index=True))

    def probability(self, candles: List[OHLCV], config: AppConfig) -> float:
        """P(win) for the latest bar of ``candles`` — uses features only."""
        if self._estimator is None:
            return 0.5
        frame = ohlcv_to_frame(candles)
        pipeline = FeaturePipeline(config.features, config.indicators)
        feats = pipeline.transform_frame(frame)[list(pipeline.columns)].dropna()
        if feats.empty:
            return 0.5
        row = feats.iloc[[-1]].to_numpy(dtype=float)
        return float(self._estimator.predict_proba(row)[0][1])

    def probability_frame(self, X: pd.DataFrame) -> np.ndarray:
        """P(win) for every row of an already-prepared feature matrix."""
        if self._estimator is None:
            return np.full(len(X), 0.5)
        return self._estimator.predict_proba(X.to_numpy(dtype=float))[:, 1]

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pickle.dump({"estimator": self._estimator,
                         "feature_names": self.feature_names,
                         "samples": self.samples, "version": self.version,
                         "seed": self.seed}, fh)
        return path

    @classmethod
    def load(cls, path: Path | str) -> "MetaModel":
        with Path(path).open("rb") as fh:
            data = pickle.load(fh)
        model = cls(seed=data.get("seed", 42))
        model._estimator = data.get("estimator")
        model.feature_names = data.get("feature_names", [])
        model.samples = data.get("samples", 0)
        model.version = data.get("version", "untrained")
        return model


class MetaGate:
    """Blocks entries whose meta-model P(win) is below the floor."""

    def __init__(self, model: MetaModel, min_probability: float) -> None:
        self.model = model
        self.min_probability = min_probability

    def evaluate(self, candles: List[OHLCV], config: AppConfig) -> GateDecision:
        """Decide whether the latest bar's trade clears the meta-model floor."""
        if not self.model.is_trained:
            return GateDecision(True, "meta-model untrained — gate inactive")
        prob = self.model.probability(candles, config)
        if prob < self.min_probability:
            return GateDecision(
                False,
                f"meta-model P(win) {prob:.0%} below floor "
                f"{self.min_probability:.0%} — low-precision trade vetoed")
        return GateDecision(
            True,
            f"meta-model P(win) {prob:.0%} clears floor "
            f"{self.min_probability:.0%}")
