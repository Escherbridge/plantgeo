"""Open-Meteo GloFAS adapter: the reviewed flood hosts and the bounded daily river-discharge reader."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.ingest.http import UpstreamBounds
from agri_data_service.ingest.open_meteo_endpoint import (
    OPEN_METEO_CELL_SELECTION,
    OpenMeteoEndpoint,
    fetch_open_meteo_product_text,
    open_meteo_product_request,
    open_meteo_product_url,
    require_sorted_unique_variables,
    require_wgs84_request_coordinates,
)
from agri_data_service.ingest.policy import format_javascript_number

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    import httpx

    from agri_data_service.ingest.open_meteo_endpoint import OpenMeteoProductRequest

# A closed set, so a host can be carried through provenance and validated on the way back in.
OpenMeteoFloodBaseUrl = Literal[
    "https://flood-api.open-meteo.com/v1/flood",
    "https://customer-flood-api.open-meteo.com/v1/flood",
]

OPEN_METEO_FLOOD_BASE_URL: Final[OpenMeteoFloodBaseUrl] = "https://flood-api.open-meteo.com/v1/flood"
OPEN_METEO_FLOOD_CUSTOMER_BASE_URL: Final[OpenMeteoFloodBaseUrl] = (
    "https://customer-flood-api.open-meteo.com/v1/flood"
)

# A four-year daily replay for 200 reaches is roughly 16 MB of JSON; the ceiling leaves headroom for
# the full ensemble-statistic variable set without ever allowing an unbounded body.
OPEN_METEO_FLOOD_BOUNDS: Final = UpstreamBounds(max_bytes=64 * 1024 * 1024, timeout_seconds=300.0)

# `require_base_url` on this endpoint IS the flood host rule; there is deliberately no
# `require_flood_base_url` copy of it. See ingest/AGENTS.md on open_meteo_endpoint.py.
OPEN_METEO_FLOOD_ENDPOINT: Final[OpenMeteoEndpoint[OpenMeteoFloodBaseUrl]] = OpenMeteoEndpoint(
    free_base_url=OPEN_METEO_FLOOD_BASE_URL,
    customer_base_url=OPEN_METEO_FLOOD_CUSTOMER_BASE_URL,
    bounds=OPEN_METEO_FLOOD_BOUNDS,
)

OPEN_METEO_FLOOD_CELL_SELECTION: Final = OPEN_METEO_CELL_SELECTION

# `models` MUST be sent: v3 and v4 sit on different native lattices, so this value decides which
# grid box a returned reach belongs to and therefore which spatial support the row may claim.
GLOFAS_MODELS: Final = (
    "consolidated_v3",
    "consolidated_v4",
    "forecast_v3",
    "forecast_v4",
    "seamless_v3",
    "seamless_v4",
)


def flood_daily_parameters(
    coordinates: Sequence[tuple[float, float]],
    daily_variables: Sequence[str],
    start_date: date,
    end_date: date,
    model: str,
) -> dict[str, str]:
    """Validate one multi-location flood request and order its governed parameters for a checksum."""
    require_wgs84_request_coordinates(coordinates)
    require_sorted_unique_variables(daily_variables, "flood daily")
    if end_date < start_date:
        raise ValueError("Open-Meteo flood end_date must not precede start_date")
    if model not in GLOFAS_MODELS:
        raise ValueError(f"Open-Meteo flood model must be one of: {', '.join(GLOFAS_MODELS)}")
    return {
        "latitude": ",".join(format_javascript_number(latitude) for latitude, _ in coordinates),
        "longitude": ",".join(format_javascript_number(longitude) for _, longitude in coordinates),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": ",".join(daily_variables),
        "models": model,
        "cell_selection": OPEN_METEO_FLOOD_CELL_SELECTION,
    }


def flood_daily_url(  # noqa: PLR0913
    coordinates: Sequence[tuple[float, float]],
    daily_variables: Sequence[str],
    start_date: date,
    end_date: date,
    model: str,
    *,
    base_url: str | None = None,
) -> str:
    """Build the credential-free flood URL, whose parameter order is stable for a checksum.

    Safe to persist, and structurally unable to carry a key. `base_url` defaults to the host this
    process would call now; pass the host a past retrieval really used when recording provenance
    for cached bytes.
    """
    return open_meteo_product_url(
        OPEN_METEO_FLOOD_ENDPOINT,
        flood_daily_parameters(coordinates, daily_variables, start_date, end_date, model),
        base_url=base_url,
    )


def flood_daily_request(
    coordinates: Sequence[tuple[float, float]],
    daily_variables: Sequence[str],
    start_date: date,
    end_date: date,
    model: str,
) -> OpenMeteoProductRequest:
    """Resolve the credential once, returning the host to record and the credentialed URL to send."""
    return open_meteo_product_request(
        OPEN_METEO_FLOOD_ENDPOINT,
        flood_daily_parameters(coordinates, daily_variables, start_date, end_date, model),
    )


async def fetch_flood_daily(client: httpx.AsyncClient, url: str) -> str:
    """Fetch one flood response as raw text so the caller can checksum exactly what arrived."""
    return await fetch_open_meteo_product_text(client, url, OPEN_METEO_FLOOD_ENDPOINT)
