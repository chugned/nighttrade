# PLAN.md
# Multi-Layer Intelligent STOCK Trading Research & Paper-Trading Platform

## Master Autonomous Implementation Plan for Claude Code

This repository is not a toy bot.

This is a full-stack research-grade intelligent trading platform focused on:

- educational algorithmic trading research
- market microstructure analysis
- AI-assisted decision fusion
- ML prediction systems
- feature engineering pipelines
- walk-forward validation
- simulation and paper trading
- robust testing
- realistic execution modeling
- safety-first architecture

The architecture must feel like:
“A professional quant-research platform built by a small obsessive engineering team.”

---

# ABSOLUTE CORE PRINCIPLES

## 1. SAFETY FIRST

This system is educational only.

NEVER:
- place real trades
- support leverage
- support futures
- support margin
- execute live orders
- store withdrawal credentials
- connect to live execution brokers

ALL execution must be:
- simulated
- paper-only
- deterministic when testing

Any function related to real order execution MUST:
raise NotImplementedError("Real trading is disabled.")

---

## 2. REALISM OVER HYPE

The platform must explicitly model:
- spread
- slippage
- latency
- partial fills
- liquidity
- orderbook imbalance
- fees
- market regimes
- volatility
- exchange outages
- degraded APIs
- overfitting risk
- lookahead bias

The codebase must repeatedly emphasize:

“Backtests are NOT reality.”

---

# MASTER IMPLEMENTATION PHASES

## PHASE 1 — REPOSITORY AUDIT & FOUNDATION
Estimated Heavy Implementation Time: 3–5 hours

Goals:
- Inspect repository state
- Detect existing code
- Establish Python package structure
- Configure tooling

Deliverables:
- pyproject.toml
- README.md
- PLAN.md
- .gitignore
- .env.example

Dependencies:
- pandas
- numpy
- pydantic
- typer
- rich
- httpx
- tenacity
- pyyaml
- pytest
- python-dotenv
- scikit-learn

---

## PHASE 2 — CORE DOMAIN MODELS
Estimated Heavy Implementation Time: 4–6 hours

Models:
- PriceTick
- ConsensusPrice
- OHLCV
- OrderBookSnapshot
- TechnicalSignal
- MicrostructureSignal
- MacroSignal
- MLSignal
- TradingDecision
- PortfolioSnapshot
- BacktestMetrics

Requirements:
- pydantic validation
- JSON serialization
- timestamp normalization
- enum usage

---

## PHASE 3 — CONFIGURATION SYSTEM
Estimated Heavy Implementation Time: 3–4 hours

Create:
- YAML configs
- validation system
- environment overrides

Critical Safety:
live_trading_enabled: false
allow_real_orders: false
paper_trading: true

---

## PHASE 4 — STOCK DATA INFRASTRUCTURE
Estimated Heavy Implementation Time: 5–7 hours

Implement:
- yfinance (Yahoo Finance) public API
- stooq public CSV API
- Mock stock-data source

Features:
- retries
- timeouts
- failover
- outlier filtering
- degraded mode

Consensus engine:
- averages prices
- removes bad-print outliers
- computes consensus prices

---

## PHASE 5 — TECHNICAL INDICATOR ENGINE
Estimated Heavy Implementation Time: 6–8 hours

Indicators:
- RSI
- EMA
- MACD
- volatility
- momentum
- trend slope

Requirements:
- vectorized computation
- configurable windows
- numerical stability
- no lookahead bias

---

## PHASE 6 — TAPE-BASED MICROSTRUCTURE SYSTEM
Estimated Heavy Implementation Time: 7–10 hours

US equities have no free Level-2 feed, so microstructure reads the intraday
tape instead of resting depth.

Features:
- order-flow imbalance (volume-weighted close-in-range pressure)
- VWAP stretch
- relative volume (RVOL)
- session gaps
- swing support/resistance
- effective-spread proxy
- thin-participation detection
- trading-halt heuristics
- chop-zone detection

Outputs:
- microstructure bias
- confidence
- liquidity interpretation

---

## PHASE 7 — FEATURE ENGINEERING FRAMEWORK
Estimated Heavy Implementation Time: 8–12 hours

Create reusable research-grade feature pipelines.

Feature examples:
- RSI
- MACD
- rolling std
- returns
- skew
- kurtosis
- imbalance
- spread
- macro encoding

Critical Rule:
ONLINE and OFFLINE pipelines MUST share identical feature logic.

---

## PHASE 8 — LABEL GENERATION SYSTEM
Estimated Heavy Implementation Time: 4–6 hours

Labels:
- future return
- threshold breakout
- directional labels

Safety:
Labels ONLY exist in offline training.

---

