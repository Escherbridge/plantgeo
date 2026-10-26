"""`vegetation-sentinel2-ndvi-direct-forward`: one Sentinel-2 L2A NDVI cell-day plane per UTC day.

Strategy key `vegetation.sentinel2_ndvi` (spec S14; track `config_driven_ingestion_20260926`, spec §7
vegetation row). Behaviour is derived from `pipeline/direct/vegetation/{source,adapter,forward,rows}.py`
(never imported); the partial-day policy, the probe semantics, the Wave O fetch path and the cost
model are in `pipeline/lanes/vegetation/AGENTS.md`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.identity import format_javascript_timestamp
from agri_data_service.ingest.vegetation import (
    COG_BOUNDS,
    MAX_SCENE_CLOUD_COVER_PERCENT,
    NDVI_GRID_NAME,
    SEARCH_PAGE_SIZE,
    SENTINEL2_L2A_COLLECTION,
    GridSampleWindow,
    SceneMetadataError,
    collect_ndvi_grid_records,
    parse_scene_timestamp,
)
from agri_data_service.pipeline.lanes._shared.digest import COMPACT_SEPARATORS, canonical_json_bytes, sha256_hex
from agri_data_service.pipeline.lanes.vegetation.support import load_pinned_support, require_lattice_agreement
from agri_data_service.pipeline.runner.contract import (
    Absent,
    ProviderEdge,
    SourceRequest,
    SourceResponse,
    Unsettled,
    Written,
)
from agri_data_service.warehouse.field_products.vegetation import VEGETATION_PRODUCT

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.pipeline.lanes.vegetation.support import VegetationSupport, VegetationSupportCell
    from agri_data_service.pipeline.runner.contract import DayContext, ProbeWindow, ProviderClient, Settlement

#: `lanes/_providers/earth-search.toml` endpoint the probe sends through and every unit is labelled with.
ENDPOINT: Final = "search"
STREAM: Final = VEGETATION_PRODUCT.stream
#: Same slack as the legacy fetch (`direct/vegetation/source.py::_MAX_CELLS_SLACK`): never `truncated` on a true box.
MAX_CELLS_SLACK: Final = 32
#: A record with NO cloud cover ranks worst, never clearest (legacy `_UNREPORTED_CLOUD_COVER_PERCENT`).
UNREPORTED_CLOUD_COVER_PERCENT: Final = 100.0
#: Sentinel-2 L2A on AWS Open Data is openly exposable (legacy `rows.py::VEGETATION_ALLOWED_CLIENT_EXPOSURE`).
ALLOWED_CLIENT_EXPOSURE: Final = True
#: The probe asks only for the newest matching scene in its window.
PROBE_SORT: Final = "-properties.datetime"


class VegetationDayError(RuntimeError):
    """A day's sampled answer cannot be governed: truncated, off-lattice, or a malformed record field."""


@dataclass(frozen=True, slots=True)
class VegetationCellValue:
    """One governed cell-day value: the clearest scene's reading and how many candidates it beat."""

    cell: VegetationSupportCell
    metric_value: float
    cloud_cover_percent: float
    release_count: int
    record_sha256: str


@dataclass(frozen=True, slots=True)
class VegetationDay:
    """One day's fetch, resolved into governed values plus its order-free content digest."""

    day: date
    values: tuple[VegetationCellValue, ...]
    cells_requested: int
    response_sha256: str


def record_sha256(record: Mapping[str, object]) -> str:
    """The per-record `observation_checksum`, byte-identical to legacy `direct/vegetation/source.py::_record_sha256`."""
    payload = {
        "cellKey": record.get("cellKey"),
        "gridName": record.get("gridName"),
        "ndvi": record.get("ndvi"),
        "observedAt": record.get("observedAt"),
        "sceneId": record.get("sceneId"),
        "cloudCover": record.get("cloudCover"),
        "sampleCount": record.get("sampleCount"),
    }
    return sha256_hex(canonical_json_bytes(payload, sort_keys=True, separators=COMPACT_SEPARATORS, allow_nan=True))


def day_response_sha256(records: Sequence[Mapping[str, object]]) -> str:
    """The day's S11 source digest under a TOTAL order (legacy `_day_response_sha256`): arrival order never counts."""
    ordered = sorted(
        (str(record.get("cellKey", "")), str(record.get("sceneId", "")), record_sha256(record)) for record in records
    )
    digests = [digest for _, _, digest in ordered]
    return sha256_hex(canonical_json_bytes(digests, sort_keys=False, separators=COMPACT_SEPARATORS, allow_nan=True))


def search_parameters(
    bbox: str, start: datetime, end: datetime, *, limit: int, sortby: str
) -> tuple[tuple[str, str], ...]:
    """The Earth Search item-search query `ingest/vegetation.py::scene_search_url` sends, with this limit and sort."""
    return (
        ("collections", SENTINEL2_L2A_COLLECTION),
        ("bbox", bbox),
        ("datetime", f"{format_javascript_timestamp(start)}/{format_javascript_timestamp(end)}"),
        ("limit", str(limit)),
        ("query", json.dumps({"eo:cloud_cover": {"lt": MAX_SCENE_CLOUD_COVER_PERCENT}}, separators=COMPACT_SEPARATORS)),
        ("sortby", sortby),
    )


