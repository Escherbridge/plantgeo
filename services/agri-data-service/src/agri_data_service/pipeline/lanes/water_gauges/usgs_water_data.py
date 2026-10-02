"""`water-gauges-daily`: USGS Water Data daily mean discharge, per tile, on the named day the source serves.

Strategy key `water_gauges.usgs_water_data` (spec S14, §7a). Imports only `ingest`, `warehouse` and
`pipeline/runner/contract.py`. Why each rule holds: `pipeline/lanes/water_gauges/AGENTS.md`.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
import structlog

from agri_data_service.ingest.usgs_water_data import (
    DAILY_VALUES_ENDPOINT,
    FALLBACK_UTC_OFFSET,
    MAX_TILE_DEGREES,
    MONITORING_LOCATIONS_ENDPOINT,
    DailyValue,
    MonitoringLocation,
    RejectedFeature,
    TileBox,
    daily_values_query,
    monitoring_locations_query,
    parse_daily_values,
    parse_monitoring_locations,
    tile_boxes,
)
from agri_data_service.pipeline.parquet.source_checkpoint import CHECKPOINT_MAX_BODY_BYTES
from agri_data_service.pipeline.runner.contract import SourceRequest, SourceResponse, Unsettled, Written
from agri_data_service.warehouse.schemas.water_gauges_daily import (
    WATER_GAUGES_DAILY_GRAIN,
    WATER_GAUGES_DAILY_SCHEMA,
    WATER_GAUGES_DAILY_SOURCE,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.pipeline.runner.contract import DayContext, ProviderClient, Settlement

logger = structlog.get_logger()

#: The parameter a daily-values unit settles, and the one a monitoring-locations unit answers.
DISCHARGE_PARAMETER: Final = "discharge_daily_mean"
LOCATIONS_PARAMETER: Final = "monitoring_location_name"

#: Days per daily-values unit: ~250 stream sites x 31 days stays far inside one 50,000-item page (P4).
MAX_DAYS_PER_REQUEST: Final = 31

#: `Written.dropped_rows` reasons: a feature the page contract refused, and an identity two copies disagree on.
REJECTED_FEATURE: Final = "rejected_feature"
IDENTITY_CONFLICT: Final = "identity_conflict"

_DAILY_UNIT_PREFIX: Final = "daily"
_LOCATIONS_UNIT_PREFIX: Final = "locations"


@dataclass(frozen=True, slots=True)
class DailyValuesAnswer:
    """One tile's parsed daily values over the unit's days, and the features it could not read."""

    tile: str
    values: tuple[DailyValue, ...]
    rejected: tuple[RejectedFeature, ...] = ()


@dataclass(frozen=True, slots=True)
class LocationsAnswer:
    """One tile's stream sites: id -> name and time zone."""

    tile: str
    locations: Mapping[str, MonitoringLocation]


@dataclass(frozen=True, slots=True)
class GaugeDay:
    """One gauge's valued daily mean on one named day, joined to its site facts and retrieval instant."""

    value: DailyValue
    location: MonitoringLocation | None
    retrieved_at: datetime

    def digest_facts(self) -> list[object]:
        """Everything the written row carries except the retrieval instant, in a fixed order (S11)."""
        location = self.location
        return [
            self.value.identity,
            self.value.value_text,
            self.value.approval_status,
            list(self.value.qualifiers),
            self.value.time_series_id,
            self.value.longitude,
            self.value.latitude,
            None if location is None else location.name,
            None if location is None else location.standard_utc_offset,
        ]


@dataclass(frozen=True, slots=True)
class DayView:
    """One named day as the answered units show it: which tiles are whole, their gauges, and what was dropped."""

    #: Tiles whose daily-values unit AND monitoring-locations unit both answered; nothing else is read.
    present_tiles: frozenset[str]
    gauge_days: tuple[GaugeDay, ...]
    #: The standard offset most of the present tiles' sites name: the stamp of a site with no known zone.
    regional_offset: str
    dropped: Mapping[str, int]


def contiguous_chunks(days: Iterable[date], max_days: int = MAX_DAYS_PER_REQUEST) -> tuple[tuple[date, ...], ...]:
    """Split the asked days into runs of consecutive days, each at most `max_days` long, oldest first."""
    chunks: list[list[date]] = []
    for day in sorted(set(days)):
        current = chunks[-1] if chunks else None
        if current is not None and day - current[-1] == timedelta(days=1) and len(current) < max_days:
            current.append(day)
        else:
            chunks.append([day])
    return tuple(tuple(chunk) for chunk in chunks)


def _tile_of(request: SourceRequest) -> str:
    """The unit's tile key: its own `bbox` query value."""
    return request.query()["bbox"]


def _support_digest(tile: TileBox) -> str:
    """Binds a checkpoint to the tile it was planned over (`SourceRequest.support_digest`)."""
    return hashlib.sha256(tile.bbox.encode("utf-8")).hexdigest()


