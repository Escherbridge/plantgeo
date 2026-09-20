"""The `weather-forecast` layer's source contract: coverage and one bounded run pull.

See `AGENTS.md` in this directory for what the layer owns, what a source owns, and the exact
normalisation decisions the Open-Meteo implementation makes at this boundary
(`federation.md` §2, "units, datums and calendars normalize at the source boundary").

`WeatherForecastRunPayload`/`WeatherForecastLocationPayload`/`WeatherForecastSamplePayload`
enumerate EXACTLY the members a future `pipeline/direct/weather_forecast/rows.py` (S3) reads off a
run -- nothing wider, so `mypy` sees the same shape the code does, and nothing narrower, so a
second provider's source can satisfy them without inheriting from anything. Modelled on
`pipeline/direct/drought/source_protocol.py`'s `DroughtSource`/`DroughtReleasePayload` pair; the
one deliberate difference is that this protocol carries NO retry knobs
(`retry_attempts`/`retry_base_seconds`/`retry_max_seconds`) -- `open_meteo.py::fetch_forecast_run`
raises `OpenMeteoRateLimitError` on a 429 exactly once, per `.omc/ultrapilot-20260918/
W8-E-PLAN.md` §2 row S2's acceptance ("not a retry loop"), and there is nothing here to retry
around: the caller decides whether and when to try again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim


@runtime_checkable
class WeatherForecastSamplePayload(Protocol):
    """One variable's reading at one valid instant, at one requested location.

    `variable`/`unit`/`statistic` are carried on the row itself rather than looked up from a table
    by the lane, matching `warehouse/schemas/weather_forecast.py`'s "a reader never needs this
    module's code to interpret a published file". `cell_id` is deliberately ABSENT here: flooring a
    raw sample point onto the region's lattice is a lane concern (`rows.py`, S3), never the
    source's -- this protocol answers in the source's own coordinate space.
    """

    @property
    def valid_time(self) -> datetime:
        """The instant this row's value verifies at, UTC. See `AGENTS.md` "The precipitation window"."""
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
        """One of `warehouse.schemas.weather_forecast.VARIABLES`' keys, e.g. `"wind_u_10m"`."""
        ...

    @property
    def value(self) -> float | None:
        """The reading, or `None` for a governed absence -- `missing_reason` is populated instead."""
        ...

    @property
    def unit(self) -> str:
        """The value's unit, e.g. `"degC"`."""
        ...

    @property
    def statistic(self) -> str:
        """How the value was computed, e.g. `"instantaneous"` or `"sum_over_following_hour"`."""
        ...

    @property
    def support(self) -> str:
        """One of `warehouse.schemas.weather_forecast.SUPPORT_KINDS`."""
        ...

    @property
    def missing_reason(self) -> str | None:
        """One of `warehouse.schemas.weather_forecast.MISSING_REASONS`, or `None` when `value` is set."""
        ...


@runtime_checkable
class WeatherForecastLocationPayload(Protocol):
    """One requested point's full sample series for a run, in the source's own coordinate space."""

    @property
    def latitude(self) -> float:
        """The location the source actually answered for -- may differ from the request by a nearest-grid snap."""
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
    """One admitted NWP model run: its identity, when it was fetched, and every location it covers."""

    @property
    def run_id(self) -> str:
        """`<provider>:<model>:<init_time ISO>`; pins one published series (ML spec.md "Readers pin one run")."""
        ...

    @property
    def model_init_time(self) -> datetime:
        """The model's own initialization instant, UTC -- the value the CALLER requested, not an echo."""
        ...

    @property
    def provider_issue_time(self) -> datetime | None:
        """The provider's release instant, when supplied; `None` for Open-Meteo (`AGENTS.md`, "issue time")."""
        ...

    @property
    def fetched_at(self) -> datetime:
        """When this run was read, UTC."""
        ...

    @property
    def locations(self) -> Sequence[WeatherForecastLocationPayload]:
        """Every requested point this run answered for, in request order."""
        ...


@runtime_checkable
class WeatherForecastSource(Protocol):
    """One provider's weather-forecast source: what it covers and how one bounded run is read.

    Implemented by `open_meteo.py` for the pilot. A second provider implements this against its own
    NWP API and binds it in the region manifest (S3); nothing in a future `rows.py`, `adapter.py` or
    `forward.py` learns the source's name (`federation.md` §2).
    """

    #: The manifest's `source_slug` for this implementation, e.g. `"open-meteo"`.
    source_slug: str
    #: Where this source can fill the weather-forecast layer at all (`federation.md` §2).
    coverage: SourceCoverageClaim

    async def fetch_run(
        self,
        *,
        model_init_time: datetime,
        coordinates: Sequence[tuple[float, float]],
        variables: Sequence[str],
        forecast_days: int,
    ) -> WeatherForecastRunPayload:
        """Fetch exactly one bounded run pull: one model, one init time, up to 200 points, no retry loop.

        Refuses (raises) rather than truncates when `coordinates`, `variables` or `forecast_days`
        exceed the source's declared budget (`.omc/ultrapilot-20260918/W8-E-PLAN.md` §2 row S2,
        "Bounded fetch budget (S2)"). A 429 raises `OpenMeteoRateLimitError` once; the caller (a
        future S3 `forward.py`) decides whether to persist a durable cooldown, never this method.
        """
        ...


__all__ = [
    "WeatherForecastLocationPayload",
    "WeatherForecastRunPayload",
    "WeatherForecastSamplePayload",
    "WeatherForecastSource",
]
