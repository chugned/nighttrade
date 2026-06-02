"""Live stock-data feed for the observatory.

``YFinanceFeed`` is a drop-in replacement for :class:`LiveMockFeed` backed by
real Yahoo Finance intraday data. It implements the same four methods the
observer calls — ``candles_at``, ``orderbook_at``, ``tick_at``, ``price_at`` —
so the observatory, dashboard and learning session work unchanged.

It batches one download for the whole universe per refresh window and serves
every symbol from that cache, so a 100–500 name universe costs one network
round-trip per cycle rather than one per symbol.

This feed is READ-ONLY market data. It never places an order.
"""

from __future__ import annotations

import time as _time
from datetime import datetime
from typing import Dict, List, Optional

from ..exchanges.mock import build_orderbook
from ..models import OHLCV, OrderBookSnapshot, PriceTick
from ..runtime import get_logger

_log = get_logger("observatory.live_feed")


def _synthetic_book(symbol: str, price: float, when: datetime) -> OrderBookSnapshot:
    """A synthetic top-of-book (equities have no free L2 feed)."""
    base_qty = max(200_000.0 / (price * 6.0), 1.0)
    return build_orderbook(
        symbol=symbol,
        mid_price=price,
        exchange="yfinance",
        depth=20,
        spread_bps=5.0,
        base_quantity=base_qty,
        imbalance=0.0,
        timestamp=when,
        jitter=0.0,
    )


