"""Bucket a poll's observations into registered-schema Arrow tables, one per named calendar day.

THE DAY KEY IS AN ISO-STRING PREFIX, NOT A TIMESTAMP CAST -- the same trap `water_gauges.py` names
and the SAME reason `drizzle/0018_fire_discovery_observation_day.sql:46-48` gives for
`geo.feature_observation_day` itself: "NEVER `(...)::timestamptz::date` and never `(... AT TIME ZONE
'UTC')::date`: an instant-based conversion moves 6,279 of the 16,743 production water-gauge rows onto
the day AFTER the one they name." `geo.feature_observation_day` takes `substring(properties->>
'observedAt', 1, 10)` of the FIRST populated key in its COALESCE chain, and for this layer that key is
always `observedAt` -- every write validates it before it reaches `geo.features`
(`ingest/open_meteo.py::_bounded_value`), so the chain never falls through. `_observation_day` below
reproduces that exact substring, not a `.date()` call on the parsed instant.

IT HAPPENS TO AGREE WITH A UTC DATE HERE, and that is provable rather than assumed: `observedAt` is
always rendered by `format_javascript_timestamp`, which unconditionally converts to UTC before
formatting (`ingest/identity.py:126-136`, `utc_moment = moment.astimezone(UTC)`). Unlike the NWIS
`updatedAt` field water-gauges keys on -- which is NOT always UTC-rendered by its publisher -- there is
no other offset this producer could have written, so the substring and a UTC-truncated instant can
never disagree FOR THIS PRODUCER. The substring is still what is implemented, because it is what the
warehouse's own day function evaluates, and matching the rule beats matching a proof about the rule.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.identity import MissingNativeKeyError, build_weather_observation_identity
from agri_data_service.ingest.open_meteo import OPEN_METEO_PROPERTY_SOURCE
from agri_data_service.warehouse.schemas.weather_observations import WEATHER_OBSERVATIONS_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.pipeline.direct.weather_observations.source import WeatherPointObservation

#: Refreshed from the incoming reading on a repeat grain match. Mirrors
#: `water_gauges.py::WATER_GAUGES_SOURCE_COLUMNS` -- the columns a source truthfully re-states.
#: `feature_id` is deliberately NOT in this tuple -- see `_feature_id`'s docstring below for why.
WEATHER_OBSERVATIONS_SOURCE_COLUMNS: Final[tuple[str, ...]] = (
    "latitude",
    "longitude",
    "observed_at",
    "observed_day",
    "external_id",
    "temperature_c",
    "relative_humidity_pct",
    "wind_speed_ms",
    "wind_direction_deg",
    "precipitation_mm",
    "source",
)
#: NOT refreshed on a repeat grain match -- `ingested_at` is when THIS writer first captured the row,
#: and overwriting it on a later poll of the same instant would erase that fact for no reason: the
#: reading itself did not change. Mirrors `water_gauges.py::WATER_GAUGES_PROVENANCE_COLUMNS`.
WEATHER_OBSERVATIONS_PROVENANCE_COLUMNS: Final[tuple[str, ...]] = ("ingested_at",)

_OBSERVED_DAY_PREFIX_LENGTH: Final = 10


class DirectWeatherObservationsRowError(ValueError):
    """Raised when one polled observation cannot become a registered-schema row."""


def _observation_day(observed_at_text: str) -> date:
    """Return `geo.feature_observation_day`'s day for one `observedAt` string: its first ten characters."""
    if len(observed_at_text) < _OBSERVED_DAY_PREFIX_LENGTH:
        raise DirectWeatherObservationsRowError(f"observedAt {observed_at_text!r} is too short to name a day")
    named = observed_at_text[:_OBSERVED_DAY_PREFIX_LENGTH]
    try:
        parsed = date.fromisoformat(named)
    except ValueError as error:
        raise DirectWeatherObservationsRowError(f"observedAt day is not YYYY-MM-DD: {named!r}") from error
    if parsed.isoformat() != named:
        raise DirectWeatherObservationsRowError(f"observedAt day is not canonical YYYY-MM-DD: {named!r}")
    return parsed


