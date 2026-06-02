"""Pin the memory-hygiene rotation: observer exits cleanly after
``_MAX_CYCLES_BEFORE_RESTART`` cycles so launchd respawns it with
fresh RSS. Bounds the yfinance/pandas-internal-state RSS growth
that climbs ~6 MB/cycle (observed 2026-06-02)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from nighttrade.config import load_config
from nighttrade.observatory import LiveMockFeed, ObservatoryDB, Observer
from nighttrade.config.schema import WatchlistConfig


def _make_observer(tmp_path):
    return Observer(
        load_config(load_dotenv_file=False),
        WatchlistConfig(symbols=["AAPL"]),
        db=ObservatoryDB(tmp_path / "obs.db"),
        feed=LiveMockFeed(),
    )


def test_rotation_constant_is_sane():
    """_MAX_CYCLES_BEFORE_RESTART must be high enough to be productive
    and low enough to actually bound memory growth.

    At interval=180 and ~6 MB/cycle leak:
      60 cycles × 180s = 3 hours
      60 cycles × 6 MB = 360 MB peak RSS growth between rotations
    """
    assert 30 <= Observer._MAX_CYCLES_BEFORE_RESTART <= 200, (
        f"_MAX_CYCLES_BEFORE_RESTART={Observer._MAX_CYCLES_BEFORE_RESTART} "
        "outside reasonable bounds 30..200"
    )


def test_observer_exits_after_max_cycles(tmp_path):
    """When the cycle counter hits _MAX_CYCLES_BEFORE_RESTART, the
    while loop must break (_stop=True). The finally block then stops
    the run cleanly."""
    obs = _make_observer(tmp_path)
    obs._cycle = Observer._MAX_CYCLES_BEFORE_RESTART  # already at limit

    run_count = 0

    def fake_run_once():
        nonlocal run_count
        run_count += 1
        # Don't increment _cycle here — the gate fires BEFORE run_once

    with patch.object(obs, "run_once", side_effect=fake_run_once), \
         patch("nighttrade.observatory.observer.time.sleep",
               side_effect=lambda *a: None), \
         patch("nighttrade.observatory.observer.time.monotonic",
               side_effect=iter([100.0, 100.0])):
        obs.run_forever(interval=10)

    # Loop should have broken before run_once ran
    assert run_count == 0
    assert obs._stop is True


def test_observer_stops_run_cleanly_on_rotation(tmp_path):
    """The rotation path must call stop() (which writes bot_runs.stopped_ts).
    Otherwise mark_dangling_runs_crashed would catch it as crashed on
    the next start."""
    obs = _make_observer(tmp_path)
    obs._cycle = Observer._MAX_CYCLES_BEFORE_RESTART
    stop_called = []
    orig_stop = obs.stop

    def tracked_stop(status="stopped"):
        stop_called.append(status)
        orig_stop(status)

    with patch.object(obs, "stop", side_effect=tracked_stop), \
         patch.object(obs, "run_once", side_effect=lambda: None), \
         patch("nighttrade.observatory.observer.time.sleep",
               side_effect=lambda *a: None), \
         patch("nighttrade.observatory.observer.time.monotonic",
               side_effect=iter([100.0, 100.0])):
        obs.run_forever(interval=10)

    assert len(stop_called) >= 1
    # The status string should NOT be "crashed" — clean rotation
    assert stop_called[0] != "crashed"
