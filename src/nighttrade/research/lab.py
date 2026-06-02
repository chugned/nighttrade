"""The research lab — backtest + purged walk-forward over real history.

Given a set of symbols, the lab loads years of real daily bars, runs the
backtester and a *purged* walk-forward validation on each, and aggregates the
result into one honest :class:`ResearchReport` with a baseline and a verdict.

The verdict is deliberately harsh. "NO EDGE" is the default. Implausibly-high
accuracy is flagged as suspected leakage, not celebrated. A win rate is not
trusted until 30+ trades have accumulated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..backtest import Backtester
from ..config.schema import AppConfig
from ..models import OHLCV, BacktestMetrics, WalkForwardReport
from ..runtime import get_logger
from ..validation import walk_forward_validate
from .history import HistoryCache

_log = get_logger("research.lab")

# A win rate is not statistically meaningful below this many trades.
_MIN_TRADES_FOR_VERDICT = 30
# Below this many bars a symbol is skipped — too little history to judge.
_MIN_BARS = 150


@dataclass(frozen=True)
class SymbolResult:
    """One symbol's backtest + walk-forward result."""

    symbol: str
    bars: int
    backtest: BacktestMetrics
    walkforward: WalkForwardReport

    @property
    def wf_accuracy(self) -> float:
        return self.walkforward.mean_test_accuracy


@dataclass(frozen=True)
class ResearchReport:
    """The aggregated, honest baseline across all tested symbols."""

    symbols: List[SymbolResult] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    mean_return_pct: float = 0.0
    mean_win_rate: float = 0.0
    mean_sharpe: float = 0.0
    mean_wf_accuracy: float = 0.0
    mean_overfit_gap: float = 0.0
    total_trades: int = 0
    leakage_flags: int = 0
    verdict: str = "NO EDGE"
    notes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SweepPoint:
    """One (stop, reward:risk) grid point's in-sample backtest aggregate."""

    stop_mult: float
    reward_risk: float  # target_mult = stop_mult * reward_risk
    return_pct: float
    win_rate: float
    trades: int


@dataclass(frozen=True)
class SweepReport:
    """Result of an ATR stop/target multiplier sweep."""

    grid: List[SweepPoint] = field(default_factory=list)
    best: Optional[SweepPoint] = None
    baseline: Optional[SweepPoint] = None  # the current config, in-sample
    oos_return: float = 0.0  # best params, out-of-sample
    oos_win_rate: float = 0.0
    oos_trades: int = 0
    baseline_oos_return: float = 0.0  # current config, out-of-sample
    notes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class MetaReport:
    """Out-of-sample evaluation of the Phase 4 meta-model."""

    train_samples: int = 0
    test_samples: int = 0
    base_rate: float = 0.0  # P(win) over the held-out test set
    accuracy: float = 0.0  # meta-model test accuracy (p >= 0.5)
    precision: float = 0.0  # of accepted trades, the fraction that won
    coverage: float = 0.0  # fraction of test trades the meta-model accepts
    lift: float = 0.0  # precision - base_rate
    notes: List[str] = field(default_factory=list)


