"""The continuous Market Safety Observer.

Runs forever (until Ctrl+C). Each cycle it, for every healthy watchlist
symbol: fetches data, runs the full analysis pipeline, scores market safety,
records a prediction, steps the paper-trading simulation, and evaluates older
predictions against what actually happened. Everything is written to the
SQLite database and the log file.

It is built to survive: a per-cycle exception is logged and the loop
continues; signals trigger a graceful shutdown; all state lives in the
database, so a restart resumes cleanly (open paper positions are reloaded).

This is observation and paper simulation only — no real orders, ever.
"""

from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..config.schema import AppConfig
from ..cross_section import compute_factors, rank_universe
from ..gates import (
    CalibrationGate,
    ConfidenceCalibrator,
    MetaGate,
    MetaModel,
    RegimeGate,
)
from ..market_hours import is_market_open
from ..models import Action, Side
from ..pipeline import AnalysisPipeline
from ..risk import RiskEngine, position_size
from ..runtime import add_file_logging, get_logger
from ..watchlist import WatchlistScreener, extract_metrics
from .alerts import AlertManager, Alert, LEVEL_CRITICAL, build_condition_alerts
from .daily_report import write_daily_report
from .database import ObservatoryDB
from .feed import LiveMockFeed
from .metrics import roll_up_day
from .prediction_tracker import (
    HORIZONS,
    build_prediction_memory,
    evaluate_prediction,
)

# A prediction is evaluated exactly once — when its longest horizon has fully
# elapsed in wall-clock time. Re-evaluating sooner only re-counts the same
# prediction and, at S&P-500 scale, is quadratically expensive.
_MAX_HORIZON_MINUTES = max(HORIZONS.values())
from .readiness import ReadinessInputs, compute_readiness
from .safety_score import SafetyInputs, aggregate_safety, compute_safety_score

_log = get_logger("observatory.observer")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOG_FILE = _REPO_ROOT / "logs" / "nighttrade.log"
_OBSERVER_REPORTS = _REPO_ROOT / "reports" / "observer"
_NOW_PATH = _REPO_ROOT / "data" / "now.json"

# Idle-time ML training: when the market is closed the observer retrains the
# model on real data instead of sitting idle.
_MODEL_PATH = _REPO_ROOT / "artifacts" / "model.pkl"
_META_MODEL_PATH = _REPO_ROOT / "artifacts" / "meta_model.pkl"
_MODEL_RETRAIN_HOURS = 12.0   # retrain only when the model is older than this
_TRAIN_SYMBOL_SAMPLE = 120    # symbols sampled to build the training set

# The 10 steps of one observation cycle (for the dashboard "Now" panel).
CYCLE_STEPS = [
    "Fetching market data", "Validating liquidity",
    "Running technical analysis", "Running orderbook analysis",
    "Running macro/regime analysis", "Creating prediction",
    "Simulating trade", "Updating outcomes", "Saving metrics",
    "Waiting for next cycle",
]


@dataclass
class CycleSummary:
    """A one-line-per-cycle summary of what the observer did."""

    cycle: int
    timestamp: str
    symbols_observed: int = 0
    tradeable: int = 0
    predictions_made: int = 0
    predictions_evaluated: int = 0
    open_trades: int = 0
    closed_this_cycle: int = 0
    global_score: float = 50.0
    global_status: str = "WAIT"
    global_condition: str = "MIXED"
    equity: float = 0.0
    drawdown_pct: float = 0.0
    recent_accuracy: Optional[float] = None
    ranking_longs: int = 0
    ranking_shorts: int = 0
    alerts: List[str] = field(default_factory=list)


