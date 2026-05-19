"""Shared gate-decision type."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GateDecision:
    """A strategy gate's verdict on whether a live entry may proceed."""

    allowed: bool
    reason: str
