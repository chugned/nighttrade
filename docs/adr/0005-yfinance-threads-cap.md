# ADR-0005: `yfinance.download(threads=False)` for the S&P 500 universe

- **Status:** Accepted (revised from threads=8)
- **Date:** 2026-06-02 (revised same day)
- **Repo:** nighttrade
- **Supersedes:** earlier in-session decision to use threads=8

## Context

`YFinanceFeed._refresh` calls `yf.download(503_symbols, threads=…)`.
The `yfinance` `multi.download` spawns one OS thread per ticker when
`threads=True`. macOS rejected the burst with
`RuntimeError: can't start new thread` (`threading.py:892`).

First attempt: cap to `threads=8`. This worked in isolation but
failed again hours later under modest concurrent thread pressure —
the user's host had ~2386 of its ~2784 (`ulimit -u`) user threads in
use across all processes (browsers, dev tools, multiple bots), so
even 8 fresh threads was too many.

## Decision

Use `threads=False` (sequential fetch). Trade-off:

- **Slower per fetch**: ~90s vs ~40s. Negligible against the 300s
  cycle interval.
- **Bulletproof**: succeeds regardless of host thread pressure.
- **Memory profile unchanged**: same DataFrame shape, no
  concurrency-related buffering.

## Consequences

**Positive**
- Reliable startup under any system load.
- No more launchd restart loops on `RuntimeError`.

**Negative**
- ~50s additional latency per cycle.
- If the universe ever grows past ~5000 symbols, sequential becomes
  truly slow (~10 min per fetch) and we'd need to revisit with batched
  parallelism.

## Implementation

- `src/nighttrade/observatory/live_feed.py:_refresh` — `threads=False`.

## Verification

After the change, observer started cleanly via `launchctl kickstart`,
ran cycle 1 + cycle 2 without thread-related crashes, RAM stayed at
~220 MB, heartbeat updated in the DB on schedule.

The earlier launchd `KeepAlive=true` machinery is preserved: if a
*different* yfinance failure occurs (network, throttling), launchd
will still restart the observer after the `ThrottleInterval`.
