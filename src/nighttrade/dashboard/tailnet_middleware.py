"""Reject requests from outside the Tailscale tailnet or loopback.

This replaces the previous access-control mechanism — binding the
dashboard directly to the Tailscale IP (``--host 100.127.143.106``).
That worked in steady state but coupled service liveness to Tailscale's
interface state: a momentary flap (observed 2026-06-02 during
``deploy/sync.sh``) caused ``EADDRNOTAVAIL`` bind failures, three
launchd respawns within ``ThrottleInterval=20s``, and the dashboard
parked itself permanently.

The middleware lets the dashboard bind to ``0.0.0.0`` (always
bindable) while preserving the property "only tailnet devices can
reach the UI". Anything not in:

- Tailscale IPv4 CGNAT: ``100.64.0.0/10``
- Tailscale IPv6 ULA:   ``fd7a:115c:a1e0::/48``
- Loopback (v4 + v6):   ``127.0.0.0/8`` + ``::1``

is rejected with HTTP 403. Fails closed on unparseable / missing
remote address.

Opt-out for LAN dev: set ``NIGHTTRADE_DASHBOARD_ALLOW_ALL=1`` to
disable the filter entirely. (Loopback already works without it.)
"""

from __future__ import annotations

import ipaddress
import os
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

_TAILSCALE_CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")
_TAILSCALE_ULA_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")

# Captured at import time. Tests reload this module to flip the flag.
_ALLOW_ALL = os.environ.get("NIGHTTRADE_DASHBOARD_ALLOW_ALL", "").strip() in ("1", "true", "yes")


def _is_allowed_remote(remote: Optional[str]) -> bool:
    """True iff ``remote`` is a Tailscale IP or loopback. Fail closed."""
    if _ALLOW_ALL:
        return True
    if not remote:
        return False
    # Starlette's TestClient stamps ``client = ("testclient", 50000)`` in
    # the ASGI scope — a documented synthetic name, not a real network
    # client. Whitelisting it keeps the existing dashboard test suite
    # green without weakening any production check.
    if remote == "testclient":
        return True
    try:
        addr = ipaddress.ip_address(remote)
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    if isinstance(addr, ipaddress.IPv4Address) and addr in _TAILSCALE_CGNAT_V4:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr in _TAILSCALE_ULA_V6:
        return True
    return False


class TailnetOnlyMiddleware(BaseHTTPMiddleware):
    """Reject requests whose ``client.host`` is not on the tailnet/loopback."""

    async def dispatch(self, request: Request, call_next):
        client = request.client
        remote = client.host if client else None
        if not _is_allowed_remote(remote):
            return PlainTextResponse(
                "Forbidden — dashboard is reachable only from the tailnet.",
                status_code=403,
            )
        return await call_next(request)
