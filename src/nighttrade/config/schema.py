"""Validated configuration schema.

The config is a tree of pydantic models. Validation happens at load time, so a
malformed YAML file fails immediately with a precise error rather than blowing
up deep inside the pipeline.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models.enums import ModelKind


class _Section(BaseModel):
    """Base for config sections: reject unknown keys, allow nothing extra."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SafetyConfig(_Section):
    """Hard safety rails. These fields exist so the values are *visible* and
    *asserted* — not so they can be flipped. The validator refuses any value
    that would imply real trading; there is intentionally no escape hatch.
    """

    live_trading_enabled: bool = False
    allow_real_orders: bool = False
    paper_trading: bool = True

    @model_validator(mode="after")
    def _enforce_paper_only(self) -> "SafetyConfig":
        if self.live_trading_enabled:
            raise ValueError(
                "live_trading_enabled must be false — real trading is disabled."
            )
        if self.allow_real_orders:
            raise ValueError(
                "allow_real_orders must be false — real trading is disabled."
            )
        if not self.paper_trading:
            raise ValueError("paper_trading must be true — this platform is paper-only.")
        return self


class RuntimeConfig(_Section):
    log_level: str = "INFO"
    deterministic: bool = True
    random_seed: int = 42
    allow_network: bool = False

    @field_validator("log_level")
    @classmethod
    def _level(cls, v: str) -> str:
        v = v.upper()
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError(f"invalid log_level: {v}")
        return v


