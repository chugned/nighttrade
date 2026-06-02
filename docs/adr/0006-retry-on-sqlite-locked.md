# ADR-0006: Retry-with-backoff for SQLite `database is locked` on writes

- **Status:** Accepted
- **Date:** 2026-06-02
- **Repo:** nighttrade
- **Tracks:** P4-2 from the daytrade mission-control roadmap

## Context

On 2026-06-02 at 03:50 the observer logged:

```
ERROR nighttrade.observatory.observer: error observing SW
sqlite3.OperationalError: database is locked
```

The cycle continued — one symbol skipped, no real damage. But the
write **should** have succeeded with a short retry rather than being
dropped on the floor.

State at the time:
- WAL mode active (`PRAGMA journal_mode=WAL`).
- Connection had `timeout=10.0` (sqlite's built-in busy_timeout).
- Likely concurrent op: a `PRAGMA wal_checkpoint(TRUNCATE)` from the
  daily-rollover prune job (ADR-0004), or a long-held read snapshot
  from the dashboard process.

WAL keeps readers and writers from blocking each other for the most
part, but a few cases still surface `database is locked`:
- The single-writer rule: two writers contend, the loser raises
  after the `busy_timeout` elapses.
- `wal_checkpoint(TRUNCATE)` needs the write lock briefly.
- Some sqlite versions raise `SQLITE_BUSY` immediately for
  `BEGIN EXCLUSIVE` regardless of `busy_timeout`.

The connection-level `timeout` already absorbs short waits, but it's
not enough — once it expires, the error bubbles to Python and the
caller has no second chance.

## Decision

Introduce a thin proxy `_RetryingConnection` wrapping the real
`sqlite3.Connection`:

- Intercepts `execute` and `commit`.
- On `OperationalError` whose message contains `locked` or `busy`,
  sleeps `base_delay` seconds, retries, doubles the delay each time
  up to `max_delay`.
- Gives up after `max_retries` retries — re-raises so the outer
  error path still runs.
- Other `OperationalError`s (syntax, missing table, schema mismatch)
  re-raise immediately — silent retry on real bugs would be worse
  than the original outage.
- All other connection attributes (`row_factory`, `executescript`,
  `rollback`, `close`, `in_transaction`, …) pass through.

Defaults: `max_retries=5`, `base_delay=0.05`, `max_delay=2.0`. With
exponential backoff that gives the bot ~6 seconds of "wait it out"
before surfacing the error.

Also bumped the connection-level `timeout=10.0` → `timeout=30.0`
while we're here. The proxy is the second line of defence, not the
first.

## Why not just bump the timeout further?

Bumping the connection `timeout` to 60+ seconds defends against the
same scenarios but with a worse failure mode: the whole cycle blocks
on one stuck write for a minute, instead of getting back signal
after a fraction of a second. The retry proxy gives up after ~6s
worst-case AND yields control between retries (`time.sleep`), so a
SIGTERM during the retry window is processed promptly.

## Why not swallow the error entirely?

The retry proxy re-raises after `max_retries`. If the lock genuinely
won't release in 6 seconds, something is wrong (a stuck transaction
elsewhere, disk i/o pathology) and the observer logging an error +
skipping the symbol is the correct behaviour — silent swallowing
would mean a write disappearing without trace.

## Consequences

**Positive**
- Transient lock contention (the common case) is now invisible to
  callers — no more single-symbol drops on prune-job timing.
- Real lock pathology (stuck writer) still surfaces, just after a
  ~6s retry window instead of immediately.
- Zero call-site changes — every existing `self._conn.execute(...)`
  and `self._conn.commit()` benefits transparently.

**Negative**
- A `time.sleep` inside the data-access layer means a single slow
  write can take up to ~6s under contention. Acceptable: the cycle
  interval is 300s and the observer is single-threaded per cycle.
- One extra layer of indirection in stack traces (`_RetryingConnection.execute`
  → `_RetryingConnection._retry` → `sqlite3.Connection.execute`).

## Implementation

- `src/nighttrade/observatory/database.py`:
  - `_is_locked_error(exc)` helper — true for `locked`/`busy`.
  - `_RetryingConnection` proxy class.
  - `ObservatoryDB.__init__` wraps the raw connection.
  - `timeout=10.0` → `timeout=30.0` on connect.

## Verification

- `tests/test_db_retry_on_locked.py` — 8 unit + integration tests:
  - retries on `database is locked`,
  - retries on `database is busy`,
  - does NOT retry on unrelated `OperationalError`,
  - exhausts retries and re-raises,
  - backoff genuinely sleeps (not busy-spinning),
  - passthrough for non-wrapped attributes,
  - end-to-end against a real `ObservatoryDB` with concurrent lock.
- Full nighttrade `pytest` suite green.

## Daytrade parity

Daytrade has the same SQLite write surface in
`daytrade/src/daytrade/observatory/database.py`. The same proxy
should be ported there as P4-2-DAYTRADE — but daytrade has not
observed this bug in production (its observer cycle is 60s, the
prune-job collision window is narrower). Port reactively if seen,
or proactively if any new long-running consumer is added.
