"""Reporting — Rich console, JSON and Markdown output."""

from __future__ import annotations

from .console import render_backtest, render_decision, render_walkforward
from .daily import (
    DailyReport,
    build_daily_report,
    daily_report_dict,
    daily_report_markdown,
    render_daily_report,
)
from .serialize import (
    backtest_report_dict,
    backtest_report_markdown,
    decision_report_dict,
    decision_report_markdown,
    save_json,
    save_text,
)

__all__ = [
    "render_decision",
    "render_backtest",
    "render_walkforward",
    "decision_report_dict",
    "decision_report_markdown",
    "backtest_report_dict",
    "backtest_report_markdown",
    "save_json",
    "save_text",
    "DailyReport",
    "build_daily_report",
    "render_daily_report",
    "daily_report_dict",
    "daily_report_markdown",
]
