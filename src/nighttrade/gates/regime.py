"""Phase 2 — the regime gate.

A live run revealed problem #2: the strategy traded *most* in the market
regime where it was *measurably weakest*. The regime gate fixes that — it
blocks new entries in any regime whose own measured direction accuracy is
below the break-even win rate implied by the strategy's reward:risk ratio.

A regime with too few evaluated predictions is allowed through: the gate
gathers evidence before it judges, so it self-corrects as data accumulates.
"""

from __future__ import annotations

from .decision import GateDecision


def break_even_win_rate(reward_risk: float) -> float:
    """The win rate at which a strategy breaks even for a given reward:risk.

    A win pays ``reward_risk`` units, a loss costs 1; expectancy is zero when
    ``w * reward_risk == (1 - w)``, i.e. ``w = 1 / (1 + reward_risk)``.
    """
    return 1.0 / (1.0 + max(reward_risk, 1e-9))


class RegimeGate:
    """Blocks entries in regimes measured to perform below break-even."""

    def __init__(self, reward_risk: float, min_samples: int = 20) -> None:
        self.reward_risk = reward_risk
        self.break_even = break_even_win_rate(reward_risk)
        self.min_samples = max(1, min_samples)

    def evaluate(self, regime: str, memory) -> GateDecision:
        """Decide whether new entries are allowed in ``regime``.

        Args:
            regime: the current market regime / condition label.
            memory: a ``PredictionMemory`` — its ``by_condition`` maps a
                regime to a group with ``samples`` and ``accuracy``.
        """
        group = (getattr(memory, "by_condition", {}) or {}).get(regime)
        samples = getattr(group, "samples", 0) if group is not None else 0

        if samples < self.min_samples:
            return GateDecision(
                True,
                f"regime '{regime}': only {samples} evaluated prediction(s) "
                f"— gathering evidence, gate inactive",
            )

        accuracy = group.accuracy
        if accuracy < self.break_even:
            return GateDecision(
                False,
                f"regime '{regime}': measured accuracy {accuracy:.0%} is "
                f"below break-even {self.break_even:.0%} — entries blocked",
            )
        return GateDecision(
            True,
            f"regime '{regime}': accuracy {accuracy:.0%} clears break-even "
            f"{self.break_even:.0%}",
        )
