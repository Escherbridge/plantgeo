"""Explicit localhost-only fixture app; unmounted from all production profiles."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from sanic import Blueprint, Request, Sanic, response

from agri_data_service.agent.weather_forecast import weather_forecast_field, weather_forecast_location
from agri_data_service.planes.weather_forecast import FieldRequest, LocationRequest

if TYPE_CHECKING:
    from types import SimpleNamespace

    from sanic.config import Config

BOX_COORDINATES = 4


def _parameter(request: Request, name: str, default: str | None = None) -> str:
    values = request.args.getlist(name)
    if not values and default is not None:
        return default
    if len(values) != 1 or not isinstance(values[0], str):
        raise ValueError(f"Expected exactly one {name}")
    return values[0]


def create_fixture_app(root: Path) -> Sanic[Config, SimpleNamespace]:
    """Expose only the explicitly supplied local artifacts on a standalone app."""
    app: Sanic[Config, SimpleNamespace] = Sanic("weather_forecast_fixture")
    blueprint = Blueprint("weather_forecast_fixture", url_prefix="/weather-forecast")

    @blueprint.get("/location")
    async def location(request: Request) -> response.HTTPResponse:
        try:
            query = LocationRequest(
                run_id=_parameter(request, "run_id"),
                lat=float(_parameter(request, "lat")),
                lon=float(_parameter(request, "lon")),
                start=_parameter(request, "start"),
                end=_parameter(request, "end"),
            )
            return response.json(await weather_forecast_location(root, query))
        except (KeyError, ValueError, IndexError) as error:
            return response.json({"status": "refused", "reason": f"Invalid fixture query: {error}"}, status=400)

    @blueprint.get("/field")
    async def field(request: Request) -> response.HTTPResponse:
        try:
            bbox = tuple(float(value) for value in _parameter(request, "bbox").split(","))
            if len(bbox) != BOX_COORDINATES:
                raise ValueError("bbox needs four coordinates")
            query = FieldRequest(
                _parameter(request, "run_id"),
                _parameter(request, "valid_time"),
                _parameter(request, "variable"),
                (bbox[0], bbox[1], bbox[2], bbox[3]),
            )
            return response.json(
                await weather_forecast_field(
                    root, query, requested_zoom=int(_parameter(request, "requested_zoom", "13"))
                )
            )
        except (KeyError, ValueError, IndexError) as error:
            return response.json({"status": "refused", "reason": f"Invalid fixture query: {error}"}, status=400)

    app.blueprint(blueprint)
    return app


def main() -> None:
    """Start a read-only fixture service bound to loopback."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8097)
    args = parser.parse_args()
    create_fixture_app(args.root).run(host="127.0.0.1", port=args.port, single_process=True, access_log=False)


if __name__ == "__main__":
    main()
