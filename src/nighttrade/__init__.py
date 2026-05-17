"""nighttrade — multi-layer educational trading research & paper-trading platform.

EDUCATIONAL ONLY. This package cannot place real trades. Every execution path
is simulated; any function that would reach a live broker raises
``NotImplementedError("Real trading is disabled.")``.

Backtests are NOT reality.
"""

from __future__ import annotations

__version__ = "0.1.0"

# A single, import-time constant other modules assert against. There is no way
# — through config, env vars or arguments — to flip this to True.
REAL_TRADING_ENABLED: bool = False

__all__ = ["__version__", "REAL_TRADING_ENABLED"]
