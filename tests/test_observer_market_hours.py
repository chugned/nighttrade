"""Pin the market-hours-aware run loop.

When the feed reports ``respects_market_hours=True`` (i.e. the live
production feed), ``run_forever`` must:

  * cycle normally during the REGULAR session,
  * run **exactly one** warm-up cycle in the 30 min before market open
    each trading day (so the model is ready at 09:30 ET),
  * sleep otherwise (overnight, weekends, US holidays),
  * keep heartbeating during sleep so mission control sees "intentionally
    idle" instead of "crashed".

When the feed reports ``respects_market_hours=False`` (mock / dev feed)
the loop runs every cycle as before — preserving every existing test
and the dev workflow.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from nighttrade.config import load_config
from nighttrade.config.schema import WatchlistConfig
from nighttrade.observatory import LiveMockFeed, ObservatoryDB, Observer


_ET = ZoneInfo("America/New_York")


def _utc_at_et(year: int, month: int, day: int, hour: int, minute: int = 0):
    """Build a UTC ``datetime`` for the given wall-clock time in US/Eastern."""
    return datetime(year, month, day, hour, minute, tzinfo=_ET).astimezone(timezone.utc)


def _live_observer(tmp_path):
    """Observer whose feed *declares* it respects market hours."""
    obs = Observer(
        load_config(load_dotenv_file=False),
        WatchlistConfig(symbols=["AAPL"]),
        db=ObservatoryDB(tmp_path / "obs.db"),
        feed=LiveMockFeed(),
    )
    obs.feed.respects_market_hours = True  # pretend we're the real feed
    return obs


def _mock_observer(tmp_path):
    """Observer whose feed bypasses market hours (LiveMockFeed default)."""
    return Observer(
        load_config(load_dotenv_file=False),
        WatchlistConfig(symbols=["AAPL"]),
        db=ObservatoryDB(tmp_path / "obs.db"),
        feed=LiveMockFeed(),
    )


# --------------------------------------------------------------------------- #
# _next_action — the pure decision helper                                      #
# --------------------------------------------------------------------------- #

def test_observe_during_regular_session(tmp_path):
    """Tuesday 10:00 ET is squarely inside the regular session → observe."""
    obs = _live_observer(tmp_path)
    action, sleep_s = obs._next_action(_utc_at_et(2026, 6, 9, 10, 0))
    assert action == "observe"
    assert sleep_s == 0


def test_sleep_overnight(tmp_path):
    """Tuesday 22:00 ET is closed; next open is Wed 09:30 ET, ~11h away."""
    obs = _live_observer(tmp_path)
    action, sleep_s = obs._next_action(_utc_at_et(2026, 6, 9, 22, 0))
    assert action == "sleep"
    assert sleep_s > 0


def test_warmup_in_pre_open_window(tmp_path):
    """Tuesday 09:15 ET — 15 min before open, inside the 30-min warm-up
    window. Warm-up hasn't run yet today → run it."""
    obs = _live_observer(tmp_path)
    action, _ = obs._next_action(_utc_at_et(2026, 6, 9, 9, 15))
    assert action == "warmup"


def test_warmup_only_once_per_trading_day(tmp_path):
    """Once today's warm-up has run, a second pass in the same window
    must sleep, not warm up again."""
    obs = _live_observer(tmp_path)
    now = _utc_at_et(2026, 6, 9, 9, 15)
    # Pretend we already ran warm-up for this open.
    obs._warmup_done_for = now.astimezone(_ET).date()
    action, _ = obs._next_action(now)
    assert action == "sleep"


def test_sleep_on_weekend(tmp_path):
    """Saturday noon ET — markets closed until Monday."""
    obs = _live_observer(tmp_path)
    action, sleep_s = obs._next_action(_utc_at_et(2026, 6, 13, 12, 0))
    assert action == "sleep"
    assert sleep_s > 0


def test_sleep_on_us_holiday(tmp_path):
    """2026-07-03 is a hardcoded holiday in market_hours._HOLIDAYS."""
    obs = _live_observer(tmp_path)
    action, _ = obs._next_action(_utc_at_et(2026, 7, 3, 10, 0))
    assert action == "sleep"