class ResearchLab:
    """Runs the measurement harness over a set of symbols."""

    def __init__(self, config: AppConfig, cache: Optional[HistoryCache] = None) -> None:
        self.config = config
        self.cache = cache or HistoryCache()

    def run(
        self,
        symbols: List[str],
        years: int = 3,
        candles_by_symbol: Optional[Dict[str, List[OHLCV]]] = None,
    ) -> ResearchReport:
        """Backtest + walk-forward every symbol; aggregate into a report.

        Args:
            candles_by_symbol: pre-supplied history (skips the cache/download).
        """
        results: List[SymbolResult] = []
        skipped: List[str] = []

        for symbol in symbols:
            candles = (candles_by_symbol or {}).get(symbol) or self.cache.get(symbol, years=years)
            if len(candles) < _MIN_BARS:
                skipped.append(symbol)
                continue
            try:
                backtest = Backtester(self.config).run(candles).metrics
                walkforward = walk_forward_validate(candles, self.config)
            except Exception as exc:  # noqa: BLE001 - one symbol can't kill the run
                _log.warning("research run failed for %s: %s", symbol, exc)
                skipped.append(symbol)
                continue
            results.append(SymbolResult(symbol, len(candles), backtest, walkforward))

        return self._aggregate(results, skipped)

    # -- internals -----------------------------------------------------------

    def _aggregate(self, results: List[SymbolResult], skipped: List[str]) -> ResearchReport:
        if not results:
            return ResearchReport(
                skipped=skipped,
                verdict="NO DATA",
                notes=["No symbol had enough real history to evaluate."],
            )

        def _mean(values: List[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        with_folds = [r for r in results if r.walkforward.n_folds > 0]
        total_trades = sum(r.backtest.total_trades for r in results)
        leakage = sum(1 for r in results if r.walkforward.leakage_suspected)

        report_kwargs = dict(
            symbols=results,
            skipped=skipped,
            mean_return_pct=_mean([r.backtest.total_return_pct for r in results]),
            mean_win_rate=_mean([r.backtest.win_rate for r in results]),
            mean_sharpe=_mean([r.backtest.sharpe_like for r in results]),
            mean_wf_accuracy=_mean([r.wf_accuracy for r in with_folds]),
            mean_overfit_gap=_mean([r.walkforward.mean_overfit_gap for r in with_folds]),
            total_trades=total_trades,
            leakage_flags=leakage,
        )
        verdict, notes = self._verdict(report_kwargs, with_folds)
        return ResearchReport(verdict=verdict, notes=notes, **report_kwargs)

    def _verdict(self, agg: dict, with_folds: List[SymbolResult]) -> tuple[str, List[str]]:
        """Decide an honest verdict. 'NO EDGE' is the default."""
        notes: List[str] = [
            "Backtests are optimistic — they omit competition, your own market "
            "impact, and the future. Treat every number as an upper bound.",
        ]
        wf_acc = agg["mean_wf_accuracy"]
        trades = agg["total_trades"]
        suspicious = self.config.walkforward.suspicious_accuracy

        if not with_folds:
            return "INSUFFICIENT DATA", notes + [
                "Not enough history for a walk-forward split — no edge claim " "can be made."
            ]
        if agg["leakage_flags"] or wf_acc >= suspicious:
            return "SUSPECTED LEAKAGE", notes + [
                f"Mean walk-forward accuracy {wf_acc:.1%} is implausibly high "
                "for noisy markets — almost certainly lookahead/leakage, not "
                "edge. Do NOT trust this."
            ]
        if trades < _MIN_TRADES_FOR_VERDICT:
            return "INSUFFICIENT EVIDENCE", notes + [
                f"Only {trades} backtested trades — a win rate means nothing "
                f"below ~{_MIN_TRADES_FOR_VERDICT}. Gather more before judging."
            ]
        if wf_acc < 0.50:
            return "NO EDGE", notes + [
                f"Walk-forward direction accuracy {wf_acc:.1%} is below a coin "
                "flip — the model has no predictive edge."
            ]
        if agg["mean_overfit_gap"] >= self.config.walkforward.overfit_gap_warn:
            return "OVERFIT", notes + [
                f"Train-vs-test gap {agg['mean_overfit_gap']:.1%} is large — "
                "the model memorizes the training window, it does not generalize."
            ]
        if wf_acc >= 0.52 and agg["mean_return_pct"] > 0:
            return "MARGINAL EDGE — UNPROVEN", notes + [
                f"A faint signal ({wf_acc:.1%} walk-forward accuracy, positive "
                "backtest return). Not proof — needs the Phase 1-4 gates and "
                "out-of-sample paper confirmation before it means anything."
            ]
        return "NO EDGE", notes + [
            f"Walk-forward accuracy {wf_acc:.1%} with no convincing return — "
            "no edge worth trading."
        ]

    # -- Phase 1: ATR stop/target multiplier sweep ---------------------------

    _STOP_GRID = (1.0, 1.5, 2.0, 2.5, 3.0)
    _RR_GRID = (1.0, 1.5, 2.0, 3.0)

    def _with_stops(self, stop_mult: float, target_mult: float) -> AppConfig:
        """A config copy with the ATR stop/target multipliers overridden."""
        fusion = self.config.fusion.model_copy(
            update={"stop_vol_mult": stop_mult, "target_vol_mult": target_mult}
        )
        return self.config.model_copy(update={"fusion": fusion})

    @staticmethod
    def _agg_backtest(cfg: AppConfig, series: List[List[OHLCV]]) -> tuple[float, float, int]:
        """Backtest every series with ``cfg``; return (mean return%, win%, trades)."""
        returns: List[float] = []
        win_rates: List[float] = []
        trades = 0
        for candles in series:
            try:
                m = Backtester(cfg).run(candles).metrics
            except Exception:  # noqa: BLE001
                continue
            returns.append(m.total_return_pct)
            if m.total_trades:
                win_rates.append(m.win_rate)
            trades += m.total_trades
        mean = lambda v: sum(v) / len(v) if v else 0.0  # noqa: E731
        return mean(returns), mean(win_rates), trades

    def sweep_stops(
        self,
        symbols: List[str],
        years: int = 3,
        candles_by_symbol: Optional[Dict[str, List[OHLCV]]] = None,
        split: float = 0.7,
    ) -> SweepReport:
        """Sweep ATR stop/target multipliers; pick the best, validate OOS.

        The grid is optimized on an in-sample window (``split`` fraction of
        each symbol's history); the winner is then measured on the held-out
        out-of-sample tail. A param that wins in-sample but collapses OOS is
        overfit — the report says so.
        """
        warm = self.config.backtest.warmup_bars + 11
        in_sample: List[List[OHLCV]] = []
        out_sample: List[List[OHLCV]] = []
        for symbol in symbols:
            candles = (candles_by_symbol or {}).get(symbol) or self.cache.get(symbol, years=years)
            cut = int(len(candles) * split)
            ins, oos = candles[:cut], candles[cut:]
            if len(ins) >= warm and len(oos) >= warm:
                in_sample.append(ins)
                out_sample.append(oos)

        if not in_sample:
            return SweepReport(
                notes=["No symbol had enough history for an " "in-sample / out-of-sample split."]
            )

        grid: List[SweepPoint] = []
        for stop in self._STOP_GRID:
            for rr in self._RR_GRID:
                cfg = self._with_stops(stop, stop * rr)
                ret, win, trades = self._agg_backtest(cfg, in_sample)
                grid.append(SweepPoint(stop, rr, ret, win, trades))

        best = max(grid, key=lambda p: p.return_pct)

        base_stop = self.config.fusion.stop_vol_mult
        base_rr = self.config.fusion.target_vol_mult / max(base_stop, 1e-9)
        b_ret, b_win, b_trades = self._agg_backtest(self.config, in_sample)
        baseline = SweepPoint(base_stop, base_rr, b_ret, b_win, b_trades)

        oos_ret, oos_win, oos_trades = self._agg_backtest(
            self._with_stops(best.stop_mult, best.stop_mult * best.reward_risk), out_sample
        )
        base_oos_ret, _, _ = self._agg_backtest(self.config, out_sample)

        notes = [
            "Backtests are optimistic — treat every number as an upper "
            "bound, and the sweep result as a hypothesis, not a fact."
        ]
        if oos_ret < best.return_pct * 0.5:
            notes.append(
                f"The best in-sample params ({best.return_pct:+.1f}%) earn only "
                f"{oos_ret:+.1f}% out-of-sample — likely overfit to the "
                "in-sample window; do not adopt them blindly."
            )
        elif oos_ret <= base_oos_ret:
            notes.append(
                f"Out-of-sample, the swept stops ({oos_ret:+.1f}%) do NOT beat "
                f"the current config ({base_oos_ret:+.1f}%) — keep the default."
            )
        else:
            notes.append(
                f"Out-of-sample, the swept stops ({oos_ret:+.1f}%) beat the "
                f"current config ({base_oos_ret:+.1f}%) — a candidate worth "
                "confirming in paper trading."
            )
        return SweepReport(
            grid=grid,
            best=best,
            baseline=baseline,
            oos_return=oos_ret,
            oos_win_rate=oos_win,
            oos_trades=oos_trades,
            baseline_oos_return=base_oos_ret,
            notes=notes,
        )

    # -- Phase 4: meta-model evaluation --------------------------------------

    def evaluate_meta(
        self,
        symbols: List[str],
        years: int = 3,
        candles_by_symbol: Optional[Dict[str, List[OHLCV]]] = None,
        split: float = 0.7,
    ) -> MetaReport:
        """Train the meta-model in-sample, measure its precision out-of-sample.

        The honest question: of the trades the meta-model *accepts*, do more of
        them win than the base rate? If precision does not beat the base rate
        out-of-sample, the meta-model has no edge — and the report says so.
        """
        import pandas as pd

        from ..gates import MetaModel, build_meta_dataset

        floor = self.config.gates.meta_min_probability
        x_tr: List = []
        y_tr: List = []
        x_te: List = []
        y_te: List = []
        for symbol in symbols:
            candles = (candles_by_symbol or {}).get(symbol) or self.cache.get(symbol, years=years)
            cut = int(len(candles) * split)
            ins, oos = candles[:cut], candles[cut:]
            if len(ins) < 120 or len(oos) < 80:
                continue
            try:
                xi, yi, _ = build_meta_dataset(ins, self.config)
                xo, yo, _ = build_meta_dataset(oos, self.config)
            except Exception:  # noqa: BLE001
                continue
            if len(yi) >= 20 and len(yo) >= 10:
                x_tr.append(xi)
                y_tr.append(yi)
                x_te.append(xo)
                y_te.append(yo)

        notes = [
            "Backtests are optimistic — out-of-sample precision is the "
            "only number here worth any trust."
        ]
        if not x_tr:
            return MetaReport(
                notes=notes + ["Not enough real history to " "evaluate the meta-model."]
            )

        model = MetaModel(self.config.runtime.random_seed).fit(
            pd.concat(x_tr, ignore_index=True), pd.concat(y_tr, ignore_index=True)
        )
        x_test = pd.concat(x_te, ignore_index=True)
        y_test = pd.concat(y_te, ignore_index=True).to_numpy(dtype=int)
        if not model.is_trained:
            return MetaReport(
                train_samples=len(y_test),
                notes=notes + ["The meta-model could not train (single class / too few rows)."],
            )

        probs = model.probability_frame(x_test)
        accepted = probs >= floor
        base_rate = float(y_test.mean())
        accuracy = float(((probs >= 0.5).astype(int) == y_test).mean())
        precision = float(y_test[accepted].mean()) if accepted.any() else 0.0
        coverage = float(accepted.mean())
        lift = precision - base_rate

        if int(accepted.sum()) < 20:
            notes.append(
                f"Only {int(accepted.sum())} test trades cleared the "
                f"{floor:.0%} floor — too few to trust the precision."
            )
        elif lift > 0.03:
            notes.append(
                f"Accepted trades win {precision:.0%} vs a {base_rate:.0%} "
                f"base rate (+{lift * 100:.0f} pts) — a real out-of-sample "
                "precision lift, the point of meta-labelling."
            )
        else:
            notes.append(
                f"Accepted-trade precision {precision:.0%} does not beat the "
                f"{base_rate:.0%} base rate — the meta-model is not yet "
                "identifying better trades. No edge."
            )
        return MetaReport(
            train_samples=int(model.samples),
            test_samples=int(len(y_test)),
            base_rate=base_rate,
            accuracy=accuracy,
            precision=precision,
            coverage=coverage,
            lift=lift,
            notes=notes,
        )
