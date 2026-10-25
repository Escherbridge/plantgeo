"""`sensors-direct-forward`: NOAA NWS ground-station observations, re-asked every window day.

Strategy key `sensors.nws` (spec S14, §7). DARK: the shipped lane TOML still carries
`executor = "legacy"` (`lanes/sensors-direct-forward.toml`), so `pipeline/direct/sensors/forward.py`
is what the executor actually drives today, hourly. This module is authored and reviewed ahead of
the G6 cut-over (plan Phase 5); nothing here runs in production until that flip.

WHY ONE UNIT PER DAY, NOT ONE PER (STATION, DAY). `plan_requests` is synchronous and cannot fetch
the station roster to pre-plan per-station units (`source.py`'s module docstring, "Wave O"); `fetch`
sweeps the whole roster for one day in a single call, bypassing the governed `ProviderClient`
exactly as `pipeline/lanes/watersheds/nhdplus.py` does for NHDPlus_HR's own two-phase walk. `nws.toml`
needs no change for this: `plan_requests` names the provider's one existing `stations` endpoint only
so every `SourceRequest` carries a valid, declared endpoint key -- `fetch` never sends through it.

RETENTION IS A STRATEGY-LEVEL CHECK, NOT THE GENERIC GAP-FILL ONE, BECAUSE IT ROLLS.
`pipeline/runner/windows.py::plan_gap_fill`'s `retention_exceeded` reads `lane.source.history.
earliest`, a FIXED date set once at TOML load; NWS's retention floor is `now - 6 days`, a moving
target `foundation/lane_config/models.py::SourceHistory` cannot express (its `HistoryCapability` is
`archive`/`endpoint`/`none`, none of them rolling). `[source].history.capability = "none"` in the
TOML therefore declares, correctly, that this lane has no FIXED earliest day -- and
`source.fetch_sensors_day_snapshot` computes the real rolling floor at fetch time instead, folding
a day that has already rolled out of NWS's window into `settle`'s own `Unsettled(reason=
"retention_exceeded", ...)` (reusing the shared `UnwrittenReason` word; `pipeline/lanes/watersheds/
nhdplus.py:201` sets the precedent for a strategy constructing `strategy_error` directly, and this
is the same pattern for a different reserved word). `gap_fill_enabled = false` because of this: the
legacy writer's own "gap walk" never reaches past the SAME 6-day window either (`pipeline/direct/
sensors/AGENTS.md`, "Ask once per day"), so there is no DEEPER historical backfill duty to port.

MALFORMED UPSTREAM IS LEAN: WHATEVER ESCAPES `fetch()` REFUSES THE WHOLE DAY, NOTHING PARTIAL.
`source.py`'s two isolation layers (per-state roster, per-station poll) absorb every failure shape
the legacy writer itself absorbs; what is left to reach `fetch()` uncaught is a code fault or an
upstream shape change neither isolation layer anticipated, and that becomes this unit's
`strategy_error` with no `Written` ever constructed (`pipeline/runner/AGENTS.md`, "A strategy fault
... is that day's strategy_error"). `tests/lanes/sensors/test_nws.py` exercises this by making the
fetch seam raise directly, proving the wiring rather than a specific NWS failure mode.

NO VOLATILE COLUMN IS DECLARED, UNLIKE EVERY SIBLING STRATEGY (H1, flag for the per-lane review).
`warehouse/schemas/sensors.py::SENSORS_SCHEMA` has no column this strategy stamps from the fetch's
own retrieval instant: `observed_at` is the WINNING REPORT'S OWN instant (content, read off
`FeatureIdentity.observed_at`), `data_available_at` is always `None` (content, not a stamp), and
every other column is upstream-reported or station metadata. `day_source_digest` therefore excludes
nothing -- contrast `water_gauges.usgs_water_data::day_source_digest`, which excludes nothing either
but for the opposite reason (its `ingested_at` column is NOT part of the digest input list, not
dropped from it). `lanes/sensors-direct-forward.toml`'s `[[streams]]` carries no
`volatile_columns` entry; this is evidence, not an omission.

THE ROSTER ITSELF IS NOT A STABLE PARTITION, UNLIKE `water_gauges`' FIXED TILES (H2, flag for the
per-lane review). `UsgsWaterDataDailyStrategy.source_unit_ids` is deterministic from the region's
camera envelope alone; `source.py::fetch_sensors_day_snapshot` discovers the roster fresh every
turn (`SENSOR_STATION_STATES`/`SENSOR_MAX_STATIONS`/NWS's own roster churn all vary it turn to
turn). `settle`'s `expected_unit_ids` is therefore THIS TURN's roster, not a registered constant, so
the S11 "fewer units never written" rule it leans on (module docstring, `source.py`) can read an
ORDINARY roster shrink (NWS retires a station) as if it were a failed poll, and refuse to settle a
day that is, in truth, simply reporting on fewer stations now. The legacy writer has no equivalent
failure mode (its merge keeps a retired station's last block forever, by never being asked to drop
it). Left open rather than fixed here: a corrected rule would need to compare against the PREVIOUS
turn's receipt's `present_unit_ids`, not `expected_unit_ids`, which `DayContext` does not expose
today (`short_day_units` reads `present_units`/`expected_units` counts only, not the identity sets).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.policy import format_javascript_number
from agri_data_service.ingest.sensors import NWS_OBSERVATION_RETENTION
from agri_data_service.pipeline.lanes._shared.digest import COMPACT_SEPARATORS, canonical_json_bytes, canonical_json_text, sha256_hex, sha256_hex_of_lines
from agri_data_service.pipeline.lanes.sensors.source import SensorsDaySnapshot, fetch_sensors_day_snapshot
from agri_data_service.pipeline.runner.contract import SourceRequest, SourceResponse, Unsettled, Written
from agri_data_service.warehouse.schemas.sensors import SENSORS_GRAIN, SENSORS_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region, RegionEnvelope
    from agri_data_service.pipeline.runner.contract import DayContext, ProviderClient, Settlement

#: The provider's one declared endpoint (`lanes/_providers/nws.toml`). `fetch` never sends through
#: it (module docstring); it is named only so every planned unit carries a valid endpoint key.
STATIONS_ENDPOINT: Final = "stations"


class SensorsRowsRequestedWithNoSnapshotError(RuntimeError):
    """Raised when `rows()` is called with no in-retention, non-empty snapshot: a runner contract violation."""


def _bbox(envelope: RegionEnvelope) -> str:
    """The region's camera envelope as a `west,south,east,north` string -- restated, never imported
    across layers, from `pipeline/lanes/fire_perimeters/wfigs.py::_bbox`'s identical helper."""
    return ",".join(
        format_javascript_number(value) for value in (envelope.west, envelope.south, envelope.east, envelope.north)
    )


