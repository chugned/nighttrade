"""The liquid US equity universe — the S&P 500.

``liquid_universe()`` returns the current S&P 500 constituents (fetched from
Wikipedia), falling back to a built-in list of ~120 of the most liquid US
large caps + ETFs if the network is unavailable.

The S&P 500 *is* the practical "all liquid US stocks" universe: monitoring the
full ~8,000-ticker tape would be dominated by the thin, illiquid microcaps the
watchlist screener exists to reject.
"""

from __future__ import annotations

from typing import List, Optional

from ..runtime import get_logger

_log = get_logger("watchlist.sp500")

_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Fallback: ~120 of the most liquid US large caps + index ETFs. Used only when
# the live S&P 500 list cannot be fetched.
_FALLBACK: List[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AVGO",
    "BRK-B", "JPM", "V", "MA", "WMT", "JNJ", "XOM", "UNH", "PG", "HD", "COST",
    "ORCL", "MRK", "ABBV", "BAC", "KO", "PEP", "CVX", "ADBE", "CRM", "NFLX",
    "AMD", "DIS", "INTC", "QCOM", "TXN", "CSCO", "ACN", "MCD", "ABT", "DHR",
    "WFC", "LIN", "VZ", "PM", "NKE", "TMO", "IBM", "GE", "CAT", "AXP", "NOW",
    "INTU", "AMGN", "ISRG", "GS", "MS", "SPGI", "BKNG", "RTX", "PFE", "UBER",
    "T", "HON", "LOW", "BLK", "ELV", "PLD", "AMAT", "C", "SBUX", "BA", "DE",
    "MDT", "ADP", "GILD", "LMT", "CB", "MMC", "SYK", "TJX", "VRTX", "REGN",
    "PGR", "ETN", "BSX", "CI", "MU", "SO", "ZTS", "FI", "BX", "MO", "DUK",
    "SCHW", "EOG", "SLB", "APD", "CL", "ITW", "PANW", "WM", "CME", "MCK",
    "TGT", "USB", "PNC", "AON", "GM", "F", "PYPL", "COF", "MAR", "FCX", "EMR",
    "NSC", "ORLY", "MMM", "PXD", "ECL", "ADSK",
    # Index / sector ETFs.
    "SPY", "QQQ", "IWM", "DIA", "VTI", "XLK", "XLF", "XLE", "SMH", "ARKK",
]


def liquid_universe(limit: Optional[int] = None) -> List[str]:
    """Return the S&P 500 tickers (live), or the built-in fallback list.

    Args:
        limit: optionally cap the universe to the first ``limit`` symbols.
    """
    symbols = _fetch_sp500() or list(_FALLBACK)
    # Drop duplicates while preserving order.
    seen: set = set()
    unique = [s for s in symbols if not (s in seen or seen.add(s))]
    return unique[:limit] if limit else unique


def _fetch_sp500() -> Optional[List[str]]:
    """Fetch current S&P 500 tickers from Wikipedia; None on any failure."""
    try:
        import io

        import httpx
        import pandas as pd

        # Wikipedia 403s the default urllib agent — send a browser one.
        resp = httpx.get(_WIKI_URL, follow_redirects=True, timeout=10.0,
                         headers={"User-Agent": "Mozilla/5.0 (nighttrade)"})
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
        symbols = tables[0]["Symbol"].astype(str).tolist()
        # Wikipedia uses 'BRK.B'; Yahoo Finance uses 'BRK-B'.
        cleaned = [s.strip().upper().replace(".", "-") for s in symbols
                   if s and s.strip()]
        if len(cleaned) >= 400:
            _log.info("fetched %d S&P 500 constituents", len(cleaned))
            return cleaned
        return None
    except Exception as exc:  # noqa: BLE001 - any failure -> fallback
        _log.warning("could not fetch S&P 500 list (%s) — using fallback", exc)
        return None
