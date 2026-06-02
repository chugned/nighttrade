"""Dashboard data assembly.

Turns the observatory database into the JSON payloads the dashboard pages
consume. Every accessor is defensive — an empty or freshly-created database
yields sensible empty/default structures rather than errors, so the dashboard
works even before the observer has run.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import load_config
from ..observatory.database import DEFAULT_DB_PATH, ObservatoryDB
from ..observatory.metrics import (
    confidence_calibration,
    learning_metrics,
    regime_metrics,
)
from ..observatory.prediction_tracker import build_prediction_memory

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LATEST_CYCLE = _REPO_ROOT / "reports" / "observer" / "latest.json"
_NOW_PATH = _REPO_ROOT / "data" / "now.json"
_LEARNING_STATE = _REPO_ROOT / "data" / "learning_state.json"
_DAILY_DIR = _REPO_ROOT / "reports" / "daily"

# A heartbeat older than this means the observer is not considered "live".
_HEARTBEAT_STALE_SECONDS = 1200.0


def _starting_cash() -> float:
    """The configured paper starting capital (matches what the observer uses)."""
    try:
        return load_config(load_dotenv_file=False).paper.starting_cash
    except Exception:  # noqa: BLE001 - fall back to the schema default
        return 1_000.0


_STARTING_CASH = _starting_cash()


def _read_json(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            return {}
    return {}


def _json_field(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _trend_label(slope: Optional[float]) -> str:
    if slope is None:
        return "flat"
    if slope > 0.0004:
        return "up"
    if slope < -0.0004:
        return "down"
    return "flat"


def _drawdown(closed: List[Dict[str, Any]]) -> Dict[str, float]:
    """Equity curve + max drawdown from closed paper trades."""
    equity = _STARTING_CASH
    peak = equity
    max_dd = 0.0
    ordered = sorted(closed, key=lambda t: t.get("ts_close") or "")
    for trade in ordered:
        equity += trade.get("pnl") or 0.0
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    return {
        "equity": round(equity, 2),
        "peak": round(peak, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
    }


class DashboardData:
    """Read-only data accessor for the dashboard backend."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db = ObservatoryDB(db_path)

    # -- pages ---------------------------------------------------------------

    def overview(self) -> Dict[str, Any]:
        safety = self.db.latest_safety_score() or {}
        run = self.db.current_bot_run() or {}
        health = self.db.latest_symbol_health()
        closed = self.db.closed_paper_trades(limit=5000)
        memory = build_prediction_memory(self.db.outcomes(limit=5000))

        live = self._observer_live(run)
        cycle = self._latest_cycle()
        dd = _drawdown(closed)
        equity = cycle.get("equity", dd["equity"])

        ranked = sorted(health, key=lambda h: h.get("safety_score") or 0, reverse=True)
        return {
            "safety_score": safety.get("score", 50.0),
            "status": safety.get("status", "WAIT"),
            "condition": safety.get("condition", "MIXED"),
            "reasons": _json_field(safety.get("reasons")) or [],
            "bot_running": live,
            "bot_status": run.get("status", "never started"),
            "cycles": run.get("cycles", 0),
            "last_heartbeat": run.get("last_heartbeat_ts"),
            "equity": equity,
            "starting_cash": _STARTING_CASH,
            "drawdown_pct": cycle.get("drawdown_pct", dd["max_drawdown_pct"] / 100.0),
            "max_drawdown_pct": dd["max_drawdown_pct"],
            "symbols_observed": len(health),
            "best_symbols": [self._mini(h) for h in ranked[:3]],
            "worst_symbols": (
                [self._mini(h) for h in reversed(ranked[-3:])] if len(ranked) >= 3 else []
            ),
            "model_reliable": memory.is_reliable,
            "prediction_accuracy": round(memory.overall_accuracy * 100, 1),
            "updated": datetime.now(timezone.utc).isoformat(),
        }

    def symbols(self) -> List[Dict[str, Any]]:
        health = {h["symbol"]: h for h in self.db.latest_symbol_health()}
        snaps = {s["symbol"]: s for s in self.db.latest_snapshots()}
        preds = self._latest_prediction_per_symbol()
        rows: List[Dict[str, Any]] = []
        for symbol, h in sorted(health.items()):
            s = snaps.get(symbol, {})
            p = preds.get(symbol, {})
            rows.append(
                {
                    "symbol": symbol,
                    "price": s.get("price"),
                    "trend": _trend_label(s.get("trend_slope")),
                    "volatility": s.get("volatility"),
                    "liquidity": h.get("book_notional"),
                    "spread_bps": s.get("spread_bps"),
                    "imbalance": s.get("imbalance"),
                    "chop": bool(s.get("chop")),
                    "prediction": p.get("direction", "—"),
                    "confidence": p.get("confidence"),
                    "recent_accuracy": h.get("recent_accuracy"),
                    "safety_score": h.get("safety_score"),
                    "status": h.get("status", "WATCH ONLY"),
                }
            )
        return rows

    def symbol_detail(self, symbol: str) -> Dict[str, Any]:
        symbol = symbol.upper()
        snaps = self.db.snapshots_for(symbol, limit=240)
        preds = self.db.recent_predictions(limit=60, symbol=symbol)
        health = {h["symbol"]: h for h in self.db.latest_symbol_health()}
        closed = [t for t in self.db.closed_paper_trades(limit=2000) if t["symbol"] == symbol]
        open_trades = [t for t in self.db.open_paper_trades() if t["symbol"] == symbol]
        return {
            "symbol": symbol,
            "health": health.get(symbol, {}),
            "series": [
                {
                    "ts": s["ts"],
                    "price": s["price"],
                    "rsi": s["rsi"],
                    "macd": s["macd"],
                    "volatility": s["volatility"],
                    "trend_slope": s["trend_slope"],
                    "spread_bps": s["spread_bps"],
                    "imbalance": s["imbalance"],
                    "chop": bool(s["chop"]),
                }
                for s in snaps
            ],
            "predictions": [
                {
                    "ts": p["ts"],
                    "direction": p["direction"],
                    "confidence": p["confidence"],
                    "entry": p["entry"],
                    "stop": p["stop"],
                    "target": p["target"],
                    "condition": p["market_condition"],
                }
                for p in preds
            ],
            "paper_trades": {"open": open_trades, "closed": closed[:50]},
        }

    def accuracy(self) -> Dict[str, Any]:
        outcomes = self.db.outcomes(limit=5000)
        memory = build_prediction_memory(outcomes)
        return {
            "overall_accuracy": round(memory.overall_accuracy * 100, 1),
            "total_evaluated": memory.total,
            "reliable": memory.is_reliable,
            "should_stop": memory.should_stop_trading(),
            "by_symbol": {
                k: {"accuracy": round(g.accuracy * 100, 1), "samples": g.samples}
                for k, g in memory.by_symbol.items()
            },
            "by_condition": {
                k: {"accuracy": round(g.accuracy * 100, 1), "samples": g.samples}
                for k, g in memory.by_condition.items()
            },
            "confidence_calibration": {
                k: {
                    "accuracy": round(g.accuracy * 100, 1),
                    "stated_confidence": round(g.mean_confidence * 100, 1),
                    "samples": g.samples,
                }
                for k, g in memory.confidence_buckets.items()
            },
            "false_confidence_warnings": memory.false_confidence_warnings(),
            "best_regimes": memory.best_regimes(),
            "worst_regimes": memory.worst_regimes(),
        }

    def paper(self) -> Dict[str, Any]:
        closed = self.db.closed_paper_trades(limit=5000)
        open_trades = self.db.open_paper_trades()
        dd = _drawdown(closed)
        wins = [t for t in closed if (t.get("pnl") or 0) > 0]
        best = max(closed, key=lambda t: t.get("pnl") or 0, default=None)
        worst = min(closed, key=lambda t: t.get("pnl") or 0, default=None)

        # Enrich each open position with the latest market price and the
        # unrealized PnL — what would crystallize if it closed at this tick.
        prices = {s["symbol"]: s.get("price") for s in self.db.latest_snapshots()}
        enriched_open: List[Dict[str, Any]] = []
        total_invested = 0.0
        total_unrealized = 0.0
        for t in open_trades:
            qty = float(t.get("quantity") or 0.0)
            entry = float(t.get("entry_price") or 0.0)
            invested = qty * entry
            price_now = prices.get(t["symbol"])
            side = (t.get("side") or "").lower()
            unrealized: Optional[float] = None
            unrealized_pct: Optional[float] = None
            if price_now is not None and qty > 0 and entry > 0:
                diff = (float(price_now) - entry) * qty
                unrealized = -diff if side == "sell" else diff
                unrealized_pct = unrealized / invested * 100.0 if invested else None
                total_unrealized += unrealized
            total_invested += invested
            enriched_open.append(
                {
                    **t,
                    "invested": round(invested, 2),
                    "price_now": price_now,
                    "unrealized_pnl": (round(unrealized, 2) if unrealized is not None else None),
                    "unrealized_pnl_pct": (
                        round(unrealized_pct, 2) if unrealized_pct is not None else None
                    ),
                }
            )

        return {
            "equity": dd["equity"],
            "starting_cash": _STARTING_CASH,
            "total_pnl": round(dd["equity"] - _STARTING_CASH, 2),
            "max_drawdown_pct": dd["max_drawdown_pct"],
            "open_positions": enriched_open,
            "open_total_invested": round(total_invested, 2),
            "open_total_unrealized_pnl": round(total_unrealized, 2),
            "closed_trades": closed[:100],
            "closed_count": len(closed),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
            "total_fees": round(sum(t.get("fees") or 0 for t in closed), 2),
            "total_slippage": round(sum(t.get("slippage") or 0 for t in closed), 2),
            "best_trade": best,
            "worst_trade": worst,
        }

    def investments(self) -> Dict[str, Any]:
        """Simulated capital invested — per stock, when, and amount, in euros.

        Each paper position's invested amount is quantity x entry price. The
        platform is euro-denominated — the live feed converts US stock prices
        from USD to EUR — so these figures are already in euros.
        """

        def _enrich(t: Dict[str, Any]) -> Dict[str, Any]:
            qty = float(t.get("quantity") or 0.0)
            entry = float(t.get("entry_price") or 0.0)
            return {**t, "invested": round(qty * entry, 2)}

        open_e = [_enrich(t) for t in self.db.open_paper_trades()]
        closed_e = [_enrich(t) for t in self.db.closed_paper_trades(limit=5000)]
        total = round(sum(t["invested"] for t in open_e), 2)

        # Per-stock aggregate of what is currently invested.
        by_symbol: Dict[str, Dict[str, Any]] = {}
        for t in open_e:
            row = by_symbol.setdefault(
                t["symbol"],
                {"symbol": t["symbol"], "positions": 0, "quantity": 0.0, "invested": 0.0},
            )
            row["positions"] += 1
            row["quantity"] = round(row["quantity"] + float(t.get("quantity") or 0), 6)
            row["invested"] = round(row["invested"] + t["invested"], 2)

        return {
            "currency": "EUR",
            "total_invested": total,
            "open": open_e,
            "closed": closed_e[:100],
            "by_symbol": sorted(by_symbol.values(), key=lambda r: -r["invested"]),
        }

    def risk(self) -> Dict[str, Any]:
        health = self.db.latest_symbol_health()
        memory = build_prediction_memory(self.db.outcomes(limit=5000))
        hazardous = [
            h for h in health if h.get("status") in ("PANIC", "TOO ILLIQUID", "TOO CHOPPY")
        ]
        return {
            "errors": self.db.recent_errors(limit=40),
            "illiquid_symbols": [h["symbol"] for h in health if h.get("status") == "TOO ILLIQUID"],
            "panic_symbols": [h["symbol"] for h in health if h.get("status") == "PANIC"],
            "choppy_symbols": [h["symbol"] for h in health if h.get("status") == "TOO CHOPPY"],
            "skipped": [
                {
                    "symbol": h["symbol"],
                    "status": h["status"],
                    "reasons": _json_field(h.get("rejections")) or [],
                }
                for h in hazardous
            ],
            "false_confidence_warnings": memory.false_confidence_warnings(),
            "model_unreliable": not memory.is_reliable,
            "should_stop_trading": memory.should_stop_trading(),
        }

    def safety_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        return [
            {"ts": s["ts"], "score": s["score"], "status": s["status"], "condition": s["condition"]}
            for s in self.db.safety_score_history(limit)
        ]

    def equity_history(self) -> Dict[str, Any]:
        """Accumulated paper-equity curve — the visual 'gain over time'."""
        curve = self.db.equity_curve(limit=3000)
        points = [
            {
                "ts": r["ts"],
                "equity": r["equity"],
                "gain": round((r["equity"] or _STARTING_CASH) - _STARTING_CASH, 2),
                "gain_pct": round(((r["equity"] or _STARTING_CASH) / _STARTING_CASH - 1) * 100, 3),
                "drawdown_pct": round((r.get("drawdown_pct") or 0.0) * 100, 2),
            }
            for r in curve
        ]
        current = curve[-1]["equity"] if curve else _STARTING_CASH
        peak = max((p["equity"] for p in points), default=_STARTING_CASH)
        return {
            "starting_cash": _STARTING_CASH,
            "current_equity": round(current, 2),
            "peak_equity": round(peak, 2),
            "total_gain": round(current - _STARTING_CASH, 2),
            "total_gain_pct": round((current / _STARTING_CASH - 1) * 100, 2),
            "points": points,
        }

    # -- learning observatory pages -----------------------------------------

    def progress(self) -> Dict[str, Any]:
        """30-day learning progress: day, phase, cycles, uptime, day timeline."""
        state = _read_json(_LEARNING_STATE)
        session = self.db.current_learning_session() or {}
        timeline = [
            {
                "day": m.get("day_number"),
                "date": m.get("day_date"),
                "status": m.get("status", "yellow"),
                "uptime_pct": m.get("uptime_pct"),
                "accuracy": m.get("accuracy"),
                "cycles": m.get("cycles"),
            }
            for m in self.db.daily_metrics()
        ]
        return {
            "active": bool(state) and not state.get("complete", False),
            "current_day": state.get("current_day", 0),
            "target_days": state.get("target_days", session.get("target_days", 30)),
            "days_remaining": state.get("days_remaining"),
            "progress_pct": state.get("progress_pct", 0.0),
            "cycles_completed": state.get("cycles_completed", 0),
            "expected_cycles": state.get("expected_cycles", 0),
            "total_expected_cycles": state.get("total_expected_cycles", 0),
            "predictions_made": state.get("predictions_made", 0),
            "predictions_evaluated": state.get("predictions_evaluated", 0),
            "fake_trades": state.get("fake_trades", 0),
            "skipped_trades": state.get("skipped_trades", 0),
            "uptime_pct": state.get("uptime_pct", 0.0),
            "current_phase": state.get("current_phase", "not started"),
            "status": state.get("status", "NOT STARTED"),
            "symbols_monitored": state.get("symbols_monitored", 0),
            "day_timeline": timeline,
        }

    def ranking(self) -> Dict[str, Any]:
        """The latest cross-sectional ranking — long / short baskets."""
        payload = self.db.latest_ranking()
        if not payload:
            return {
                "available": False,
                "top": [],
                "bottom": [],
                "long_count": 0,
                "short_count": 0,
                "total": 0,
                "excluded": 0,
                "weights": {},
                "timestamp": None,
            }
        payload["available"] = True
        return payload

    def status(self) -> Dict[str, Any]:
        """The 'what is it doing right now' panel."""
        now = _read_json(_NOW_PATH)
        run = self.db.current_bot_run() or {}
        state = _read_json(_LEARNING_STATE)
        return {
            "bot_running": self._observer_live(run),
            "bot_status": run.get("status", "never started"),
            "cycle": now.get("cycle", run.get("cycles", 0)),
            "current_step": now.get("current_step", "idle"),
            "current_symbol": now.get("current_symbol", ""),
            "started_at": now.get("started_at"),
            "next_cycle_at": now.get("next_cycle_at"),
            "errors_this_cycle": now.get("errors_this_cycle", 0),
            "steps": now.get("steps", []),
            "phase": state.get("current_phase", "—"),
            "learning_status": state.get("status", "—"),
        }

    def regimes(self) -> Dict[str, Any]:
        return regime_metrics(self.db.outcomes(limit=8000), self.db.regime_periods(limit=3000))

    def calibration(self) -> Dict[str, Any]:
        return confidence_calibration(self.db.outcomes(limit=8000))

    def readiness(self) -> Dict[str, Any]:
        latest = self.db.latest_readiness() or {}
        return {
            "score": latest.get("score", 0.0),
            "level": latest.get("level", "NOT ENOUGH DATA"),
            "capped": bool(latest.get("capped")),
            "day_number": latest.get("day_number", 0),
            "breakdown": _json_field(latest.get("breakdown")) or {},
            "blockers": _json_field(latest.get("blockers")) or [],
            "history": [
                {"ts": r["ts"], "score": r["score"]} for r in self.db.readiness_history(200)
            ],
            "notice": "A high score never means 'safe to invest' — only "
            "'strong paper performance, still not guaranteed'.",
        }

    def learning(self) -> Dict[str, Any]:
        return learning_metrics(self.db)

    def activity(self, limit: int = 60) -> List[Dict[str, Any]]:
        return self.db.recent_activity(limit)

    def predictions(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.db.recent_predictions(limit=limit)

    def daily_reports(self) -> Dict[str, Any]:
        files = (
            sorted((p.name for p in _DAILY_DIR.glob("*.md")), reverse=True)
            if _DAILY_DIR.exists()
            else []
        )
        return {"metrics": self.db.daily_metrics(), "report_files": files}

    def gates(self, limit: int = 80) -> Dict[str, Any]:
        """Strategy-gate activity — the four research-backed filters.

        Surfaces Phases 1-4 as they run live: how often each gate blocked an
        entry, the most recent decisions, and the configured thresholds.
        """
        events = self.db.recent_gate_events(limit=limit)
        blocked = self.db.gate_block_counts()
        try:
            cfg = load_config(load_dotenv_file=False).gates
            thresholds = {
                "regime_min_samples": cfg.regime_min_samples,
                "calibration_min_samples": cfg.calibration_min_samples,
                "calibration_floor": cfg.calibration_floor,
                "meta_min_probability": cfg.meta_min_probability,
            }
        except Exception:  # noqa: BLE001 - dashboard must stay up
            thresholds = {}
        recent_allowed = sum(1 for e in events if e.get("allowed"))
        return {
            "total_blocked": sum(blocked.values()),
            "blocked_by_gate": blocked,
            "recent_allowed": recent_allowed,
            "recent_total": len(events),
            "thresholds": thresholds,
            "events": events,
            "gates": [
                {
                    "key": "time_stop",
                    "name": "Time stop",
                    "phase": 1,
                    "kind": "structural",
                    "desc": "Force-closes a position after the time barrier "
                    "even if neither stop nor target is hit.",
                },
                {
                    "key": "regime",
                    "name": "Regime gate",
                    "phase": 2,
                    "kind": "adaptive",
                    "desc": "Blocks new entries in any market regime whose "
                    "measured accuracy is below its break-even win rate.",
                },
                {
                    "key": "calibration",
                    "name": "Calibration gate",
                    "phase": 3,
                    "kind": "adaptive",
                    "desc": "Admits an entry only when the isotonic-calibrated "
                    "win probability clears the floor — not raw confidence.",
                },
                {
                    "key": "meta",
                    "name": "Meta-label gate",
                    "phase": 4,
                    "kind": "model",
                    "desc": "A secondary model scores P(target before stop); "
                    "the low-precision entries are vetoed.",
                },
            ],
            "notice": "Gates only ever remove trades — they never invent one. "
            "A gate with zero blocks is gathering evidence, not idle.",
        }

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _mini(h: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "symbol": h.get("symbol"),
            "safety_score": h.get("safety_score"),
            "status": h.get("status"),
        }

    def _latest_prediction_per_symbol(self) -> Dict[str, Dict[str, Any]]:
        preds = self.db.recent_predictions(limit=400)
        out: Dict[str, Dict[str, Any]] = {}
        for p in preds:  # recent_predictions is newest-first
            out.setdefault(p["symbol"], p)
        return out

    def _observer_live(self, run: Dict[str, Any]) -> bool:
        if run.get("status") != "running":
            return False
        hb = run.get("last_heartbeat_ts")
        if not hb:
            return False
        try:
            delta = (datetime.now(timezone.utc) - datetime.fromisoformat(hb)).total_seconds()
        except (ValueError, TypeError):
            return False
        return delta < _HEARTBEAT_STALE_SECONDS

    @staticmethod
    def _latest_cycle() -> Dict[str, Any]:
        if _LATEST_CYCLE.exists():
            try:
                return json.loads(_LATEST_CYCLE.read_text())
            except (ValueError, OSError):
                return {}
        return {}

    def close(self) -> None:
        self.db.close()
