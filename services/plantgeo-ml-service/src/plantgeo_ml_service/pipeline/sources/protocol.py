"""The weather-forecast layer's source contract: coverage, bounds, and one bounded run pull.

Layer L3. See `AGENTS.md` in this directory for what a source owns, what the lane owns, and the
normalisation decisions the Open-Meteo implementation makes at this boundary.

The payload protocols enumerate EXACTLY the members `pipeline/weather_forecast_daily.py` reads off
a run: nothing wider, so a type checker sees the shape the code does, and nothing narrower, so a
second provider's source satisfies them without inheriting from anything. There are deliberately NO
retry knobs here: a rate limit raises once and the caller decides whether and when to try again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime


class SourceRequestError(ValueError):
    """Raised when a request would exceed a source's declared bounded-fetch budget."""


class SourcePayloadError(ValueError):
    """Raised when a response cannot be admitted as the shape the source contract promises."""


class SourceTransportError(RuntimeError):
    """Raised when a bounded fetch could not complete: a status, a timeout, or an oversized body."""


class SourceRateLimitError(SourceTransportError):
    """Raised ONCE when a provider refuses for quota; never retried inside the fetch.

    The classified quota `scope` is what a caller persists as a durable cooldown. Sleeping here
    would hide the refusal inside a fetch that looks slow rather than rate limited.
    """

    def __init__(self, scope: str, reason: str) -> None:
        super().__init__(f"{scope}: {reason}")
        self.scope = scope
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SourceBounds:
    """What one fetch may cost: bytes off the wire, and seconds on the clock."""

    max_bytes: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if self.max_bytes <= 0 or self.timeout_seconds <= 0:
            raise SourceRequestError("a bounded fetch declares a positive byte ceiling and timeout")


@dataclass(frozen=True, slots=True)
class SourceCoverage:
    """Where a source can fill a layer at all: a named claim, optionally bounded by a box."""

    claim: str
    #: `(west, south, east, north)` in WGS 84 degrees, or `None` for a global claim.
    bounding_box: tuple[float, float, float, float] | None = None

    def covers(self, longitude: float, latitude: float) -> bool:
        """Report whether one point lies inside this claim."""
        if self.bounding_box is None:
            return True
        west, south, east, north = self.bounding_box
        return west <= longitude <= east and south <= latitude <= north


#: A provider whose model spans the whole planet makes this claim; a regional model declares a box.
GLOBAL_SOURCE_COVERAGE: Final = SourceCoverage(claim="global")


@dataclass(frozen=True, slots=True)
class SourceReceipt:
    """What a fetch actually asked for and got back: the audit trail a published row cites.

    `request_url` is credential-free by construction, and `response_sha256` digests the exact bytes
    that were parsed, so a replay of the same run is provably the same evidence.
    """

    source_slug: str
    request_url: str
    response_sha256: str
    response_bytes: int
    location_count: int


@runtime_checkable
class WeatherForecastSamplePayload(Protocol):
    """One variable's reading at one valid instant, at one requested location."""

    @property
    def valid_time(self) -> datetime:
        """The instant this value verifies at, UTC. For an accumulation, the window's START."""
        ...

    @property
    def interval_start(self) -> datetime | None:
        """The accumulation window's start, UTC; `None` for an instantaneous reading."""
        ...

    @property
    def interval_end(self) -> datetime | None:
        """The accumulation window's end, UTC; `None` for an instantaneous reading."""
        ...

    @property
    def variable(self) -> str:
        """One of `warehouse.weather_forecast.VARIABLES`' keys."""
        ...

    @property
    def value(self) -> float | None:
        """The reading, or `None` for a governed absence, which populates `missing_reason`."""
        ...

    @property
    def missing_reason(self) -> str | None:
        """One of `warehouse.weather_forecast.MISSING_REASONS`, or `None` when `value` is set."""
        ...

    @property
    def support(self) -> str:
        """One of `warehouse.weather_forecast.SUPPORT_KINDS`."""
        ...


@runtime_checkable
class WeatherForecastLocationPayload(Protocol):
    """One requested point's full sample series, in the source's own coordinate space."""

    @property
    def latitude(self) -> float:
        """The latitude the source ANSWERED for; a nearest-grid snap moves it off the request."""
        ...

    @property
    def longitude(self) -> float:
        """See `latitude`."""
        ...

    @property
    def samples(self) -> Sequence[WeatherForecastSamplePayload]:
        """Every variable reading at every valid instant this location's series carries."""
        ...


@runtime_checkable
class WeatherForecastRunPayload(Protocol):
    """One admitted NWP model run: its identity, when it was read, and every location it covers."""

    @property
    def run_id(self) -> str:
        """`<provider>:<model>:<init_time ISO>`; pins one published series."""
        ...

    @property
    def model_init_time(self) -> datetime:
        """The model's own initialization instant, UTC: the value the CALLER requested, not an echo."""
        ...

    @property
    def provider_issue_time(self) -> datetime | None:
        """The provider's release instant, when it publishes one."""
        ...

    @property
    def fetched_at(self) -> datetime:
        """When this run was read, UTC."""
        ...

    @property
    def locations(self) -> Sequence[WeatherForecastLocationPayload]:
        """One entry per requested coordinate, aligned to the request by VERIFIED pairing."""
        ...

    @property
    def receipt(self) -> SourceReceipt:
        """The credential-free request and the digest of the bytes this run was parsed from."""
        ...


@runtime_checkable
class WeatherForecastSource(Protocol):
    """One provider's weather-forecast source: what it covers, and how one bounded run is read."""

    @property
    def source_slug(self) -> str:
        """The manifest slug for this implementation, e.g. `"open-meteo"`."""
        ...

    @property
    def coverage(self) -> SourceCoverage:
        """Where this source can fill the weather-forecast layer at all."""
        ...

    @property
    def max_locations_per_request(self) -> int:
        """The most locations one request may carry; a longer cell list is fetched in batches."""
        ...

    async def fetch_run(
        self,
        *,
        model_init_time: datetime,
        coordinates: Sequence[tuple[float, float]],
        variables: Sequence[str],
        forecast_days: int,
    ) -> WeatherForecastRunPayload:
        """Fetch exactly one bounded run pull; refuse rather than truncate an over-budget request."""
        ...


__all__ = [
    "GLOBAL_SOURCE_COVERAGE",
    "SourceBounds",
    "SourceCoverage",
    "SourcePayloadError",
    "SourceRateLimitError",
    "SourceReceipt",
    "SourceRequestError",
    "SourceTransportError",
    "WeatherForecastLocationPayload",
    "WeatherForecastRunPayload",
    "WeatherForecastSamplePayload",
    "WeatherForecastSource",
]