class ExchangeConfig(_Section):
    """Market-data source settings.

    ``sources`` names the public stock-data providers used when network access
    is enabled. Both ``yfinance`` and ``stooq`` are free and key-less, so the
    consensus engine still gets a genuine cross-source price.
    """

    sources: List[str] = Field(default_factory=lambda: ["yfinance", "stooq"])
    timeout_seconds: float = Field(default=5.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    retry_backoff_seconds: float = Field(default=0.5, ge=0)


class ConsensusConfig(_Section):
    min_sources: int = Field(default=1, ge=1)
    outlier_z_threshold: float = Field(
        default=3.0, gt=0,
        description="Source prices beyond this many MADs from the median are dropped.",
    )
    max_dispersion: float = Field(
        default=0.05, gt=0,
        description="If accepted-source dispersion exceeds this, mark degraded.",
    )


class IndicatorConfig(_Section):
    rsi_period: int = Field(default=14, ge=2)
    ema_fast: int = Field(default=12, ge=2)
    ema_slow: int = Field(default=26, ge=3)
    macd_signal: int = Field(default=9, ge=2)
    volatility_window: int = Field(default=20, ge=2)
    momentum_window: int = Field(default=10, ge=2)
    trend_window: int = Field(default=20, ge=2)

    @model_validator(mode="after")
    def _fast_slow(self) -> "IndicatorConfig":
        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast must be < ema_slow")
        return self


class MicrostructureConfig(_Section):
    """Stock microstructure settings.

    Equities have no free Level-2 order book, so this layer reads *intraday
    tape* features instead: signed order-flow imbalance, VWAP stretch,
    relative volume, session gaps and trading-halt heuristics.
    """

    flow_window: int = Field(
        default=20, ge=2,
        description="Bars used to estimate signed intraday order-flow imbalance.",
    )
    imbalance_strong: float = Field(default=0.30, gt=0, le=1)
    wide_spread_bps: float = Field(
        default=8.0, gt=0,
        description="Effective spread proxy above this counts as wide.",
    )
    rvol_window: int = Field(
        default=30, ge=2,
        description="Bars averaged for the relative-volume (RVOL) baseline.",
    )
    low_rvol_threshold: float = Field(
        default=0.40, gt=0,
        description="RVOL below this flags thin participation / low liquidity.",
    )
    vwap_window: int = Field(
        default=60, ge=2,
        description="Bars used for the rolling session VWAP.",
    )
    vwap_stretch_strong: float = Field(
        default=0.015, gt=0,
        description="|price-VWAP|/VWAP beyond this counts as a stretched tape.",
    )
    gap_strong_pct: float = Field(
        default=0.012, gt=0,
        description="Session gap (open vs prior close) beyond this is notable.",
    )
    swing_window: int = Field(
        default=30, ge=2,
        description="Bars scanned for swing-high/low support & resistance.",
    )


class FeatureConfig(_Section):
    return_windows: List[int] = Field(default_factory=lambda: [1, 3, 5, 10])
    rolling_std_window: int = Field(default=20, ge=2)
    skew_kurtosis_window: int = Field(default=30, ge=4)


class LabelConfig(_Section):
    horizon: int = Field(default=5, ge=1, description="Bars ahead for the label.")
    breakout_threshold: float = Field(
        default=0.004, gt=0,
        description="Forward return magnitude that counts as a directional move.",
    )


class MLConfig(_Section):
    model_kind: ModelKind = ModelKind.GRADIENT_BOOSTING
    test_size: float = Field(default=0.25, gt=0, lt=1)
    min_train_samples: int = Field(default=100, ge=20)

    model_config = _Section.model_config | {"protected_namespaces": ()}


class WalkForwardConfig(_Section):
    n_folds: int = Field(default=5, ge=2)
    train_window: int = Field(default=400, ge=50)
    test_window: int = Field(default=100, ge=20)
    overfit_gap_warn: float = Field(
        default=0.15, gt=0,
        description="train-test accuracy gap above this flags overfitting.",
    )
    suspicious_accuracy: float = Field(
        default=0.85, gt=0.5, le=1.0,
        description="Test accuracy above this flags likely leakage.",
    )


class MacroConfig(_Section):
    source: str = Field(default="mock", description="'mock' or 'gemini'.")
    default_risk_level: str = "medium"

    @field_validator("source")
    @classmethod
    def _src(cls, v: str) -> str:
        v = v.lower()
        if v not in {"mock", "gemini"}:
            raise ValueError("macro.source must be 'mock' or 'gemini'")
        return v


class FusionWeights(_Section):
    technical: float = Field(default=0.35, ge=0)
    microstructure: float = Field(default=0.25, ge=0)
    macro: float = Field(default=0.20, ge=0)
    ml: float = Field(default=0.20, ge=0)

    @model_validator(mode="after")
    def _positive_sum(self) -> "FusionWeights":
        if self.technical + self.microstructure + self.macro + self.ml <= 0:
            raise ValueError("fusion weights must sum to a positive number")
        return self


class FusionConfig(_Section):
    weights: FusionWeights = Field(default_factory=FusionWeights)
    action_threshold: float = Field(
        default=0.15, gt=0, lt=1,
        description="Abs fused score required to act (else HOLD).",
    )
    min_confidence: float = Field(
        default=0.35, ge=0, le=1,
        description="Confidence below this downgrades the action to HOLD.",
    )
    # Entry/stop/target are placed in units of a volatility unit U, where
    # U = reference_price * clip(ATR/price, min_vol_fraction, max_vol_fraction).
    entry_offset_vol_mult: float = Field(
        default=1.0, ge=0,
        description="Entry is offset this many volatility units toward a better fill.",
    )
    stop_vol_mult: float = Field(default=1.0, gt=0,
                                 description="Stop distance from entry, in vol units.")
    target_vol_mult: float = Field(default=5.0, gt=0,
                                   description="Target distance from entry, in vol units.")
    min_volatility_fraction: float = Field(
        default=0.002, gt=0,
        description="Volatility-unit floor as a fraction of price — keeps stops "
                    "from being placed unrealistically tight in calm markets.",
    )
    max_volatility_fraction: float = Field(
        default=0.05, gt=0,
        description="Volatility-unit cap as a fraction of price.",
    )

    @model_validator(mode="after")
    def _vol_bounds(self) -> "FusionConfig":
        if self.min_volatility_fraction >= self.max_volatility_fraction:
            raise ValueError("min_volatility_fraction must be < max_volatility_fraction")
        return self


class KillSwitchConfig(_Section):
    macro_risk_block: str = Field(
        default="extreme",
        description="Macro risk at/above this level blocks all entries.",
    )
    micro_max_spread_bps: float = Field(default=12.0, gt=0)
    block_on_chop: bool = True
    block_on_thin_liquidity: bool = True


class GatesConfig(_Section):
    """Strategy decision gates — regime / calibration / meta-model.

    Each gate filters live entries on the strategy's own measured evidence.
    A gate with too little evidence stays inactive (passes trades through)
    so it gathers data before it judges — self-correcting by design.
    """

    regime_min_samples: int = Field(
        default=20, ge=1,
        description="Evaluated predictions a regime needs before the regime "
                    "gate will block it.")
    calibration_min_samples: int = Field(
        default=40, ge=1,
        description="Evaluated predictions needed before the isotonic "
                    "confidence calibration is fitted and enforced.")
    calibration_floor: float = Field(
        default=0.50, ge=0.0, le=1.0,
        description="Minimum calibrated win probability for a live entry.")
    meta_min_probability: float = Field(
        default=0.20, ge=0.0, le=1.0,
        description="Minimum meta-model P(target before stop) for a live "
                    "entry. Must sit ABOVE the triple-barrier base rate (which "
                    "the 5:1 geometry puts near ~12%), not at a naive 50%+.")


class RiskConfig(_Section):
    fee_bps: float = Field(default=10.0, ge=0, description="Per-side fee, bps.")
    base_slippage_bps: float = Field(default=2.0, ge=0)
    impact_slippage_bps: float = Field(
        default=8.0, ge=0,
        description="Extra slippage scaled by order size vs available liquidity.",
    )
    latency_ms: float = Field(default=250.0, ge=0)
    risk_per_trade: float = Field(
        default=0.01, gt=0, le=0.25,
        description="Fraction of equity risked between entry and stop.",
    )
    max_position_pct: float = Field(
        default=0.25, gt=0, le=1.0,
        description="Max notional of a single per-stock position, as a fraction "
                    "of equity.",
    )
    max_daily_loss_pct: float = Field(default=0.05, gt=0, le=1.0)
    max_weekly_loss_pct: float = Field(
        default=0.12, gt=0, le=1.0,
        description="Rolling 7-day loss limit; blocks new entries once hit.",
    )
    max_open_positions: int = Field(
        default=3, ge=1,
        description="Maximum number of simultaneously open positions.",
    )
    loss_cooldown_bars: int = Field(
        default=20, ge=0,
        description="Bars to wait after a losing trade before a new entry.",
    )
    time_stop_bars: int = Field(
        default=20, ge=0,
        description="Triple-barrier time stop: force-close a position after "
                    "this many bars even if neither stop nor target is hit. "
                    "0 disables it.",
    )
    partial_fill_liquidity_frac: float = Field(
        default=0.25, gt=0, le=1.0,
        description="Max fraction of top-of-book liquidity one order may consume.",
    )

    @model_validator(mode="after")
    def _loss_limits_ordered(self) -> "RiskConfig":
        if self.max_weekly_loss_pct < self.max_daily_loss_pct:
            raise ValueError("max_weekly_loss_pct must be >= max_daily_loss_pct")
        return self


class PaperConfig(_Section):
    starting_cash: float = Field(default=10_000.0, gt=0)
    base_currency: str = "EUR"


class BacktestConfig(_Section):
    warmup_bars: int = Field(default=50, ge=0)
    sharpe_warn_threshold: float = Field(
        default=4.0, gt=0,
        description="A backtest Sharpe-like ratio above this is flagged unrealistic.",
    )


class WatchlistConfig(_Section):
    """Multi-stock watchlist with liquidity / quality screening."""

    symbols: List[str] = Field(
        default_factory=lambda: ["AAPL", "MSFT", "NVDA"],
        description="Tradeable universe — extend with any liquid US tickers.",
    )
    min_24h_volume_usd: float = Field(
        default=20_000_000.0, gt=0,
        description="Reject stocks below this average daily dollar volume.",
    )
    max_spread_bps: float = Field(
        default=20.0, gt=0,
        description="Reject stocks whose effective (proxy) spread exceeds this.",
    )
    min_orderbook_notional_usd: float = Field(
        default=100_000.0, gt=0,
        description="Reject stocks with too little resting top-of-book size.",
    )
    pump_dump_max_1h_move_pct: float = Field(
        default=0.20, gt=0,
        description="Reject stocks that moved more than this intraday "
                    "(suspected halt-bound news spike / low-float squeeze).",
    )

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: List[str]) -> List[str]:
        cleaned = [s.strip().upper() for s in v if s.strip()]
        if not cleaned:
            raise ValueError("watchlist.symbols must not be empty")
        return cleaned


