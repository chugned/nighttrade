# Real-money trading — risks and mitigations

An objective risk inventory for connecting nighttrade (or any copy of it) to a
real broker/exchange for live trading. For each risk: the mechanism, the
magnitude, and how far it can realistically be mitigated.

> nighttrade itself is and remains paper / simulation only. This document is
> analysis, not a plan to enable live trading.

Each risk is tagged: **ELIMINABLE** (proper engineering removes it),
**REDUCIBLE** (can be lowered, not removed), or **NOT FIXABLE BY
ENGINEERING** (only resolved by evidence).

---

## 1. Strategy risk — NOT FIXABLE BY ENGINEERING

- **Negative expected value of an edgeless strategy.** ~50% direction
  accuracy is a zero-mean bet; transaction costs (~0.2% round-trip) are a
  guaranteed subtraction. Zero-mean minus a positive cost = negative drift —
  a steady bleed at roughly the fee rate × turnover.
- **Backtest → live regression.** A backtest flatters; the walk-forward
  number is the honest one, and live lands at it or worse (added slippage).
- **Regime decay.** The calibrator, meta-model and regime-gate are fitted on
  recent history and lag a regime change.

**Mitigation:** you cannot engineer an edge into existence — only measure
whether one exists. Treat edge as a *precondition*:
- Do not deploy capital until months of out-of-sample paper results show
  positive expectancy after pessimistically-modelled costs, over a large
  trade sample (200+), across multiple instruments and periods.
- Keep the strategy simple (fewer tunable parameters → less overfit); use
  purged walk-forward plus a final untouched holdout.
- Add a live circuit-breaker: auto-halt if rolling live accuracy falls below
  break-even for N trades.

## 2. Execution gap (paper vs reality) — REDUCIBLE

- **Slippage** — real fills are worse than the decision price.
- **Latency** — price moves between data, decision and order.
- **Partial / missed fills**; **your own market impact** on thin books.
- Net effect: real returns are systematically below paper returns.

**Mitigation:** limit (maker) orders not market orders; trade only highly
liquid instruments; size small vs order-book depth; co-locate the bot near
the exchange; model costs pessimistically and require edge to survive that.

## 3. Gap / tail risk — PARTLY ELIMINABLE

- **Stops do not hold against gaps** — price can jump straight through a stop
  (news, flash crash, halt). A single gap can exceed many days of P&L.
- Exchange outages, stablecoin depegs, halts at the worst moment.

**Mitigation:**
- **Stay spot — no leverage, no margin, no derivatives.** This *fully
  eliminates* the catastrophic tail: no liquidation, no debt, maximum loss
  capped at deposited capital. Keep this permanently.
- Gap-through-stop itself cannot be removed: reduce it with smaller
  positions, avoiding known high-gap windows, and diversification.

## 4. Software / operational risk — largely ELIMINABLE

The most engineerable category, and the main money-loss risk if neglected.

- **Live bugs cost money; paper bugs cost nothing.** A logic error in the
  order path can place wrong-sized, wrong-direction, or looping orders.
- Observed in paper testing: process crashes, and two bot processes running
  at once on one database. Live, those become unmanaged positions and
  double orders.
- Stale data feeds, reconnect bugs, clock errors → wrong automated decisions.

**Mitigation:**
- **Exchange testnet / paper API** (Yahoo Finance testnet, Alpaca paper): run the
  entire real code path with fake money first — finds execution bugs at zero
  cost. Mandatory bridge between internal sim and real money.
- **Single-instance lock** (PID file / OS lock) — two processes can never run
  at once.
- **Idempotent orders** — unique client-order-id; the same order can never be
  sent twice.
- **Startup reconciliation** — query the exchange for actual positions/orders
  and refuse to act until they match local state. The exchange is the source
  of truth.
- **Hard kill-switches, tested**: max orders/hour, max daily loss → halt, max
  open positions, max notional per order — in code and exchange-side.
- **Staleness guard** — reject decisions on data older than X seconds.
- **Watchdog + alerting** — notify on every order/error/halt; alert or
  auto-flatten on a missed heartbeat.
- **Isolated, minimal, heavily-tested execution module**; a **shadow mode**
  that logs intended orders without sending, diffed against paper for weeks.

## 5. Security risk — largely ELIMINABLE

- API keys are a theft surface; the dashboard and a personal machine add
  attack surface.

**Mitigation:**
- **API keys: trade-only permission, withdrawal DISABLED** — even a fully
  compromised key cannot drain the account. The single most important
  control.
- **IP allowlist** the key to the bot's server only.
- Never store keys in the repo or plaintext config — OS keychain / secrets
  manager / runtime-injected env, locked file permissions.
- Run on a **dedicated hardened server**, not a personal laptop.
- **Dashboard stays strictly read-only and off the public internet**; only
  the bot process can trade.
- Exchange account: 2FA, withdrawal-address allowlist, anti-phishing code.
- Pin and audit dependencies.

## 6. Sizing / capital risk — REDUCIBLE

- A sizing bug that mis-scales a position makes every loss proportionally
  larger; in-code risk caps can be bypassed by a bug.

**Mitigation:** risk limits in code *and tested*; an absolute
max-notional-per-order backstop; start with capital small enough that a 10×
bug is survivable; conservative fixed-fractional sizing.

## 7. Counterparty risk — PARTLY REDUCIBLE

- The exchange holds the money — insolvency, freezes, hacks, lockouts put
  capital at risk independent of strategy performance.

**Mitigation:** reputable/regulated exchange, possibly split across two;
withdraw profits regularly to self-custody; keep only working capital on the
exchange. Cannot be fully removed.

## 8 & 9. Cost asymmetry & behavioural risk — PROCESS, not code

- Fees and taxes are certain; returns are uncertain. The certain cost is only
  worth paying if edge is proven (collapses into risk #1).
- The "small-win trap": a few wins create confidence that justifies scaling
  capital before there is statistically meaningful evidence.

**Mitigation:** pre-commit, in writing, to a scaling rule — no capital
increase until X trades / Y months of positive evidence. Kill-switches bound
automated losses while unattended.

---

## Summary

| Category | How far mitigation goes |
|---|---|
| Operational / software (4) | **Eliminable** with proper engineering |
| Security (5) | **Eliminable** — trade-only keys + isolation |
| Tail / leverage (3) | **Eliminable** by staying spot |
| Execution gap (2) | Reducible only |
| Sizing (6), Counterparty (7) | Reducible only |
| **Strategy edge (1)** | **Not fixable by engineering — gate on evidence** |

## Objective sequence to "remove the threats"

1. Engineer categories 4, 5, 6 properly — single-instance lock, idempotent
   orders, startup reconciliation, tested kill-switches, trade-only keys,
   isolated execution module.
2. Run the whole live code path on the exchange's **testnet / paper API** for
   weeks — validates the engineering with zero money risk.
3. In parallel, gather paper evidence on the **strategy** until it
   demonstrably has edge after costs.
4. Only when both the engineering is proven on testnet *and* the strategy
   shows real edge — deploy with trivially small capital, kill-switches armed.

**Decisive fact:** the engineering risk can be driven to near-zero. The
strategy risk cannot — it is an empirical question, and the strategy's own
walk-forward verdict is currently "no edge". A perfectly-built live system
today would still have negative expected value, because risk #1 is unmet.
