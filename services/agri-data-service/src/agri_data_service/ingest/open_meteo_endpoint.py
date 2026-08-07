"""Shared Open-Meteo product plumbing: host and credential resolution, and one bounded reader per endpoint."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlencode

from agri_data_service.ingest.http import (
    HTTP_TOO_MANY_REQUESTS,
    UpstreamHttpError,
    UpstreamPayloadError,
    fetch_bounded,
)
from agri_data_service.ingest.open_meteo import (
    OPEN_METEO_API_KEY_PARAMETER,
    OpenMeteoRateLimitError,
    _rate_limit_scope,  # One 429 classifier for every Open-Meteo endpoint; imported so it cannot drift.
    resolve_open_meteo_api_key,
)
from agri_data_service.ingest.policy import (
    MAX_LATITUDE,
    MAX_LONGITUDE,
    MIN_LATITUDE,
    MIN_LONGITUDE,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import httpx

    from agri_data_service.ingest.http import UpstreamBounds

# Every Open-Meteo multi-location endpoint answers at most this many coordinates in one request.
MAX_OPEN_METEO_LOCATIONS_PER_REQUEST: Final = 200

# `nearest` is pinned rather than left to the default so a request can never be silently relocated
# to a neighbouring grid box that is not the one the analysis lattice names.
OPEN_METEO_CELL_SELECTION: Final = "nearest"


@dataclass(frozen=True, slots=True)
class OpenMeteoEndpoint[BaseUrl: str]:
    """One Open-Meteo product endpoint: its free host, its paid host, and the budget one request may consume."""

    free_base_url: BaseUrl
    customer_base_url: BaseUrl
    bounds: UpstreamBounds

    def require_base_url(self, value: str) -> BaseUrl:
        """Accept only one of this endpoint's two reviewed hosts, so a tampered local receipt cannot become provenance.

        The ONE provenance rule for every Open-Meteo product. It returns the endpoint's own literal
        type, which is why a per-lane copy narrowed to a per-lane Literal is never needed.
        """
        if value == self.free_base_url:
            return self.free_base_url
        if value == self.customer_base_url:
            return self.customer_base_url
        raise ValueError("Open-Meteo base URL is not a reviewed endpoint host")


@dataclass(frozen=True, slots=True)
class OpenMeteoProductRequest:
    """One resolved product request: the host provenance records, and the URL actually sent."""

    base_url: str
    # Carries the paid-tier credential when one is configured. Never persist, log, or checksum it.
    request_url: str


def open_meteo_product_base_url[BaseUrl: str](endpoint: OpenMeteoEndpoint[BaseUrl]) -> BaseUrl:
    """Return the host this process will really call: the customer host when a key is set, the free one otherwise."""
    return endpoint.free_base_url if resolve_open_meteo_api_key() is None else endpoint.customer_base_url


def open_meteo_product_url(
    endpoint: OpenMeteoEndpoint[Any],
    parameters: Mapping[str, str],
    *,
    base_url: str | None = None,
) -> str:
    """Build the credential-free product URL, whose parameter order is stable for a checksum.

    Safe to persist, and structurally unable to carry a key. `base_url` defaults to the host this
    process would call now; pass the host a past retrieval really used when recording provenance
    for cached bytes.
    """
    resolved = open_meteo_product_base_url(endpoint) if base_url is None else endpoint.require_base_url(base_url)
    return f"{resolved}?{urlencode(dict(parameters))}"


def open_meteo_product_request(
    endpoint: OpenMeteoEndpoint[Any],
    parameters: Mapping[str, str],
) -> OpenMeteoProductRequest:
    """Resolve the credential once, returning the host to record and the credentialed URL to send.

    The key is appended after every governed parameter, so the keyless URL never shifts.
    """
    api_key = resolve_open_meteo_api_key()
    base_url = endpoint.free_base_url if api_key is None else endpoint.customer_base_url
    sent = dict(parameters)
    if api_key is not None:
        sent[OPEN_METEO_API_KEY_PARAMETER] = api_key
    return OpenMeteoProductRequest(base_url=base_url, request_url=f"{base_url}?{urlencode(sent)}")


def require_wgs84_request_coordinates(coordinates: Sequence[tuple[float, float]]) -> None:
    """Reject an empty, oversized, or off-globe multi-location request before it is sent."""
    if not coordinates or len(coordinates) > MAX_OPEN_METEO_LOCATIONS_PER_REQUEST:
        raise ValueError("Open-Meteo requests must carry between one and 200 locations")
    for latitude, longitude in coordinates:
        if (
            not math.isfinite(latitude)
            or not MIN_LATITUDE <= latitude <= MAX_LATITUDE
            or not math.isfinite(longitude)
            or not MIN_LONGITUDE <= longitude <= MAX_LONGITUDE
        ):
            raise ValueError("Open-Meteo coordinates are outside WGS84 bounds")


def require_sorted_unique_variables(variables: Sequence[str], label: str) -> None:
    """Reject an unsorted, repeated, or empty variable list so one request maps to one stable URL."""
    if not variables:
        raise ValueError(f"Open-Meteo {label} variables must be sorted and non-empty")
    listed = list(variables)
    if listed != sorted(listed) or len(listed) != len(set(listed)):
        raise ValueError(f"Open-Meteo {label} variables must be sorted and non-empty")


async def fetch_open_meteo_product_text(
    client: httpx.AsyncClient,
    url: str,
    endpoint: OpenMeteoEndpoint[Any],
) -> str:
    """Fetch one product response as raw text so the caller can checksum exactly what arrived."""
    response = await fetch_bounded(client, url, endpoint.bounds)
    if response.status == HTTP_TOO_MANY_REQUESTS:
        raise OpenMeteoRateLimitError(*_rate_limit_scope(response.text))
    if not response.ok:
        raise UpstreamHttpError(response.status)
    if response.payload_error is not None:
        raise response.payload_error
    if response.content_type is not None and "json" not in response.content_type.lower():
        raise UpstreamPayloadError("Open-Meteo product response was not JSON")
    return response.text
