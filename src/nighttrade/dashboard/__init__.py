"""Market Safety Observatory dashboard — FastAPI backend + single-page UI.

Read-only: the dashboard observes the observatory database. It cannot place
orders or move money.
"""

from __future__ import annotations

from .app import app, create_app
from .data import DashboardData

__all__ = ["app", "create_app", "DashboardData"]
