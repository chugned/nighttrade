"""Tests for the tailnet-only middleware on the nighttrade dashboard.

The dashboard previously bound to the Tailscale IP directly
(``--host 100.127.143.106``), using the bind scope as access control:
LAN devices physically couldn't reach the port. On 2026-06-02 a
Tailscale flap during ``deploy/sync.sh`` left the dashboard in
``EADDRNOTAVAIL`` → launchd throttle backoff → dead.

The fix decouples binding from access control: bind to ``0.0.0.0``
and reject any request whose ``client.host`` is not in the Tailscale
CGNAT range or loopback.

The middleware logic lives in ``_is_allowed_remote`` — a pure
function that takes a remote IP string and returns bool. The
``TailnetOnlyMiddleware`` class is a thin Starlette wrapper around
it. Both are covered.
"""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Pure logic — _is_allowed_remote
# ---------------------------------------------------------------------------

def _logic():
    """Get the function fresh (env-var captured at module import)."""
    import nighttrade.dashboard.tailnet_middleware as m
    importlib.reload(m)
    return m._is_allowed_remote


def test_allows_tailscale_cgnat_ipv4():
    assert _logic()("100.127.143.106") is True


def test_allows_tailscale_cgnat_lower_range():
    assert _logic()("100.64.0.1") is True


def test_allows_tailscale_cgnat_upper_range():
    assert _logic()("100.127.255.254") is True


def test_allows_loopback_ipv4():
    assert _logic()("127.0.0.1") is True


def test_allows_loopback_ipv6():
    assert _logic()("::1") is True


def test_allows_tailscale_ipv6_ula():
    """Tailscale also assigns ULA addresses (fd7a:115c:a1e0::/48)."""
    assert _logic()("fd7a:115c:a1e0::1") is True


def test_rejects_lan_ipv4():
    """A LAN client (192.168.x.x) was previously blocked by the bind
    scope; with 0.0.0.0 binding, the middleware must block it."""
    assert _logic()("192.168.1.42") is False


def test_rejects_public_ipv4():
    assert _logic()("8.8.8.8") is False


def test_rejects_other_cgnat_ranges():
    """100.0.0.0/8 is the wider CGNAT range; 100.0.0.0 itself is NOT
    in Tailscale's 100.64.0.0/10 subset. Must reject."""
    assert _logic()("100.63.255.255") is False


def test_rejects_unparseable_remote():
    assert _logic()("not-an-ip") is False


def test_rejects_empty_remote():
    assert _logic()("") is False


def test_rejects_none_remote_fails_closed():
    """Starlette may set request.client to None for some lifespan
    probes — fail closed, not open."""
    assert _logic()(None) is False


def test_allows_starlette_testclient_synthetic_name():
    """Starlette's TestClient stamps the literal string ``testclient``
    into the ASGI scope (not a real IP). Whitelisted so the existing
    dashboard test suite passes without weakening real checks."""
    assert _logic()("testclient") is True


# ---------------------------------------------------------------------------
# Opt-out env var
# ---------------------------------------------------------------------------

def test_env_var_disables_filter(monkeypatch):
    """A dev running ``make dashboard`` from a colleague's laptop on
    the LAN should be able to opt out via env var. Loopback already
    works for the same-machine case; this is the LAN-from-laptop case."""
    monkeypatch.setenv("NIGHTTRADE_DASHBOARD_ALLOW_ALL", "1")
    fn = _logic()
    try:
        assert fn("192.168.1.42") is True
        assert fn("8.8.8.8") is True
    finally:
        monkeypatch.delenv("NIGHTTRADE_DASHBOARD_ALLOW_ALL", raising=False)
        importlib.reload(__import__("nighttrade.dashboard.tailnet_middleware",
                                     fromlist=["_is_allowed_remote"]))


def test_env_var_off_keeps_filter_active(monkeypatch):
    monkeypatch.setenv("NIGHTTRADE_DASHBOARD_ALLOW_ALL", "0")
    fn = _logic()
    try:
        assert fn("192.168.1.42") is False
    finally:
        monkeypatch.delenv("NIGHTTRADE_DASHBOARD_ALLOW_ALL", raising=False)
        importlib.reload(__import__("nighttrade.dashboard.tailnet_middleware",
                                     fromlist=["_is_allowed_remote"]))


# ---------------------------------------------------------------------------
# Starlette integration — dispatch with a synthetic request
# ---------------------------------------------------------------------------

def _dispatch_with_remote(remote: str):
    """Call the middleware's dispatch coroutine with a synthetic
    request whose .client.host is ``remote``. Returns the response."""
    from nighttrade.dashboard.tailnet_middleware import TailnetOnlyMiddleware

    mw = TailnetOnlyMiddleware(app=MagicMock())
    request = MagicMock()
    request.client.host = remote

    async def call_next(_req):
        from starlette.responses import PlainTextResponse
        return PlainTextResponse("downstream", status_code=200)

    return asyncio.run(mw.dispatch(request, call_next))


def test_dispatch_rejects_lan_with_403():
    importlib.reload(__import__("nighttrade.dashboard.tailnet_middleware",
                                 fromlist=["_is_allowed_remote"]))
    resp = _dispatch_with_remote("192.168.1.42")
    assert resp.status_code == 403
    assert b"Forbidden" in resp.body


def test_dispatch_passes_tailscale_through():
    importlib.reload(__import__("nighttrade.dashboard.tailnet_middleware",
                                 fromlist=["_is_allowed_remote"]))
    resp = _dispatch_with_remote("100.127.143.106")
    assert resp.status_code == 200
    assert resp.body == b"downstream"
