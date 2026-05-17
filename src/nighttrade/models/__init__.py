"""Domain models — immutable, validated, JSON-serializable pydantic objects."""

from __future__ import annotations

from ._base import NighttradeModel, normalize_timestamp
from .decision import TradingDecision
from .enums import (
    Action,
    Bias,
    ExchangeStatus,
    MarketRegime,
    ModelKind,
    OrderStatus,
    OrderType,
    RiskLevel,
    Side,
)
from .market import (
    ConsensusPrice,
    OHLCV,
    OrderBookLevel,
    OrderBookSnapshot,
    PriceTick,
)
from .metrics import BacktestMetrics, WalkForwardFold, WalkForwardReport
from .portfolio import Fill, PortfolioSnapshot, Position
from .signals import (
    MacroSignal,
    MicrostructureSignal,
    MLSignal,
    TechnicalSignal,
)

__all__ = [
    "NighttradeModel",
    "normalize_timestamp",
    # enums
    "Action",
    "Bias",
    "ExchangeStatus",
    "MarketRegime",
    "ModelKind",
    "OrderStatus",
    "OrderType",
    "RiskLevel",
    "Side",
    # market
    "PriceTick",
    "ConsensusPrice",
    "OHLCV",
    "OrderBookLevel",
    "OrderBookSnapshot",
    # signals
    "TechnicalSignal",
    "MicrostructureSignal",
    "MacroSignal",
    "MLSignal",
    # decision
    "TradingDecision",
    # portfolio
    "Fill",
    "Position",
    "PortfolioSnapshot",
    # metrics
    "BacktestMetrics",
    "WalkForwardFold",
    "WalkForwardReport",
]
