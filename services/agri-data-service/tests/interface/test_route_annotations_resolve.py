"""Every route handler the app registers must have annotations that resolve at runtime.

sanic-ext evaluates handler annotations while workers start. So a return type imported only under
`TYPE_CHECKING` passes the route tests, which call handlers directly, but stops every worker at boot.
That happened to the 2026-09-28 soil-survey S3 deploy: `name 'HTTPResponse' is not defined`.
See `src/agri_data_service/interface/http/AGENTS.md`.
"""

from __future__ import annotations

import inspect
import typing
from typing import TYPE_CHECKING

from sanic import Sanic

from agri_data_service.app import create_app

if TYPE_CHECKING:
    import pytest


def test_every_registered_route_handler_resolves_its_annotations(monkeypatch: pytest.MonkeyPatch) -> None:
    # Another test may already hold the "agri-data-service" app; test mode lets Sanic register it twice.
    monkeypatch.setattr(Sanic, "test_mode", True)
    app = create_app()
    unresolved: list[str] = []
    for route in app.router.routes:
        handler = inspect.unwrap(route.handler)
        try:
            typing.get_type_hints(handler)
        except NameError as error:
            unresolved.append(f"{route.path} ({handler.__module__}.{handler.__qualname__}): {error}")
    assert unresolved == [], "route annotations that only exist under TYPE_CHECKING: " + "; ".join(unresolved)
