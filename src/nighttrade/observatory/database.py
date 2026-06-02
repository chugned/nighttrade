"""SQLite persistence for the Market Safety Observatory.

A single SQLite file (``artifacts/observatory.db``) is the system of record.
It is written by the observer process and read by the dashboard process; WAL
mode keeps those two from blocking each other.

The store is **append-mostly**: snapshots, predictions, paper trades, safety
scores, health rows and errors are only ever inserted. The few mutations are
intentional and bounded — a prediction's outcome is filled in once its
horizons mature, and a bot run's heartbeat/stop fields are updated as it runs.

Nothing here touches money, wallets, or a real exchange.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, symbol TEXT NOT NULL,
    price REAL, rsi REAL, macd REAL, volatility REAL, trend_slope REAL,
    spread_bps REAL, imbalance REAL, chop INTEGER, liquidity_notional REAL,
    regime TEXT
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, symbol TEXT NOT NULL,
    direction TEXT, confidence REAL, entry REAL, stop REAL, target REAL,
    market_condition TEXT, fused_score REAL, reasons TEXT,
    evaluated INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS prediction_outcomes (
    prediction_id INTEGER PRIMARY KEY,
    symbol TEXT, predicted_ts TEXT,
    price_5m REAL, price_15m REAL, price_1h REAL, price_4h REAL,
    directionally_correct INTEGER, stop_hit INTEGER, target_hit INTEGER,
    realized_pnl REAL, slippage_estimate REAL, evaluated_ts TEXT,
    FOREIGN KEY (prediction_id) REFERENCES predictions(id)
);
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_open TEXT NOT NULL, ts_close TEXT, symbol TEXT NOT NULL,
    side TEXT, quantity REAL, entry_price REAL, exit_price REAL,
    pnl REAL, fees REAL, slippage REAL, status TEXT DEFAULT 'open',
    stop REAL, target REAL
);
CREATE TABLE IF NOT EXISTS safety_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, score REAL, status TEXT, condition TEXT,
    reasons TEXT, breakdown TEXT, equity REAL, drawdown_pct REAL
);
CREATE TABLE IF NOT EXISTS symbol_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, symbol TEXT NOT NULL, price REAL,
    volume_24h REAL, spread_bps REAL, book_notional REAL,
    healthy INTEGER, rejections TEXT, recent_accuracy REAL,
    safety_score REAL, status TEXT
);
CREATE TABLE IF NOT EXISTS bot_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ts TEXT NOT NULL, last_heartbeat_ts TEXT, stopped_ts TEXT,
    cycles INTEGER DEFAULT 0, status TEXT DEFAULT 'running', pid INTEGER
);
CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, context TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS learning_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts TEXT NOT NULL, target_days INTEGER, interval_seconds INTEGER,
    status TEXT DEFAULT 'active', cycles_completed INTEGER DEFAULT 0,
    last_update_ts TEXT
);
CREATE TABLE IF NOT EXISTS daily_metrics (
    day_date TEXT PRIMARY KEY, day_number INTEGER, cycles INTEGER,
    expected_cycles INTEGER, uptime_pct REAL, predictions_made INTEGER,
    predictions_evaluated INTEGER, accuracy REAL, fake_pnl REAL,
    drawdown_pct REAL, paper_trades INTEGER, skipped INTEGER,
    dominant_regime TEXT, dominant_condition TEXT, errors INTEGER,
    status TEXT, readiness REAL
);
CREATE TABLE IF NOT EXISTS regime_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, day_number INTEGER, condition TEXT, regime TEXT,
    safety_score REAL
);
CREATE TABLE IF NOT EXISTS readiness_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, score REAL, level TEXT, capped INTEGER,
    day_number INTEGER, breakdown TEXT, blockers TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, level TEXT, kind TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS activity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, level TEXT, event TEXT, detail TEXT, cycle INTEGER
);
CREATE TABLE IF NOT EXISTS rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gate_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, symbol TEXT, gate TEXT NOT NULL,
    allowed INTEGER NOT NULL, reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_snap_symbol_ts ON market_snapshots(symbol, ts);
CREATE INDEX IF NOT EXISTS ix_activity_id ON activity_events(id);
CREATE INDEX IF NOT EXISTS ix_regime_ts ON regime_periods(ts);
CREATE INDEX IF NOT EXISTS ix_pred_symbol_ts ON predictions(symbol, ts);
CREATE INDEX IF NOT EXISTS ix_pred_evaluated ON predictions(evaluated);
CREATE INDEX IF NOT EXISTS ix_trades_status ON paper_trades(status);
CREATE INDEX IF NOT EXISTS ix_safety_ts ON safety_scores(ts);
"""

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = _REPO_ROOT / "artifacts" / "observatory.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _is_locked_error(exc: sqlite3.OperationalError) -> bool:
    """Return True when this OperationalError is SQLITE_BUSY/SQLITE_LOCKED.

    SQLite stringifies the lock errors as 'database is locked' or
    'database is busy' depending on which lock was contended. Other
    OperationalErrors (syntax errors, missing tables, ALTER conflicts)
    must NOT trigger a retry — they're real bugs and silent retry
    would mask them.
    """
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