def test_mock_feed_always_observes(tmp_path):
    """When the feed does NOT respect market hours (LiveMockFeed default),
    the loop must keep cycling regardless of the clock — preserves every
    existing test and the dev workflow."""
    obs = _mock_observer(tmp_path)
    # Saturday — would be closed if the feed respected hours
    action, sleep_s = obs._next_action(_utc_at_et(2026, 6, 13, 12, 0))
    assert action == "observe"
    assert sleep_s == 0


def test_sleep_duration_capped_at_closed_sleep_s(tmp_path):
    """Far-from-open sleeps cap at ``_CLOSED_SLEEP_S`` so the loop wakes
    periodically to re-check (and so SIGTERM is honored within minutes)."""
    obs = _live_observer(tmp_path)
    # Saturday 00:00 ET — many hours from the next open.
    _, sleep_s = obs._next_action(_utc_at_et(2026, 6, 13, 0, 0))
    assert sleep_s <= obs._CLOSED_SLEEP_S


# --------------------------------------------------------------------------- #
# run_forever integration — does it actually skip run_once when closed?       #
# --------------------------------------------------------------------------- #

def test_run_forever_skips_run_once_when_closed(tmp_path):
    """During a closed session the outer loop must NOT call ``run_once``.
    This is the entire point of the feature — no data fetch, no ML inference,
    no per-symbol loop, just heartbeat + sleep."""
    obs = _live_observer(tmp_path)
    # Force the action to "sleep" with a tiny duration.
    with patch.object(obs, "_next_action", return_value=("sleep", 1)), \
         patch.object(obs, "run_once") as mock_run_once, \
         patch.object(obs.db, "heartbeat") as mock_hb, \
         patch("nighttrade.observatory.observer.time.sleep",
               side_effect=lambda *a: setattr(obs, "_stop", True)):
        obs.run_forever(interval=1)

    mock_run_once.assert_not_called()
    # Heartbeat should still have been called at least once during sleep.
    assert mock_hb.call_count >= 1


def test_run_forever_runs_warmup_then_marks_done(tmp_path):
    """When ``_next_action`` returns 'warmup', the loop must call
    ``run_once`` AND set ``_warmup_done_for`` to today's ET date so the
    next iteration won't re-warmup."""
    obs = _live_observer(tmp_path)

    warmup_now = _utc_at_et(2026, 6, 9, 9, 15)
    expected_et_date = warmup_now.astimezone(_ET).date()

    # First _next_action returns 'warmup', second returns 'sleep' to exit.
    actions = iter([("warmup", 0), ("sleep", 1)])

    def stop_after_sleep(*_args, **_kw):
        obs._stop = True

    with patch.object(obs, "_next_action", side_effect=lambda *_a: next(actions)), \
         patch.object(obs, "run_once") as mock_run_once, \
         patch.object(obs.db, "heartbeat"), \
         patch("nighttrade.observatory.observer.datetime") as mock_dt, \
         patch("nighttrade.observatory.observer.time.sleep",
               side_effect=stop_after_sleep), \
         patch("nighttrade.observatory.observer.time.monotonic",
               side_effect=[0.0, 0.0]):
        mock_dt.now.return_value = warmup_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        obs.run_forever(interval=1)

    mock_run_once.assert_called_once()
    assert obs._warmup_done_for == expected_et_date


def test_set_now_sleeping_writes_status(tmp_path, monkeypatch):
    """The sleep-status writer must produce a now.json whose current_step
    names the closed market and the next open, with sleeping=True."""
    import json as _json
    from nighttrade.observatory import observer as obs_mod

    now_path = tmp_path / "now.json"
    monkeypatch.setattr(obs_mod, "_NOW_PATH", now_path)

    obs = _live_observer(tmp_path)
    # Saturday night ET — closed, next open is Monday 09:30 ET.
    obs._set_now_sleeping(_utc_at_et(2026, 6, 13, 22, 0))

    payload = _json.loads(now_path.read_text())
    assert payload["sleeping"] is True
    assert "Sleeping" in payload["current_step"]
    assert "market closed" in payload["current_step"].lower()
