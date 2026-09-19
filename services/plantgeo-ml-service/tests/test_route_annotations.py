"""Every route handler's annotations must resolve at runtime, because sanic-ext evaluates them."""

from __future__ import annotations

import typing

from sanic import Sanic
from sanic.exceptions import SanicException

from plantgeo_ml_service.app import create_app


def test_every_handler_annotation_resolves_at_runtime() -> None:
    """Deployment c125e2c2 (2026-09-19) died with NameError: 'Request' is not defined.

    `from __future__ import annotations` turns every annotation into a string, and sanic-ext
    evaluates those strings when the worker starts. A name imported only under TYPE_CHECKING
    therefore passes ruff, mypy and pytest and still kills the process in production. This
    performs the same evaluation sanic-ext does, for every registered route, inside the test
    body so no app is registered at collection time.
    """
    try:
        # test_app.py may already have registered the factory's app; Sanic refuses a duplicate name.
        application = Sanic.get_app("plantgeo-ml")
    except SanicException:
        application = create_app()
    unresolved: list[str] = []
    for name, route in application.router.routes_all.items():
        try:
            hints = typing.get_type_hints(route.handler)
        except NameError as error:
            unresolved.append(f"{name}: {error}")
            continue
        if not hints:
            unresolved.append(f"{name}: handler has no resolvable annotations")
    joined = "\n".join(unresolved)
    assert not unresolved, f"handler annotations that would kill a sanic-ext worker:\n{joined}"
