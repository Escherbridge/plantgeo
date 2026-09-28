"""The config provider client: a declared endpoint's keyed request, and single-location Open-Meteo requests.

`provider_endpoint_request` + `send_provider_request` are the config runner's seam: `pipeline/runner/
binding.py::ConfigProviderClient` resolves every request through them (FR-2 key rule, `KeyedRequestUrl`)
and makes ONE attempt, because the runner's unit ladder (`pipeline/runner/fetch.py`) owns the retries.
`fetch_single_location` keeps its own SOFT-8 ladder for a non-runner caller; no production caller uses
it yet (framework dark in Phase 1).

See `ingest/AGENTS.md` "provider_client.py" for the free/customer host reuse, the single-location
body contract, `KeyedRequestUrl`, and SOFT-8's Retry-After clamp.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlencode, urlsplit

from agri_data_service.foundation.observability import redaction
from agri_data_service.ingest.http import (
    UpstreamHttpError,
    UpstreamPayloadError,
    fetch_bounded,
)
from agri_data_service.ingest.open_meteo import (
    OPEN_METEO_API_KEY_PARAMETER,
    OPEN_METEO_API_KEY_VARIABLE,
    OPEN_METEO_BASE_URL,
    OPEN_METEO_BOUNDS,
    OPEN_METEO_FORECAST_CUSTOMER_BASE_URL,
    OPEN_METEO_HISTORICAL_FORECAST_BASE_URL,
    OPEN_METEO_HISTORICAL_FORECAST_BOUNDS,
    OPEN_METEO_HISTORICAL_FORECAST_CUSTOMER_BASE_URL,
)
from agri_data_service.ingest.open_meteo_endpoint import (
    OPEN_METEO_CELL_SELECTION,
    OpenMeteoEndpoint,
    OpenMeteoProductRequest,
    open_meteo_product_request,
)
from agri_data_service.ingest.policy import (
    MAX_LATITUDE,
    MAX_LONGITUDE,
    MIN_LATITUDE,
    MIN_LONGITUDE,
    format_javascript_number,
)
from agri_data_service.ingest.upstream_retry import UpstreamRetryPolicy, retry_upstream

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    import httpx

    from agri_data_service.foundation.lane_config.models import ProviderConfig
    from agri_data_service.ingest.http import BoundedResponse, UpstreamBounds


class ProviderConfigError(Exception):
    """Raised when a provider request REQUIRES a key and its environment variable is empty or unset.

    Distinct from `open_meteo.py::resolve_open_meteo_api_key`, which treats an absent/empty key as
    "use the free tier" -- correct there, since an ordinary forward run has no reason to fail just
    because no key was ever configured. A caller of `fetch_single_location(require_customer_host=True)`
    has already committed to the paid tier; silently answering from the free host instead would be
    worse than failing loud (plan 1C).
    """


class KeyedRequestUrl:
    """A request URL that may carry a paid-tier credential; `str`/`repr` are ALWAYS redacted.

    The structural guard behind LOG-3's "a keyed URL never reaches a log or an exception string":
    every other keyed URL in this package relies on the caller remembering to keep it out of an
    f-string (`open_meteo.py::ArchiveDailyRequest.request_url`, `OpenMeteoProductRequest.request_url`
    below). Wrapping the credentialed value here means an accidental `f"failed: {url}"` or
    `logger.warning(..., url=url)` publishes `str(url)` -- which is the REDACTED form, via
    `foundation.observability.redaction.redact_for_log` (the same substitution every other live
    secret already gets) -- not the key itself. `.reveal()` is the one escape hatch, called only
    where the credentialed string must actually leave the process (the httpx send).
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        """Wrap one credentialed URL. `value` is stored as given; only `reveal()` returns it whole."""
        self._value = value

    def reveal(self) -> str:
        """The real, credentialed URL. Pass this ONLY to the HTTP client that sends it."""
        return self._value

    def __str__(self) -> str:
        return redaction.redact_for_log(self._value)

    def __repr__(self) -> str:
        return f"KeyedRequestUrl({self.__str__()!r})"