## PHASE 9 — MACHINE LEARNING INFRASTRUCTURE
Estimated Heavy Implementation Time: 10–14 hours

Models:
- LogisticRegression
- RandomForest
- GradientBoosting

Outputs:
- probability up
- probability down
- intelligent score [-1, 1]

---

## PHASE 10 — WALK-FORWARD VALIDATION
Estimated Heavy Implementation Time: 5–8 hours

Process:
- train on past
- predict future
- move forward
- repeat

Must detect:
- overfitting
- leakage
- unrealistic performance

---

## PHASE 11 — MACRO AI CONTEXT SYSTEM
Estimated Heavy Implementation Time: 6–8 hours

Modes:
- deterministic mock
- optional Gemini integration

Detect:
- market panic
- risk-on
- institutional buying
- dovish / hawkish Fed
- credit crisis

Outputs:
- macro bias
- confidence
- risk level

---

## PHASE 12 — AI FUSION ENGINE
Estimated Heavy Implementation Time: 8–10 hours

Inputs:
- technical signal
- microstructure signal
- macro signal
- ML score
- risk state
- kill switch state

Outputs:
- BUY / SELL / HOLD
- confidence
- entry
- stop
- target
- reasoning

---

## PHASE 13 — KILL SWITCH SYSTEM
Estimated Heavy Implementation Time: 4–5 hours

Macro Kill Switch:
- war
- credit crisis
- market panic

Micro Kill Switch:
- chop zones
- thin participation
- trading halts
- extreme spread

---

## PHASE 14 — RISK ENGINE
Estimated Heavy Implementation Time: 6–9 hours

Implement:
- slippage
- fees
- position sizing
- max daily loss
- realistic fills

---

## PHASE 15 — PAPER TRADING ENGINE
Estimated Heavy Implementation Time: 7–10 hours

Build:
- PaperBroker
- portfolio tracking
- fake fills
- PnL engine
- trade logs

---

## PHASE 16 — BACKTESTING & SIMULATION ENGINE
Estimated Heavy Implementation Time: 8–12 hours

Must include:
- realistic fills
- spread
- fees
- latency
- outlier handling

Metrics:
- win rate
- drawdown
- exposure
- Sharpe-like ratio

---

## PHASE 17 — CLI & RUNTIME PIPELINE
Estimated Heavy Implementation Time: 5–7 hours

Commands:
- trading-bot demo
- trading-bot paper
- trading-bot backtest
- trading-bot train
- trading-bot simulate

Runtime Flow:
1. fetch data
2. compute features
3. generate signals
4. run ML
5. fuse decisions
6. apply risk
7. simulate execution
8. generate report

---

## PHASE 18 — REPORTING SYSTEM
Estimated Heavy Implementation Time: 4–6 hours

Build:
- Rich console reporting
- JSON reports
- Markdown reports

Reports must include:
- market state
- technical signals
- ML predictions
- risk warnings
- execution assumptions

---

## PHASE 19 — TESTING & RELIABILITY
Estimated Heavy Implementation Time: 10–15 hours

Goals:
- unit tests
- integration tests
- pipeline tests
- leakage tests
- safety tests

Test Count Goal:
50–100+ meaningful tests.

Critical Tests:
- no lookahead bias
- slippage worsens fills
- outliers removed
- no real trading possible

---

## PHASE 20 — FINAL POLISH & SYSTEM HARDENING
Estimated Heavy Implementation Time: 8–12 hours

Improve:
- performance
- typing
- architecture
- logging
- documentation
- deterministic behavior

Final Tasks:
- run all tests
- run demo
- run backtest
- validate configs
- generate final report

---

# FINAL REQUIRED DEMO

The system MUST reproduce a deterministic "buy-the-dip" scenario:

AAPL ≈ 234.00

Macro:
- bullish (risk-on)
- confidence 0.85

Technical:
- RSI ≈ 25
- oversold (bullish mean-reversion)

Intraday tape:
- sell-heavy order flow after a sharp pullback

Final:
BUY
moderate confidence

Entry / stop / target are placed by the fusion engine in volatility units
around the reference price (entry slightly below market, stop below, target
above). The exact levels are produced by the pipeline — never hard-coded —
and are printed by `trading-bot demo`.

---

# FINAL EXECUTION DIRECTIVE

Claude Code:

You are expected to autonomously execute this entire roadmap.

Do not stop after planning.
Do not create fake scaffolding.
Do not leave placeholders.
Do not skip testing.
Do not skip runtime verification.

You are building:
a research-grade educational intelligent STOCK trading platform with realistic execution simulation, ML research infrastructure, tape-based microstructure analysis, macro AI reasoning, and robust engineering standards.
