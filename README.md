# nighttrade

**Multi-layer educational STOCK trading research & paper-trading platform.**

> ⚠️ **EDUCATIONAL ONLY. THIS SOFTWARE CANNOT PLACE REAL TRADES.**
> Every execution path is simulated. Any function that would touch a live
> broker raises `NotImplementedError("Real trading is disabled.")`.
> No live brokerage accounts, no margin, no options, no funding/transfers —
> by design.

> ⚠️ **Backtests are NOT reality.** Historical simulation systematically
> overstates performance. This platform deliberately models spread, slippage,
> latency, partial fills, fees and regime shifts to *narrow* — never close —
> that gap.

`nighttrade` is the equities sibling of `daytrade`: the same research-grade
quant pipeline, re-grounded in the US stock market — market hours, session
gaps, VWAP, relative volume and a tape-based microstructure layer instead of
24/7 crypto and Level-2 order books.

---

## What this is

A research-grade reference implementation of an intelligent trading pipeline,
built for learning how the pieces of a quant stock-trading stack fit together:

```
stock data ─▶ consensus ─▶ indicators ─┐
                                        ├─▶ feature pipeline ─▶ ML model ─┐
intraday tape ─▶ microstructure ────────┤                                 │
macro context (mock / Gemini) ──────────┘                                 │
                                                                           ▼
                          kill switches ◀─── AI fusion engine ◀────────────┘
                                │
                                ▼
                  risk engine ─▶ paper broker ─▶ reporting
```

## Install

```bash
python -m pip install -e ".[dev]"          # offline (mock data)
python -m pip install -e ".[dev,online]"   # + yfinance for live read-only data
```

## Quick start

```bash
trading-bot demo          # run the canonical AAPL decision demo
trading-bot paper         # run a paper-trading session on mock data
trading-bot backtest      # run a backtest with realistic execution
trading-bot train         # train the ML model with walk-forward validation
trading-bot simulate      # full end-to-end simulation + report
trading-bot config        # show the active (validated) configuration
trading-bot market-hours  # show the current US market session
trading-bot rank          # cross-sectional ranking of the universe
trading-bot rank --live   # ...ranked on the real S&P 500

# operations layer
trading-bot watchlist     # screen the multi-stock watchlist for liquidity
trading-bot approve       # decide a trade and require manual CLI approval
trading-bot accounting    # accounting report (+ optional tax CSV export)
trading-bot daily-report  # end-of-session operations report
trading-bot sandbox-check # verify sandbox setup; prove real execution off

# 24/7 Market Safety Observatory
trading-bot observe --interval 300   # run the continuous observer (Ctrl+C to stop)
trading-bot dashboard                # launch the visual dashboard (FastAPI + web UI)
trading-bot status                   # show observatory status
trading-bot report-daily             # generate today's daily report
trading-bot watchlist-check          # screen configs/watchlist.yaml

# 30-Day Paper Trading Learning Observatory
trading-bot learn --days 30 --interval 300   # run the 30-day learning observatory
```

`make learn`, `make observe`, `make dashboard`, `make report`, `make status`
and `make test` are also provided.

By default everything runs **offline** against a deterministic mock data
source. Set `NIGHTTRADE_ALLOW_NETWORK=true` (and install the `online` extra)
to allow read-only public stock-data calls.

## Stock market data

The platform monitors many US tickers but **only paper-trades** — it never
places real orders or moves money.

- **Data sources** — offline, a deterministic mock generator; online, two free
  key-less providers: **yfinance** (Yahoo Finance — intraday + daily candles)
  and **stooq** (a CSV quote/daily endpoint). A consensus engine fuses their
  prices and rejects bad prints.
- **No Level-2 order book.** US equities have no free public depth feed, so the
  microstructure layer reads the *intraday tape* — order-flow imbalance, VWAP
  stretch, relative volume, session gaps and trading-halt heuristics — rather
  than resting bid/ask depth.
- **Market hours.** Stocks trade in sessions, not 24/7. `market_hours` models
  the regular (09:30–16:00 ET), pre-market and post-market sessions plus US
  holidays. Paper trades are placed only during the **regular** session.

## Cross-sectional ranking

Equity alpha is *relative* — the question is not "will this stock rise?" but
"which stocks are strongest **versus the rest of the universe** right now?"
`trading-bot rank` answers that: every stock's factors —

- **momentum** (trailing return), **trend** (signed R² of log-price),
- **mean-reversion** (RSI distance from 50), **low-volatility**,
- and the **ML** score, if a model is loaded —

are z-scored *across the whole universe*, blended with configurable weights,
and ranked. The top fraction becomes the long basket, the bottom the
short/avoid basket. `--live` ranks the real S&P 500.

The 24/7 observer also computes the ranking **every cycle** and stores it, so
the dashboard's **Ranking** tab shows the live long/short baskets updating
alongside the safety score. It is relative-strength stock selection, not a
market call — and still paper / research only.

## Paper / sandbox operations

- **Watchlist** — tickers are screened for average daily dollar volume,
  effective spread, top-of-book size and extreme (halt-bound) intraday moves
  before they are tradeable.
- **Manual approval** — every trade prints a full card (entry/stop/target,
  confidence, risk, expected slippage, liquidity & kill-switch status) and
  requires the operator to type the confirmation phrase.
