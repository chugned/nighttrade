"""Safety layer — the real-trading guard and the kill-switch system."""

from __future__ import annotations

from .guard import assert_paper_only, forbid_real_trading
from .killswitch import (
    KillSwitchResult,
    evaluate_kill_switch,
    evaluate_macro_kill_switch,
    evaluate_micro_kill_switch,
)

__all__ = [
    "forbid_real_trading",
    "assert_paper_only",
    "KillSwitchResult",
    "evaluate_kill_switch",
    "evaluate_macro_kill_switch",
    "evaluate_micro_kill_switch",
]
