# Safety model

`nighttrade` is an **educational research platform**. It is structurally
incapable of placing a real trade. This document explains how that guarantee
is enforced — not merely promised.

## 1. There is exactly one broker

The only object that executes orders is `nighttrade.paper.PaperBroker`. It
matches orders against simulated liquidity and updates an in-memory portfolio.
No socket is ever opened to an exchange order-entry endpoint.

## 2. The real-trading path raises

`nighttrade.safety.guard` exposes `forbid_real_trading()`. Any function that
*would* represent a live-execution boundary calls it, which raises:

```python
NotImplementedError("Real trading is disabled.")
```

There is no argument, config flag, or environment variable that suppresses it.

## 3. Config validation refuses unsafe values

`config.SafetyConfig` validates that:

- `live_trading_enabled` is `false`
- `allow_real_orders` is `false`
- `paper_trading` is `true`

Any other combination makes config loading **fail**. The flags exist so the
safe values are explicit and test-asserted — not so they can be changed.

## 4. No credentials, no leverage

- Stock-data clients (yfinance, stooq) use **public, read-only** market-data
  endpoints only — no API keys or secrets are needed or stored for data.
- There is no margin, leverage, options, or short-selling code path.
- Network access is **off by default** (`runtime.allow_network: false`); with
  it on, only public read-only data endpoints are reachable.

## 5. Tests enforce all of the above

`pytest -m safety` runs the safety suite: it asserts `forbid_real_trading()`
raises, that unsafe configs are rejected, and that no module exposes a
live-order function.

## 6. Sandbox execution is broker-paper-account-only

The optional sandbox layer (`nighttrade.exchanges.sandbox`) can place orders on
a broker **paper-trading account** — simulated shares, no real money. Its
safety is structural:

- A `SandboxExchangeClient` can only be built against a URL in a hard-coded
  **paper-account allowlist** (`paper-api.alpaca.markets`). There is no
  parameter for an arbitrary URL; every request re-asserts it, and the live
  hosts (`api.alpaca.markets`, `broker-api.alpaca.markets`) are explicitly
  blocked.
- On connect, the client reads the account and **rejects any key tied to a
  LIVE brokerage account or with funding/transfer access** (`LiveAccountError`).
- Keys must be **read-only by default**; placing paper orders requires the
  operator to explicitly set `sandbox.require_read_only_keys: false`. Even
  then, live accounts are still banned.
- Sandbox is **off by default** (`sandbox.enabled: false`) and needs
  `runtime.allow_network: true` plus paper-account keys in `.env`.

There is no live-brokerage execution client anywhere in the codebase.

## 7. No money movement

There is no bank-transfer, withdrawal, wire, or payout code. The accounting
layer (`nighttrade.accounting`) only **reports** simulated results and exports a
tax CSV; it never moves funds. `pytest -m safety` includes a test that scans
the source for money-movement function definitions and asserts there are none.

## 8. Manual approval

Every trade — even a paper trade — passes through a manual-approval card and
requires the operator to type the confirmation phrase. Nothing executes on a
default answer, an empty line, or a timeout.

## 9. The 24/7 observatory is observe-only

The continuous observer and the dashboard add monitoring, not capability:

- The observer fetches data, analyses, **paper-simulates** and records — it
  has no order-entry method to any real exchange. `pytest -m safety` asserts
  `Observer` exposes no `place_order` / `withdraw` / `connect_wallet`.
- The dashboard backend is **read-only**: it queries the observatory's SQLite
  database and renders it. It has no write or order endpoint; `/api/health`
  reports `real_trading: false`, `paper_only: true`.
- The Safety Score uses *observation-condition* language
  (`SAFE_TO_OBSERVE`, `WAIT`, ...) — never "safe to invest". The daily report
  describes whether conditions favoured the paper strategy; it never says to
  buy or sell.
- No wallet, no bank transfer, no payment code exists anywhere — two
  source-scanning safety tests enforce this across the whole package.

## Why this matters

Backtests and paper trading systematically *overstate* performance. Making
real execution impossible removes the temptation to act on results that have
not survived contact with real markets — slippage, latency, competition, and
the simple fact that the future is not the past.
