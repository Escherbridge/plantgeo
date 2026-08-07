"""Open-Meteo CAMS adapter: the reviewed air-quality hosts and the bounded hourly concentration reader."""

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
OpenMeteoAirQualityBaseUrl = Literal[
    "https://air-quality-api.open-meteo.com/v1/air-quality",
    "https://customer-air-quality-api.open-meteo.com/v1/air-quality",
]

OPEN_METEO_AIR_QUALITY_BASE_URL: Final[OpenMeteoAirQualityBaseUrl] = (
    "https://air-quality-api.open-meteo.com/v1/air-quality"
)
OPEN_METEO_AIR_QUALITY_CUSTOMER_BASE_URL: Final[OpenMeteoAirQualityBaseUrl] = (
    "https://customer-air-quality-api.open-meteo.com/v1/air-quality"
)

# CAMS is served hourly, not daily, so a request carries 24 values per variable per cell per day.
# The ceiling bounds one cell block x one day block; the day block is what the plan tunes to stay
# under it, never this constant. See the chunking rule in execution/historical_cams.py.
OPEN_METEO_AIR_QUALITY_BOUNDS: Final = UpstreamBounds(max_bytes=32 * 1024 * 1024, timeout_seconds=300.0)

# `require_base_url` on this endpoint IS the air-quality host rule; there is deliberately no
# `require_air_quality_base_url` copy of it. See ingest/AGENTS.md on open_meteo_endpoint.py.
OPEN_METEO_AIR_QUALITY_ENDPOINT: Final[OpenMeteoEndpoint[OpenMeteoAirQualityBaseUrl]] = OpenMeteoEndpoint(
    free_base_url=OPEN_METEO_AIR_QUALITY_BASE_URL,
    customer_base_url=OPEN_METEO_AIR_QUALITY_CUSTOMER_BASE_URL,
    bounds=OPEN_METEO_AIR_QUALITY_BOUNDS,
)

OPEN_METEO_AIR_QUALITY_CELL_SELECTION: Final = OPEN_METEO_CELL_SELECTION

# `domains` MUST be sent: the two CAMS domains sit on different native lattices (0.4 degree global,
# 0.1 degree European), so this value decides which spatial support a returned value may claim.
CAMS_DOMAINS: Final = ("cams_europe", "cams_global")

HOURS_PER_DAY: Final = 24


def air_quality_hourly_parameters(
    coordinates: Sequence[tuple[float, float]],
    hourly_variables: Sequence[str],
    start_date: date,
    end_date: date,
    domain: str,
) -> dict[str, str]:
    """Validate one multi-location air-quality request and order its governed parameters for a checksum."""
    require_wgs84_request_coordinates(coordinates)
    require_sorted_unique_variables(hourly_variables, "air-quality hourly")
    if end_date < start_date:
        raise ValueError("Open-Meteo air-quality end_date must not precede start_date")
    if domain not in CAMS_DOMAINS:
        raise ValueError(f"Open-Meteo air-quality domain must be one of: {', '.join(CAMS_DOMAINS)}")
    return {
        "latitude": ",".join(format_javascript_number(latitude) for latitude, _ in coordinates),
        "longitude": ",".join(format_javascript_number(longitude) for _, longitude in coordinates),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "hourly": ",".join(hourly_variables),
        "domains": domain,
        "timezone": "GMT",
        "cell_selection": OPEN_METEO_AIR_QUALITY_CELL_SELECTION,
    }


def air_quality_hourly_url(  # noqa: PLR0913
    coordinates: Sequence[tuple[float, float]],
    hourly_variables: Sequence[str],
    start_date: date,
    end_date: date,
    domain: str,
    *,
    base_url: str | None = None,
) -> str:
    """Build the credential-free air-quality URL, whose parameter order is stable for a checksum.

    Safe to persist, and structurally unable to carry a key. `base_url` defaults to the host this
    process would call now; pass the host a past retrieval really used when recording provenance
    for cached bytes.
    """
    return open_meteo_product_url(
        OPEN_METEO_AIR_QUALITY_ENDPOINT,
        air_quality_hourly_parameters(coordinates, hourly_variables, start_date, end_date, domain),
        base_url=base_url,
    )


def air_quality_hourly_request(
    coordinates: Sequence[tuple[float, float]],
    hourly_variables: Sequence[str],
    start_date: date,
    end_date: date,
    domain: str,
) -> OpenMeteoProductRequest:
    """Resolve the credential once, returning the host to record and the credentialed URL to send."""
    return open_meteo_product_request(
        OPEN_METEO_AIR_QUALITY_ENDPOINT,
        air_quality_hourly_parameters(coordinates, hourly_variables, start_date, end_date, domain),
    )


async def fetch_air_quality_hourly(client: httpx.AsyncClient, url: str) -> str:
    """Fetch one air-quality response as raw text so the caller can checksum exactly what arrived."""
    return await fetch_open_meteo_product_text(client, url, OPEN_METEO_AIR_QUALITY_ENDPOINT)
