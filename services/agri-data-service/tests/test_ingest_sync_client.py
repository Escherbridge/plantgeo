"""`upstream_sync_client`: the synchronous metered factory `burn_severity/capture.py` needs.

See `test_ingest_http_meter.py` for the metering behaviour shared with `upstream_client`; this file
only pins the two properties specific to the SYNC factory -- that it honours the caller's own
`follow_redirects`/`trust_env` choice (never defaulted the way `upstream_client`'s redirect cap is),
and that it accepts a test-only `transport=` the way `upstream_client` does (design BUI2-12).
"""

# ruff: noqa: PLR2004

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from agri_data_service.ingest.http import upstream_sync_client

if TYPE_CHECKING:
    from collections.abc import Callable


def _responding(response: httpx.Response) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return response

    return handler


def test_upstream_sync_client_honours_the_callers_redirect_and_trust_env_choice() -> None:
    """Burn-severity refuses both redirects and the environment proxy config; the factory must not override either."""
    with upstream_sync_client(
        follow_redirects=False,
        trust_env=False,
        transport=httpx.MockTransport(_responding(httpx.Response(200, json={"ok": True}))),
    ) as client:
        assert client.follow_redirects is False
        assert client.trust_env is False
        response = client.get("https://waterservices.usgs.gov/x")
        assert response.status_code == 200


def test_upstream_sync_client_accepts_a_test_only_transport() -> None:
    """The test-only `transport=` keyword must reach the real client construction, not be swallowed."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(200, content=b"ok")

    with upstream_sync_client(transport=httpx.MockTransport(handler)) as client:
        client.get("https://example.test/x")
        client.get("https://example.test/y")

    assert calls["n"] == 2
