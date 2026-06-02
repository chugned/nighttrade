"""Named macro scenarios.

These presets are the deterministic backbone of the mock macro analyzer and
the vocabulary the Gemini analyzer is asked to classify into. Keeping them in
one table makes macro behaviour auditable and reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models.enums import Bias, RiskLevel


@dataclass(frozen=True)
class MacroScenario:
    """A canonical macro regime."""

    label: str
    bias: Bias
    score: float  # directional, [-1, 1]
    confidence: float  # [0, 1]
    risk_level: RiskLevel
    headline: str


SCENARIOS: dict[str, MacroScenario] = {
    "risk_on": MacroScenario(
        label="risk_on",
        bias=Bias.BULLISH,
        score=0.70,
        confidence=0.85,
        risk_level=RiskLevel.LOW,
        headline="Risk-on: broad equity advance, calm VIX, healthy breadth.",
    ),
    "institutional_buying": MacroScenario(
        label="institutional_buying",
        bias=Bias.BULLISH,
        score=0.80,
        confidence=0.88,
        risk_level=RiskLevel.LOW,
        headline="Institutional accumulation: persistent block buying, low realized vol.",
    ),
    "fed_dovish": MacroScenario(
        label="fed_dovish",
        bias=Bias.BULLISH,
        score=0.62,
        confidence=0.80,
        risk_level=RiskLevel.LOW,
        headline="Dovish Fed: rate-cut expectations lift equity multiples.",
    ),
    "neutral": MacroScenario(
        label="neutral",
        bias=Bias.NEUTRAL,
        score=0.0,
        confidence=0.40,
        risk_level=RiskLevel.MEDIUM,
        headline="Neutral macro: no dominant directional driver.",
    ),
    "fed_hawkish": MacroScenario(
        label="fed_hawkish",
        bias=Bias.BEARISH,
        score=-0.45,
        confidence=0.72,
        risk_level=RiskLevel.MEDIUM,
        headline="Hawkish Fed: higher-for-longer rates compress valuations.",
    ),
    "risk_off": MacroScenario(
        label="risk_off",
        bias=Bias.BEARISH,
        score=-0.55,
        confidence=0.75,
        risk_level=RiskLevel.HIGH,
        headline="Risk-off: defensive rotation into bonds and staples, rising VIX.",
    ),
    "panic": MacroScenario(
        label="panic",
        bias=Bias.BEARISH,
        score=-0.85,
        confidence=0.90,
        risk_level=RiskLevel.HIGH,
        headline="Market panic: broad selloff, VIX spike, circuit-breaker risk.",
    ),
    "war": MacroScenario(
        label="war",
        bias=Bias.BEARISH,
        score=-0.80,
        confidence=0.92,
        risk_level=RiskLevel.EXTREME,
        headline="Geopolitical shock (war): extreme uncertainty, flight to safety.",
    ),
    "credit_crisis": MacroScenario(
        label="credit_crisis",
        bias=Bias.BEARISH,
        score=-0.95,
        confidence=0.95,
        risk_level=RiskLevel.EXTREME,
        headline="Credit crisis: funding stress, widening spreads, systemic contagion risk.",
    ),
}

DEFAULT_SCENARIO = "neutral"


def get_scenario(name: str) -> MacroScenario:
    """Look up a scenario by name, falling back to ``neutral``."""
    return SCENARIOS.get(name.lower().strip(), SCENARIOS[DEFAULT_SCENARIO])