class Observer:
    """The continuous observatory engine."""

    def __init__(
        self,
        config: AppConfig,
        watchlist_config,
        db: Optional[ObservatoryDB] = None,
        feed: Optional[LiveMockFeed] = None,
        model=None,
        learning_session=None,
    ) -> None:
        self.config = config
        self.watchlist_config = watchlist_config
        self.db = db or ObservatoryDB()
        self.feed = feed or LiveMockFeed()
        self.pipeline = AnalysisPipeline(config, model)
        self.screener = WatchlistScreener(watchlist_config)
        # Phase 2 — the regime gate. Reward:risk = target/stop volatility mults.
        reward_risk = (config.fusion.target_vol_mult
                       / max(config.fusion.stop_vol_mult, 1e-9))
        self.regime_gate = RegimeGate(reward_risk,
                                      config.gates.regime_min_samples)
        # Phase 3 — the confidence-calibration gate. The calibrator is refit
        # from evaluated history each cycle; the gate reads it live.
        self.calibrator = ConfidenceCalibrator(
            config.gates.calibration_min_samples)
        self.calibration_gate = CalibrationGate(
            self.calibrator, config.gates.calibration_floor)
        # Phase 4 — the meta-labelling gate. Loads a saved meta-model if one
        # exists; (re)trained during market-closed downtime.
        self.meta_model = MetaModel(config.runtime.random_seed)
        if _META_MODEL_PATH.exists():
            try:
                self.meta_model = MetaModel.load(_META_MODEL_PATH)
            except Exception:  # noqa: BLE001 - a bad pickle must not block startup
                pass
        self.meta_gate = MetaGate(self.meta_model,
                                  config.gates.meta_min_probability)
        self.alerts = AlertManager(
            db=self.db, allow_network=config.runtime.allow_network)
        # Optional 30-day learning session (set by `nighttrade learn`).
        self.learning_session = learning_session

        self._run_id: Optional[int] = None
        self._cycle = 0
        self._stop = False
        self._interval = 300
        self._last_day: Optional[str] = None
        self._starting_cash = config.paper.starting_cash
        # symbol -> open paper position {trade_id, qty, entry, stop, target}
        self._open: Dict[str, Dict[str, float]] = {}
        self._risk = RiskEngine(config.risk, self._starting_cash)
        # Ported from daytrade QA-HIGH-2: seed peak from DB so drawdown
        # reports survive restart honestly.
        try:
            historic_peak = float(self.db.historical_peak_equity())
        except (AttributeError, TypeError, ValueError, Exception):  # noqa: BLE001
            historic_peak = 0.0
        self._peak_equity = max(self._starting_cash, historic_peak)

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Begin a run: recover from any prior crash, register this run."""
        add_file_logging(str(_LOG_FILE))
        _OBSERVER_REPORTS.mkdir(parents=True, exist_ok=True)
        crashed = self.db.mark_dangling_runs_crashed()
        if crashed:
            _log.warning("recovered %d crashed/abandoned prior run(s)", crashed)
        self._run_id = self.db.start_bot_run(pid=os.getpid())
        self._reload_open_positions()
        _log.info("observer run #%d started (pid=%d), %d open position(s) reloaded",
                  self._run_id, os.getpid(), len(self._open))

    def stop(self, status: str = "stopped") -> None:
        if self._run_id is not None:
            self.db.stop_bot_run(self._run_id, status)
        _log.info("observer run #%d %s after %d cycle(s)",
                  self._run_id, status, self._cycle)

    def _reload_open_positions(self) -> None:
        """Restart-safety: re-adopt paper positions left open by a prior run."""
        for trade in self.db.open_paper_trades():
            self._open[trade["symbol"]] = {
                "trade_id": trade["id"], "qty": trade["quantity"],
                "entry": trade["entry_price"], "stop": trade["stop"],
                "target": trade["target"],
            }

    # -- the cycle -----------------------------------------------------------

    def run_once(self, now: Optional[datetime] = None) -> CycleSummary:
        """Execute exactly one observation cycle and return its summary."""
        now = now or datetime.now(timezone.utc)
        self._cycle += 1
        self._errors_this_cycle = 0
        self._cycle_factors = []  # cross-sectional factors, filled per symbol

        # A live (real-data) feed only has a market when the market is open.
        # When it is closed the observer still observes — safety, regimes,
        # ranking — but does NOT create predictions it cannot honestly check.
        feed_is_live = getattr(self.feed, "respects_market_hours", False)
        self._market_open = (not feed_is_live) or is_market_open(now)
        if feed_is_live and not self._market_open:
            self._activity("market closed — observing the last session",
                           "no new predictions until the open", level="info")
        summary = CycleSummary(cycle=self._cycle, timestamp=now.isoformat())
        self._set_now("Fetching market data", "", now)
        self._activity(f"cycle {self._cycle} started", level="info")

        outcomes = self.db.outcomes(limit=500)
        memory = build_prediction_memory(outcomes)
        self._fit_calibrator(outcomes)  # Phase 3 — refit on evaluated history
        recent_accuracy = memory.overall_accuracy if memory.total >= 10 else None
        summary.recent_accuracy = recent_accuracy

        equity = self._equity(now)
        self._risk.observe_equity(now, equity)

        assessments = []
        illiquid: List[str] = []
        for symbol in self.watchlist_config.symbols:
            self._set_now("Running technical analysis", symbol, now)
            try:
                assessment = self._observe_symbol(symbol, now, memory,
                                                  recent_accuracy, equity)
            except Exception as exc:  # noqa: BLE001 - one symbol must not kill the cycle
                self.db.insert_error(f"observe:{symbol}", repr(exc))
                self._errors_this_cycle += 1
                _log.exception("error observing %s", symbol)
                continue
            summary.symbols_observed += 1
            if assessment is not None:
                assessments.append(assessment)
                summary.tradeable += 1
                if assessment.condition == "ILLIQUID":
                    illiquid.append(symbol)
            if self._market_open:
                summary.predictions_made += 1

        # Cross-sectional ranking — rank the whole universe relative to itself.
        self._set_now("Ranking the universe", "", now)
        self._rank_cross_section(now, summary)

        # Market-closed downtime is not wasted — retrain the ML model on the
        # real data the feed has accumulated.
        if not self._market_open:
            self._train_model_idle(now)

        # Evaluate matured predictions against reality.
        self._set_now("Updating outcomes", "", now)
        summary.predictions_evaluated = self._evaluate_predictions(now)
        if summary.predictions_evaluated:
            self._activity(f"{summary.predictions_evaluated} prediction(s) "
                           "evaluated against reality", level="info")

        # Manage open paper positions (stop / target exits).
        summary.closed_this_cycle = self._manage_positions(now)
        summary.open_trades = len(self._open)

        equity = self._equity(now)
        summary.equity = round(equity, 2)
        self._peak_equity = max(self._peak_equity, equity)
        drawdown = (self._peak_equity - equity) / self._peak_equity \
            if self._peak_equity > 0 else 0.0
        summary.drawdown_pct = round(drawdown, 4)

        # Global safety score.
        self._set_now("Saving metrics", "", now)
        global_assessment = aggregate_safety(assessments)
        summary.global_score = global_assessment.score
        summary.global_status = global_assessment.status
        summary.global_condition = global_assessment.condition
        self.db.insert_safety_score(
            ts=now.isoformat(),
            score=global_assessment.score, status=global_assessment.status,
            condition=global_assessment.condition,
            reasons=global_assessment.reasons,
            breakdown=global_assessment.breakdown,
            equity=round(equity, 2), drawdown_pct=round(drawdown, 4))
        # Per-cycle regime record (drives the regime timeline).
        day_no = (self.learning_session.day_number(now)
                  if self.learning_session else 0)
        self.db.insert_regime_period(
            ts=now.isoformat(), day_number=day_no,
            condition=global_assessment.condition,
            regime=global_assessment.condition.lower(),
            safety_score=global_assessment.score)

        # Alerts.
        alerts = build_condition_alerts(
            global_condition=global_assessment.condition,
            illiquid_symbols=illiquid, paper_drawdown_pct=drawdown,
            max_drawdown_pct=self.config.risk.max_daily_loss_pct,
            recent_accuracy=recent_accuracy, now=now)
        for alert in alerts:
            if self.alerts.emit(alert):
                summary.alerts.append(f"{alert.kind}: {alert.message}")
                self.db.insert_alert(alert.level, alert.kind, alert.message)
                self._activity(f"alert: {alert.message}", level=alert.level)

        # Learning-session bookkeeping: progress, readiness, day rollover.
        if self.learning_session is not None:
            self._learning_cycle(now, summary, drawdown)

        # Heartbeat + per-cycle report artifact.
        if self._run_id is not None:
            self.db.heartbeat(self._run_id, self._cycle)
        self._write_cycle_report(summary)
        self._set_now("Waiting for next cycle", "", now, done=True)
        _log.info("cycle %d: score=%.0f %s/%s | %d tradeable | equity=%.0f "
                  "dd=%.1f%%", summary.cycle, summary.global_score,
                  summary.global_status, summary.global_condition,
                  summary.tradeable, summary.equity, summary.drawdown_pct * 100)
        return summary

    def _observe_symbol(self, symbol: str, now: datetime, memory,
                        recent_accuracy: Optional[float], equity: float):
        """Observe one symbol: analyse, score, record, maybe paper-trade."""
        candles = self.feed.candles_at(symbol, now, n_bars=240)
        orderbook = self.feed.orderbook_at(symbol, now)
        tick = self.feed.tick_at(symbol, now)
        price = candles[-1].close

        result = self.pipeline.analyze(candles, orderbook, reference_price=price)
        tech, micro, macro = result.technical, result.microstructure, result.macro
        decision = result.decision

        # --- watchlist health screening ---
        metrics = extract_metrics(symbol, tick, orderbook, candles)
        screening = self.screener.screen_one(symbol, tick, orderbook, candles)

        liquidity_notional = (orderbook.notional_depth("bid")
                              + orderbook.notional_depth("ask"))
        panic = (macro.regime_label in ("panic", "war", "credit_crisis")
                 or result.kill_switch.macro_triggered)
        spread_bps = micro.spread_bps or 0.0
        slippage_bps = self.config.risk.base_slippage_bps + spread_bps * 0.5
        trend_strength = min(1.2, abs(tech.trend_slope or 0.0) / 0.002)
        sym_accuracy = None
        if symbol in memory.by_symbol and memory.by_symbol[symbol].samples >= 5:
            sym_accuracy = memory.by_symbol[symbol].accuracy

        safety = compute_safety_score(SafetyInputs(
            trend_strength=trend_strength,
            volatility=tech.volatility or 0.0,
            liquidity_notional=liquidity_notional,
            spread_bps=spread_bps,
            imbalance=micro.imbalance,
            chop=micro.chop_zone,
            slippage_estimate_bps=slippage_bps,
            panic=panic,
            recent_accuracy=sym_accuracy if sym_accuracy is not None
            else recent_accuracy,
            paper_drawdown_pct=0.0,
            prediction_reliability=recent_accuracy,
        ))

        # --- persist snapshot, prediction, symbol health ---
        # Timestamps use the OBSERVATION time so prediction outcomes can be
        # evaluated against the feed at exactly predicted_ts + horizon.
        ts = now.isoformat()
        self.db.insert_snapshot(
            ts=ts, symbol=symbol, price=price, rsi=tech.rsi, macd=tech.macd,
            volatility=tech.volatility, trend_slope=tech.trend_slope,
            spread_bps=spread_bps, imbalance=micro.imbalance,
            chop=int(micro.chop_zone), liquidity_notional=liquidity_notional,
            regime=micro.regime.value)

        # A prediction is only recorded while the market is open — one made
        # against a frozen, closed-market tape cannot be honestly evaluated.
        prediction_id = None
        if self._market_open:
            prediction_id = self.db.insert_prediction(
                ts=ts, symbol=symbol, direction=decision.action.value,
                confidence=decision.confidence, entry=decision.entry,
                stop=decision.stop, target=decision.target,
                market_condition=safety.condition,
                fused_score=decision.fused_score, reasons=decision.reasoning)

        status = self._symbol_status(safety, screening, decision)
        self.db.insert_symbol_health(
            ts=ts, symbol=symbol, price=price, volume_24h=metrics.volume_24h_usd,
            spread_bps=spread_bps, book_notional=liquidity_notional,
            healthy=int(screening.approved), rejections=screening.rejections,
            recent_accuracy=sym_accuracy, safety_score=safety.score,
            status=status)

        # --- activity feed ---
        if not screening.approved:
            reason = screening.rejections[0] if screening.rejections else "filtered"
            self._activity(f"skipped {symbol}", reason)
        elif self._market_open and decision.action.value != "hold":
            self._activity(f"prediction created for {symbol}",
                           f"{decision.action.value.upper()} "
                           f"conf {decision.confidence:.0%}")
        else:
            self._activity(f"scanning {symbol}", f"condition {safety.condition}")

        # --- paper-trading simulation step (entry only; exits in _manage) ---
        if self._market_open and prediction_id is not None:
            # Strategy gates: regime (Phase 2) and confidence calibration
            # (Phase 3). The first gate to object blocks the entry.
            checks = (
                ("regime", self.regime_gate.evaluate(safety.condition, memory)),
                ("calibration",
                 self.calibration_gate.evaluate(decision.confidence)),
                ("meta", self.meta_gate.evaluate(candles, self.config)),
            )
            blocked = next(((name, g) for name, g in checks
                            if not g.allowed), None)
            if blocked is not None and decision.action is not Action.HOLD:
                name, gate = blocked
                self.db.insert_gate_event(symbol=symbol, gate=name,
                                          allowed=False, reason=gate.reason)
                self._activity(f"{name} gate blocked {symbol}", gate.reason)
            else:
                if decision.action is not Action.HOLD:
                    self.db.insert_gate_event(
                        symbol=symbol, gate="all", allowed=True,
                        reason="cleared regime + calibration + meta gates")
                self._maybe_open_position(symbol, decision, screening, price,
                                          liquidity_notional, equity, now,
                                          prediction_id)

        # --- collect this stock's factors for the cross-sectional ranking ---
        ml_score = (result.ml.score if self.pipeline.model is not None
                    else None)
        snapshot = compute_factors(symbol, candles, self.config.cross_section,
                                   ml_score)
        if snapshot is not None:
            self._cycle_factors.append(snapshot)
        return safety

    def _evaluate_predictions(self, now: datetime) -> int:
        """Score predictions against reality — each one exactly once.

        A prediction is evaluated only after its longest (4h) horizon has fully
        elapsed, then marked done so it is never reprocessed. This keeps the
        cost O(predictions that matured this cycle) instead of re-walking the
        whole backlog every cycle. A prediction the feed can no longer price is
        marked done too — it must not pile up or crash the cycle.
        """
        evaluated = 0
        matured_before = now - timedelta(minutes=_MAX_HORIZON_MINUTES)
        for prediction in self.db.unevaluated_predictions():
            try:
                pred_ts = datetime.fromisoformat(prediction["ts"])
            except (ValueError, TypeError, KeyError):
                self.db.mark_prediction_evaluated(prediction["id"])
                continue
            if pred_ts > matured_before:
                continue  # not fully matured yet — leave it for a later cycle
            try:
                outcome, _ = evaluate_prediction(prediction, self.feed, now)
            except Exception as exc:  # noqa: BLE001 - one symbol can't kill the cycle
                _log.warning("could not evaluate prediction %s (%s): %s",
                             prediction.get("id"), prediction.get("symbol"), exc)
                self.db.mark_prediction_evaluated(prediction["id"])
                continue
            if outcome is not None:
                # Ported from daytrade QA-HIGH-1: single transaction so
                # a crash between upsert and the evaluated flag does
                # NOT cause duplicate work on the next cycle.
                self.db.upsert_outcome_and_mark_evaluated(
                    prediction["id"], **outcome)
                evaluated += 1
            else:
                self.db.mark_prediction_evaluated(prediction["id"])
        return evaluated

    def _rank_cross_section(self, now: datetime, summary: CycleSummary) -> None:
        """Rank the whole universe relative to itself and persist a snapshot."""
        factors = getattr(self, "_cycle_factors", [])
        if len(factors) < 5:
            return  # too little of the universe to rank meaningfully
        try:
            ranked = rank_universe(factors, self.config.cross_section, now)
        except ValueError as exc:  # nothing cleared the liquidity gate
            _log.warning("cross-sectional ranking skipped: %s", exc)
            return

        def _row(s) -> Dict[str, object]:
            return {
                "symbol": s.symbol, "rank": s.rank,
                "percentile": round(s.percentile, 4),
                "composite_z": round(s.composite_z, 4),
                "basket": s.basket, "price": round(s.price, 2),
                "factors": {k: round(v, 3) for k, v in s.factor_z.items()},
            }

        payload = {
            "timestamp": now.isoformat(),
            "cycle": self._cycle,
            "weights": {k: round(v, 4) for k, v in ranked.weights.items()},
            "total": len(ranked.stocks),
            "long_count": len(ranked.long_basket),
            "short_count": len(ranked.short_basket),
            "excluded": len(ranked.excluded),
            "top": [_row(s) for s in ranked.top(40)],
            "bottom": [_row(s) for s in ranked.bottom(40)],
        }
        self.db.insert_ranking(now.isoformat(), payload)
        summary.ranking_longs = len(ranked.long_basket)
        summary.ranking_shorts = len(ranked.short_basket)
        self._activity(
            f"ranked {len(ranked.stocks)} stocks cross-sectionally",
            f"long {summary.ranking_longs} / short {summary.ranking_shorts}")

    def _fit_calibrator(self, outcomes: List[Dict]) -> None:
        """Refit the confidence calibrator from evaluated prediction history.

        Each evaluated prediction contributes a (stated confidence, was-correct)
        pair; isotonic regression turns those into a calibrated probability map.
        Cheap enough to redo every cycle.
        """
        confidences: List[float] = []
        correct: List[int] = []
        for row in outcomes:
            verdict = row.get("directionally_correct")
            confidence = row.get("confidence")
            if verdict is not None and confidence is not None:
                confidences.append(float(confidence))
                correct.append(int(verdict))
        self.calibrator.fit(confidences, correct)

    def _train_model_idle(self, now: datetime) -> None:
        """Use market-closed downtime to (re)train the ML model on real data.

        Builds a supervised dataset from the feed's accumulated candles across
        a sample of the universe, fits a fresh model, saves it, and loads it
        into the live pipeline. Throttled to retrain at most every
        ``_MODEL_RETRAIN_HOURS``. Any failure is logged, never fatal.
        """
        try:
            age_h = ((time.time() - _MODEL_PATH.stat().st_mtime) / 3600.0
                     if _MODEL_PATH.exists() else 1e9)
        except OSError:
            age_h = 1e9
        if age_h < _MODEL_RETRAIN_HOURS:
            return  # the model is still fresh — nothing to do

        self._set_now("Training the ML model (market closed)", "", now)
        self._activity("market closed — training the ML model on real data",
                        level="info")
        try:
            import pandas as pd

            from ..ml import PredictiveModel, build_dataset
            from ..ml.dataset import Dataset
            from ..models.enums import ModelKind

            symbols = list(self.watchlist_config.symbols)[:_TRAIN_SYMBOL_SAMPLE]
            x_parts: List = []
            y_parts: List = []
            candle_series: List = []   # reused for the meta-model below
            names: Optional[List[str]] = None
            for symbol in symbols:
                try:
                    candles = self.feed.candles_at(symbol, now, n_bars=400)
                    if len(candles) < 80:
                        continue
                    candle_series.append(candles)
                    ds = build_dataset(candles, self.config)
                    if len(ds) < 30:
                        continue
                    x_parts.append(ds.X)
                    y_parts.append(ds.y)
                    names = ds.feature_names
                except Exception:  # noqa: BLE001 - skip a bad symbol
                    continue
            if len(x_parts) < 5 or names is None:
                _log.warning("idle training skipped — not enough usable data")
                return

            dataset = Dataset(
                X=pd.concat(x_parts, ignore_index=True),
                y=pd.concat(y_parts, ignore_index=True),
                feature_names=names)
            model = PredictiveModel(ModelKind(self.config.ml.model_kind),
                                    self.config.runtime.random_seed)
            result = model.fit(dataset)
            _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            model.save(_MODEL_PATH)
            self.pipeline.model = model  # the live pipeline picks it up at once
            _log.info("idle training: %d samples, in-sample accuracy %.3f -> %s",
                      result.samples, result.accuracy, model.version)
            self._activity(
                f"ML model retrained on {result.samples:,} real samples",
                f"in-sample accuracy {result.accuracy:.0%} "
                f"({len(x_parts)} stocks)", level="info")

            # Phase 4 — also retrain the meta-model on triple-barrier labels.
            self._set_now("Training the meta-model (market closed)", "", now)
            self.meta_model.fit_from_candles(candle_series, self.config)
            if self.meta_model.is_trained:
                self.meta_model.save(_META_MODEL_PATH)
                _log.info("idle training: meta-model %s", self.meta_model.version)
                self._activity(
                    "meta-model retrained",
                    f"{self.meta_model.samples:,} triple-barrier samples",
                    level="info")
        except Exception as exc:  # noqa: BLE001 - training must never crash a cycle
            _log.warning("idle model training failed: %s", exc)

    # -- learning session ----------------------------------------------------

    def _learning_cycle(self, now: datetime, summary: "CycleSummary",
                        drawdown: float) -> None:
        """Per-cycle learning bookkeeping: progress, readiness, day rollover."""
        session = self.learning_session
        session.cycles_completed = self._cycle
        if session.session_id is not None:
            self.db.update_learning_session(session.session_id, self._cycle)

        counts = {
            "symbols_monitored": len(self.watchlist_config.symbols),
            "predictions_made": self.db.count("predictions"),
            "predictions_evaluated": self.db.count("prediction_outcomes"),
            "fake_trades": self.db.count("paper_trades"),
            "skipped_trades": sum(1 for h in self.db.latest_symbol_health()
                                  if h.get("status") != "GOOD PAPER CONDITIONS"),
        }
        status = "PAPER TRADING" if self._open else "OBSERVING"
        session.save_state(now, counts, status)

        readiness = self._compute_readiness(now, drawdown)
        self.db.insert_readiness(
            ts=now.isoformat(), score=readiness.score, level=readiness.level,
            capped=int(readiness.capped), day_number=readiness.day_number,
            breakdown=readiness.breakdown, blockers=readiness.blockers)

        # Day rollover: aggregate the previous day, write its report.
        today = now.date().isoformat()
        if self._last_day is None:
            self._last_day = today
        elif today != self._last_day:
            self._day_rollover(self._last_day, now)
            self._last_day = today

    def _compute_readiness(self, now: datetime, drawdown: float):
        """Build readiness inputs from the session + database and score them."""
        session = self.learning_session
        memory = build_prediction_memory(self.db.outcomes(limit=8000))
        regimes = {r.get("condition") for r in self.db.regime_periods(limit=8000)}
        accs = [g.accuracy for g in memory.by_condition.values() if g.samples >= 3]
        spread = (max(accs) - min(accs)) if len(accs) >= 2 else 0.0
        errors = self.db.recent_errors(limit=4000)
        api_failures = sum(1 for e in errors
                           if "api" in (e.get("context") or "").lower()
                           or "exchange" in (e.get("context") or "").lower())
        return compute_readiness(ReadinessInputs(
            day_number=session.day_number(now),
            target_days=session.target_days,
            predictions_evaluated=memory.total,
            uptime_pct=session.uptime_pct(now),
            max_drawdown_pct=drawdown * 100.0,
            overall_accuracy=memory.overall_accuracy,
            false_confidence_count=len(memory.false_confidence_warnings()),
            regimes_observed=len([r for r in regimes if r]),
            regime_accuracy_spread=spread,
            api_failures=api_failures))

    def _day_rollover(self, day_date: str, now: datetime) -> None:
        """Aggregate a completed day and write its daily report."""
        session = self.learning_session
        day_number = max(1, session.day_number(now) - 1)
        try:
            metric = roll_up_day(self.db, day_date, day_number,
                                 int(86_400 / session.interval_seconds))
            self.db.upsert_daily_metric(day_date, **metric)
            write_daily_report(self.db, day_date)
            self._activity(f"daily report generated for {day_date}",
                           f"day {day_number}", level="info")
            _log.info("day %d rolled up (%s): %s", day_number, day_date,
                      metric.get("status"))
            # Ported from daytrade QA-HIGH-3: prune old high-volume rows
            # + WAL checkpoint. Keeps the DB from growing forever.
            try:
                pruned = self.db.prune_old(days=30)
                total = sum(pruned.values())
                if total:
                    _log.info("pruned %d old rows: %s", total, pruned)
            except (AttributeError, Exception) as exc:  # noqa: BLE001
                _log.warning("prune_old failed: %s", exc)
        except Exception as exc:  # noqa: BLE001 - rollover must not crash the loop
            self.db.insert_error("day_rollover", repr(exc))

    def _activity(self, event: str, detail: str = "", level: str = "info") -> None:
        """Record one live-activity-feed event (best-effort)."""
        try:
            self.db.insert_activity(event, detail, level, self._cycle)
        except Exception:  # noqa: BLE001 - activity logging must never crash
            pass

    def _set_now(self, step: str, symbol: str, now: datetime,
                 done: bool = False) -> None:
        """Write the live 'what is it doing right now' state to data/now.json.

        Ported from daytrade QA-HIGH-4: atomic write via tempfile +
        os.replace so a dashboard reader never sees a truncated JSON.
        """
        try:
            next_cycle = (now + timedelta(seconds=self._interval)).isoformat() \
                if done else None
            _NOW_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({
                "cycle": self._cycle,
                "started_at": now.isoformat(),
                "current_step": step,
                "current_symbol": symbol,
                "next_cycle_at": next_cycle,
                "errors_this_cycle": getattr(self, "_errors_this_cycle", 0),
                "steps": CYCLE_STEPS,
            })
            tmp = _NOW_PATH.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, _NOW_PATH)
        except OSError:  # pragma: no cover
            pass

    # -- paper trading -------------------------------------------------------

    def _maybe_open_position(self, symbol: str, decision, screening, price: float,
                             liquidity_notional: float, equity: float,
                             now: datetime, prediction_id: int) -> None:
        if symbol in self._open or not screening.approved:
            return
        if decision.action is not Action.BUY or decision.kill_switch_active:
            return
        if not (decision.entry and decision.stop and decision.target):
            return
        permission = self._risk.evaluate_entry(
            equity, open_positions=len(self._open), bar_index=self._cycle)
        if not permission.allowed:
            return
        sizing = position_size(equity, decision.entry, decision.stop,
                               self.config.risk)
        if not sizing.is_tradeable:
            return
        trade_id = self.db.insert_paper_trade(
            symbol=symbol, side=Side.BUY.value, quantity=sizing.quantity,
            entry_price=decision.entry, stop=decision.stop,
            target=decision.target, fees=0.0, slippage=0.0, pnl=0.0)
        self._open[symbol] = {
            "trade_id": trade_id, "qty": sizing.quantity,
            "entry": decision.entry, "stop": decision.stop,
            "target": decision.target,
        }
        self._activity(f"paper trade opened: {symbol}",
                       f"qty {sizing.quantity:.4f} @ {decision.entry:.4f} (sim)")
        _log.info("paper-opened %s qty=%.6f entry=%.4f (sim)",
                  symbol, sizing.quantity, decision.entry)

    def _manage_positions(self, now: datetime) -> int:
        """Close any open paper position whose stop or target was reached."""
        closed = 0
        for symbol, pos in list(self._open.items()):
            price = self.feed.price_at(symbol, now)
            exit_price: Optional[float] = None
            if price <= pos["stop"]:
                exit_price = pos["stop"]
            elif price >= pos["target"]:
                exit_price = pos["target"]
            if exit_price is None:
                continue
            qty = pos["qty"]
            gross = (exit_price - pos["entry"]) * qty
            fee = (exit_price + pos["entry"]) * qty * \
                self.config.risk.fee_bps / 10_000.0
            pnl = gross - fee
            slippage = exit_price * 0.0004 * qty
            self.db.close_paper_trade(pos["trade_id"], exit_price=exit_price,
                                      pnl=pnl, fees=fee, slippage=slippage)
            self._risk.register_trade_close(pnl, self._cycle)
            del self._open[symbol]
            closed += 1
            _log.info("paper-closed %s exit=%.4f pnl=%.2f (sim)",
                      symbol, exit_price, pnl)
        return closed

    def _equity(self, now: datetime) -> float:
        """Simulated equity = cash + realised PnL + open unrealised PnL."""
        # Ported from daytrade QA-HIGH-6: SUM over the full table, not
        # a 500-row window. After 500 closed trades the windowed
        # approach silently under-reported PnL.
        try:
            realized = self.db.total_realised_pnl()
        except AttributeError:
            realized = sum(t["pnl"] or 0.0
                           for t in self.db.closed_paper_trades())
        unrealized = 0.0
        for symbol, pos in self._open.items():
            price = self.feed.price_at(symbol, now)
            unrealized += (price - pos["entry"]) * pos["qty"]
        return self._starting_cash + realized + unrealized

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _symbol_status(safety, screening, decision) -> str:
        """Map a symbol's state to a dashboard status label."""
        if not screening.approved:
            if any("liquidity" in r or "orderbook" in r
                   for r in screening.rejections):
                return "TOO ILLIQUID"
            return "WATCH ONLY"
        if safety.condition == "PANIC":
            return "PANIC"
        if safety.condition == "ILLIQUID":
            return "TOO ILLIQUID"
        if safety.condition == "CHOPPY":
            return "TOO CHOPPY"
        if decision.kill_switch_active:
            return "WATCH ONLY"
        if safety.score >= 65:
            return "GOOD PAPER CONDITIONS"
        return "WATCH ONLY"

    def _write_cycle_report(self, summary: CycleSummary) -> None:
        """Write the cycle summary to reports/observer/ (latest + run log)."""
        try:
            (_OBSERVER_REPORTS / "latest.json").write_text(
                json.dumps(asdict(summary), indent=2))
            run_log = _OBSERVER_REPORTS / f"run_{self._run_id}.jsonl"
            with run_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(summary)) + "\n")
        except OSError as exc:  # pragma: no cover - disk issues must not crash
            _log.warning("could not write cycle report: %s", exc)

    # -- the forever loop ----------------------------------------------------

    #: Ported from daytrade QA-CRIT-4 — backoff + abort thresholds for
    #: consecutive cycle failures.
    _BACKOFF_THRESHOLD = 3
    _BACKOFF_MAX_SECONDS = 1800
    _ABORT_THRESHOLD = 50

    def run_forever(self, interval: int = 300) -> None:
        """Run cycles every ``interval`` seconds.

        Stops on a signal, or — in a learning session — once the configured
        observation window (e.g. 30 days) has fully elapsed.

        Sustained failures trigger exponential backoff after
        ``_BACKOFF_THRESHOLD`` and abort the run at
        ``_ABORT_THRESHOLD`` — preventing a permafail loop from
        hammering the data feed.
        """
        self._install_signal_handlers()
        self._interval = interval
        self.start()
        consecutive_failures = 0
        try:
            while not self._stop:
                if (self.learning_session is not None
                        and self.learning_session.is_complete(
                            datetime.now(timezone.utc))):
                    _log.info("learning window complete — stopping observer")
                    self._learning_complete = True
                    break
                try:
                    self.run_once()
                    consecutive_failures = 0
                except Exception as exc:  # noqa: BLE001 - crash recovery
                    consecutive_failures += 1
                    self.db.insert_error("cycle", repr(exc))
                    _log.exception("cycle failed (%d in a row)",
                                   consecutive_failures)
                    self.alerts.emit(Alert(
                        LEVEL_CRITICAL, "crash",
                        f"observer cycle crashed: {exc!r}",
                        datetime.now(timezone.utc)))
                    if consecutive_failures >= self._ABORT_THRESHOLD:
                        _log.error(
                            "%d consecutive cycle failures — aborting; "
                            "watchdog can restart cleanly",
                            consecutive_failures)
                        self._stop = True
                        break
                extra_sleep = 0.0
                if consecutive_failures > self._BACKOFF_THRESHOLD:
                    n = consecutive_failures - self._BACKOFF_THRESHOLD
                    extra_sleep = min(
                        self._BACKOFF_MAX_SECONDS,
                        interval * (2 ** min(n, 10)),
                    )
                    _log.warning("backoff: extra %.0f s sleep "
                                 "(consecutive failures = %d)",
                                 extra_sleep, consecutive_failures)
                slept = 0.0
                total = interval + extra_sleep
                while slept < total and not self._stop:
                    time.sleep(min(1.0, total - slept))
                    slept += 1.0
        finally:
            final = "completed" if getattr(self, "_learning_complete", False) \
                else ("stopped" if self._stop else "crashed")
            if (self.learning_session is not None
                    and self.learning_session.session_id is not None):
                self.db.update_learning_session(
                    self.learning_session.session_id, self._cycle,
                    status="completed" if final == "completed" else "stopped")
            self.stop(final)
            self.db.close()

    def _install_signal_handlers(self) -> None:
        def _handler(signum, _frame):
            _log.info("signal %d received — shutting down gracefully", signum)
            self._stop = True
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except ValueError:  # pragma: no cover - not on main thread (tests)
                pass
