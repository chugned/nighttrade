"""Walk-forward validation tests.

All tests share the session-scoped ``wf_report`` fixture — walk-forward
training is expensive and the report is deterministic and read-only.
"""

from __future__ import annotations


def test_walk_forward_runs(wf_report):
    assert wf_report.n_folds >= 1
    assert 0.0 <= wf_report.mean_test_accuracy <= 1.0


def test_walk_forward_folds_are_chronological(wf_report):
    """Each fold's test window must come AFTER its training window."""
    for fold in wf_report.folds:
        assert fold.train_end <= fold.test_start


def test_walk_forward_folds_roll_forward(wf_report):
    starts = [f.test_start for f in wf_report.folds]
    assert starts == sorted(starts)


def test_overfitting_is_reported_on_noise(wf_report):
    """A model fit to random-walk noise has no edge — validation must say so."""
    # On pure noise, expect either an overfitting or a no-edge warning.
    assert wf_report.warnings  # never silent about a no-edge model


def test_overfit_gap_definition(wf_report):
    for fold in wf_report.folds:
        assert fold.overfit_gap == fold.train_accuracy - fold.test_accuracy