class CrossSectionWeights(_Section):
    """Blend weights for the cross-sectional factor model."""

    momentum: float = Field(default=0.35, ge=0)
    trend: float = Field(default=0.25, ge=0)
    reversion: float = Field(default=0.15, ge=0)
    low_vol: float = Field(default=0.15, ge=0)
    ml: float = Field(default=0.10, ge=0)

    @model_validator(mode="after")
    def _positive_sum(self) -> "CrossSectionWeights":
        if (self.momentum + self.trend + self.reversion
                + self.low_vol + self.ml) <= 0:
            raise ValueError("cross-section weights must sum to a positive number")
        return self


class CrossSectionConfig(_Section):
    """Cross-sectional ranking — rank the universe relative to itself.

    Each cycle the per-stock factors are z-scored *across the universe* and
    blended; stocks are then ranked and split into long / short baskets. This
    is relative-strength stock selection, not per-stock absolute prediction.
    """

    momentum_lookback: int = Field(
        default=60, ge=2,
        description="Bars over which trailing-return momentum is measured.",
    )
    trend_window: int = Field(
        default=60, ge=3,
        description="Bars used for the trend-quality (signed R²) factor.",
    )
    reversion_rsi_period: int = Field(default=14, ge=2)
    volatility_window: int = Field(default=30, ge=2)
    weights: CrossSectionWeights = Field(default_factory=CrossSectionWeights)
    long_fraction: float = Field(
        default=0.10, gt=0, le=0.5,
        description="Top fraction of the ranked universe placed in the long basket.",
    )
    short_fraction: float = Field(
        default=0.10, ge=0, le=0.5,
        description="Bottom fraction placed in the short/avoid basket.",
    )
    winsorize_limit: float = Field(
        default=0.05, ge=0, lt=0.5,
        description="Fraction clipped from each tail before z-scoring a factor.",
    )
    min_dollar_volume: float = Field(
        default=10_000_000.0, ge=0,
        description="Stocks below this daily dollar volume are excluded from ranking.",
    )


