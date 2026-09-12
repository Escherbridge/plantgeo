"""Bucket one poll's readings into registered-schema Arrow tables, one winning report per station-day.

THE DAY KEY IS AN ISO-STRING PREFIX, NOT A TIMESTAMP CAST -- the project-wide rule
(`pipeline/direct/AGENTS.md`, water-gauges and weather-observations sections;
`drizzle/0018_fire_discovery_observation_day.sql:46-48`): `geo.feature_observation_day` takes
`substring(properties ->> 'observedAt', 1, 10)`, never an instant cast, because an instant cast moved
6,279 of 16,743 water-gauge rows onto the wrong day. `_observation_day` below reproduces that exact
substring against the RAW upstream `timestamp` string this producer stores verbatim into
`properties['observedAt']` (`ingest/sensors.py::build_sensor_reading_write`, "Stored unconverted,
because OBSERVATION_DAY reads the ISO string's own date part: the day the PUBLISHER named, not a
re-zoned one").

UNLIKE `weather_observations/rows.py`'s TWIN CLAIM, THIS ONE IS NOT PROVABLY A UTC DATE. Open-Meteo's
`observedAt` is always rendered by THIS APPLICATION's own `format_javascript_timestamp`
(`ingest/identity.py:126-136`), which unconditionally converts to UTC before formatting -- so the
substring and a UTC-truncated instant can never disagree for that producer. NWS's `timestamp` field
is the OPPOSITE case: it is the UPSTREAM'S OWN string, carried through unmodified
(`ingest/sensors.py::parse_observation`: `"timestamp": timestamp` is `_optional_text(properties.get
("timestamp"))`, no re-render), so nothing in this codebase controls or has verified its offset. This
is the SAME shape as the NWIS `updatedAt` trap `water_gauges.py` names, not the Open-Meteo shape --
which is exactly why the substring rule, not a parsed-then-truncated instant, is implemented here:
whatever offset NWS actually used, the substring of the stored string is what
`geo.feature_observation_day` evaluates, and matching that evaluation is what matters, not a fact
about the upstream's convention.

THE WINNING-REPORT REDUCTION IS A `DISTINCT ON`, RESTATED IN PYTHON. It was transcribed from
`sql/pipeline/sensors_day_export.sql`, deleted 2026-09-07 with the Postgres-reading lane it backed;
the surviving SQL statement of the identical shape is
`sql/pipeline/direct/sensors/postgres_day_counts.sql`, which `parity.py` loads. That query picks
one row per (sensor_id, day): the latest report that day, tie-broken by
`observedAt DESC AS TEXT` then `feature.id DESC` for a total order -- "observedAt DESC as TEXT
(ISO-8601 with a UTC offset sorts chronologically when compared lexically)". `_winning_write` takes
the same `max()` over the identical (observedAt-text, external-id) tuple, which is the literal
Python equivalent of that `ORDER BY ... DESC, ... DESC` reduction: for one station-day, at most one
report survives, and every one of its (at most sixteen) measurements becomes one row -- the same
`CROSS JOIN LATERAL jsonb_each(...) WHERE measurement.key <> 'textDescription'` fan-out the SQL runs.

`feature_id` CARRIES A `direct:` TOKEN, NOT A FABRICATED UUID -- the same discipline
`weather_observations/rows.py::_feature_id` documents. The schema declares `feature_id` as
`features.id::text` for a Postgres-sourced row and forbids nulling it at the base rung
(`warehouse/schemas/sensors.py::SENSORS_TIER_DERIVATION.base_non_null_columns`); a direct write has
no such row, so it carries `f"direct:{external_id}"`, where `external_id` is already the reading's
full `{stationIdentifier}:{timestamp}` identity (`ingest/sensors.py::build_sensor_reading_identity`)
-- deterministic across retries and never colliding with a real Postgres UUID, which never contains
a colon.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.schemas.sensors import SENSORS_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.ingest.records import FeatureWrite

#: Mirrors the day export's exclusion (`sql/pipeline/direct/sensors/postgres_day_counts.sql`, the
#: surviving transcription): free text, not one of the sixteen
#: measurement fields, and carries no numeric `value` to cast.
_EXCLUDED_MEASUREMENT_KEY: Final = "textDescription"
_OBSERVED_DAY_PREFIX_LENGTH: Final = 10


class DirectSensorsRowError(ValueError):
    """Raised when one polled reading cannot become a registered-schema row."""


def _observation_day(observed_at_text: str) -> date:
    """Return `geo.feature_observation_day`'s day for one raw `observedAt` string: its first ten characters."""
    if len(observed_at_text) < _OBSERVED_DAY_PREFIX_LENGTH:
        raise DirectSensorsRowError(f"observedAt {observed_at_text!r} is too short to name a day")
    named = observed_at_text[:_OBSERVED_DAY_PREFIX_LENGTH]
    try:
        parsed = date.fromisoformat(named)
    except ValueError as error:
        raise DirectSensorsRowError(f"observedAt day is not YYYY-MM-DD: {named!r}") from error
    if parsed.isoformat() != named:
        raise DirectSensorsRowError(f"observedAt day is not canonical YYYY-MM-DD: {named!r}")
    return parsed


