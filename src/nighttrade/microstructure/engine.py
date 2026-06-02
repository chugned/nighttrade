"""Stock microstructure analysis.

Equities have no free public Level-2 order book, so this layer reads the
*intraday tape* instead of resting depth. From recent candles it derives:

* **order-flow imbalance** — volume-weighted close-in-range pressure, a tape
  proxy for whether buyers or sellers are lifting/hitting prints;
* **VWAP stretch** — how far price has run from the rolling session VWAP
  (a mean-reversion context, not a trend signal);
* **relative volume (RVOL)** — current participation vs its recent baseline;
* **session gap** — the most recent bar's open vs the prior close;
* **trading-halt heuristics** — frozen-price / zero-volume bars.

The output is the same :class:`MicrostructureSignal` the rest of the pipeline
consumes: a directional ``score``/``bias`` plus spread, thin-liquidity and
chop-zone hazard flags the kill switch later reads. The ``OrderBookSnapshot``
argument is used only for its (synthetic) effective-spread reading.
"""

from __future__ import annotations

import math
from typing import List, Optional

import numpy as np

from ..config.schema import MicrostructureConfig
from ..indicators import core
from ..indicators.frame import ohlcv_to_frame
from ..models import (
    OHLCV,
    Bias,
    MarketRegime,
    MicrostructureSignal,
    OrderBookSnapshot,
)


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def order_flow_imbalance(candles: List[OHLCV], window: int) -> float:
    """Volume-weighted signed order-flow imbalance in [-1, 1].

    Each bar contributes its *close position in range* — a close on the high
    is +1 (buyers in control), a close on the low is -1 — weighted by volume.
    A tape proxy for resting-depth imbalance, which equities do not expose.
    """
    recent = candles[-window:]
    num = 0.0
    den = 0.0
    for c in recent:
        rng = c.high - c.low
        signed = 0.0 if rng <= 0 else (2.0 * (c.close - c.low) / rng - 1.0)
        weight = max(c.volume, 1e-9)
        num += signed * weight
        den += weight
    return 0.0 if den <= 0 else _clip(num / den)


def session_vwap(candles: List[OHLCV], window: int) -> Optional[float]:
    """Rolling volume-weighted average price over the last ``window`` bars."""
    recent = candles[-window:]
    num = sum(c.typical_price * max(c.volume, 1e-9) for c in recent)
    den = sum(max(c.volume, 1e-9) for c in recent)
    return None if den <= 0 else num / den


def relative_volume(candles: List[OHLCV], window: int) -> float:
    """Latest bar volume divided by its trailing ``window``-bar average."""
    recent = candles[-window:]
    vols = [c.volume for c in recent]
    avg = float(np.mean(vols)) if vols else 0.0
    if avg <= 0:
        return 1.0
    return candles[-1].volume / avg


def detect_halt(candles: List[OHLCV], lookback: int = 5) -> bool:
    """Heuristic trading-halt detector: frozen-price, zero-volume bars."""
    recent = candles[-lookback:]
    if len(recent) < 3:
        return False
    frozen = sum(1 for c in recent if c.volume <= 0.0 and c.high == c.low == c.open == c.close)
    return frozen >= 3


