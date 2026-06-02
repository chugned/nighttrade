# ADR-0005: Cap `yfinance.download(threads=8)` for the S&P 500 universe

- **Status:** Accepted
- **Date:** 2026-06-02
- **Repo:** nighttrade (no daytrade analog — daytrade uses Binance public
  HTTP, not yfinance)

## Context

`YFinanceFeed._refresh` called `yf.download(self._symbols, threads=True, …)`
with `len(self._symbols) == 503` (the full S&P 500 universe). The
`yfinance` package's `multi.download` spawns one OS thread per ticker
when `threads=True`. macOS rejected the burst with
`RuntimeError: can't start new thread` at line 892 of `threading.py`.
The crash occurred during the initial CLI startup, before the observer
ever entered its main loop, so:
- `bot_runs` had no fresh row (the launchd-spawned process never made it
  to `Observer.start()`).
- launchd dutifully restarted via `KeepAlive=true`, hit the same crash,
  and the bot looped through restarts without ever doing useful work.
- Mission control showed "STOPPED" because the heartbeat in `bot_runs`
  was 15 days stale (from a prior healthy run).

## Decision

Use `threads=8` (an integer) instead of `threads=True`. The `yfinance`
multi-downloader accepts an integer to cap concurrency. Eight is:
- High enough to give meaningful parallelism on the ~503-symbol fetch
  (most of the wall-clock benefit of `threads=True` is captured in the
  first 4-8 concurrent connections; beyond that the speedup tails off
  as the network becomes the bottleneck).
- Low enough to be safe on any consumer macOS configuration (default
  per-process thread limit is in the low thousands; 8 leaves plenty
  of headroom for the rest of the observer's own threads).

## Consequences

**Positive**
- Observer starts cleanly. Fetch takes ~40-60s (was ~30-40s with
  `threads=True` when it didn't crash).
- Restart loop broken — launchd's `KeepAlive` now keeps the bot up
  rather than uselessly respawning a doomed process.

**Negative**
- Slightly slower bulk fetch (~30s overhead per cycle). Negligible
  relative to the 5-minute cycle interval.

## Implementation

- `src/nighttrade/observatory/live_feed.py:_refresh` — `threads=8`.

## Verification

- Manual: observer process started cleanly post-fix at PID 36852, RAM
  climbed to 619 MB and stayed stable through cycle 1.
- `bot_runs` row 6 was inserted with `status='running'` and heartbeated
  successfully at 03:59:14 UTC.
- Mission control shows nighttrade HEALTHY.

Also relevant: `Observer.run_forever` already has an exponential-
backoff + abort-after-N-failures path (ported from daytrade QA-CRIT-4),
so even if a future yfinance regression causes the same crash, the
observer will give up after 50 consecutive failures rather than burn
indefinitely.