def _feature_id(external_id: str) -> str:
    """Synthesize a `direct:`-namespaced feature identity: no `geo.features` row backs a direct write.

    See this module's docstring, "`feature_id` carries a `direct:` token" -- the same rule
    `weather_observations/rows.py::_feature_id` documents for its own lane.
    """
    return f"direct:{external_id}"


def _station_coordinates(properties: Mapping[str, object]) -> tuple[float | None, float | None]:
    """Return a reading's (longitude, latitude) exactly as stored, or (None, None) for a shape mismatch.

    Provenance-only floats at full precision -- the station's own reported position
    (`ingest/sensors.py::SensorStation`), never a rounded or re-derived coordinate. Matches the
    schema's own note that this is "the station's own location", not a cell centroid.
    """
    geometry = properties.get("geometry")
    if not isinstance(geometry, dict):
        return None, None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:  # noqa: PLR2004 - a GeoJSON Point pair
        return None, None
    longitude, latitude = coordinates[0], coordinates[1]
    if isinstance(longitude, bool) or not isinstance(longitude, int | float):
        return None, None
    if isinstance(latitude, bool) or not isinstance(latitude, int | float):
        return None, None
    return float(longitude), float(latitude)


def _observed_at_text(write: FeatureWrite) -> str:
    """Return the raw `observedAt` property text one write's day and tie-break are both keyed on."""
    observed_at_text = write.properties.get("observedAt")
    if not isinstance(observed_at_text, str) or not observed_at_text:
        raise DirectSensorsRowError(f"{write.external_id} has no observedAt property")
    return observed_at_text


def _winning_write(candidates: Sequence[FeatureWrite]) -> FeatureWrite:
    """Pick one station-day's winning report: `max` over (observedAt text, external id), both DESC.

    The literal Python form of `sql/pipeline/direct/sensors/postgres_day_counts.sql`'s
    `ORDER BY ... observedAt DESC, feature.id DESC` (via `DISTINCT ON`) -- `max()` over that
    same two-part tuple picks exactly the row that ordering would rank first, so the two reductions
    can never disagree on which report is "the winner" for a given station-day.
    """
    return max(candidates, key=lambda write: (_observed_at_text(write), write.external_id))


def _measurement_rows(write: FeatureWrite, *, day: date) -> list[dict[str, object]]:
    """Fan out one winning report's measurements into base rows, at the registered schema's own grain."""
    readings = write.properties.get("readings")
    if not isinstance(readings, dict):
        raise DirectSensorsRowError(f"{write.external_id} has no readings mapping")
    if write.identity.observed_at is None:
        raise DirectSensorsRowError(f"{write.external_id} has no observed_at")
    longitude, latitude = _station_coordinates(write.properties)
    feature_id = _feature_id(write.external_id)
    rows: list[dict[str, object]] = []
    for measurement_name, measurement in readings.items():
        if measurement_name == _EXCLUDED_MEASUREMENT_KEY or not isinstance(measurement, dict):
            continue
        value = measurement.get("value")
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        rows.append(
            {
                "sensor_id": write.identity.entity_local_id,
                "station_name": write.properties.get("station_name"),
                "network": write.properties.get("network"),
                "observed_day": day,
                "observed_at": write.identity.observed_at,
                "measurement_name": measurement_name,
                "value": float(value),
                "unit_code": measurement.get("unitCode"),
                "quality_control": measurement.get("qualityControl"),
                "feature_id": feature_id,
                # Left unmeasured for this layer, matching `warehouse/schemas/sensors.py`'s own
                # note (conductor/RUNBOOK.md:907, an existence probe timed out on it) -- carried
                # through as None rather than dropped or fabricated.
                "data_available_at": None,
                "station_longitude": longitude,
                "station_latitude": latitude,
            }
        )
    return rows


def direct_sensor_tables(writes: Sequence[FeatureWrite]) -> dict[date, pa.Table]:
    """Reduce one poll's writes to one winning report per (station, day), then build one table per day.

    A day this poll did not produce any surviving measurement for (every field null on the winning
    report, or a station whose only readings that day were all rejected upstream) contributes no
    table -- matching the SQL export's own `CROSS JOIN LATERAL jsonb_each(...)` yielding zero rows
    for an empty `readings` object, never an empty-but-present Parquet table (`write_partition`
    refuses one; see `adapter.py`).
    """
    by_station_day: dict[tuple[str, date], list[FeatureWrite]] = defaultdict(list)
    for write in writes:
        station_id = write.identity.entity_local_id
        if not station_id:
            raise DirectSensorsRowError(f"{write.external_id} has no station identity")
        day = _observation_day(_observed_at_text(write))
        by_station_day[(station_id, day)].append(write)

    rows_by_day: dict[date, list[dict[str, object]]] = defaultdict(list)
    for (_station_id, day), candidates in by_station_day.items():
        winner = _winning_write(candidates)
        rows_by_day[day].extend(_measurement_rows(winner, day=day))

    return {
        day: pa.Table.from_pylist(day_rows, schema=SENSORS_SCHEMA.arrow_schema)
        for day, day_rows in sorted(rows_by_day.items())
        if day_rows
    }


__all__ = ["DirectSensorsRowError", "direct_sensor_tables"]