- **Sandbox (broker paper account)** — opt-in, off by default. Execution is
  locked to a broker **paper-trading** URL allowlist (Alpaca paper); API keys
  are loaded from `.env`, must be read-only by default, and **any key tied to
  a LIVE brokerage account is rejected on connect**. There is no live
  execution path.
- **Risk controls** — per-stock position cap, daily & weekly loss limits,
  max open positions, post-loss cooldown, plus spread/liquidity/chop and
  confidence gates.

## Market Safety Observatory

`trading-bot observe` runs **forever** (until Ctrl+C). Each cycle it fetches
data, runs every analysis (technical, microstructure, volatility, trend,
chop, liquidity, regime/panic), paper-simulates trades, stores every
prediction, and later compares predictions to what actually happened. It is
crash-recovering and restart-safe — all state lives in a SQLite database
(`artifacts/observatory.db`); logs go to `logs/nighttrade.log`.

`trading-bot dashboard` opens a visual dashboard (default
`http://127.0.0.1:8000`) with a giant **MARKET STATUS** card, a
safety-score timeline, a per-symbol table, a cross-sectional **ranking board**
(live long/short baskets), prediction-accuracy analytics, a paper-trading view
and a risk console — live via WebSocket.

The **Market Safety Score** (0-100) summarizes conditions as
`SAFE_TO_OBSERVE` / `WAIT` / `HIGH_RISK` / `UNSAFE`, with a market condition
of `CALM` / `OPPORTUNISTIC` / `MIXED` / `CHOPPY` / `PANIC` / `ILLIQUID` /
`OVEREXTENDED`. It describes *observation conditions for this paper
strategy* — never investment advice.

It is a market **training simulator and safety dashboard**: it monitors many
stocks, learns which regimes it predicts well, flags when its confidence is
"fake", and only ever paper-trades.

## 30-Day Paper Trading Learning Observatory

`trading-bot learn --days 30` runs the observatory as a **multi-day learning
session**. It is restart-safe — the 30-day clock is persisted to
`data/learning_state.json` and resumes where it left off.

The dashboard then shows, at a glance:

- **Progress** — day N/30, learning progress %, cycles completed vs expected,
  uptime, and the current learning phase.
- **Day timeline** — one cell per day, green/yellow/red by how well it ran.
- **Paper Strategy Readiness** — a 0-100 score (NOT ENOUGH DATA → UNRELIABLE →
  PROMISING BUT UNPROVEN → STABLE IN PAPER CONDITIONS → STRONG PAPER
  PERFORMANCE, STILL NOT GUARANTEED). **Capped at 60 before day 30.**
- **Regime dashboard** — accuracy and fake PnL per market regime.
- **Confidence calibration** — does an 80%-confidence prediction actually win
  ~80% of the time? Overconfidence is flagged.
- **Live activity feed** + a **"what is it doing right now"** panel.

The readiness score and reports never say "safe to invest" — only whether
*paper conditions look stable* or the *strategy is currently unreliable*.

## Project layout

| Path | Purpose |
|------|---------|
| `src/nighttrade/models` | Pydantic domain models |
| `src/nighttrade/config` | YAML config loading + validation + env overrides |
| `src/nighttrade/exchanges` | Mock + public read-only stock-data clients, consensus |
| `src/nighttrade/market_hours.py` | US equity session / holiday calendar |
| `src/nighttrade/indicators` | Vectorized technical indicators (no lookahead) |
| `src/nighttrade/microstructure` | Tape-based stock microstructure analysis |
| `src/nighttrade/features` | Shared online+offline feature pipeline |
| `src/nighttrade/labels` | Offline-only label generation |
| `src/nighttrade/ml` | Model training / inference |
| `src/nighttrade/validation` | Walk-forward validation + leakage checks |
| `src/nighttrade/macro` | Macro context engine (mock / Gemini) |
| `src/nighttrade/fusion` | AI decision-fusion engine |
| `src/nighttrade/cross_section` | Cross-sectional factor ranking (long/short baskets) |
| `src/nighttrade/safety` | Real-trading guard + macro/micro kill switches |
| `src/nighttrade/risk` | Slippage, fees, sizing, daily/weekly loss, cooldown |
| `src/nighttrade/watchlist` | Multi-stock liquidity / quality screening |
| `src/nighttrade/paper` | Paper broker + sandbox broker + portfolio + PnL |
| `src/nighttrade/accounting` | Accounting report + tax-CSV export (no transfers) |
| `src/nighttrade/backtest` | Backtesting / simulation engine |
| `src/nighttrade/reporting` | Console / JSON / Markdown + daily reports |
| `src/nighttrade/observatory` | 24/7 observer, SQLite store, safety score, alerts |
| `src/nighttrade/dashboard` | FastAPI backend + single-page visual dashboard |
| `src/nighttrade/cli` | `trading-bot` command line interface |
| `exchanges/sandbox.py` | Broker paper-account execution (URL-allowlisted) |
| `exchanges/credentials.py` | API-key loading + live-account rejection |

## Safety model

See [`docs/SAFETY.md`](docs/SAFETY.md). In short: the only broker in this
codebase is `PaperBroker`. There is no code path — and no config flag — that
sends an order to a real brokerage account.

## Testing

```bash
pytest                       # full suite
pytest -m safety             # "real trading is impossible" tests
pytest -m leakage            # "no lookahead bias" tests
```

## License

MIT — for educational use.