def resolve_required_open_meteo_api_key() -> str:
    """Read `OPEN_METEO_API_KEY` for a call that REQUIRES the paid tier; empty/unset raises."""
    value = os.environ.get(OPEN_METEO_API_KEY_VARIABLE, "").strip()
    if not value:
        raise ProviderConfigError(f"{OPEN_METEO_API_KEY_VARIABLE} is required for this provider request but is empty")
    return value


# --- The two Phase-1 endpoints (plan 1C: open_meteo.py gains the hosts, this module pairs them) ----
# Built here, not in `open_meteo.py`, because `open_meteo_endpoint.py` already imports FROM
# `open_meteo.py` (its 429 classifier, `resolve_open_meteo_api_key`); importing `OpenMeteoEndpoint`
# back into `open_meteo.py` would be a circular import. This module depends on both and nothing
# depends on it yet, so it is the safe place for the pairing.

FORECAST_ENDPOINT: Final = OpenMeteoEndpoint(
    free_base_url=OPEN_METEO_BASE_URL,
    customer_base_url=OPEN_METEO_FORECAST_CUSTOMER_BASE_URL,
    bounds=OPEN_METEO_BOUNDS,
)

HISTORICAL_FORECAST_ENDPOINT: Final = OpenMeteoEndpoint(
    free_base_url=OPEN_METEO_HISTORICAL_FORECAST_BASE_URL,
    customer_base_url=OPEN_METEO_HISTORICAL_FORECAST_CUSTOMER_BASE_URL,
    bounds=OPEN_METEO_HISTORICAL_FORECAST_BOUNDS,
)

PROVIDER_CLIENT_RETRY_POLICY: Final = UpstreamRetryPolicy(
    event="provider_client_retry",
    exhausted_message="the config provider client exhausted its retry ladder",
)


@dataclass(frozen=True, slots=True)
class SingleLocationResponse:
    """One location's parsed response beside the exact text it was parsed from.

    Mirrors `open_meteo.py::CurrentWeatherResponse`'s "keep the parser's real input" rule: a rolling
    or checkpointed caller that wants to persist exactly what arrived (rather than a re-render of the
    parsed dict) needs `text`, not only `body`.
    """

    body: dict[str, object]
    text: str


def _require_single_coordinate(latitude: float, longitude: float) -> None:
    if not MIN_LATITUDE <= latitude <= MAX_LATITUDE or not MIN_LONGITUDE <= longitude <= MAX_LONGITUDE:
        raise ValueError("provider client coordinates are outside WGS84 bounds")


def single_location_parameters(latitude: float, longitude: float, parameters: Mapping[str, str]) -> dict[str, str]:
    """Order one location's governed parameters (latitude, longitude, cell_selection) before the caller's own.

    `cell_selection=nearest` is pinned for the same reason every other Open-Meteo product here pins
    it: left to the default, a request can be silently relocated to a neighbouring grid box the
    caller never named.
    """
    _require_single_coordinate(latitude, longitude)
    ordered: dict[str, str] = {
        "latitude": format_javascript_number(latitude),
        "longitude": format_javascript_number(longitude),
        "cell_selection": OPEN_METEO_CELL_SELECTION,
    }
    ordered.update(parameters)
    return ordered


def required_single_location_request(
    endpoint: OpenMeteoEndpoint[Any],
    latitude: float,
    longitude: float,
    parameters: Mapping[str, str],
) -> OpenMeteoProductRequest:
    """Resolve one location's request, REQUIRING the customer host; an empty/unset key raises.

    `open_meteo_endpoint.py::open_meteo_product_request` treats an absent key as "use the free
    host," which is correct for every existing product (an ordinary run has no reason to fail just
    because no key was ever configured). A caller of THIS function has already committed to the paid
    tier, so an empty key must fail loud instead of silently answering from the free host it never
    asked for (plan 1C).
    """
    api_key = resolve_required_open_meteo_api_key()
    ordered = single_location_parameters(latitude, longitude, parameters)
    sent = dict(ordered)
    sent[OPEN_METEO_API_KEY_PARAMETER] = api_key
    base_url = endpoint.customer_base_url
    return OpenMeteoProductRequest(base_url=base_url, request_url=f"{base_url}?{urlencode(sent)}")