def _feature_id(external_id: str) -> str:
    """Synthesize a `direct:`-namespaced feature identity: no `geo.features` row backs a direct write.

    `feature_id` is documented (`warehouse/schemas/weather_observations.py`) as `features.id::text`,
    a real Postgres UUID, for every Postgres-sourced row. A direct row has no such row to cite. Rather
    than leave the (non-null, per `TierDerivation.base_non_null_columns`) column empty or fabricate a
    UUID that would misrepresent a Postgres identity that never existed, it carries a `direct:` token
    over `external_id` -- itself already the full `{lat4dp}:{lon4dp}:{observedAtISO}` identity, so this
    is deterministic and needs no extra hash. See `pipeline/direct/AGENTS.md`, "Direct lineage
    namespace": a reader must check this prefix before treating `feature_id` as a `geo.features.id`.

    THIS IS ALSO WHY `feature_id` IS EXCLUDED FROM `WEATHER_OBSERVATIONS_SOURCE_COLUMNS`. A day the
    drain republished from Postgres before this writer next polled it carries a REAL `geo.features`
    UUID in this column, and `adapter.py::merge_weather_observations_day` has no way to tell that
    apart from a synthetic `direct:` token once the row is on disk. If `feature_id` were refreshed on
    every repeat grain match like the other source columns, the next poll of that same point-instant
    would silently overwrite the real UUID with this synthetic one. Leaving it out of the refreshed
    set means `feature_id` is set only once -- when a grain is first appended, the `existing_index is
    None` branch, which always writes the whole incoming row -- so whichever value (real or synthetic)
    got there first is preserved for the row's whole life. A synthetic value is never promoted over a
    real one, nor a real one ever demoted, so this is safe in both directions.
    """
    return f"direct:{external_id}"


def direct_weather_observation_tables(
    observations: Sequence[WeatherPointObservation],
    *,
    ingested_at: datetime,
) -> dict[date, pa.Table]:
    """Build one registered-schema Arrow table per named day out of one poll's successful readings."""
    if ingested_at.tzinfo is None or ingested_at.utcoffset() is None:
        raise DirectWeatherObservationsRowError("ingested_at must include a timezone")
    rows_by_day: dict[date, list[dict[str, object]]] = defaultdict(list)
    for point in observations:
        try:
            identity = build_weather_observation_identity(point.latitude, point.longitude, point.observation)
        except (MissingNativeKeyError, ValueError) as error:
            raise DirectWeatherObservationsRowError(
                f"point ({point.latitude}, {point.longitude}) could not be identified: {error}"
            ) from error
        if identity.observed_at is None:
            raise DirectWeatherObservationsRowError(f"point ({point.latitude}, {point.longitude}) has no observed_at")
        observed_at_text = point.observation.get("observedAt")
        if not isinstance(observed_at_text, str):
            raise DirectWeatherObservationsRowError(f"point ({point.latitude}, {point.longitude}) has no observedAt")
        day = _observation_day(observed_at_text)
        external_id = identity.producer_local_id
        rows_by_day[day].append(
            {
                "latitude": point.latitude,
                "longitude": point.longitude,
                "observed_at": identity.observed_at,
                "observed_day": day,
                "external_id": external_id,
                "temperature_c": point.observation.get("temperature"),
                "relative_humidity_pct": point.observation.get("humidity"),
                "wind_speed_ms": point.observation.get("windSpeed"),
                "wind_direction_deg": point.observation.get("windDirection"),
                "precipitation_mm": point.observation.get("precipitation"),
                "source": OPEN_METEO_PROPERTY_SOURCE,
                "feature_id": _feature_id(external_id),
                "ingested_at": ingested_at,
            }
        )
    return {
        day: pa.Table.from_pylist(day_rows, schema=WEATHER_OBSERVATIONS_SCHEMA.arrow_schema)
        for day, day_rows in sorted(rows_by_day.items())
    }


__all__ = [
    "WEATHER_OBSERVATIONS_PROVENANCE_COLUMNS",
    "WEATHER_OBSERVATIONS_SOURCE_COLUMNS",
    "DirectWeatherObservationsRowError",
    "direct_weather_observation_tables",
]