class ApprovalConfig(_Section):
    """Manual-approval gate for paper / sandbox execution."""

    require_manual_approval: bool = Field(
        default=True,
        description="If true, every trade must be confirmed at the CLI.",
    )
    confirmation_phrase: str = Field(
        default="YES",
        description="Phrase the operator must type to authorize a paper trade.",
    )


class SandboxConfig(_Section):
    """Broker paper-account (sandbox) settings.

    SANDBOX = a broker PAPER account only (e.g. Alpaca paper trading). There is
    no live-brokerage execution path. These flags exist so the safe values are
    explicit and validated — the validator refuses any combination that would
    imply real-money execution.
    """

    enabled: bool = Field(
        default=False,
        description="Opt-in: enable broker paper-account execution (still no "
                    "real money).",
    )
    broker: str = Field(
        default="alpaca",
        description="Which broker paper API to use. Only 'alpaca' is supported.",
    )
    require_read_only_keys: bool = Field(
        default=True,
        description="Require API keys without order-placement scope by default.",
    )
    reject_live_keys: bool = Field(
        default=True,
        description="Refuse any API key tied to a LIVE brokerage account.",
    )

    @field_validator("broker")
    @classmethod
    def _broker(cls, v: str) -> str:
        v = v.lower().strip()
        if v != "alpaca":
            raise ValueError("sandbox.broker must be 'alpaca'")
        return v

    @model_validator(mode="after")
    def _enforce_sandbox_safety(self) -> "SandboxConfig":
        # This guard cannot be turned off — a live account is never allowed.
        if not self.reject_live_keys:
            raise ValueError(
                "reject_live_keys must be true — live brokerage accounts are "
                "never permitted."
            )
        return self


class AppConfig(_Section):
    """The complete validated application configuration."""

    profile: str = "default"
    symbol: str = "AAPL"

    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    exchanges: ExchangeConfig = Field(default_factory=ExchangeConfig)
    consensus: ConsensusConfig = Field(default_factory=ConsensusConfig)
    indicators: IndicatorConfig = Field(default_factory=IndicatorConfig)
    microstructure: MicrostructureConfig = Field(default_factory=MicrostructureConfig)
    features: FeatureConfig = Field(default_factory=FeatureConfig)
    labels: LabelConfig = Field(default_factory=LabelConfig)
    ml: MLConfig = Field(default_factory=MLConfig)
    walkforward: WalkForwardConfig = Field(default_factory=WalkForwardConfig)
    macro: MacroConfig = Field(default_factory=MacroConfig)
    fusion: FusionConfig = Field(default_factory=FusionConfig)
    killswitch: KillSwitchConfig = Field(default_factory=KillSwitchConfig)
    gates: GatesConfig = Field(default_factory=GatesConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    watchlist: WatchlistConfig = Field(default_factory=WatchlistConfig)
    cross_section: CrossSectionConfig = Field(default_factory=CrossSectionConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, v: str) -> str:
        return v.strip().upper()