class _RetryingConnection:
    """Transparent ``sqlite3.Connection`` proxy that retries write ops on
    SQLITE_BUSY/SQLITE_LOCKED with exponential backoff.

    ``sqlite3.connect(timeout=...)`` already absorbs short waits at the
    VFS layer, but doesn't help with higher-level contention like a
    concurrent ``PRAGMA wal_checkpoint(TRUNCATE)`` or a long-held
    read snapshot. P4-2 observed exactly this on 2026-06-02: one
    symbol's write surfaced ``database is locked`` and was dropped.

    The proxy adds a Python-level retry budget on top:
    - ``execute`` and ``commit`` are wrapped.
    - On lock/busy error, wait, retry, doubling the delay each time
      up to ``2.0s``.
    - After ``max_retries`` retries, re-raise (do not swallow).
    - Non-lock errors re-raise immediately.
    - Any other attribute access (``row_factory``, ``executescript``,
      ``close``, …) passes through unchanged.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        max_retries: int = 5,
        base_delay: float = 0.05,
        max_delay: float = 2.0,
    ) -> None:
        self._conn = conn
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay

    def _retry(self, op: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        delay = self._base_delay
        for attempt in range(self._max_retries + 1):
            try:
                return op(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if not _is_locked_error(exc):
                    raise
                if attempt >= self._max_retries:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, self._max_delay)

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._retry(self._conn.execute, *args, **kwargs)

    def commit(self) -> None:
        self._retry(self._conn.commit)

    def __getattr__(self, name: str) -> Any:
        # Anything not overridden above falls through to the real
        # connection — including row_factory, executescript, rollback,
        # close, in_transaction etc.
        return getattr(self._conn, name)


class ObservatoryDB:
    """Thin, dependency-free DAO over the observatory SQLite database."""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # ADR-0006: timeout=30s gives sqlite's built-in busy_timeout
        # ample headroom for short bursts; _RetryingConnection on top
        # catches the residual cases where a Python-visible
        # ``database is locked`` still bubbles up.
        raw = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30.0)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA journal_mode=WAL")
        raw.execute("PRAGMA synchronous=NORMAL")
        raw.executescript(_SCHEMA)
        raw.commit()
        self._conn = _RetryingConnection(raw)

    def close(self) -> None:
        self._conn.close()

    # -- inserts -------------------------------------------------------------

    def insert_snapshot(self, **f: Any) -> int:
        return self._insert("market_snapshots", {"ts": f.get("ts", _now()), **f})

    def insert_prediction(self, **f: Any) -> int:
        row = {"ts": f.get("ts", _now()), "evaluated": 0, **f}
        if isinstance(row.get("reasons"), (list, dict)):
            row["reasons"] = json.dumps(row["reasons"])
        return self._insert("predictions", row)

    def upsert_outcome(self, prediction_id: int, **f: Any) -> None:
        """Insert or update a prediction's outcome (filled in as it matures)."""
        cols = {"prediction_id": prediction_id, "evaluated_ts": _now(), **f}
        placeholders = ", ".join("?" for _ in cols)
        names = ", ".join(cols)
        updates = ", ".join(f"{k}=excluded.{k}" for k in cols if k != "prediction_id")
        self._conn.execute(
            f"INSERT INTO prediction_outcomes ({names}) VALUES ({placeholders}) "
            f"ON CONFLICT(prediction_id) DO UPDATE SET {updates}",
            list(cols.values()),
        )
        self._conn.commit()

    def mark_prediction_evaluated(self, prediction_id: int) -> None:
        self._conn.execute("UPDATE predictions SET evaluated=1 WHERE id=?", (prediction_id,))
        self._conn.commit()

    def upsert_outcome_and_mark_evaluated(self, prediction_id: int, **f: Any) -> None:
        """Ported from daytrade QA-HIGH-1: outcome upsert + evaluated
        flag in one transaction, so a crash between them does NOT
        cause duplicate work on the next cycle."""
        cols = {"prediction_id": prediction_id, "evaluated_ts": _now(), **f}
        placeholders = ", ".join("?" for _ in cols)
        names = ", ".join(cols)
        updates = ", ".join(f"{k}=excluded.{k}" for k in cols if k != "prediction_id")
        try:
            self._conn.execute("BEGIN")
            self._conn.execute(
                f"INSERT INTO prediction_outcomes ({names}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT(prediction_id) DO UPDATE SET {updates}",
                list(cols.values()),
            )
            self._conn.execute("UPDATE predictions SET evaluated=1 WHERE id=?", (prediction_id,))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def total_realised_pnl(self) -> float:
        """Ported from daytrade QA-HIGH-6: SUM over all closed trades,
        not a 500-row window."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(pnl), 0.0) FROM paper_trades " "WHERE status='closed'"
        ).fetchone()
        return float(row[0] if row else 0.0)

    def historical_peak_equity(self) -> float:
        """Ported from daytrade QA-HIGH-2: MAX equity from safety_scores
        so Observer can seed _peak_equity on restart."""
        row = self._conn.execute(
            "SELECT MAX(equity) FROM safety_scores " "WHERE equity IS NOT NULL"
        ).fetchone()
        peak = row[0] if row and row[0] is not None else 0.0
        return float(peak)

    def prune_old(self, days: int = 30) -> Dict[str, int]:
        """Ported from daytrade QA-HIGH-3: drop aged rows + checkpoint
        the WAL. Safe to call from the daily roll-over."""
        cutoff = "datetime('now', '-' || ? || ' days')"
        targets = {
            "activity_events": "ts",
            "market_snapshots": "ts",
            "symbol_health": "ts",
        }
        deleted: Dict[str, int] = {}
        for table, ts_col in targets.items():
            try:
                cur = self._conn.execute(f"DELETE FROM {table} WHERE {ts_col} < {cutoff}", (days,))
                deleted[table] = cur.rowcount
            except sqlite3.OperationalError:
                deleted[table] = 0
        self._conn.commit()
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("PRAGMA optimize")
        except sqlite3.OperationalError:
            pass
        return deleted

    def insert_paper_trade(self, **f: Any) -> int:
        return self._insert(
            "paper_trades", {"ts_open": f.get("ts_open", _now()), "status": "open", **f}
        )

    def close_paper_trade(
        self,
        trade_id: int,
        exit_price: float,
        pnl: float,
        fees: float,
        slippage: float,
        ts_close: Optional[str] = None,
    ) -> None:
        self._conn.execute(
            "UPDATE paper_trades SET ts_close=?, exit_price=?, pnl=?, fees=?, "
            "slippage=?, status='closed' WHERE id=?",
            (ts_close or _now(), exit_price, pnl, fees, slippage, trade_id),
        )
        self._conn.commit()

    def insert_safety_score(self, **f: Any) -> int:
        row = {"ts": f.get("ts", _now()), **f}
        for key in ("reasons", "breakdown"):
            if isinstance(row.get(key), (list, dict)):
                row[key] = json.dumps(row[key])
        return self._insert("safety_scores", row)

    def insert_symbol_health(self, **f: Any) -> int:
        row = {"ts": f.get("ts", _now()), **f}
        if isinstance(row.get("rejections"), (list, dict)):
            row["rejections"] = json.dumps(row["rejections"])
        return self._insert("symbol_health", row)

    def insert_error(self, context: str, message: str) -> int:
        return self._insert("errors", {"ts": _now(), "context": context, "message": message})

    # -- bot run lifecycle ---------------------------------------------------

    def mark_dangling_runs_crashed(self) -> int:
        """Mark runs as crashed ONLY when their PID is no longer alive
        (ported from daytrade QA-RUNS-1). Prevents a sibling bot's
        startup from incorrectly marking the live observer as crashed.
        """
        import os as _os

        rows = self._conn.execute("SELECT id, pid FROM bot_runs WHERE status='running'").fetchall()
        crashed = 0
        for row_id, pid in rows:
            alive = False
            try:
                if pid:
                    _os.kill(int(pid), 0)
                    alive = True
            except ProcessLookupError:
                alive = False
            except (PermissionError, OSError):
                alive = True
            if not alive:
                self._conn.execute(
                    "UPDATE bot_runs SET status='crashed', stopped_ts=? " "WHERE id=?",
                    (_now(), row_id),
                )
                crashed += 1
        self._conn.commit()
        return crashed

    def start_bot_run(self, pid: int) -> int:
        now = _now()
        return self._insert(
            "bot_runs",
            {
                "started_ts": now,
                "last_heartbeat_ts": now,
                "cycles": 0,
                "status": "running",
                "pid": pid,
            },
        )

    def heartbeat(self, run_id: int, cycles: int) -> None:
        """A heartbeat is the definitive 'I am alive' signal: also
        restore status='running' and clear any spurious stopped_ts
        (ported from daytrade QA-RUNS-2)."""
        self._conn.execute(
            "UPDATE bot_runs SET last_heartbeat_ts=?, cycles=?, "
            "status='running', stopped_ts=NULL WHERE id=?",
            (_now(), cycles, run_id),
        )
        self._conn.commit()

    def stop_bot_run(self, run_id: int, status: str = "stopped") -> None:
        self._conn.execute(
            "UPDATE bot_runs SET status=?, stopped_ts=? WHERE id=?", (status, _now(), run_id)
        )
        self._conn.commit()

    # -- learning sessions ---------------------------------------------------

    def start_learning_session(self, target_days: int, interval_seconds: int) -> int:
        now = _now()
        return self._insert(
            "learning_sessions",
            {
                "start_ts": now,
                "target_days": target_days,
                "interval_seconds": interval_seconds,
                "status": "active",
                "cycles_completed": 0,
                "last_update_ts": now,
            },
        )

    def update_learning_session(self, session_id: int, cycles: int, status: str = "active") -> None:
        self._conn.execute(
            "UPDATE learning_sessions SET cycles_completed=?, status=?, "
            "last_update_ts=? WHERE id=?",
            (cycles, status, _now(), session_id),
        )
        self._conn.commit()

    def current_learning_session(self) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM learning_sessions " "ORDER BY id DESC LIMIT 1")

    # -- daily metrics / regimes / readiness ---------------------------------

    def upsert_daily_metric(self, day_date: str, **f: Any) -> None:
        cols = {"day_date": day_date, **f}
        names = ", ".join(cols)
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{k}=excluded.{k}" for k in cols if k != "day_date")
        self._conn.execute(
            f"INSERT INTO daily_metrics ({names}) VALUES ({placeholders}) "
            f"ON CONFLICT(day_date) DO UPDATE SET {updates}",
            list(cols.values()),
        )
        self._conn.commit()

    def daily_metrics(self) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM daily_metrics ORDER BY day_date")

    def insert_regime_period(self, **f: Any) -> int:
        return self._insert("regime_periods", {"ts": f.get("ts", _now()), **f})

    def insert_ranking(self, ts: str, payload: Dict[str, Any]) -> int:
        """Store one cross-sectional ranking snapshot (payload kept as JSON)."""
        return self._insert("rankings", {"ts": ts, "payload": json.dumps(payload)})

    def latest_ranking(self) -> Optional[Dict[str, Any]]:
        """Return the most recent cross-sectional ranking, or None."""
        row = self._one("SELECT payload FROM rankings ORDER BY id DESC LIMIT 1")
        if not row:
            return None
        try:
            return json.loads(row["payload"])
        except (ValueError, TypeError):
            return None

    def insert_gate_event(
        self, symbol: Optional[str], gate: str, allowed: bool, reason: str, ts: Optional[str] = None
    ) -> int:
        """Record a strategy-gate decision (allowed / blocked) for a symbol."""
        return self._insert(
            "gate_events",
            {
                "ts": ts or _now(),
                "symbol": symbol,
                "gate": gate,
                "allowed": int(allowed),
                "reason": reason,
            },
        )

    def gate_block_counts(self) -> Dict[str, int]:
        """Number of *blocked* entries per gate."""
        rows = self._conn.execute(
            "SELECT gate, COUNT(*) AS c FROM gate_events WHERE allowed=0 " "GROUP BY gate"
        ).fetchall()
        return {r["gate"]: r["c"] for r in rows}

    def recent_gate_events(self, limit: int = 60) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM gate_events ORDER BY id DESC LIMIT ?", (limit,))

    def regime_periods(self, limit: int = 2000) -> List[Dict[str, Any]]:
        return list(
            reversed(self._all("SELECT * FROM regime_periods ORDER BY id DESC LIMIT ?", (limit,)))
        )

    def insert_readiness(self, **f: Any) -> int:
        row = {"ts": f.get("ts", _now()), **f}
        if isinstance(row.get("breakdown"), (list, dict)):
            row["breakdown"] = json.dumps(row["breakdown"])
        if isinstance(row.get("blockers"), (list, dict)):
            row["blockers"] = json.dumps(row["blockers"])
        return self._insert("readiness_scores", row)

    def latest_readiness(self) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM readiness_scores " "ORDER BY id DESC LIMIT 1")

    def readiness_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        return list(
            reversed(self._all("SELECT * FROM readiness_scores ORDER BY id DESC LIMIT ?", (limit,)))
        )

    # -- alerts / activity feed ----------------------------------------------

    def insert_alert(self, level: str, kind: str, message: str) -> int:
        return self._insert(
            "alerts", {"ts": _now(), "level": level, "kind": kind, "message": message}
        )

    def recent_alerts(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))

    def insert_activity(
        self, event: str, detail: str = "", level: str = "info", cycle: int = 0
    ) -> int:
        return self._insert(
            "activity_events",
            {"ts": _now(), "level": level, "event": event, "detail": detail, "cycle": cycle},
        )

    def recent_activity(self, limit: int = 60) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM activity_events " "ORDER BY id DESC LIMIT ?", (limit,))

    # -- queries -------------------------------------------------------------

    def latest_safety_score(self) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM safety_scores ORDER BY id DESC LIMIT 1")

    def safety_score_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        return list(
            reversed(self._all("SELECT * FROM safety_scores ORDER BY id DESC LIMIT ?", (limit,)))
        )

    def equity_curve(self, limit: int = 3000) -> List[Dict[str, Any]]:
        """Per-cycle paper-equity history (drives the accumulated-gain chart)."""
        return list(
            reversed(
                self._all(
                    "SELECT ts, equity, drawdown_pct, score FROM safety_scores "
                    "WHERE equity IS NOT NULL ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            )
        )

    def latest_symbol_health(self) -> List[Dict[str, Any]]:
        return self._all(
            "SELECT sh.* FROM symbol_health sh JOIN ("
            "  SELECT symbol, MAX(id) AS mid FROM symbol_health GROUP BY symbol"
            ") last ON sh.id = last.mid ORDER BY sh.symbol"
        )

    def latest_snapshots(self) -> List[Dict[str, Any]]:
        return self._all(
            "SELECT ms.* FROM market_snapshots ms JOIN ("
            "  SELECT symbol, MAX(id) AS mid FROM market_snapshots GROUP BY symbol"
            ") last ON ms.id = last.mid ORDER BY ms.symbol"
        )

    def snapshots_for(self, symbol: str, limit: int = 200) -> List[Dict[str, Any]]:
        return list(
            reversed(
                self._all(
                    "SELECT * FROM market_snapshots WHERE symbol=? ORDER BY id DESC LIMIT ?",
                    (symbol, limit),
                )
            )
        )

    def recent_predictions(
        self, limit: int = 100, symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        if symbol:
            return self._all(
                "SELECT * FROM predictions WHERE symbol=? " "ORDER BY id DESC LIMIT ?",
                (symbol, limit),
            )
        return self._all("SELECT * FROM predictions ORDER BY id DESC LIMIT ?", (limit,))

    def unevaluated_predictions(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """All predictions that haven't been evaluated yet. ``limit``
        bounds how many are returned per call (added for parity with
        daytrade so the same reconciliation tooling works on both)."""
        if limit is not None and limit > 0:
            return self._all(
                "SELECT * FROM predictions WHERE evaluated=0 " "ORDER BY id LIMIT ?", (int(limit),)
            )
        return self._all("SELECT * FROM predictions WHERE evaluated=0 ORDER BY id")

    def outcomes(self, limit: int = 500) -> List[Dict[str, Any]]:
        return self._all(
            "SELECT o.*, p.confidence, p.market_condition, p.direction "
            "FROM prediction_outcomes o JOIN predictions p "
            "ON o.prediction_id = p.id ORDER BY o.prediction_id DESC LIMIT ?",
            (limit,),
        )

    def open_paper_trades(self) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM paper_trades WHERE status='open' " "ORDER BY id")

    def closed_paper_trades(self, limit: int = 500) -> List[Dict[str, Any]]:
        return self._all(
            "SELECT * FROM paper_trades WHERE status='closed' " "ORDER BY id DESC LIMIT ?", (limit,)
        )

    def current_bot_run(self) -> Optional[Dict[str, Any]]:
        """Prefer a row whose status='running' AND PID is alive (ported
        from daytrade QA-RUNS-3). A short-lived sibling test process
        no longer pushes the actually-running bot off the dashboard."""
        import os as _os

        for row in self._conn.execute(
            "SELECT * FROM bot_runs WHERE status='running' " "ORDER BY id DESC LIMIT 5"
        ).fetchall():
            d = dict(row)
            pid = d.get("pid")
            if not pid:
                continue
            try:
                _os.kill(int(pid), 0)
                return d
            except (ProcessLookupError, ValueError, TypeError):
                continue
            except (PermissionError, OSError):
                return d
        return self._one("SELECT * FROM bot_runs ORDER BY id DESC LIMIT 1")

    def recent_errors(self, limit: int = 50, include_alerts: bool = False) -> List[Dict[str, Any]]:
        """Real code-level errors. Excludes ``alert:*`` rows by default."""
        if include_alerts:
            return self._all("SELECT * FROM errors ORDER BY id DESC LIMIT ?", (limit,))
        return self._all(
            "SELECT * FROM errors "
            "WHERE context IS NULL OR context NOT LIKE 'alert:%' "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    def count(self, table: str) -> int:
        # table name is from a fixed internal set — not user input.
        return int(self._conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])

    # -- internals -----------------------------------------------------------

    def _insert(self, table: str, row: Dict[str, Any]) -> int:
        names = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        cur = self._conn.execute(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders})", list(row.values())
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def _one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]