def _support_digest(bbox: str) -> str:
    """Binds a checkpoint to the extent this unit was planned over (`SourceRequest.support_digest`)."""
    return sha256_hex(bbox.encode("utf-8"))


def _snapshot_payload(responses: Sequence[SourceResponse]) -> SensorsDaySnapshot | None:
    """The one unit's day snapshot, or `None` when no response carried one."""
    for response in responses:
        if isinstance(response.payload, SensorsDaySnapshot):
            return response.payload
    return None


def day_source_digest(rows: Sequence[Mapping[str, object]]) -> str:
    """SHA-256 of every row's content, sorted by the registered grain; no column is excluded (module docstring)."""
    return sha256_hex_of_lines(
        canonical_json_text(dict(row), sort_keys=True, separators=COMPACT_SEPARATORS, allow_nan=True, default=str)
        for row in sorted(
            rows,
            key=lambda row: (str(row["sensor_id"]), row["observed_day"].isoformat(), str(row["measurement_name"])),  # type: ignore[union-attr]
        )
    )


@dataclass(frozen=True)
class NwsSensorsStrategy:
    """One whole-roster sweep per owed day; `fetch` bypasses the governed client (Wave O, module docstring)."""

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:  # noqa: ARG002 - the Protocol's lane
        """One unit per asked day, over the region's camera envelope; the roster is discovered inside `fetch`."""
        bbox = _bbox(region.default_camera_envelope)
        digest = _support_digest(bbox)
        return [
            SourceRequest(
                unit=f"sensors:{day.isoformat()}",
                endpoint=STATIONS_ENDPOINT,
                days=(day,),
                parameters=(("bbox", bbox),),
                support_digest=digest,
            )
            for day in sorted(set(days))
        ]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:  # noqa: ARG002 - Wave O, see module docstring
        """Sweep the whole roster for this unit's one day through `source.py`'s own `upstream_client`."""
        day = request.days[0]
        bbox = request.query()["bbox"]
        snapshot = await fetch_sensors_day_snapshot(day, bbox)
        body = canonical_json_bytes(
            {
                "day": day.isoformat(),
                "in_retention": snapshot.in_retention,
                "roster_size": snapshot.roster_size,
                "stations_answered": snapshot.stations_answered,
                "stations_unavailable": snapshot.stations_unavailable,
                "rows": len(snapshot.rows),
                "fetched_at": snapshot.fetched_at.isoformat(),
            },
            sort_keys=True,
            separators=COMPACT_SEPARATORS,
            allow_nan=True,
        )
        return SourceResponse(request=request, body=body, retrieved_at=snapshot.fetched_at, payload=snapshot)

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:  # noqa: ARG002 - the Protocol's day and context
        """Written from whichever stations contributed a row; retention/upstream-outage/empty-poll named distinctly."""
        snapshot = _snapshot_payload(responses)
        if snapshot is None:
            return Unsettled("strategy_error", "fetch returned no sensors day snapshot")
        if not snapshot.in_retention:
            return Unsettled(
                "retention_exceeded",
                f"{day.isoformat()} is older than NWS's {NWS_OBSERVATION_RETENTION.days}-day rolling "
                f"retention as of {snapshot.fetched_at.isoformat()}",
            )
        if snapshot.roster_size == 0:
            return Unsettled(
                "upstream_unavailable", "the NWS station roster answered with no station inside the configured coverage"
            )
        if not snapshot.rows:
            return Unsettled(
                "unsettled",
                f"NWS answered {snapshot.stations_answered} of {snapshot.roster_size} station(s) with no "
                "writable reading for this day yet",
            )
        return Written(
            expected_units=len(snapshot.roster_station_ids),
            present_units=len(snapshot.present_station_ids),
            source_digest=day_source_digest(snapshot.rows),
            expected_unit_ids=snapshot.roster_station_ids,
            present_unit_ids=snapshot.present_station_ids,
        )

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:  # noqa: ARG002 - the Protocol's day
        """Every present station's winning-report measurement rows, in the grain's order."""
        snapshot = _snapshot_payload(responses)
        if snapshot is None or not snapshot.rows:
            raise SensorsRowsRequestedWithNoSnapshotError(
                "rows() was called with no in-retention, non-empty sensors day snapshot"
            )
        table = pa.Table.from_pylist(list(snapshot.rows), schema=SENSORS_SCHEMA.arrow_schema)
        return table.sort_by([(column, "ascending") for column in SENSORS_GRAIN])


STRATEGY: Final = NwsSensorsStrategy()

__all__ = [
    "STATIONS_ENDPOINT",
    "STRATEGY",
    "NwsSensorsStrategy",
    "SensorsRowsRequestedWithNoSnapshotError",
    "day_source_digest",
]
