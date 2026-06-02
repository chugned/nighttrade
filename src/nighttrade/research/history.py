"""Real historical market data — downloaded once, cached in SQLite.

The research lab needs years of real daily bars. Downloading them every run
would be slow and rude to the data provider, so the first fetch is persisted
to ``artifacts/history.db`` and reused. This is read-only market data; no
order endpoint is touched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List

from ..models import OHLCV
from ..runtime import get_logger

_log = get_logger("research.history")

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HISTORY_DB = _REPO_ROOT / "artifacts" / "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL NOT NULL,
    high   REAL NOT NULL,
    low    REAL NOT NULL,
    close  REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY (symbol, date)
);
"""


class HistoryCache:
    """A SQLite-backed cache of real daily OHLCV history."""

    def __init__(self, path: Path | str = DEFAULT_HISTORY_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- read ----------------------------------------------------------------

    def cached_symbols(self) -> List[str]:
        rows = self._conn.execute("SELECT DISTINCT symbol FROM bars").fetchall()
        return sorted(r["symbol"] for r in rows)

    def load(self, symbol: str) -> List[OHLCV]:
        """Return all cached bars for ``symbol``, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM bars WHERE symbol=? ORDER BY date", (symbol.upper(),)
        ).fetchall()
        return [
            OHLCV(
                symbol=symbol.upper(),
                timestamp=r["date"],
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
            )
            for r in rows
        ]

    # -- write ---------------------------------------------------------------

    def store(self, symbol: str, candles: List[OHLCV]) -> int:
        """Insert (or replace) ``candles`` for ``symbol``. Returns rows written."""
        rows = [
            (
                symbol.upper(),
                c.timestamp.date().isoformat(),
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
            )
            for c in candles
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO bars "
            "(symbol, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        return len(rows)

    # -- fetch-or-cache ------------------------------------------------------

    def get(self, symbol: str, years: int = 3, refresh: bool = False) -> List[OHLCV]:
        """Return daily history for ``symbol``, downloading + caching on a miss.

        Args:
            years: how many years of daily bars to fetch on a download.
            refresh: when True, always re-download even if cached.
        """
        symbol = symbol.upper()
        if not refresh:
            cached = self.load(symbol)
            if cached:
                return cached
        downloaded = self._download(symbol, years)
        if downloaded:
            self.store(symbol, downloaded)
            return downloaded
        return self.load(symbol)  # fall back to anything already cached

    def _download(self, symbol: str, years: int) -> List[OHLCV]:
        """Best-effort daily-history download via yfinance."""
        try:
            import yfinance as yf  # noqa: WPS433 - optional, lazy
        except ImportError:  # pragma: no cover - optional dependency
            _log.warning("yfinance not installed — cannot download %s", symbol)
            return []
        try:
            frame = yf.Ticker(symbol).history(
                period=f"{max(1, years)}y", interval="1d", auto_adjust=False
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("history download failed for %s: %s", symbol, exc)
            return []
        if frame is None or frame.empty:
            return []
        candles: List[OHLCV] = []
        for ts, row in frame.dropna().iterrows():
            o, h, l, c = (row.get("Open"), row.get("High"), row.get("Low"), row.get("Close"))
            if None in (o, h, l, c) or min(o, h, l, c) <= 0:
                continue
            try:
                candles.append(
                    OHLCV(
                        symbol=symbol,
                        timestamp=ts.to_pydatetime(),
                        open=float(o),
                        high=float(h),
                        low=float(l),
                        close=float(c),
                        volume=float(row.get("Volume") or 0.0),
                    )
                )
            except (ValueError, TypeError):
                continue
        _log.info("downloaded %d daily bars for %s", len(candles), symbol)
        return candles

    def get_many(
        self, symbols: List[str], years: int = 3, refresh: bool = False
    ) -> Dict[str, List[OHLCV]]:
        """Fetch-or-cache history for several symbols."""
        return {s: self.get(s, years=years, refresh=refresh) for s in symbols}
