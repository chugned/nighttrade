"""The US-listed equity universe.

``liquid_universe()`` returns the **S&P 500** by default — the ~500 most
liquid US stocks. That is also the practical ceiling for *free intraday*
data: Yahoo Finance rate-limits a larger batch pull, so a bigger universe
just yields a flaky, fluctuating subset rather than more coverage.

``liquid_universe(full=True)`` returns every US-listed common stock (~5,500,
from the NASDAQ Trader directory) — useful only with a data source that can
actually serve them at intraday resolution.

Resolution order, each step falling back to the next:
  1. the S&P 500 (from Wikipedia) — or the full directory when ``full=True``
  2. a built-in list of ~120 liquid large caps
"""

from __future__ import annotations

import csv
import io
import re
from typing import List, Optional

from ..runtime import get_logger

_log = get_logger("watchlist.universe")

_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# NASDAQ Trader symbol directory — the authoritative list of US-listed symbols.
_NASDAQ_FEEDS = (
    ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "Symbol"),
    ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "ACT Symbol"),
)
# A plain common-stock ticker — drops obvious non-equity symbols.
_TICKER = re.compile(r"^[A-Z]{1,5}$")
# Security-name keywords that mark a non-common-stock listing (SPAC warrants,
# units, rights, preferreds, depositary shares, notes).
_EXCLUDE_NAME = re.compile(
    r"\b(warrants?|units?|rights?|preferred|depositary|notes?|when[- ]issued)\b", re.IGNORECASE
)

# Fallback: ~120 of the most liquid US large caps + index ETFs. Used only when
# the live directories cannot be fetched.
_FALLBACK: List[str] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "GOOG",
    "TSLA",
    "AVGO",
    "BRK-B",
    "JPM",
    "V",
    "MA",
    "WMT",
    "JNJ",
    "XOM",
    "UNH",
    "PG",
    "HD",
    "COST",
    "ORCL",
    "MRK",
    "ABBV",
    "BAC",
    "KO",
    "PEP",
    "CVX",
    "ADBE",
    "CRM",
    "NFLX",
    "AMD",
    "DIS",
    "INTC",
    "QCOM",
    "TXN",
    "CSCO",
    "ACN",
    "MCD",
    "ABT",
    "DHR",
    "WFC",
    "LIN",
    "VZ",
    "PM",
    "NKE",
    "TMO",
    "IBM",
    "GE",
    "CAT",
    "AXP",
    "NOW",
    "INTU",
    "AMGN",
    "ISRG",
    "GS",
    "MS",
    "SPGI",
    "BKNG",
    "RTX",
    "PFE",
    "UBER",
    "T",
    "HON",
    "LOW",
    "BLK",
    "ELV",
    "PLD",
    "AMAT",
    "C",
    "SBUX",
    "BA",
    "DE",
    "MDT",
    "ADP",
    "GILD",
    "LMT",
    "CB",
    "MMC",
    "SYK",
    "TJX",
    "VRTX",
    "REGN",
    "PGR",
    "ETN",
    "BSX",
    "CI",
    "MU",
    "SO",
    "ZTS",
    "FI",
    "BX",
    "MO",
    "DUK",
    "SCHW",
    "EOG",
    "SLB",
    "APD",
    "CL",
    "ITW",
    "PANW",
    "WM",
    "CME",
    "MCK",
    "TGT",
    "USB",
    "PNC",
    "AON",
    "GM",
    "F",
    "PYPL",
    "COF",
    "MAR",
    "FCX",
    "EMR",
    "NSC",
    "ORLY",
    "MMM",
    "ECL",
    "ADSK",
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
]


def liquid_universe(limit: Optional[int] = None, full: bool = False) -> List[str]:
    """Return the intraday-tradeable US stock universe.

    Args:
        limit: optionally cap the universe to the first ``limit`` symbols.
        full: when True, return every US-listed common stock (~5,500) instead
            of the S&P 500. Only useful with a data source that can serve them.
    """
    if full:
        symbols = _fetch_all_us_listed() or _fetch_sp500() or list(_FALLBACK)
    else:
        symbols = _fetch_sp500() or list(_FALLBACK)
    seen: set = set()
    unique = [s for s in symbols if not (s in seen or seen.add(s))]
    return unique[:limit] if limit else unique


def _fetch_all_us_listed() -> Optional[List[str]]:
    """Fetch every US-listed common stock from the NASDAQ Trader directory."""
    try:
        import httpx
    except ImportError:  # pragma: no cover
        return None

    symbols: List[str] = []
    for url, sym_col in _NASDAQ_FEEDS:
        try:
            resp = httpx.get(
                url,
                timeout=20.0,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (nighttrade)"},
            )
            resp.raise_for_status()
            for row in csv.DictReader(io.StringIO(resp.text), delimiter="|"):
                sym = (row.get(sym_col) or "").strip().upper()
                if not _TICKER.match(sym):
                    continue  # warrant / unit / preferred / footer line
                if (row.get("ETF") or "").strip().upper() == "Y":
                    continue
                if (row.get("Test Issue") or "").strip().upper() == "Y":
                    continue
                if _EXCLUDE_NAME.search(row.get("Security Name") or ""):
                    continue  # SPAC warrant/unit/right, preferred, note
                symbols.append(sym)
        except Exception as exc:  # noqa: BLE001 - fall back on any failure
            _log.warning("could not fetch %s (%s)", url, exc)

    unique = sorted(set(symbols))
    if len(unique) >= 1000:
        _log.info("fetched %d US-listed common stocks", len(unique))
        return unique
    return None


def _fetch_sp500() -> Optional[List[str]]:
    """Fetch current S&P 500 tickers from Wikipedia; None on any failure."""
    try:
        import httpx
        import pandas as pd

        # Wikipedia 403s the default urllib agent — send a browser one.
        resp = httpx.get(
            _WIKI_URL,
            follow_redirects=True,
            timeout=10.0,
            headers={"User-Agent": "Mozilla/5.0 (nighttrade)"},
        )
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
        symbols = tables[0]["Symbol"].astype(str).tolist()
        cleaned = [s.strip().upper().replace(".", "-") for s in symbols if s and s.strip()]
        if len(cleaned) >= 400:
            _log.info("fetched %d S&P 500 constituents", len(cleaned))
            return cleaned
        return None
    except Exception as exc:  # noqa: BLE001 - any failure -> fallback
        _log.warning("could not fetch S&P 500 list (%s) — using fallback", exc)
        return None
