"""Mirror of daytrade's test: nighttrade's Observer.run_forever now treats
``interval`` as cycle period (was: extra sleep after work).

Cycles overran by exactly the work time previously — that's why the
nighttrade cycle was ~480s with a configured 300s interval and ~120s
of fetch + analysis work. Fix lets the bot hit its target cadence."""

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


def test_short_work_sleeps_remainder_to_hit_interval(tmp_path):
    obs = _make_observer(tmp_path)
    monot = iter([100.0, 102.0])  # work took 2s
    sleeps = []

    def fake_run_once(): pass

    def fake_sleep(s):
        sleeps.append(s)
        obs._stop = True

    with patch("nighttrade.observatory.observer.time.monotonic", side_effect=monot), \
         patch("nighttrade.observatory.observer.time.sleep", side_effect=fake_sleep), \
         patch.object(obs, "run_once", side_effect=fake_run_once):
        obs.run_forever(interval=10)

    # With interval=10, work=2 → remaining=8. First slice is min(1.0, 8) = 1.0.
    assert sleeps == [pytest.approx(1.0)]


def test_long_work_does_not_sleep(tmp_path):
    obs = _make_observer(tmp_path)
    monot = iter([100.0, 115.0])  # work took 15s
    sleeps = []
    def fake_run_once(): obs._stop = True

    with patch("nighttrade.observatory.observer.time.monotonic", side_effect=monot), \
         patch("nighttrade.observatory.observer.time.sleep",
               side_effect=lambda s: sleeps.append(s)), \
         patch.object(obs, "run_once", side_effect=fake_run_once):
        obs.run_forever(interval=10)

    assert sum(sleeps) == 0.0
