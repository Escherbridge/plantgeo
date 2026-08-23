"""Reconcile what the `sensors` lane WROTE against what `api.weather.gov` holds, right now.

Layer L2 (pipeline): may import `foundation` and `warehouse`; may NOT import `method`, `planes`,
or `interface`. `httpx` is allowed at this layer -- this module needs the network, which is
exactly why `layer-lanes.md` section 4 puts source-reconciling validation in `pipeline`, never in
`method`.

The comparison is against the SOURCE, never this lane's own intermediate state: it lists what the
sensors lane's Parquet stream actually holds via `ObjectStore` (existence only -- see
`pipeline/parquet/AGENTS.md`, "this module writes and lists, and deliberately offers no
`get_bytes`") and separately re-fetches `api.weather.gov` for the same day, then asks whether the
two agree that something existed. It never re-derives the comparison from Postgres or from a
previous validation run's own record, both of which would only prove the code agrees with itself.

**The hard constraint this module cannot get around:** api.weather.gov keeps a rolling ~6-day
window (`NWS_OBSERVATION_RETENTION`, duplicated below from `ingest/sensors.py:96` -- see the note
on that constant for why it is duplicated rather than imported). A day older than that window is a
question the source itself can no longer answer, for any caller, ever. This module records that
day as `unverifiable`, never as a pass or a fail -- reporting a pass for a comparison that was
never actually performed is exactly the confident-and-wrong failure mode the engineering
principles forbid.

A station's absence from a specific check is not, by itself, evidence of a lane defect: NWS's own
station roster changes over time, a station can legitimately go offline, and NWS omits a
measurement key entirely rather than reporting a null for one it did not capture
(`ingest/sensors.py:350-364`). This module checks PARTITION-LEVEL existence (did a day get written
or governed-absent, and did the source confirm any reading existed for that day across a sampled
set of stations) rather than field-level content, precisely so none of that normal per-station or
per-field variation is mistaken for a defect. Field-level content comparison would require reading
the Parquet bytes back, which is `planes/sensors.py`'s concern, not this one's.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from http import HTTPStatus
from typing import TYPE_CHECKING, Final
from urllib.parse import quote, urlencode

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import httpx

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

from agri_data_service.warehouse.schemas.sensors import SENSORS_STREAM

NWS_API_BASE_URL: Final = "https://api.weather.gov"
NWS_ACCEPT_HEADER: Final = "application/geo+json"
NWS_USER_AGENT_VARIABLE: Final = "NWS_API_USER_AGENT"
DEFAULT_NWS_USER_AGENT: Final = "plantgeo-agri-data-service"

# Duplicated from `ingest/sensors.py:94-96` (`NWS_OBSERVATION_RETENTION`) rather than imported.
# Importing `ingest.sensors` would pull in the whole legacy ingest-job framework
# (`ingest.http`/`.source`/`.writer`/`.identity`/`.policy`/`.results`) for three constants that
# module does not even own conceptually -- they describe api.weather.gov, not that job runner.
# The correct fix is a shared `foundation` module holding these, landed in its own commit
# (`conductor/code_styleguides/layer-lanes.md` section 1: "shared needs move down the lattice ...
# never sideways as a drive-by"); until that lands, this is documented duplication, not an
# oversight. If NWS's retention policy ever changes, BOTH copies need updating.
NWS_OBSERVATION_RETENTION: Final = timedelta(days=6)

# Bounds the network fan-out per day checked: a handful of stations is enough to confirm a whole
# layer-day had SOME NWS activity (the roster-level check the docs call for), not an exhaustive
# per-station audit.
MAX_STATIONS_PER_DAY_CHECK: Final = 10


class ReconciliationStatus(StrEnum):
    """The only three honest answers a day-reconciliation can give."""

    PASSED = "passed"
    FAILED = "failed"
    UNVERIFIABLE = "unverifiable"


class SensorReconciliationError(RuntimeError):
    """Raised when api.weather.gov itself answers in a shape this module cannot interpret."""


@dataclass(frozen=True, slots=True)
class SensorReconciliationFinding:
    """One honest, named comparison outcome for one sensors-lane day. Never silently dropped."""

    day: date
    lane: str
    check: str
    status: ReconciliationStatus
    detail: str
    source_response_summary: str


def nws_request_headers(user_agent: str | None = None) -> dict[str, str]:
    """The headers every api.weather.gov call carries: the caller identity and the GeoJSON accept."""
    resolved = user_agent or os.environ.get(NWS_USER_AGENT_VARIABLE, "").strip() or DEFAULT_NWS_USER_AGENT
    return {"User-Agent": resolved, "Accept": NWS_ACCEPT_HEADER}


def reconcilable_window(now: datetime) -> tuple[date, date]:
    """Return `[first_day, last_day]`, the ONLY days api.weather.gov can still confirm, as of `now`."""
    return (now - NWS_OBSERVATION_RETENTION).date(), now.date()


def unverifiable_days(*, first_day: date, last_day: date, now: datetime) -> tuple[date, ...]:
    """Return every day in `[first_day, last_day]` the source's rolling retention has already closed over."""
    reconcilable_first, _ = reconcilable_window(now)
    return tuple(day for day in _daterange(first_day, last_day) if day < reconcilable_first)


