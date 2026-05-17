"""Shared pytest fixtures and path setup.

Candle series and the heavyweight derived artifacts (training dataset,
walk-forward report, backtest result) are **session-scoped** — they are
deterministic and treated read-only, so computing them once and sharing
keeps the suite fast.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make ``src`` importable without an editable install.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from nighttrade.backtest import Backtester  # noqa: E402
from nighttrade.config import load_config  # noqa: E402
from nighttrade.exchanges import generate_random_walk  # noqa: E402
from nighttrade.ml import build_dataset  # noqa: E402
from nighttrade.validation import walk_forward_validate  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_observatory_state(tmp_path, monkeypatch):
    """Redirect observatory runtime state files into a per-test temp dir.

    Without this, observer tests would write the real
    ``data/learning_state.json`` / ``data/now.json`` and ``reports/observer/``
    — clobbering a live bot's state. Every test gets its own throwaway dir.
    """
    from nighttrade.observatory import learning as _learning
    from nighttrade.observatory import observer as _observer
    from nighttrade.dashboard import data as _dash_data

    monkeypatch.setattr(_learning, "LEARNING_STATE_PATH",
                        tmp_path / "learning_state.json")
    monkeypatch.setattr(_observer, "_NOW_PATH", tmp_path / "now.json")
    monkeypatch.setattr(_observer, "_OBSERVER_REPORTS", tmp_path / "observer")
    monkeypatch.setattr(_observer, "_LOG_FILE", tmp_path / "nighttrade.log")
    # The dashboard reads the same runtime-state files; redirect them too so a
    # dashboard test sees the state the observer test wrote, not the real one.
    monkeypatch.setattr(_dash_data, "_LEARNING_STATE",
                        tmp_path / "learning_state.json")
    monkeypatch.setattr(_dash_data, "_NOW_PATH", tmp_path / "now.json")


@pytest.fixture(scope="session")
def config():
    """The default config (no .env, so tests are hermetic). Read-only."""
    return load_config(load_dotenv_file=False)


@pytest.fixture(scope="session")
def uptrend_candles():
    """A deterministic upward-drifting candle series."""
    return generate_random_walk("AAPL", n_bars=300, start_price=30_000.0,
                                drift=0.0010, volatility=0.004, seed=3)


@pytest.fixture(scope="session")
def flat_candles():
    """A deterministic, low-drift candle series."""
    return generate_random_walk("AAPL", n_bars=300, start_price=30_000.0,
                                drift=0.0, volatility=0.005, seed=8)


@pytest.fixture(scope="session")
def long_candles():
    """A longer series suitable for ML training / walk-forward."""
    return generate_random_walk("AAPL", n_bars=700, start_price=30_000.0,
                                drift=0.0003, volatility=0.006, seed=5)


@pytest.fixture(scope="session")
def ml_dataset(long_candles, config):
    """A built (feature + label) dataset — computed once, shared read-only."""
    return build_dataset(long_candles, config)


@pytest.fixture(scope="session")
def wf_report(long_candles, config):
    """A walk-forward validation report — computed once, shared read-only."""
    return walk_forward_validate(long_candles, config)


@pytest.fixture(scope="session")
def uptrend_backtest(uptrend_candles, config):
    """A backtest result over the uptrend series — computed once."""
    return Backtester(config).run(uptrend_candles)