class MicrostructureEngine:
    """Turns the intraday tape into a microstructure signal."""

    def __init__(self, config: MicrostructureConfig | None = None) -> None:
        self.config = config or MicrostructureConfig()

    def compute(
        self,
        book: OrderBookSnapshot,
        candles: Optional[List[OHLCV]] = None,
    ) -> MicrostructureSignal:
        """Analyze the tape (with the synthetic book for spread) into a signal."""
        cfg = self.config
        reasoning: List[str] = []
        candles = candles or []

        symbol = book.symbol
        timestamp = book.timestamp

        # --- Effective spread (from the synthetic top-of-book) ---
        spread_bps = book.spread_bps
        wide_spread = spread_bps is not None and spread_bps > cfg.wide_spread_bps
        if spread_bps is not None:
            tag = "wide" if wide_spread else "tight"
            reasoning.append(f"Effective spread {spread_bps:.1f} bps ({tag})")

        if not candles:
            # No tape — emit an honest neutral, low-confidence signal.
            return MicrostructureSignal(
                symbol=symbol,
                timestamp=timestamp,
                bias=Bias.NEUTRAL,
                score=0.0,
                confidence=0.1,
                reasoning=reasoning + ["No intraday tape available"],
                imbalance=0.0,
                spread_bps=spread_bps,
                regime=MarketRegime.RANGE,
                thin_liquidity=True,
                chop_zone=False,
                liquidity_interpretation="no tape data",
            )

        last = candles[-1]
        price = last.close

        # --- Order-flow imbalance (tape pressure) ---
        imbalance = order_flow_imbalance(candles, cfg.flow_window)
        if abs(imbalance) >= cfg.imbalance_strong:
            side = "buyers" if imbalance > 0 else "sellers"
            reasoning.append(f"Order flow {abs(imbalance) * 100:.0f}% toward {side}")
        else:
            reasoning.append(f"Order flow {imbalance * 100:+.0f}% (balanced tape)")

        # --- VWAP stretch (mean-reversion context) ---
        vwap = session_vwap(candles, cfg.vwap_window)
        vwap_dev = 0.0 if not vwap else (price - vwap) / vwap
        stretched = abs(vwap_dev) > cfg.vwap_stretch_strong
        if vwap:
            where = "above" if vwap_dev >= 0 else "below"
            tag = " — stretched" if stretched else ""
            reasoning.append(f"Price {vwap_dev * 100:+.2f}% {where} session VWAP{tag}")

        # --- Relative volume ---
        rvol = relative_volume(candles, cfg.rvol_window)
        low_participation = rvol < cfg.low_rvol_threshold
        reasoning.append(
            f"Relative volume {rvol:.2f}x" + (" — thin participation" if low_participation else "")
        )

        # --- Session gap ---
        gap = 0.0
        if len(candles) >= 2 and candles[-2].close > 0:
            gap = (last.open - candles[-2].close) / candles[-2].close
        if abs(gap) > cfg.gap_strong_pct:
            reasoning.append(f"Session gap {gap * 100:+.2f}%")

        # --- Trading-halt heuristic ---
        halted = detect_halt(candles)
        if halted:
            reasoning.append("Trading-halt heuristic tripped (frozen tape)")

        # --- Regime & chop detection ---
        regime, chop = self._regime(candles)
        if chop:
            reasoning.append("Chop zone: directionless, low-conviction tape")
        reasoning.append(f"Regime: {regime.value}")

        # --- Swing support / resistance ---
        swing = candles[-cfg.swing_window :]
        support = round(min(c.low for c in swing), 4)
        resistance = round(max(c.high for c in swing), 4)
        reasoning.append(f"Swing support {support:,.2f} / resistance {resistance:,.2f}")

        # --- Thin-liquidity hazard ---
        thin = low_participation or wide_spread or halted

        # --- Score & bias ---
        flow_score = _clip(imbalance / cfg.imbalance_strong)
        # VWAP mean-reversion: price far BELOW VWAP leans bullish, far above
        # leans bearish. It contributes a contrarian pull, not a trend vote.
        vwap_mr = _clip(-vwap_dev / cfg.vwap_stretch_strong)
        score = _clip(0.65 * flow_score + 0.35 * vwap_mr)

        if score > 0.15:
            bias = Bias.BULLISH
        elif score < -0.15:
            bias = Bias.BEARISH
        else:
            bias = Bias.NEUTRAL

        confidence = 0.6
        confidence += 0.2 * min(abs(imbalance) / cfg.imbalance_strong, 1.0)
        if wide_spread:
            confidence -= 0.25
        if low_participation:
            confidence -= 0.2
        if chop:
            confidence -= 0.2
        if halted:
            confidence -= 0.35
        confidence = _clip(confidence, 0.0, 1.0)

        interpretation = self._interpret(
            imbalance, vwap_dev, low_participation, wide_spread, chop, halted
        )

        return MicrostructureSignal(
            symbol=symbol,
            timestamp=timestamp,
            bias=bias,
            score=score,
            confidence=confidence,
            reasoning=reasoning,
            imbalance=imbalance,
            spread_bps=spread_bps,
            regime=regime,
            thin_liquidity=thin,
            chop_zone=chop,
            support=support,
            resistance=resistance,
            liquidity_walls=sorted({support, resistance}),
            liquidity_interpretation=interpretation,
        )

    def _regime(self, candles: Optional[List[OHLCV]]) -> tuple[MarketRegime, bool]:
        """Classify the market regime and whether it is a chop zone."""
        if not candles or len(candles) < 30:
            return MarketRegime.RANGE, False
        frame = ohlcv_to_frame(candles)
        close = frame["close"]
        slope = core.trend_slope(close, min(20, len(close) - 1))
        vol = core.volatility(close, min(20, len(close) - 1))
        slope_v = slope.dropna()
        vol_v = vol.dropna()
        if slope_v.empty or vol_v.empty:
            return MarketRegime.RANGE, False
        s = float(slope_v.iloc[-1])
        v = float(vol_v.iloc[-1])
        if not (math.isfinite(s) and math.isfinite(v)):
            return MarketRegime.RANGE, False

        high_vol = v > 0.012
        weak_trend = abs(s) < 0.0006
        if high_vol and weak_trend:
            return MarketRegime.VOLATILE, True
        if weak_trend:
            return MarketRegime.CHOP, True
        if high_vol:
            return MarketRegime.VOLATILE, False
        if s > 0:
            return MarketRegime.TREND_UP, False
        return MarketRegime.TREND_DOWN, False

    @staticmethod
    def _interpret(
        imbalance: float, vwap_dev: float, low_vol: bool, wide: bool, chop: bool, halted: bool
    ) -> str:
        parts: List[str] = []
        if imbalance > 0.1:
            parts.append("buy-side tape favors upside")
        elif imbalance < -0.1:
            parts.append("sell-side tape favors downside")
        else:
            parts.append("balanced tape")
        if vwap_dev > 0.01:
            parts.append("extended above VWAP")
        elif vwap_dev < -0.01:
            parts.append("discounted below VWAP")
        if low_vol:
            parts.append("thin participation raises slippage risk")
        if wide:
            parts.append("wide spread penalizes entries")
        if chop:
            parts.append("chop zone discourages directional trades")
        if halted:
            parts.append("possible trading halt — tape frozen")
        return "; ".join(parts)