def _answers[AnswerType](responses: Sequence[SourceResponse], kind: type[AnswerType]) -> list[AnswerType]:
    return [response.payload for response in responses if isinstance(response.payload, kind)]


def planned_tiles(planned_units: Iterable[SourceRequest]) -> frozenset[str]:
    """The tiles whose daily-values unit the runner planned for a day: a day's expected units."""
    return frozenset(_tile_of(request) for request in planned_units if request.endpoint == DAILY_VALUES_ENDPOINT)


def regional_offset(locations: Iterable[MonitoringLocation]) -> str:
    """The standard offset most sites name (ties to the lowest string), else `FALLBACK_UTC_OFFSET` (review M2)."""
    counts = Counter(offset for location in locations if (offset := location.standard_utc_offset) is not None)
    if not counts:
        return FALLBACK_UTC_OFFSET
    return min(counts, key=lambda offset: (-counts[offset], offset))


def _rejected_on(day: date, response: SourceResponse, answer: DailyValuesAnswer) -> int:
    """Rejected features naming `day`; one whose day is unreadable counts on its unit's first day, once."""
    first_day = response.request.days[0] if response.request.days else None
    return sum(1 for rejected in answer.rejected if (rejected.named_day or first_day) == day)


def day_view(day: date, responses: Sequence[SourceResponse]) -> DayView:
    """The day across its whole tiles: one valued mean per identity, sorted by the grain, and the drops counted.

    A gauge on a shared tile edge arrives twice; identical copies collapse. Two DIFFERENT values under one
    identity cannot both be written, so that identity is dropped, counted and named in a warning, never guessed.
    """
    present = frozenset({answer.tile for answer in _answers(responses, DailyValuesAnswer)}) & frozenset(
        answer.tile for answer in _answers(responses, LocationsAnswer)
    )
    locations: dict[str, MonitoringLocation] = {}
    for answer in _answers(responses, LocationsAnswer):
        if answer.tile in present:
            locations.update(answer.locations)
    dropped: Counter[str] = Counter()
    by_identity: dict[str, list[GaugeDay]] = {}
    for response in responses:
        payload = response.payload
        if not isinstance(payload, DailyValuesAnswer) or payload.tile not in present:
            continue
        dropped[REJECTED_FEATURE] += _rejected_on(day, response, payload)
        for value in payload.values:
            if value.flow_cfs is None or value.named_day != day:
                continue
            gauge_day = GaugeDay(
                value=value,
                location=locations.get(value.monitoring_location_id),
                retrieved_at=response.retrieved_at,
            )
            by_identity.setdefault(value.identity, []).append(gauge_day)
    kept: list[GaugeDay] = []
    for identity, copies in by_identity.items():
        if len({json.dumps(copy.digest_facts()) for copy in copies}) > 1:
            dropped[IDENTITY_CONFLICT] += 1
            logger.warning(
                "water_gauges_daily_identity_conflict",
                identity=identity,
                time_series_ids=sorted({str(copy.value.time_series_id) for copy in copies}),
            )
            continue
        kept.append(max(copies, key=lambda copy: copy.retrieved_at))
    return DayView(
        present_tiles=present,
        gauge_days=tuple(
            sorted(kept, key=lambda item: (item.value.monitoring_location_id, item.value.time, item.value.statistic_id))
        ),
        regional_offset=regional_offset(locations.values()),
        dropped={reason: count for reason, count in sorted(dropped.items()) if count},
    )


def day_source_digest(gauge_days: Sequence[GaugeDay]) -> str:
    """SHA-256 of the day's row facts minus retrieval instants: a value or approval change moves it (S11)."""
    canonical = "\n".join(json.dumps(item.digest_facts(), separators=(",", ":")) for item in gauge_days)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _observed_at(day: date, location: MonitoringLocation | None, fallback_offset: str) -> datetime:
    """The named day at the site's standard-time midnight (legacy DV stamp); the day is never read back from it."""
    offset = (None if location is None else location.standard_utc_offset) or fallback_offset
    return datetime.fromisoformat(f"{day.isoformat()}T00:00:00{offset}").astimezone(UTC)


def _row(day: date, item: GaugeDay, fallback_offset: str) -> dict[str, object]:
    value, location = item.value, item.location
    return {
        "site_number": value.site_number,
        "observed_at": _observed_at(day, location, fallback_offset),
        "observed_day": day,
        "site_name": None if location is None else location.name,
        "latitude": value.latitude,
        "longitude": value.longitude,
        "flow_cfs": value.flow_cfs,
        "percentile": None,
        "condition": None,
        "trend": None,
        "source": WATER_GAUGES_DAILY_SOURCE,
        "geometry_linked": False,
        "data_available_at": None,
        "ingested_at": item.retrieved_at.astimezone(UTC),
        "monitoring_location_id": value.monitoring_location_id,
        "source_time": value.time,
        "statistic_id": value.statistic_id,
        "approval_status": value.approval_status,
        "qualifier": ",".join(value.qualifiers) or None,
        "time_series_id": value.time_series_id,
    }