class YFinanceFeed:
    """A live, real-data market feed backed by Yahoo Finance (``yfinance``)."""

    #: Real data only exists during/after a trading session — the observer
    #: gates prediction-making on the market clock when the feed is live.
    respects_market_hours = True

    #: SPEED — dynamic symbol pruning. Symbols that consistently fail
    #: to return data (delisted, halted, missing intraday for the period)
    #: get "parked" after _PARK_AFTER_FAILS consecutive failures. Parked
    #: symbols are skipped from the next ``_PARK_DURATION_S`` worth of
    #: refreshes, with ``_REPROBE_FRACTION`` of them re-tried each cycle
    #: to detect symbols that come back online.
    #:
    #: On the S&P 500 universe we typically see 20-50 dead tickers
    #: (recently delisted, M&A, halted). Pruning them saves ~5-10% of
    #: every fetch's wall-clock + Yahoo API budget.
    _PARK_AFTER_FAILS = 3
    _PARK_DURATION_S = 3600  # 1 hour
    _REPROBE_FRACTION = 0.10  # 10% of parked retried per cycle

    def __init__(
        self,
        symbols: List[str],
        refresh_seconds: float = 120.0,
        period: str = "2d",
        park_state_path: "Optional[Path]" = None,
    ) -> None:
        if not symbols:
            raise ValueError("YFinanceFeed needs at least one symbol")
        self._symbols = [s.upper() for s in symbols]
        self._refresh_seconds = refresh_seconds
        self._period = period
        self._cache: Dict[str, List[OHLCV]] = {}
        self._fetched_monotonic: Optional[float] = None
        # US stocks trade in USD; the platform is euro-denominated, so every
        # price is converted to EUR by this factor (EUR per 1 USD).
        self._eur_per_usd: float = 1.0
        # Dynamic pruning state.
        self._failure_streak: Dict[str, int] = {}
        self._parked_until: Dict[str, float] = {}  # symbol -> epoch seconds
        # JSON persistence so a restart preserves the parked set. The
        # default location lives next to the observatory DB.
        if park_state_path is None:
            from pathlib import Path as _P
            park_state_path = _P(__file__).resolve().parents[3] / "data" / "parked_symbols.json"
        self._park_state_path = park_state_path
        self._load_park_state()

    # -- universe ------------------------------------------------------------

    def _active_symbols(self) -> List[str]:
        """Symbols to actually fetch this cycle.

        Excludes currently-parked symbols, but includes a random
        ``_REPROBE_FRACTION`` of them so we detect when delisted-then-
        relisted or temporarily-halted symbols come back online.
        """
        import random  # noqa: PLC0415
        now = _time.time()
        active: List[str] = []
        parked: List[str] = []
        for sym in self._symbols:
            park_until = self._parked_until.get(sym)
            if park_until is None or park_until <= now:
                active.append(sym)
            else:
                parked.append(sym)
        if parked:
            n_probe = max(1, int(len(parked) * self._REPROBE_FRACTION))
            active.extend(random.sample(parked, min(n_probe, len(parked))))
        return active

    def _update_park_state(self, attempted: List[str], succeeded: set) -> None:
        """After a refresh, update failure streaks + park state.
        Persists to disk best-effort."""
        for sym in attempted:
            if sym in succeeded:
                self._failure_streak[sym] = 0
                self._parked_until.pop(sym, None)
            else:
                self._failure_streak[sym] = self._failure_streak.get(sym, 0) + 1
                if self._failure_streak[sym] >= self._PARK_AFTER_FAILS:
                    self._parked_until[sym] = _time.time() + self._PARK_DURATION_S
        self._save_park_state()

    def _load_park_state(self) -> None:
        """Read the parked-symbols JSON if present. Best-effort."""
        try:
            import json  # noqa: PLC0415
            with self._park_state_path.open("r", encoding="utf-8") as fh:
                state = json.load(fh)
            self._failure_streak = dict(state.get("failure_streak", {}))
            self._parked_until = dict(state.get("parked_until", {}))
            # Cast park_until values to float (JSON may load as int)
            self._parked_until = {k: float(v) for k, v in self._parked_until.items()}
        except (OSError, ValueError, KeyError):
            self._failure_streak = {}
            self._parked_until = {}

    def _save_park_state(self) -> None:
        """Write the parked-symbols JSON. Best-effort."""
        try:
            import json  # noqa: PLC0415
            self._park_state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._park_state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({
                "failure_streak": self._failure_streak,
                "parked_until": self._parked_until,
            }, indent=2), encoding="utf-8")
            import os as _os  # noqa: PLC0415
            _os.replace(tmp, self._park_state_path)
        except OSError:
            pass

    def available_symbols(self) -> List[str]:
        """Symbols that returned usable data on the last refresh."""
        self._ensure_fresh()
        return sorted(self._cache)

    def refresh_now(self) -> None:
        """Force an immediate batch download."""
        self._refresh()

    # -- internal ------------------------------------------------------------

    def _ensure_fresh(self) -> None:
        now = _time.monotonic()
        if (
            self._fetched_monotonic is not None
            and self._cache
            and now - self._fetched_monotonic < self._refresh_seconds
        ):
            return
        self._refresh()

    #: NT-EFF: cap the per-symbol candle list at this many bars in the
    #: in-memory cache. Downstream callers ask for n_bars=240 (analysis)
    #: or n_bars=400 (idle training); 500 leaves comfortable headroom
    #: while cutting cache footprint from ~390k OHLCV pydantic objects
    #: (503 syms × 780 bars from a 2-day yfinance pull) to ~250k. The
    #: lopped older bars are still accessible via the next refresh from
    #: yfinance if a longer history is genuinely needed.
    _MAX_CACHED_BARS_PER_SYMBOL = 500

    #: SPEED — bounded-parallel fetch. We split the symbol universe
    #: into N chunks and call yf.download(chunk, threads=False) from
    #: a ThreadPoolExecutor we own. yfinance's own threads kwarg
    #: spawned one OS thread per ticker (503 → host thread-budget
    #: blew up — see ADR-0005). Owning the pool ourselves gives a
    #: hard cap on the OS-thread count regardless of universe size.
    #:
    #: 8 workers on 503 symbols → 8 chunks of ~63 → ~28s wall-clock
    #: (vs ~110s sequential, measured 2026-06-02). The cap of 8
    #: stays well inside the host thread budget and matches what
    #: was found safe in benchmarks.
    _MAX_FETCH_WORKERS = 8

    def _refresh(self) -> None:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "yfinance not installed — run: pip install nighttrade[online]"
            ) from exc
        import gc  # noqa: PLC0415
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

        self._eur_per_usd = self._fetch_eur_per_usd()
        # SPEED — drop parked symbols from the fetch list. Reprobes
        # a small slice each cycle so re-listed/halted-recovered
        # symbols are eventually re-included.
        attempted = self._active_symbols()
        n_syms = len(attempted)
        n_parked = len(self._symbols) - n_syms
        n_workers = min(self._MAX_FETCH_WORKERS, max(1, n_syms))
        _log.info(
            "fetching live intraday data for %d symbols in %d parallel chunks "
            "(EUR/USD x%.4f, %d parked)",
            n_syms, n_workers, self._eur_per_usd, n_parked,
        )

        # Split into n_workers chunks. Each worker calls yf.download
        # with threads=False (single-thread per worker), so total OS
        # threads = n_workers ≤ _MAX_FETCH_WORKERS regardless of how
        # large the universe grows.
        chunk_size = max(1, (n_syms + n_workers - 1) // n_workers)
        chunks = [attempted[i:i + chunk_size]
                  for i in range(0, n_syms, chunk_size)]

        def _fetch_chunk(chunk: List[str]) -> Dict[str, "object"]:
            """Fetch one chunk, return per-symbol DataFrames as a dict.
            Single-thread inside (threads=False) — concurrency comes from
            the outer ThreadPoolExecutor, NOT from yfinance."""
            try:
                frame = yf.download(
                    chunk,
                    period=self._period,
                    interval="1m",
                    group_by="ticker",
                    threads=False,
                    progress=False,
                    auto_adjust=False,
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning("chunk fetch failed (%d syms): %s",
                             len(chunk), exc)
                return {}
            # Slice per-symbol out of the chunk frame here (inside the
            # worker) so the outer code never sees the MultiIndex.
            out: Dict[str, "object"] = {}
            for sym in chunk:
                try:
                    out[sym] = frame[sym] if len(chunk) > 1 else frame
                except (KeyError, TypeError):
                    continue
            return out

        with ThreadPoolExecutor(
            max_workers=n_workers, thread_name_prefix="yf-fetch"
        ) as exe:
            chunk_results = list(exe.map(_fetch_chunk, chunks))

        # Merge the per-chunk dicts — each chunk owns disjoint symbols.
        per_symbol_frames: Dict[str, "object"] = {}
        for d in chunk_results:
            per_symbol_frames.update(d)

        # SPEED — update park state. Symbols that returned a non-empty
        # frame succeeded; everyone else failed (chunk-level errors
        # OR per-symbol empty frame after group_by).
        succeeded = {sym for sym in attempted
                     if per_symbol_frames.get(sym) is not None
                     and not getattr(per_symbol_frames[sym], "empty", False)}
        self._update_park_state(attempted, succeeded)
        n_newly_parked = sum(1 for s in attempted
                              if self._failure_streak.get(s, 0) >= self._PARK_AFTER_FAILS
                              and s not in succeeded)
        if n_newly_parked > 0:
            _log.info("dynamic pruning: %d symbol(s) parked this cycle (%d total parked)",
                      n_newly_parked, len(self._parked_until))

        cache: Dict[str, List[OHLCV]] = {}
        cap = self._MAX_CACHED_BARS_PER_SYMBOL
        for sym in self._symbols:
            frame = per_symbol_frames.get(sym)
            if frame is None:
                continue
            candles = self._frame_to_candles(sym, frame)
            if len(candles) >= 30:  # need enough tape for the analysis layers
                # NT-EFF: keep only the most recent ``cap`` bars in cache
                cache[sym] = candles[-cap:]
        if cache:
            # NT-LEAK fix: drop the old cache BEFORE assigning the new one
            # so the giant pandas DataFrames are freed for GC immediately.
            old_cache = self._cache
            self._cache = cache
            self._fetched_monotonic = _time.monotonic()
            del old_cache
            _log.info(
                "live feed cached %d/%d symbols (cap=%d bars/sym)",
                len(cache),
                len(self._symbols),
                cap,
            )
        else:
            _log.warning("live feed refresh returned no usable data")
        # NT-LEAK fix: yfinance internally accumulates session state +
        # leaves DataFrame objects on the GC queue; an explicit collect
        # after each refresh keeps RSS flat instead of climbing 200+
        # MB/day. Cheap (~10ms) compared to the 40s refresh itself.
        del per_symbol_frames
        del chunk_results
        gc.collect()

    @staticmethod
    def _fetch_eur_per_usd() -> float:
        """EUR per 1 USD from Yahoo. Falls back to 1.0 (no conversion) on failure."""
        try:
            import yfinance as yf

            closes = yf.Ticker("EURUSD=X").history(period="5d")["Close"].dropna()
            if not closes.empty:
                eurusd = float(closes.iloc[-1])
                if eurusd > 0:
                    return 1.0 / eurusd
        except Exception as exc:  # noqa: BLE001
            _log.warning("EUR/USD rate unavailable (%s) — prices stay in USD", exc)
        return 1.0

    def _frame_to_candles(self, symbol: str, frame) -> List[OHLCV]:
        """Convert a yfinance frame to OHLCV, with USD prices scaled to EUR."""
        candles: List[OHLCV] = []
        if frame is None or getattr(frame, "empty", True):
            return candles
        fx = self._eur_per_usd
        for ts, row in frame.dropna().iterrows():
            o, h, l, c = (row.get("Open"), row.get("High"), row.get("Low"), row.get("Close"))
            if None in (o, h, l, c) or min(o, h, l, c) <= 0:
                continue
            try:
                candles.append(
                    OHLCV(
                        symbol=symbol,
                        timestamp=ts.to_pydatetime(),
                        open=float(o) * fx,
                        high=float(h) * fx,
                        low=float(l) * fx,
                        close=float(c) * fx,
                        volume=float(row.get("Volume") or 0.0),
                    )
                )
            except (ValueError, TypeError):
                continue
        return candles

    def _candles(self, symbol: str) -> List[OHLCV]:
        self._ensure_fresh()
        sym = symbol.upper()
        if sym not in self._cache:
            # A symbol outside the batch universe (e.g. a stale prediction from
            # a previous run) — fetch it on demand so it can't crash a cycle.
            self._cache[sym] = self._fetch_one(sym)
        return self._cache.get(sym, [])

    def _fetch_one(self, symbol: str) -> List[OHLCV]:
        """Best-effort single-symbol fetch for a symbol outside the universe."""
        try:
            import yfinance as yf

            frame = yf.Ticker(symbol).history(period=self._period, interval="1m", auto_adjust=False)
        except Exception as exc:  # noqa: BLE001
            _log.warning("live feed could not fetch %s: %s", symbol, exc)
            return []
        return self._frame_to_candles(symbol, frame)

    # -- feed interface ------------------------------------------------------

    def candles_at(self, symbol: str, as_of: datetime, n_bars: int = 300) -> List[OHLCV]:
        """Real 1-minute candles for ``symbol`` up to ``as_of``."""
        candles = [c for c in self._candles(symbol) if c.timestamp <= as_of]
        if not candles:
            candles = self._candles(symbol)  # as_of before the window — use all
        return candles[-n_bars:]

    def price_at(self, symbol: str, when: datetime) -> float:
        """The close of the 1-minute bar nearest ``when``."""
        candles = self._candles(symbol)
        if not candles:
            raise ValueError(f"no live data for {symbol}")
        at_or_before = [c for c in candles if c.timestamp <= when]
        chosen = at_or_before[-1] if at_or_before else candles[0]
        return chosen.close

    def orderbook_at(self, symbol: str, as_of: datetime) -> OrderBookSnapshot:
        candles = self.candles_at(symbol, as_of, n_bars=1)
        price = candles[-1].close if candles else self.price_at(symbol, as_of)
        return _synthetic_book(symbol, price, candles[-1].timestamp if candles else as_of)

    def tick_at(self, symbol: str, as_of: datetime) -> PriceTick:
        candles = self.candles_at(symbol, as_of, n_bars=390)
        last = candles[-1]
        # Daily dollar volume = today's traded shares x price.
        dollar_volume = sum(c.volume for c in candles) * last.close
        return PriceTick(
            symbol=symbol,
            exchange="yfinance",
            price=last.close,
            timestamp=last.timestamp,
            volume_24h=dollar_volume,
        )