def _day_window(day: date) -> tuple[datetime, datetime]:
    """The half-open UTC day the legacy fetch samples: `[day 00:00, day+1 00:00)`."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Sentinel2NdviStrategy:
    """One unit per day (the whole STAC+COG walk is the retry grain); a one-request STAC probe gives the edge."""

    #: The support source; production reads the pinned snapshot, tests hand one in.
    support: Callable[[], VegetationSupport] = field(default=load_pinned_support)

    async def probe_edge(self, client: ProviderClient, window: ProbeWindow) -> ProviderEdge:
        """The newest matching scene's UTC day is the edge; each window day up to it is answerable (AGENTS.md)."""
        start, _ = _day_window(window.first)
        _, end = _day_window(window.last)
        parameters = search_parameters(self.support().bbox, start, end, limit=1, sortby=PROBE_SORT)
        response = await client.get(ENDPOINT, dict(parameters), probe=True)
        try:
            newest = _newest_scene_day(response.body)
        except (ValueError, SceneMetadataError) as error:
            return ProviderEdge(status="invalid", window=window, detail=f"unreadable STAC probe answer: {error}")
        if newest is None:
            return ProviderEdge(status="ok", window=window, detail="no matching scene in the probe window")
        last = min(newest, window.last)
        valued = frozenset(window.first + timedelta(days=offset) for offset in range((last - window.first).days + 1))
        return ProviderEdge(status="ok", window=window, valued_days=valued, detail=f"newest scene {newest.isoformat()}")

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:
        """One unit per owed day; the support is checked against the lane's region lattice first."""
        support = self.support()
        if lane.grid is not None:
            require_lattice_agreement(support, region.analysis_lattices[lane.grid])
        requests: list[SourceRequest] = []
        for day in sorted(set(days)):
            start, end = _day_window(day)
            requests.append(
                SourceRequest(
                    unit=f"ndvi-day:{day.isoformat()}",
                    endpoint=ENDPOINT,
                    days=(day,),
                    parameters=search_parameters(
                        support.bbox, start, end, limit=SEARCH_PAGE_SIZE, sortby="+properties.eo:cloud_cover"
                    ),
                    # Never checkpointed: a replay would re-walk the network (AGENTS.md "Wave O fetch").
                    # Checkpoint-ineligible BY CONSTRUCTION, and it must stay that way: adding
                    # requested_parameters here would make `restore()` try to replay this unit from a
                    # checkpoint, which still means hitting the network (`fetch` ignores `client` and
                    # re-walks `collect_ndvi_grid_records` regardless), not actually restoring anything.
                    requested_parameters=frozenset(),
                    support_digest=support.digest_label,
                )
            )
        return requests

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """The legacy STAC+COG walk, through `ingest/vegetation.py`'s own upstream client (Wave O)."""
        del client
        day = request.days[0]
        support = self.support()
        start, end = _day_window(day)
        max_cells = len(support.cells) + MAX_CELLS_SLACK
        window = GridSampleWindow(bbox=support.bbox, start=start, end=end, max_cells=max_cells)
        async with upstream_client(COG_BOUNDS) as owned_client:
            outcome = await collect_ndvi_grid_records(owned_client, window)
        if outcome.truncated:
            raise VegetationDayError(
                f"vegetation {day.isoformat()}: the grid fetch truncated at {window.max_cells} cells for a "
                f"{len(support.cells)}-cell support; the support and the fetch bbox have drifted apart"
            )
        resolved = resolve_day(day, outcome.records, support)
        return SourceResponse(
            request=request,
            body=resolved.response_sha256.encode("ascii"),
            retrieved_at=datetime.now(UTC),
            payload=resolved,
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:
        """Any filled cell writes the whole day; zero cells is absent only behind a later published day (legacy)."""
        resolved = _resolved(responses)
        if resolved is None:
            return Unsettled("strategy_error", "fetch returned no resolved vegetation day")
        if resolved.values:
            filled = len(resolved.values)
            return Written(expected_units=filled, present_units=filled, source_digest=resolved.response_sha256)
        later = context.later_published_day(STREAM)
        if later is None:
            return Unsettled(
                "unsettled",
                f"Earth Search answered zero usable cells across all {resolved.cells_requested} support cells, and "
                "no later day is published with values, so nothing proves the catalogue has moved past this day",
            )
        return Absent(
            reason=(
                f"Earth Search answered zero usable Sentinel-2 L2A cell readings across all "
                f"{resolved.cells_requested} support cells of vegetation on {day.isoformat()}"
            ),
            proof=(
                f"vegetation is published with values for {later.isoformat()}, which is later than {day.isoformat()}, "
                "so Earth Search's catalogue has moved past this day and its zero-cell answer is settled"
            ),
        )

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """One row per filled cell, in the twelve-column `VEGETATION_PLANE_SCHEMA` (legacy `rows.py::_row`)."""
        resolved = _resolved(responses)
        if resolved is None or not resolved.values:
            raise VegetationDayError(f"vegetation {day.isoformat()} has no filled cells to build rows from")
        data_available_at = responses[0].retrieved_at
        rows = [
            {
                "cell_id": value.cell.cell_id,
                "grid_name": NDVI_GRID_NAME,
                "metric_name": VEGETATION_PRODUCT.metric_name,
                "metric_unit": VEGETATION_PRODUCT.metric_unit,
                "observed_day": day,
                "metric_value": value.metric_value,
                "observation_checksum": value.record_sha256,
                "data_available_at": data_available_at,
                "release_count": value.release_count,
                "allowed_client_exposure": ALLOWED_CLIENT_EXPOSURE,
                "cell_longitude": value.cell.cell_longitude,
                "cell_latitude": value.cell.cell_latitude,
            }
            for value in resolved.values
        ]
        return pa.Table.from_pylist(rows, schema=VEGETATION_PRODUCT.stream_schema.arrow_schema)


def resolve_day(day: date, records: Sequence[Mapping[str, object]], support: VegetationSupport) -> VegetationDay:
    """Group raw records by support cell and keep the clearest per cell; an off-lattice record refuses the day."""
    grouped: dict[VegetationSupportCell, list[Mapping[str, object]]] = {}
    off_lattice = 0
    for record in records:
        raw_key = record.get("cellKey")
        cell = support.resolve(raw_key) if isinstance(raw_key, str) else None
        if cell is None:
            off_lattice += 1
            continue
        grouped.setdefault(cell, []).append(record)
    if off_lattice:
        raise VegetationDayError(
            f"vegetation {day.isoformat()}: {off_lattice} sampled record(s) named a cellKey the {NDVI_GRID_NAME} "
            "support does not carry"
        )
    values = tuple(
        _clearest(cell, cell_records)
        for cell, cell_records in sorted(grouped.items(), key=lambda entry: entry[0].cell_key)
    )
    return VegetationDay(
        day=day, values=values, cells_requested=len(support.cells), response_sha256=day_response_sha256(records)
    )


def _clearest(cell: VegetationSupportCell, records: Sequence[Mapping[str, object]]) -> VegetationCellValue:
    """The lowest-cloud-cover record wins; a reported 0.0 is the clearest, an absent value the worst."""
    chosen = sorted(records, key=lambda record: _cloud_cover(record, cell.cell_key))[0]
    _text(chosen, "sceneId", cell.cell_key)
    observed_at = _text(chosen, "observedAt", cell.cell_key)
    try:
        datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise VegetationDayError(f"cell {cell.cell_key}: 'observedAt' {observed_at!r} is not an ISO instant") from error
    _count(chosen, "sampleCount", cell.cell_key)
    return VegetationCellValue(
        cell=cell,
        metric_value=_number(chosen, "ndvi", cell.cell_key),
        cloud_cover_percent=_cloud_cover(chosen, cell.cell_key),
        release_count=len(records),
        record_sha256=record_sha256(chosen),
    )


def _cloud_cover(record: Mapping[str, object], cell_key: str) -> float:
    if record.get("cloudCover") is None:
        return UNREPORTED_CLOUD_COVER_PERCENT
    return _number(record, "cloudCover", cell_key)


def _number(record: Mapping[str, object], name: str, cell_key: str) -> float:
    value = record.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
        raise VegetationDayError(f"cell {cell_key}: the sampled record's {name!r} is {value!r}, not a finite number")
    return float(value)


def _count(record: Mapping[str, object], name: str, cell_key: str) -> int:
    value = record.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VegetationDayError(f"cell {cell_key}: the sampled record's {name!r} is {value!r}, not a count")
    return value


def _text(record: Mapping[str, object], name: str, cell_key: str) -> str:
    value = record.get(name)
    if not isinstance(value, str) or not value.strip():
        raise VegetationDayError(f"cell {cell_key}: the sampled record's {name!r} is {value!r}, not an identifier")
    return value


def _resolved(responses: Sequence[SourceResponse]) -> VegetationDay | None:
    if not responses or not isinstance(responses[0].payload, VegetationDay):
        return None
    return responses[0].payload


def _newest_scene_day(body: bytes) -> date | None:
    """The UTC day of the first (newest) feature of a STAC item collection, or None when it has none."""
    payload = json.loads(body)
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise ValueError("the item collection carries no 'features' list")
    if not features:
        return None
    first = features[0]
    properties = first.get("properties") if isinstance(first, dict) else None
    moment = properties.get("datetime") if isinstance(properties, dict) else None
    if not isinstance(moment, str):
        raise ValueError("the newest feature carries no properties.datetime")
    return parse_scene_timestamp(moment).date()


STRATEGY: Final = Sentinel2NdviStrategy()

__all__ = [
    "ENDPOINT",
    "STRATEGY",
    "STREAM",
    "Sentinel2NdviStrategy",
    "VegetationDay",
    "VegetationDayError",
    "day_response_sha256",
    "record_sha256",
    "resolve_day",
    "search_parameters",
]
