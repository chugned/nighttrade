"""Macro AI context system — deterministic mock with optional Gemini."""

from __future__ import annotations

from .engine import MacroEngine
from .gemini import GeminiMacroAnalyzer, MacroUnavailable
from .mock import MockMacroAnalyzer
from .scenarios import SCENARIOS, MacroScenario, get_scenario

__all__ = [
    "MacroEngine",
    "MockMacroAnalyzer",
    "GeminiMacroAnalyzer",
    "MacroUnavailable",
    "MacroScenario",
    "SCENARIOS",
    "get_scenario",
]
