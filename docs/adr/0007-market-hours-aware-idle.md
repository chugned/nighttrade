# ADR-0007: Market-hours-aware idle for the observer

**Status:** Accepted
**Date:** 2026-06-02

## Context

Nighttrade observes the S&P 500 — ~503 US-equity symbols. US equity
markets trade only during the **regular session** (09:30–16:00 ET,
Mon–Fri, minus ~10 holidays/year). That is roughly **32.5 hours/week**
of open market out of 168 total — under 20%.

Before this change the observer ran a full cycle every `interval`
seconds **around the clock**, regardless of session. Each cycle:

- fetches recent candles for the universe (yfinance, the heaviest step),
- runs technical + microstructure + ML inference per symbol,
- writes snapshots / health rows to SQLite.

When the market is closed the observer already (correctly) suppressed
*predictions* (`is_market_open` gate, since a prediction made against a
frozen tape can't be honestly evaluated). But it still did all the
fetching and compute — burning CPU, growing RSS (~6 MB/cycle of
yfinance/pandas internal state, see ADR-0005 / the memory-rotation
gate), and generating heat — to re-evaluate data that does not change
while the market is closed.

The user reported the laptop overheating overnight and asked for the
bot to **freeze when the market is closed and do a single evaluation
shortly before the open**, while still being able to trade and learn
during the session.

## Decision

Make the **outer loop** (`run_forever`) market-hours-aware via a new
pure helper `Observer._next_action(now) -> (action, sleep_s)`:

| Session state | Action | Behaviour |
| --- | --- | --- |
| REGULAR session | `observe` | normal cycle (unchanged) |
| ≤30 min before the open, warm-up not yet run today | `warmup` | run **one** `run_once()` so the model + feature cache are ready at 09:30 ET |
| otherwise closed (overnight / weekend / holiday) | `sleep` | skip the cycle; sleep in 30s chunks, heartbeat each chunk |

Key properties:

1. **One evaluation before the open.** The warm-up cycle runs
   `run_once()` during PRE_MARKET (when `_market_open` is still False),
   which triggers the existing `_train_model_idle` path. Because the
   model will be >`_MODEL_RETRAIN_HOURS` (12h) old after an overnight
   sleep, the warm-up retrains the ML + meta models exactly once, then
   warms the feature cache — so the bot is "ready to trade" at 09:30 ET.
   `_warmup_done_for` (an ET `date`) guarantees the warm-up runs at most
   once per trading day.

2. **Heartbeat continues during sleep.** Sleep is chunked at
   `_SLEEP_CHUNK_S` (30s) and heartbeats each chunk, so mission control
   reads HEALTHY ("intentionally idle"), not "NOT RESPONDING", and
   SIGTERM is honored within ≤30s rather than after a multi-hour sleep.

3. **Far-from-open sleeps are capped** at `_CLOSED_SLEEP_S` (300s) so
   the loop wakes periodically to re-check the clock and stays
   responsive to shutdown.

4. **`now.json` reflects the idle.** A new `_set_now_sleeping` writer
   sets `current_step = "Sleeping — market closed until <open ET>"` and
   `sleeping: true` so the dashboard "Now" panel shows the freeze is
   intentional and when work resumes.

5. **Dev / mock feeds are unaffected.** The gate keys on
   `feed.respects_market_hours`. `LiveMockFeed` (used by the entire test
   suite and the dev workflow) reports `False`, so it keeps cycling
   every interval — every existing test passes unchanged. Only the live
   `RealMarketFeed` (`respects_market_hours = True`) sleeps.

## Consequences

**Positive**

- Awake hours drop from 24h/day to ~7h/day (6.5h session + 30 min
  warm-up) on trading days, and to ~0 on weekends + holidays (just
  heartbeat). Rough cut: **~70% less CPU/RSS/heat** on weekdays and
  ~110 fully-idle days/year.
- Strategy quality is **unchanged**: no S&P trades happen overnight or
  on weekends anyway; we only stop re-evaluating stale data. The model
  is retrained once before each open, on fresh real data, exactly as
  before — just once instead of dozens of times overnight.
- The memory-hygiene rotation (ADR-0005 region) now matters far less,
  because the process spends most of its life asleep rather than
  accumulating yfinance RSS.

**Negative / trade-offs**

- The 30-minute warm-up window is a heuristic. If the model/feature
  warm-up takes longer than 30 min for the full universe, the first
  few minutes of the session run on a cache that is still filling. The
  warm-up does the heavy retrain; per-symbol candle fetches refresh
  fast at the open, so this is low-risk. Revisit if the open shows cold
  predictions.
- Holidays are hardcoded in `market_hours._HOLIDAYS` (2025–2027).
  Refresh annually. An out-of-date holiday set only costs one wasted
  trading-day's worth of cycles (it would observe on a day the market
  is actually shut) — it never trades on a closed market because the
  `is_market_open` prediction gate is independent.
- Early-close half-days (e.g. day after Thanksgiving) are not modeled —
  the observer will keep cycling until 16:00 ET on those. Harmless
  (no trades evaluated post-close), just slightly less efficient.

## Alternatives considered

- **Stop the process entirely overnight via a launchd `StartCalendarInterval`.**
  Rejected: loses warm restart, complicates the watchdog (which checks
  `pgrep`), and an unclean stop risks the two-writer race documented in
  the SIGTERM runbook. Sleeping in-process keeps one long-lived run row
  and clean state.
- **End-of-day evaluation cycle too.** Rejected per the user's explicit
  "one evaluation before the open" — a post-close cycle would add heat
  for no trading benefit (predictions can't be opened after the close).

## References

- `src/nighttrade/observatory/observer.py` — `_next_action`,
  `_set_now_sleeping`, the `run_forever` idle branch.
- `src/nighttrade/market_hours.py` — session calendar (pre-existing).
- `tests/test_observer_market_hours.py` — 11 tests pinning every
  transition (open / overnight / weekend / holiday / warm-up-once /
  mock-feed-always-on / sleep-cap / loop-skips-run_once).
- ADR-0005 — yfinance RSS growth + memory rotation (the cost this
  change largely sidesteps).

## Addendum (2026-06-03): lean startup — overnight RSS 665 MB → 176 MB

The first cut of this ADR stopped CPU work while closed but NOT memory:
the CLI still did its eager 503-symbol warm at startup, pinning ~665 MB
RSS for the whole overnight sleep (Python never returns the allocation to
the OS — same root cause as the memory-rotation gate). Observed live on
the Polaris dashboard: a "sleeping" bot holding 665 MB.

Fix: `market_hours.should_warm_now()` gates the eager warm. The `observe`
CLI now warms the full universe up front only when the market is open or
within the pre-open window; booting into a closed market starts lean
(watchlist = full universe, no fetch) and the pre-open warm-up cycle does
the first real fetch on demand (`candles_at` → `_ensure_fresh`). Verified
live: lean startup logged, RSS = 176 MB (was 665 MB), bot still sleeping
and fully ready for the pre-open warm-up. Tests:
`test_market_hours_warm_decision.py`.
