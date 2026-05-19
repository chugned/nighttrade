"""FastAPI backend for the Market Safety Observatory dashboard.

Serves a single-page dashboard and a small read-only JSON API over the
observatory database, plus a WebSocket that pushes the overview live.

The backend is strictly read-only — it observes the observatory's database
and never writes orders, touches wallets, or moves money.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from ..observatory.database import DEFAULT_DB_PATH
from ..runtime import get_logger
from .data import DashboardData

_log = get_logger("dashboard")
_STATIC = Path(__file__).resolve().parent / "static"


def create_app(db_path: Path | str = DEFAULT_DB_PATH) -> FastAPI:
    """Build the dashboard FastAPI application bound to ``db_path``."""
    app = FastAPI(title="nighttrade — Market Safety Observatory",
                  docs_url="/api/docs")

    def data() -> DashboardData:
        return DashboardData(db_path)

    def _safe(fn) -> Any:
        """Run a data accessor, returning an error payload instead of a 500."""
        accessor = data()
        try:
            return fn(accessor)
        except Exception as exc:  # noqa: BLE001 - dashboard must stay up
            _log.exception("dashboard data error")
            return {"error": repr(exc)}
        finally:
            accessor.close()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/overview")
    def overview() -> Any:
        return _safe(lambda d: d.overview())

    @app.get("/api/symbols")
    def symbols() -> Any:
        return _safe(lambda d: d.symbols())

    @app.get("/api/symbol/{symbol}")
    def symbol_detail(symbol: str) -> Any:
        return _safe(lambda d: d.symbol_detail(symbol))

    @app.get("/api/accuracy")
    def accuracy() -> Any:
        return _safe(lambda d: d.accuracy())

    @app.get("/api/paper")
    def paper() -> Any:
        return _safe(lambda d: d.paper())

    @app.get("/api/risk")
    def risk() -> Any:
        return _safe(lambda d: d.risk())

    @app.get("/api/safety-history")
    def safety_history() -> Any:
        return _safe(lambda d: d.safety_history())

    @app.get("/api/equity")
    def equity() -> Any:
        return _safe(lambda d: d.equity_history())

    # --- learning observatory endpoints ---

    @app.get("/api/status")
    def status() -> Any:
        return _safe(lambda d: d.status())

    @app.get("/api/progress")
    def progress() -> Any:
        return _safe(lambda d: d.progress())

    @app.get("/api/regimes")
    def regimes() -> Any:
        return _safe(lambda d: d.regimes())

    @app.get("/api/ranking")
    def ranking() -> Any:
        return _safe(lambda d: d.ranking())

    @app.get("/api/calibration")
    def calibration() -> Any:
        return _safe(lambda d: d.calibration())

    @app.get("/api/readiness")
    def readiness() -> Any:
        return _safe(lambda d: d.readiness())

    @app.get("/api/learning")
    def learning() -> Any:
        return _safe(lambda d: d.learning())

    @app.get("/api/activity")
    def activity() -> Any:
        return _safe(lambda d: d.activity())

    @app.get("/api/predictions")
    def predictions() -> Any:
        return _safe(lambda d: d.predictions())

    @app.get("/api/paper-trades")
    def paper_trades() -> Any:
        return _safe(lambda d: d.paper())

    @app.get("/api/investments")
    def investments() -> Any:
        return _safe(lambda d: d.investments())

    @app.get("/api/daily-reports")
    def daily_reports() -> Any:
        return _safe(lambda d: d.daily_reports())

    @app.get("/api/gates")
    def gates() -> Any:
        return _safe(lambda d: d.gates())

    @app.get("/api/symbols/{symbol}")
    def symbol_detail_plural(symbol: str) -> Any:
        return _safe(lambda d: d.symbol_detail(symbol))

    @app.get("/api/health")
    def health() -> JSONResponse:
        return JSONResponse({"ok": True, "real_trading": False,
                             "paper_only": True, "wallets": False,
                             "bank_transfers": False})

    async def _ws_loop(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                payload = {
                    "overview": _safe(lambda d: d.overview()),
                    "status": _safe(lambda d: d.status()),
                    "progress": _safe(lambda d: d.progress()),
                }
                await socket.send_text(json.dumps(payload, default=str))
                await asyncio.sleep(4.0)
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001 - never crash the server on a socket
            return

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        """Push overview + status + progress every few seconds."""
        await _ws_loop(socket)

    @app.websocket("/ws/live")
    async def ws_live(socket: WebSocket) -> None:
        """Alias of /ws (the endpoint name from the spec)."""
        await _ws_loop(socket)

    return app


# Module-level app for ``uvicorn nighttrade.dashboard.app:app``.
app = create_app()