@dataclass(frozen=True, slots=True)
class UsgsWaterDataDailyStrategy:
    """Per-tile daily-values units (plus one names unit per tile) over the region's camera envelope."""

    max_tile_degrees: float = MAX_TILE_DEGREES
    max_days_per_request: int = MAX_DAYS_PER_REQUEST

    def source_unit_ids(self, lane: LaneConfig, region: Region) -> frozenset[str]:
        """The tile bboxes whose complete answers settle each historical day."""
        del lane
        return frozenset(tile.bbox for tile in tile_boxes(region.default_camera_envelope, self.max_tile_degrees))

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:  # noqa: ARG002 - the Protocol's lane
        """Per tile: one monitoring-locations unit over every asked day, and one daily-values unit per chunk."""
        asked = tuple(sorted(set(days)))
        chunks = contiguous_chunks(asked, self.max_days_per_request)
        requests: list[SourceRequest] = []
        for tile in tile_boxes(region.default_camera_envelope, self.max_tile_degrees):
            requests.append(
                SourceRequest(
                    unit=f"{_LOCATIONS_UNIT_PREFIX}:{tile.bbox}",
                    endpoint=MONITORING_LOCATIONS_ENDPOINT,
                    days=asked,
                    parameters=monitoring_locations_query(tile),
                    requested_parameters=frozenset({LOCATIONS_PARAMETER}),
                    support_digest=_support_digest(tile),
                )
            )
            requests.extend(
                SourceRequest(
                    unit=f"{_DAILY_UNIT_PREFIX}:{tile.bbox}:{chunk[0].isoformat()}/{chunk[-1].isoformat()}",
                    endpoint=DAILY_VALUES_ENDPOINT,
                    days=chunk,
                    parameters=daily_values_query(tile, chunk[0], chunk[-1]),
                    requested_parameters=frozenset({DISCHARGE_PARAMETER}),
                    support_digest=_support_digest(tile),
                )
                for chunk in chunks
            )
        return requests

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """ONE send per unit; a page this contract cannot read raises (`strategy_error`), a bad feature is dropped.

        An answer larger than a checkpoint may hold is not offered for one (a dense tile over 31 days).
        """
        response = await client.get(request.endpoint, request.query())
        payload: DailyValuesAnswer | LocationsAnswer
        if request.endpoint == DAILY_VALUES_ENDPOINT:
            page = parse_daily_values(response.body)
            if page.rejected:
                logger.warning(
                    "water_gauges_daily_features_rejected",
                    unit=request.unit,
                    rejected=len(page.rejected),
                    first_reason=page.rejected[0].reason,
                )
            payload = DailyValuesAnswer(tile=_tile_of(request), values=page.values, rejected=page.rejected)
        elif request.endpoint == MONITORING_LOCATIONS_ENDPOINT:
            payload = LocationsAnswer(tile=_tile_of(request), locations=parse_monitoring_locations(response.body))
        else:
            raise ValueError(f"water-gauges-daily plans no {request.endpoint!r} unit")
        return SourceResponse(
            request=request,
            body=response.body,
            retrieved_at=response.retrieved_at,
            complete_parameters=(
                request.requested_parameters if len(response.body) <= CHECKPOINT_MAX_BODY_BYTES else frozenset()
            ),
            payload=payload,
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:
        """Written from the whole tiles (both units answered) out of the tiles planned; a short day is rechecked."""
        view = day_view(day, responses)
        if not view.present_tiles:
            return Unsettled("upstream_unavailable", "no tile answered both its daily values and its gauge names")
        if not view.gauge_days:
            return Unsettled("unsettled", "no gauge in any answered tile served a daily mean for this day yet")
        expected = planned_tiles(context.planned_units) | view.present_tiles
        return Written(
            expected_units=len(expected),
            present_units=len(view.present_tiles),
            source_digest=day_source_digest(view.gauge_days),
            dropped_rows=view.dropped,
            expected_unit_ids=expected,
            present_unit_ids=view.present_tiles,
        )

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """One row per valued gauge-day of the whole tiles, in the grain's order."""
        view = day_view(day, responses)
        records = [_row(day, item, view.regional_offset) for item in view.gauge_days]
        table = pa.Table.from_pylist(records, schema=WATER_GAUGES_DAILY_SCHEMA.arrow_schema)
        return table.sort_by([(column, "ascending") for column in WATER_GAUGES_DAILY_GRAIN])


STRATEGY: Final = UsgsWaterDataDailyStrategy()

__all__ = [
    "DISCHARGE_PARAMETER",
    "IDENTITY_CONFLICT",
    "LOCATIONS_PARAMETER",
    "MAX_DAYS_PER_REQUEST",
    "REJECTED_FEATURE",
    "STRATEGY",
    "DailyValuesAnswer",
    "DayView",
    "GaugeDay",
    "LocationsAnswer",
    "UsgsWaterDataDailyStrategy",
    "contiguous_chunks",
    "day_source_digest",
    "day_view",
    "planned_tiles",
    "regional_offset",
]
