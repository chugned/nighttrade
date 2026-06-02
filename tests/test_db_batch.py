"""Pin the contract of ``ObservatoryDB.batch()``.

The per-symbol observer loop emits ~1500 row inserts per cycle. Each
``_insert`` previously committed independently — 1500 fsyncs per cycle.
The ``batch()`` context defers commits across the block, so the whole
loop is one transaction → one commit.

Tests:
- Inserts are visible after batch() exits.
- Per-row commits inside batch() do NOT commit (deferred).
- Nested batch() blocks: only outermost commits.
- Exception inside batch() rolls back.
- Batch is fast: many inserts in batch < many inserts without.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from nighttrade.observatory.database import ObservatoryDB


def test_batch_commits_at_exit(tmp_path):
    db = ObservatoryDB(path=tmp_path / "obs.db")
    with db.batch():
        for i in range(50):
            db.insert_snapshot(symbol=f"S{i:03d}", price=100.0 + i)
    # After exit, all rows are committed and visible
    rows = db._all("SELECT COUNT(*) AS c FROM market_snapshots")
    assert rows[0]["c"] == 50


def test_batch_defers_commits_within_block(tmp_path):
    db = ObservatoryDB(path=tmp_path / "obs.db")
    # Open a SECOND connection (read-only) to verify intermediate
    # state. WAL mode lets readers see committed data only.
    reader = sqlite3.connect(str(tmp_path / "obs.db"))
    reader.row_factory = sqlite3.Row

    with db.batch():
        for i in range(5):
            db.insert_snapshot(symbol=f"S{i:03d}", price=100.0 + i)
        # Inside the batch, the reader should NOT see the rows yet
        c = reader.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
        assert c == 0, f"expected 0 rows mid-batch, saw {c}"

    # After exit, reader sees all 5
    c = reader.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert c == 5
    reader.close()


def test_batch_is_nestable(tmp_path):
    db = ObservatoryDB(path=tmp_path / "obs.db")
    with db.batch():
        db.insert_snapshot(symbol="OUT", price=100.0)
        with db.batch():
            db.insert_snapshot(symbol="IN", price=101.0)
        # Inner batch exit must NOT have committed
        # (verify by checking _batch_depth or by external reader)
        assert db._batch_depth == 1
    # Outer batch exit commits both
    rows = db._all("SELECT symbol FROM market_snapshots ORDER BY id")
    assert [r["symbol"] for r in rows] == ["OUT", "IN"]


def test_batch_rolls_back_on_exception(tmp_path):
    db = ObservatoryDB(path=tmp_path / "obs.db")
    with pytest.raises(RuntimeError):
        with db.batch():
            db.insert_snapshot(symbol="A", price=1.0)
            db.insert_snapshot(symbol="B", price=2.0)
            raise RuntimeError("boom")
    # On rollback, no rows committed
    rows = db._all("SELECT COUNT(*) AS c FROM market_snapshots")
    assert rows[0]["c"] == 0


def test_batch_is_faster_than_individual_commits(tmp_path):
    """The win this is shipped for: batching N inserts must be
    materially faster than committing each one. We're not asserting
    a specific ratio (too host-dependent) — just that batched is
    NOT slower."""
    n = 200
    db1 = ObservatoryDB(path=tmp_path / "individual.db")
    t = time.monotonic()
    for i in range(n):
        db1.insert_snapshot(symbol=f"S{i:04d}", price=100.0)
    individual = time.monotonic() - t

    db2 = ObservatoryDB(path=tmp_path / "batched.db")
    t = time.monotonic()
    with db2.batch():
        for i in range(n):
            db2.insert_snapshot(symbol=f"S{i:04d}", price=100.0)
    batched = time.monotonic() - t

    # Batched must be at least 2x faster — on real hardware
    # SQLite WAL gives 10-50x.
    assert batched < individual * 0.5, (
        f"batched={batched*1000:.0f}ms expected < individual/2={individual*500:.0f}ms"
    )
