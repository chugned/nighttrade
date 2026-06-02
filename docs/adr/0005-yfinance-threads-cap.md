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

## 2026-06-02 second amendment — we own the parallel pool (chunked fetch)

`threads=False` was bulletproof but ~110s sequential for 503 symbols.
That's the dominant cost of every cycle. Profile:

| Approach | 100 syms | Extrapolated 503 |
| --- | ---: | ---: |
| `threads=False`, 1 batch (sequential) | 21.8s | ~110s |
| 8 chunks × `threads=False`, parallel via OUR pool | 5.5s | **~28s** |

The unsafe path is `yf.download(503_syms, threads=N)` which lets
yfinance spawn N OS threads PER-CALL (we observed it spawn many
more under load). The SAFE path: split into N chunks, run each
chunk single-threaded (threads=False), parallelise via our OWN
`ThreadPoolExecutor(max_workers=N)`. Total OS threads = N exactly.

Implementation in `YFinanceFeed._refresh`:
- Split `self._symbols` into `_MAX_FETCH_WORKERS` chunks (currently 8).
- Each worker calls `yf.download(chunk, threads=False)` and slices
  per-symbol DataFrames out of the MultiIndex result.
- Workers return `Dict[str, DataFrame]`; main thread merges.

Tests pin:
- `_MAX_FETCH_WORKERS <= 8` (the load-bearing safety invariant).
- Every chunk call has `threads=False` (no double-parallelism).
- Chunks cover every symbol exactly once.
- 503 syms → exactly 8 chunks (one per worker).
- Small universes don't overshoot the chunk count.
- Actual parallelism via a barrier-synchronised fake.

Expected impact: cycle time on nighttrade drops from ~480s to
~400s (fetch portion of cycle: 110s → 28s, savings ~80s).