def _daterange(first_day: date, last_day: date) -> tuple[date, ...]:
    if last_day < first_day:
        raise ValueError(f"window {first_day.isoformat()}..{last_day.isoformat()} runs backwards")
    span_days = (last_day - first_day).days + 1
    return tuple(first_day + timedelta(days=offset) for offset in range(span_days))


def _station_history_url(station_id: str, *, day: date) -> str:
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=1)
    query = urlencode({"start": start.isoformat(), "end": end.isoformat()})
    return f"{NWS_API_BASE_URL}/stations/{quote(station_id, safe='')}/observations?{query}"


async def _fetch_station_day_features(
    client: httpx.AsyncClient,
    station_id: str,
    *,
    day: date,
    headers: Mapping[str, str],
) -> tuple[bool | None, str]:
    """Return `(has_reading, summary)`; `has_reading` is `None` when the station is not registered.

    A 404 means api.weather.gov does not recognize this station id at all (deregistered, or
    dropped from the roster since it was captured) -- that is a roster change, never evidence
    this lane failed to capture something. A 200 with zero features means the station is still
    registered but genuinely reported nothing in the window, which is equally not a defect.
    """
    response = await client.get(_station_history_url(station_id, day=day), headers=dict(headers))
    if response.status_code == HTTPStatus.NOT_FOUND:
        return None, f"{station_id}: 404, not currently registered at api.weather.gov"
    if response.status_code != HTTPStatus.OK:
        raise SensorReconciliationError(
            f"api.weather.gov returned {response.status_code} for {station_id} on {day.isoformat()}: "
            f"{response.text[:200]!r}"
        )
    payload = response.json()
    features = payload.get("features") if isinstance(payload, dict) else None
    count = len(features) if isinstance(features, list) else 0
    return count > 0, f"{station_id}: {count} feature(s) reported for {day.isoformat()}"


async def _source_confirms_day(
    client: httpx.AsyncClient,
    station_ids: Sequence[str],
    *,
    day: date,
    headers: Mapping[str, str],
) -> tuple[bool, str]:
    sampled = station_ids[:MAX_STATIONS_PER_DAY_CHECK]
    summaries: list[str] = []
    for station_id in sampled:
        has_reading, summary = await _fetch_station_day_features(client, station_id, day=day, headers=headers)
        summaries.append(summary)
        if has_reading:
            return True, "; ".join(summaries)
    return False, "; ".join(summaries) if summaries else "no stations were supplied to check"


