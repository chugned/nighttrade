"""Market Safety Observatory tests — database, feed, safety, observer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nighttrade.config import WatchlistConfig, load_config
from nighttrade.observatory import (
    LiveMockFeed,
    ObservatoryDB,
    Observer,
    aggregate_safety,
    build_prediction_memory,
    compute_safety_score,
    evaluate_prediction,
)
from nighttrade.observatory.daily_report import build_daily_report_markdown
from nighttrade.observatory.safety_score import SafetyInputs

_T0 = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


def _db(tmp_path) -> ObservatoryDB:
    return ObservatoryDB(tmp_path / "obs.db")


def _small_watchlist() -> WatchlistConfig:
    # Fewer symbols keeps observer tests fast.
    return WatchlistConfig(symbols=["AAPL", "MSFT", "NVDA"])


def _observer(tmp_path) -> Observer:
    return Observer(load_config(load_dotenv_file=False), _small_watchlist(),
                    db=_db(tmp_path), feed=LiveMockFeed())


def test_observer_produces_cross_sectional_ranking(tmp_path):
    """A cycle over a sufficiently large universe persists a ranking."""
    universe = WatchlistConfig(symbols=["AAPL", "MSFT", "NVDA", "AMZN",
                                        "JPM", "XOM", "HD", "KO"])
    db = _db(tmp_path)
    obs = Observer(load_config(load_dotenv_file=False), universe,
                   db=db, feed=LiveMockFeed())
    obs.start()
    obs.run_once(_T0)
    obs.stop()
    ranking = db.latest_ranking()
    db.close()
    assert ranking is not None
    assert ranking["total"] >= 5
    assert ranking["long_count"] >= 1 and ranking["short_count"] >= 1
    assert ranking["top"] and ranking["bottom"]
    # The ranking is sorted best-first.
    ranks = [row["rank"] for row in ranking["top"]]
    assert ranks == sorted(ranks)


# --- database --------------------------------------------------------------

def test_db_creates_schema(tmp_path):
    db = _db(tmp_path)
    for table in ("market_snapshots", "predictions", "prediction_outcomes",
                  "paper_trades", "safety_scores", "symbol_health",
                  "bot_runs", "errors"):
        assert db.count(table) == 0
    db.close()


def test_db_bot_run_lifecycle(tmp_path):
    db = _db(tmp_path)
    run_id = db.start_bot_run(pid=999)
    db.heartbeat(run_id, 5)
    assert db.current_bot_run()["cycles"] == 5
    db.stop_bot_run(run_id)
    assert db.current_bot_run()["status"] == "stopped"
    db.close()


def test_db_recovers_dangling_runs(tmp_path):
    db = _db(tmp_path)
    db.start_bot_run(pid=1)  # left 'running'
    crashed = db.mark_dangling_runs_crashed()
    assert crashed == 1
    assert db.current_bot_run()["status"] == "crashed"
    db.close()


def test_db_outcome_upsert(tmp_path):
    db = _db(tmp_path)
    pid = db.insert_prediction(symbol="AAPL", direction="buy", confidence=0.6)
    db.upsert_outcome(pid, symbol="AAPL", price_5m=100.0)
    db.upsert_outcome(pid, price_1h=104.0, directionally_correct=1)
    row = db.outcomes()[0]
    assert row["price_5m"] == 100.0 and row["price_1h"] == 104.0


# --- feed ------------------------------------------------------------------

def test_feed_is_deterministic():
    feed = LiveMockFeed()
    assert feed.price_at("AAPL", _T0) == feed.price_at("AAPL", _T0)


def test_feed_restart_safe_history():
    """Candles ending at a time are identical regardless of when queried."""
    a = LiveMockFeed().candles_at("MSFT", _T0, n_bars=100)
    b = LiveMockFeed().candles_at("MSFT", _T0, n_bars=100)
    assert [c.close for c in a] == [c.close for c in b]


def test_feed_prices_are_sane():
    feed = LiveMockFeed()
    price = feed.price_at("AAPL", _T0)
    # AAPL base price is ~234; the feed oscillates within a tight band.
    assert 150 < price < 350  # not exploded, not collapsed


# --- safety score ----------------------------------------------------------

def test_safety_score_calm_is_high():
    a = compute_safety_score(SafetyInputs(
        trend_strength=0.5, volatility=0.003, liquidity_notional=900_000,
        spread_bps=2.5, imbalance=0.05, chop=False,
        slippage_estimate_bps=3, panic=False, recent_accuracy=0.58))
    assert a.score >= 61
    assert a.status == "SAFE_TO_OBSERVE"


def test_safety_score_panic_is_unsafe():
    a = compute_safety_score(SafetyInputs(
        trend_strength=0.9, volatility=0.03, liquidity_notional=400_000,
        spread_bps=12, imbalance=-0.6, chop=False,
        slippage_estimate_bps=30, panic=True))
    assert a.score <= 20
    assert a.status == "UNSAFE" and a.condition == "PANIC"


def test_safety_score_illiquid_condition():
    a = compute_safety_score(SafetyInputs(
        trend_strength=0.4, volatility=0.008, liquidity_notional=80_000,
        spread_bps=28, imbalance=0.2, chop=False, slippage_estimate_bps=22,
        panic=False))
    assert a.condition == "ILLIQUID"


def test_aggregate_safety_is_pessimistic():
    """The global score is dragged toward the weakest symbol."""
    good = compute_safety_score(SafetyInputs(0.5, 0.003, 900_000, 2.5, 0.0,
                                             False, 3, False))
    bad = compute_safety_score(SafetyInputs(0.9, 0.03, 100_000, 30, -0.6,
                                            False, 35, True))
    agg = aggregate_safety([good, bad])
    assert agg.score < (good.score + bad.score) / 2
    assert agg.condition == "PANIC"  # worst condition wins


# --- prediction tracking ---------------------------------------------------

def test_prediction_not_evaluated_before_horizon():
    feed = LiveMockFeed()
    pred = {"id": 1, "ts": _T0.isoformat(), "symbol": "AAPL",
            "direction": "buy", "entry": 100.0, "stop": 99.0, "target": 102.0,
            "confidence": 0.6, "market_condition": "CALM"}
    outcome, full = evaluate_prediction(pred, feed, _T0 + timedelta(minutes=2))
    assert outcome is None and full is False


def test_prediction_evaluated_after_horizon():
    feed = LiveMockFeed()
    entry = feed.price_at("AAPL", _T0)
    pred = {"id": 1, "ts": _T0.isoformat(), "symbol": "AAPL",
            "direction": "buy", "entry": entry, "stop": entry * 0.97,
            "target": entry * 1.03, "confidence": 0.6,
            "market_condition": "CALM"}
    outcome, _ = evaluate_prediction(pred, feed, _T0 + timedelta(minutes=70))
    assert outcome is not None
    assert outcome["price_5m"] is not None
    assert outcome["directionally_correct"] in (0, 1)


def test_prediction_memory_detects_false_confidence():
    rows = [{"directionally_correct": 0, "confidence": 0.82,
             "market_condition": "CHOPPY", "symbol": "TSLA"}
            for _ in range(6)]
    memory = build_prediction_memory(rows)
    assert memory.false_confidence_warnings()


# --- observer --------------------------------------------------------------

def test_observer_runs_one_cycle(tmp_path):
    obs = _observer(tmp_path)
    obs.start()
    summary = obs.run_once(_T0)
    assert summary.cycle == 1
    assert summary.symbols_observed == 3
    assert 0 <= summary.global_score <= 100
    obs.stop()


def test_observer_stores_predictions(tmp_path):
    obs = _observer(tmp_path)
    obs.start()
    obs.run_once(_T0)
    assert obs.db.count("predictions") == 3
    assert obs.db.count("market_snapshots") == 3
    assert obs.db.count("safety_scores") == 1
    obs.stop()


def test_observer_evaluates_outcomes_later(tmp_path):
    obs = _observer(tmp_path)
    obs.start()
    obs.run_once(_T0)
    # No outcomes yet — the 4h horizon has not elapsed.
    assert obs.db.count("prediction_outcomes") == 0
    # A cycle 70 minutes later: still not matured, so still nothing evaluated.
    obs.run_once(_T0 + timedelta(minutes=70))
    assert obs.db.count("prediction_outcomes") == 0
    # A cycle past the 4h horizon evaluates the first cycle's predictions.
    obs.run_once(_T0 + timedelta(minutes=300))
    after_maturity = obs.db.count("prediction_outcomes")
    assert after_maturity >= 3
    # Each prediction is evaluated exactly once — a later cycle does not
    # reprocess it, so the outcome count only grows with newly-matured ones.
    obs.run_once(_T0 + timedelta(minutes=305))
    assert obs.db.count("prediction_outcomes") == after_maturity
    obs.stop()


def test_observer_restart_recovers(tmp_path):
    """A new observer marks the prior dangling run crashed and resumes."""
    db_path = tmp_path / "obs.db"
    obs1 = Observer(load_config(load_dotenv_file=False), _small_watchlist(),
                    db=ObservatoryDB(db_path), feed=LiveMockFeed())
    obs1.start()
    obs1.run_once(_T0)
    obs1.db.close()  # simulate a crash (no clean stop)

    obs2 = Observer(load_config(load_dotenv_file=False), _small_watchlist(),
                    db=ObservatoryDB(db_path), feed=LiveMockFeed())
    obs2.start()
    runs = [r for r in [obs2.db.current_bot_run()] if r]
    assert runs and runs[0]["status"] == "running"
    obs2.stop()


def test_daily_report_generates(tmp_path):
    obs = _observer(tmp_path)
    obs.start()
    obs.run_once(_T0)
    md = build_daily_report_markdown(obs.db, _T0.date().isoformat())
    obs.stop()
    assert "Daily Report" in md
    assert "Recommendation" in md
    # No financial-advice language.
    assert "you should buy" not in md.lower()
    assert "you should sell" not in md.lower()
