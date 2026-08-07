"""Open-Meteo Ensemble adapter: the reviewed ensemble hosts and the bounded hourly per-member reader."""

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
OpenMeteoEnsembleBaseUrl = Literal[
    "https://ensemble-api.open-meteo.com/v1/ensemble",
    "https://customer-ensemble-api.open-meteo.com/v1/ensemble",
]

OPEN_METEO_ENSEMBLE_BASE_URL: Final[OpenMeteoEnsembleBaseUrl] = "https://ensemble-api.open-meteo.com/v1/ensemble"
OPEN_METEO_ENSEMBLE_CUSTOMER_BASE_URL: Final[OpenMeteoEnsembleBaseUrl] = (
    "https://customer-ensemble-api.open-meteo.com/v1/ensemble"
)

# One member series per variable per location, so a body is member_count times a deterministic
# response. The ceiling holds a 25-cell, 5-variable, 16-day, 51-member chunk with headroom.
OPEN_METEO_ENSEMBLE_BOUNDS: Final = UpstreamBounds(max_bytes=64 * 1024 * 1024, timeout_seconds=300.0)

# `require_base_url` on this endpoint IS the ensemble host rule; there is deliberately no
# `require_ensemble_base_url` copy of it. See ingest/AGENTS.md on open_meteo_endpoint.py.
OPEN_METEO_ENSEMBLE_ENDPOINT: Final[OpenMeteoEndpoint[OpenMeteoEnsembleBaseUrl]] = OpenMeteoEndpoint(
    free_base_url=OPEN_METEO_ENSEMBLE_BASE_URL,
    customer_base_url=OPEN_METEO_ENSEMBLE_CUSTOMER_BASE_URL,
    bounds=OPEN_METEO_ENSEMBLE_BOUNDS,
)

OPEN_METEO_ENSEMBLE_CELL_SELECTION: Final = OPEN_METEO_CELL_SELECTION

# The hourly axis is pinned to GMT so a returned instant needs no session-timezone reasoning.
OPEN_METEO_ENSEMBLE_TIME_ZONE: Final = "GMT"

# `models` MUST be sent and is never defaulted here: each ensemble sits on its own native lattice
# and carries its own member count, which is the denominator of every quantile this lane derives.
ENSEMBLE_MODELS: Final = (
    "ecmwf_ifs04",
    "gem_global",
    "gfs025",
)

# Two-digit member numbering is the provider's own format; the largest reviewed ensemble has 51.
MAX_ENSEMBLE_MEMBER_NUMBER: Final = 99


def ensemble_hourly_parameters(
    coordinates: Sequence[tuple[float, float]],
    hourly_variables: Sequence[str],
    start_date: date,
    end_date: date,
    model: str,
) -> dict[str, str]:
    """Validate one multi-location ensemble request and order its governed parameters for a checksum."""
    require_wgs84_request_coordinates(coordinates)
    require_sorted_unique_variables(hourly_variables, "ensemble hourly")
    if end_date < start_date:
        raise ValueError("Open-Meteo ensemble end_date must not precede start_date")
    if model not in ENSEMBLE_MODELS:
        raise ValueError(f"Open-Meteo ensemble model must be one of: {', '.join(ENSEMBLE_MODELS)}")
    return {
        "latitude": ",".join(format_javascript_number(latitude) for latitude, _ in coordinates),
        "longitude": ",".join(format_javascript_number(longitude) for _, longitude in coordinates),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "hourly": ",".join(hourly_variables),
        "models": model,
        "timezone": OPEN_METEO_ENSEMBLE_TIME_ZONE,
        "cell_selection": OPEN_METEO_ENSEMBLE_CELL_SELECTION,
    }


def ensemble_hourly_url(  # noqa: PLR0913
    coordinates: Sequence[tuple[float, float]],
    hourly_variables: Sequence[str],
    start_date: date,
    end_date: date,
    model: str,
    *,
    base_url: str | None = None,
) -> str:
    """Build the credential-free ensemble URL, whose parameter order is stable for a checksum.

    Safe to persist, and structurally unable to carry a key. `base_url` defaults to the host this
    process would call now; pass the host a past retrieval really used when recording provenance
    for cached bytes.
    """
    return open_meteo_product_url(
        OPEN_METEO_ENSEMBLE_ENDPOINT,
        ensemble_hourly_parameters(coordinates, hourly_variables, start_date, end_date, model),
        base_url=base_url,
    )


def ensemble_hourly_request(
    coordinates: Sequence[tuple[float, float]],
    hourly_variables: Sequence[str],
    start_date: date,
    end_date: date,
    model: str,
) -> OpenMeteoProductRequest:
    """Resolve the credential once, returning the host to record and the credentialed URL to send."""
    return open_meteo_product_request(
        OPEN_METEO_ENSEMBLE_ENDPOINT,
        ensemble_hourly_parameters(coordinates, hourly_variables, start_date, end_date, model),
    )


def ensemble_member_field_name(variable: str, member_number: int) -> str:
    """Return the per-member series key Open-Meteo publishes for one hourly variable."""
    if member_number < 1 or member_number > MAX_ENSEMBLE_MEMBER_NUMBER:
        raise ValueError(f"ensemble member numbers run from 1 to {MAX_ENSEMBLE_MEMBER_NUMBER}")
    return f"{variable}_member{member_number:02d}"


async def fetch_ensemble_hourly(client: httpx.AsyncClient, url: str) -> str:
    """Fetch one ensemble response as raw text so the caller can checksum exactly what arrived."""
    return await fetch_open_meteo_product_text(client, url, OPEN_METEO_ENSEMBLE_ENDPOINT)
