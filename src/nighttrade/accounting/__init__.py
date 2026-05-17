"""Accounting & tax reporting for the paper portfolio.

No bank-transfer, withdrawal or payment code lives in this package — by design.
It only summarizes and exports *simulated* trading results.
"""

from __future__ import annotations

from .ledger import AccountingReport, AssetPnL, build_accounting_report
from .tax import export_tax_csv, trade_to_tax_row

__all__ = [
    "AccountingReport",
    "AssetPnL",
    "build_accounting_report",
    "export_tax_csv",
    "trade_to_tax_row",
]
