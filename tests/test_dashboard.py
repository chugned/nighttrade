"""Dashboard backend tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from nighttrade.config import WatchlistConfig, load_config
from nighttrade.dashboard import create_app
from nighttrade.observatory import LiveMockFeed, ObservatoryDB, Observer

_T0 = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


def _populated_db(tmp_path):
    db_path = tmp_path / "obs.db"
    obs = Observer(load_config(load_dotenv_file=False),
                   WatchlistConfig(symbols=["AAPL", "MSFT"]),
                   db=ObservatoryDB(db_path), feed=LiveMockFeed())
    obs.start()
    for k in range(3):
        obs.run_once(_T0 + timedelta(minutes=20 * k))
    obs.run_once(_T0 + timedelta(minutes=120))
    obs.stop()
    obs.db.close()
    return db_path


def test_dashboard_serves_index(tmp_path):
    client = TestClient(create_app(tmp_path / "empty.db"))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Learning Observatory" in resp.text


def test_dashboard_health_is_paper_only(tmp_path):
    client = TestClient(create_app(tmp_path / "empty.db"))
    body = client.get("/api/health").json()
    assert body["real_trading"] is False
    assert body["paper_only"] is True


def test_dashboard_overview_returns_data(tmp_path):
    client = TestClient(create_app(_populated_db(tmp_path)))
    body = client.get("/api/overview").json()
    assert "safety_score" in body
    assert body["symbols_observed"] == 2
    assert "status" in body and "condition" in body


def test_dashboard_symbols_endpoint(tmp_path):
    client = TestClient(create_app(_populated_db(tmp_path)))
    rows = client.get("/api/symbols").json()
    assert isinstance(rows, list) and len(rows) == 2
    assert {"symbol", "price", "trend", "safety_score", "status"} <= set(rows[0])


def test_dashboard_symbol_detail(tmp_path):
    client = TestClient(create_app(_populated_db(tmp_path)))
    body = client.get("/api/symbol/AAPL").json()
    assert body["symbol"] == "AAPL"
    assert len(body["series"]) > 0
    assert len(body["predictions"]) > 0


def test_dashboard_accuracy_paper_risk(tmp_path):
    client = TestClient(create_app(_populated_db(tmp_path)))
    for endpoint in ("/api/accuracy", "/api/paper", "/api/risk",
                     "/api/safety-history"):
        resp = client.get(endpoint)
        assert resp.status_code == 200
        assert resp.json() is not None


def test_dashboard_empty_db_does_not_crash(tmp_path):
    """The dashboard works against a brand-new, empty database."""
    client = TestClient(create_app(tmp_path / "fresh.db"))
    overview = client.get("/api/overview").json()
    assert overview["symbols_observed"] == 0
    assert client.get("/api/symbols").json() == []


# --- learning observatory endpoints ----------------------------------------

def test_dashboard_ranking_endpoint(tmp_path):
    db_path = tmp_path / "rank.db"
    obs = Observer(load_config(load_dotenv_file=False),
                   WatchlistConfig(symbols=["AAPL", "MSFT", "NVDA", "AMZN",
                                            "JPM", "XOM", "HD", "KO"]),
                   db=ObservatoryDB(db_path), feed=LiveMockFeed())
    obs.start()
    obs.run_once(_T0)
    obs.stop()
    obs.db.close()
    client = TestClient(create_app(db_path))
    body = client.get("/api/ranking").json()
    assert body["available"] is True
    assert body["total"] >= 5
    assert len(body["top"]) >= 1 and len(body["bottom"]) >= 1


def test_dashboard_ranking_endpoint_empty(tmp_path):
    """With no observer run, the ranking endpoint reports 'not available'."""
    client = TestClient(create_app(tmp_path / "empty.db"))
    body = client.get("/api/ranking").json()
    assert body["available"] is False


def test_dashboard_investments_endpoint(tmp_path):
    """The investments endpoint reports invested capital per stock, in euros."""
    client = TestClient(create_app(tmp_path / "empty.db"))
    body = client.get("/api/investments").json()
    assert body["total_invested"] == 0
    assert body["currency"] == "EUR"
    assert body["open"] == [] and body["closed"] == []
    assert "by_symbol" in body


def _learning_db(tmp_path):
    from nighttrade.observatory import LearningSession
    db_path = tmp_path / "learn.db"
    db = ObservatoryDB(db_path)
    session = LearningSession.resume_or_create(db, target_days=30,
                                               interval_seconds=300)
    # Anchor the session start a couple of days before "now" so the learning
    # window is always at least day 2, regardless of the wall clock.
    session.start = datetime.now(timezone.utc) - timedelta(days=2)
    obs = Observer(load_config(load_dotenv_file=False),
                   WatchlistConfig(symbols=["AAPL", "MSFT"]),
                   db=db, feed=LiveMockFeed(), learning_session=session)
    obs.start()
    for k in range(3):
        obs.run_once(_T0 + timedelta(hours=8 * k))
    obs.run_once(_T0 + timedelta(days=1, hours=2))
    obs.stop()
    obs.db.close()
    return db_path


def test_dashboard_learning_endpoints(tmp_path):
    client = TestClient(create_app(_learning_db(tmp_path)))
    for endpoint in ("/api/progress", "/api/regimes", "/api/calibration",
                     "/api/readiness", "/api/learning", "/api/activity",
                     "/api/status", "/api/daily-reports", "/api/predictions",
                     "/api/paper-trades"):
        resp = client.get(endpoint)
        assert resp.status_code == 200, endpoint
        assert resp.json() is not None


def test_dashboard_progress_format(tmp_path):
    client = TestClient(create_app(_learning_db(tmp_path)))
    body = client.get("/api/progress").json()
    assert body["target_days"] == 30
    assert body["current_day"] >= 1
    assert "current_phase" in body
    assert isinstance(body["day_timeline"], list)


def test_dashboard_readiness_capped_and_safe_language(tmp_path):
    client = TestClient(create_app(_learning_db(tmp_path)))
    body = client.get("/api/readiness").json()
    # Early in the window readiness must be capped at 60.
    assert body["score"] <= 60.0
    assert "invest" not in body["level"].lower()


def test_dashboard_status_now_panel(tmp_path):
    client = TestClient(create_app(_learning_db(tmp_path)))
    body = client.get("/api/status").json()
    assert "current_step" in body
    assert "cycle" in body


def test_dashboard_gates_endpoint(tmp_path):
    """The /api/gates endpoint surfaces the four strategy gates."""
    client = TestClient(create_app(tmp_path / "obs.db"))
    body = client.get("/api/gates").json()
    assert {g["key"] for g in body["gates"]} == {
        "time_stop", "regime", "calibration", "meta"}
    assert body["total_blocked"] == 0          # empty db — nothing blocked yet
    assert body["events"] == []
    assert "meta_min_probability" in body["thresholds"]


def test_dashboard_gates_records_decisions(tmp_path):
    """Recorded gate decisions show up in the endpoint, allowed and blocked."""
    db_path = tmp_path / "gates.db"
    db = ObservatoryDB(db_path)
    db.insert_gate_event(symbol="AAPL", gate="all", allowed=True,
                         reason="cleared regime + calibration + meta gates")
    db.insert_gate_event(symbol="MSFT", gate="regime", allowed=False,
                         reason="regime blocked")
    db.close()
    body = TestClient(create_app(db_path)).get("/api/gates").json()
    assert body["total_blocked"] == 1
    assert body["blocked_by_gate"]["regime"] == 1
    assert body["recent_allowed"] == 1
    assert len(body["events"]) == 2


def test_dashboard_health_denies_wallets_and_transfers(tmp_path):
    client = TestClient(create_app(tmp_path / "obs.db"))
    body = client.get("/api/health").json()
    assert body["real_trading"] is False
    assert body["wallets"] is False
    assert body["bank_transfers"] is False