def _resolve_single_location_request(
    endpoint: OpenMeteoEndpoint[Any],
    latitude: float,
    longitude: float,
    parameters: Mapping[str, str],
    *,
    require_customer_host: bool,
) -> OpenMeteoProductRequest:
    if require_customer_host:
        return required_single_location_request(endpoint, latitude, longitude, parameters)
    return open_meteo_product_request(endpoint, single_location_parameters(latitude, longitude, parameters))


def _parse_single_location_body(text: str) -> dict[str, object]:
    """Parse one location's response body, refusing the multi-location ARRAY shape by construction.

    Every other Open-Meteo product here (`open_meteo_endpoint.py`'s free/customer pattern) requests
    several locations at once and expects a JSON array, one entry per location. This client requests
    exactly ONE location, and Open-Meteo answers a single-location request with a bare OBJECT --
    `execution/open_meteo_lane.py::canonical_location_document` refuses exactly that shape (spec
    NEW-4), which is why G0's own edge probe still spends 2 locations rather than 1. This is the
    parser that accepts it, so a future caller CAN spend the cheaper 1-location probe.
    """
    try:
        parsed = json.loads(text)
    except ValueError as error:
        raise UpstreamPayloadError("provider client response contained invalid JSON") from error
    if not isinstance(parsed, dict):
        raise UpstreamPayloadError("provider client response was not a single-location object")
    return parsed


# --- A declared provider endpoint (the config runner's seam; pipeline/runner/binding.py) -------------

#: The query parameter each keyed provider takes its key in, until the provider schema names it.
PROVIDER_API_KEY_PARAMETERS: Final[Mapping[str, str]] = MappingProxyType({"open-meteo": OPEN_METEO_API_KEY_PARAMETER})


@dataclass(frozen=True, slots=True)
class ProviderEndpointRequest:
    """One request to a declared provider endpoint: the credential-free URL beside the one that is sent."""

    #: The free host's URL with the caller's parameters and no key: what reports, checkpoints and logs see.
    request_url: str
    #: What is actually sent: the customer host plus the key when the endpoint sells one, else `request_url`.
    send_url: KeyedRequestUrl


def provider_endpoint_request(
    provider: ProviderConfig,
    endpoint: str,
    parameters: Mapping[str, str],
    *,
    environment: Mapping[str, str] | None = None,
) -> ProviderEndpointRequest:
    """Resolve one request to a `lanes/_providers/<id>.toml` endpoint; a keyed endpoint REQUIRES its key (FR-2).

    A provider endpoint with a `customer_host` is always sent there with the key, and an empty key is
    `ProviderConfigError`, never a silent fall-back to the free host. An undeclared endpoint, or a keyed
    provider whose key parameter is unknown here, is `ProviderConfigError` too.
    """
    declared = provider.endpoints.get(endpoint)
    if declared is None:
        raise ProviderConfigError(f"endpoint {endpoint!r} is not declared by provider {provider.id!r}")
    credential_free = f"https://{declared.host}{declared.path}?{urlencode(sorted(parameters.items()))}"
    if declared.customer_host is None or provider.api_key_env is None:
        return ProviderEndpointRequest(request_url=credential_free, send_url=KeyedRequestUrl(credential_free))
    source = os.environ if environment is None else environment
    key = source.get(provider.api_key_env, "").strip()
    if not key:
        raise ProviderConfigError(f"{provider.api_key_env} is empty; lanes on {provider.id} need it (FR-2)")
    key_parameter = PROVIDER_API_KEY_PARAMETERS.get(provider.id)
    if key_parameter is None:
        raise ProviderConfigError(f"provider {provider.id!r} names api_key_env but no key parameter is known")
    keyed = urlencode([*parameters.items(), (key_parameter, key)])
    return ProviderEndpointRequest(
        request_url=credential_free,
        send_url=KeyedRequestUrl(f"https://{declared.customer_host}{declared.path}?{keyed}"),
    )