async def reconcile_observed_day(  # noqa: PLR0913
    client: httpx.AsyncClient,
    store: ObjectStore,
    *,
    day: date,
    station_ids: Sequence[str],
    now: datetime,
    headers: Mapping[str, str] | None = None,
) -> SensorReconciliationFinding:
    """Reconcile one day of the `observed` stream against api.weather.gov, or record it unverifiable.

    Compares PARTITION-LEVEL existence only: does the sensors lane hold a data partition or a
    governed absence for `day`, and does api.weather.gov confirm any reading existed for `day`
    across the sampled `station_ids`. Field-level (per-measurement) content is not compared here
    -- see the module docstring for why.
    """
    if not station_ids:
        raise ValueError("reconciling a day needs at least one station id to sample against the source")
    first_reconcilable, last_reconcilable = reconcilable_window(now)
    if day < first_reconcilable or day > last_reconcilable:
        return SensorReconciliationFinding(
            day=day,
            lane=SENSORS_STREAM,
            check="source_window_reconciliation",
            status=ReconciliationStatus.UNVERIFIABLE,
            detail=(
                f"{day.isoformat()} is outside api.weather.gov's ~6-day rolling retention "
                f"({first_reconcilable.isoformat()}..{last_reconcilable.isoformat()} as of "
                f"{now.isoformat()}); the source cannot answer this question for any caller "
                "any longer, so this is recorded unverifiable rather than a pass or a fail."
            ),
            source_response_summary="not queried: day falls outside the source's retention window",
        )

    resolved_headers = nws_request_headers() if headers is None else headers
    wrote_data = store.partition_exists(SENSORS_STREAM, "observed", day)
    wrote_absence = store.absence_exists(SENSORS_STREAM, "observed", day)
    source_had_data, source_summary = await _source_confirms_day(client, station_ids, day=day, headers=resolved_headers)

    if source_had_data and not (wrote_data or wrote_absence):
        return SensorReconciliationFinding(
            day=day,
            lane=SENSORS_STREAM,
            check="source_window_reconciliation",
            status=ReconciliationStatus.FAILED,
            detail=(
                f"api.weather.gov reports at least one observation for {day.isoformat()} among "
                f"{min(len(station_ids), MAX_STATIONS_PER_DAY_CHECK)} sampled stations, but the "
                "sensors lane wrote neither a data partition nor a governed absence for that day."
            ),
            source_response_summary=source_summary,
        )
    if wrote_data and not source_had_data:
        return SensorReconciliationFinding(
            day=day,
            lane=SENSORS_STREAM,
            check="source_window_reconciliation",
            status=ReconciliationStatus.FAILED,
            detail=(
                f"the sensors lane holds a data partition for {day.isoformat()}, but none of the "
                f"{min(len(station_ids), MAX_STATIONS_PER_DAY_CHECK)} sampled stations currently "
                "report a reading at api.weather.gov for that day -- a roster change and a "
                "captured-then-unconfirmable reading would both look like this; investigate rather "
                "than assume either."
            ),
            source_response_summary=source_summary,
        )
    return SensorReconciliationFinding(
        day=day,
        lane=SENSORS_STREAM,
        check="source_window_reconciliation",
        status=ReconciliationStatus.PASSED,
        detail=(
            f"{day.isoformat()}: source-confirmed presence ({source_had_data}) agrees with what "
            f"the sensors lane wrote (data={wrote_data}, governed_absence={wrote_absence})."
        ),
        source_response_summary=source_summary,
    )


async def reconcile_sensors_window(  # noqa: PLR0913
    client: httpx.AsyncClient,
    store: ObjectStore,
    *,
    first_day: date,
    last_day: date,
    station_ids: Sequence[str],
    now: datetime | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[SensorReconciliationFinding, ...]:
    """Reconcile `[first_day, last_day]`: checked days against the live source, the rest unverifiable.

    Every day in the window gets exactly one finding -- a caller never has to guess whether a day
    was silently skipped.
    """
    moment = datetime.now(UTC) if now is None else now
    return tuple(
        [
            await reconcile_observed_day(
                client, store, day=day, station_ids=station_ids, now=moment, headers=headers
            )
            for day in _daterange(first_day, last_day)
        ]
    )