async def send_provider_request(
    client: httpx.AsyncClient, request: ProviderEndpointRequest, bounds: UpstreamBounds
) -> BoundedResponse:
    """ONE bounded attempt (plus `fetch_bounded`'s own transport re-sends); the caller's ladder owns every status."""
    return await fetch_bounded(client, request.send_url.reveal(), bounds)


async def fetch_single_location(  # noqa: PLR0913 - client, endpoint, coordinates, params, host mode, policy, probe, clocks are each distinct
    client: httpx.AsyncClient,
    endpoint: OpenMeteoEndpoint[Any],
    *,
    latitude: float,
    longitude: float,
    parameters: Mapping[str, str],
    require_customer_host: bool = False,
    retry_policy: UpstreamRetryPolicy = PROVIDER_CLIENT_RETRY_POLICY,
    probe: bool = False,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> SingleLocationResponse:
    """Fetch and parse one location's response, retried on SOFT-8's ladder with a Retry-After clamp.

    `client` should come from `ingest/http.py::upstream_client`/`upstream_sync_client` (metered since
    GL-2) -- this function does not open one itself, so ONE client (and its TCP connection) is reused
    across every retry attempt, matching how every other producer in this package retries. A 429 or
    5xx is retried up to `retry_policy.ladder.max_attempts` times; when the response named a
    `Retry-After`, that delay is used (clamped to the ladder's own ceiling) in place of the ladder's
    ordinary doubling backoff (`upstream_retry.py::retry_upstream`'s docstring). `require_customer_host`
    selects between the ordinary free/customer fallback (`open_meteo_product_request`, absent key ->
    free host) and the REQUIRED paid tier (`required_single_location_request`, absent key -> raises
    `ProviderConfigError`). `sleep`/`monotonic` are the same test seam `retry_upstream` itself
    exposes, forwarded rather than re-invented, so a retry-ladder test never sleeps for real.
    """
    request = _resolve_single_location_request(
        endpoint, latitude, longitude, parameters, require_customer_host=require_customer_host
    )
    keyed_url = KeyedRequestUrl(request.request_url)
    host = urlsplit(request.base_url).hostname or request.base_url

    async def _attempt_once() -> SingleLocationResponse:
        response = await fetch_bounded(client, keyed_url.reveal(), endpoint.bounds)
        if not response.ok:
            raise UpstreamHttpError(response.status, retry_after_seconds=response.retry_after_seconds)
        if response.payload_error is not None:
            raise response.payload_error
        if response.content_type is not None and "json" not in response.content_type.lower():
            raise UpstreamPayloadError("provider client response was not JSON")
        return SingleLocationResponse(body=_parse_single_location_body(response.text), text=response.text)

    return await retry_upstream(
        _attempt_once, retry_policy, host=host, probe=probe, context={"host": host}, sleep=sleep, monotonic=monotonic
    )


__all__ = [
    "FORECAST_ENDPOINT",
    "HISTORICAL_FORECAST_ENDPOINT",
    "PROVIDER_API_KEY_PARAMETERS",
    "PROVIDER_CLIENT_RETRY_POLICY",
    "KeyedRequestUrl",
    "ProviderConfigError",
    "ProviderEndpointRequest",
    "SingleLocationResponse",
    "fetch_single_location",
    "provider_endpoint_request",
    "required_single_location_request",
    "resolve_required_open_meteo_api_key",
    "send_provider_request",
    "single_location_parameters",
]
