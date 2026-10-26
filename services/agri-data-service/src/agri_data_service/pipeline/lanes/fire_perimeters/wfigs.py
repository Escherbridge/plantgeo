"""`fire-perimeters-direct-forward`: one WFIGS `_Current` snapshot per turn, probed before it is fetched.

See `lanes/fire_perimeters/AGENTS.md` for the full rationale (DARK status, the H1/H2/H3 residuals,
the two deliberate deviations, why a snapshot never partitions by day, and the legacy-casting note).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.identity import MissingNativeKeyError, build_fire_perimeter_identity
from agri_data_service.ingest.policy import format_javascript_number
from agri_data_service.ingest.upstream_retry import BUSY_MESSAGE_PATTERN
from agri_data_service.ingest.wfigs import (
    WFIGS_BOUNDS,
    epoch_milliseconds_to_iso,
    fetch_fire_perimeters_walk,
    perimeter_severity,
)
from agri_data_service.pipeline.lanes._shared.digest import (
    DEFAULT_SEPARATORS,
    canonical_json_bytes,
    canonical_json_text,
    sha256_hex,
    sha256_hex_of_lines,
)
from agri_data_service.pipeline.lanes.fire_perimeters.support import (
    fire_perimeter_geometry_session,
    repair_perimeter_geometries_to_wkb,
)
from agri_data_service.pipeline.runner.contract import ProviderEdge, SourceRequest, SourceResponse, Unsettled, Written
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_GRAIN, FIRE_PERIMETERS_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region, RegionEnvelope
    from agri_data_service.pipeline.runner.contract import DayContext, ProbeWindow, ProviderClient, Settlement

#: The provider's cheap layer-metadata resource (`lanes/_providers/nifc-wfigs.toml`'s
#: `[endpoints.layer]`), distinct from the heavy `[endpoints.query]` the fetch pages through.
LAYER_ENDPOINT: Final = "layer"
QUERY_ENDPOINT: Final = "query"
#: There is exactly one question this lane ever asks: what does `_Current` hold right now. The unit
#: name is therefore constant across every turn, matching `tests.runner.fixtures.static_watermark`.
SNAPSHOT_UNIT: Final = "snapshot"

#: `direct:<uniqueFireIdentifier>` -- kept BYTE-IDENTICAL to
#: `pipeline/direct/fire_perimeters/rows.py::DIRECT_FEATURE_ID_PREFIX` on purpose: the G6 compare step
#: (plan Phase 5) diffs this strategy's rows against the legacy writer's, and a `feature_id` namespace
#: change alone would manufacture a delta on every row despite nothing else differing.
DIRECT_FEATURE_ID_PREFIX: Final = "direct"
#: `geo.features.status`'s schema default, reproduced for the same reason. See
#: `pipeline/direct/fire_perimeters/rows.py`'s module docstring, "THE THREE COLUMNS THAT CANNOT COME
#: FROM UPSTREAM".
PUBLISHED_STATUS: Final = "published"

#: The repair-audit stamp, carried on `FirePerimeterSnapshot.rows` but NOT a schema column. See
#: `AGENTS.md` "The repair-audit keys (`GEOMETRY_REPAIRED_KEY`/`GEOMETRY_REPAIRED_AREA_CHANGE_KEY`)".
GEOMETRY_REPAIRED_KEY: Final = "geometry_repaired"
GEOMETRY_REPAIRED_AREA_CHANGE_KEY: Final = "geometry_repaired_area_change"

#: The observation-day COALESCE, narrowed to the two keys this module's input can carry. See
#: `AGENTS.md` "Why `_OBSERVATION_DAY_KEYS` is narrower than legacy's `OBSERVATION_DAY_KEYS`".
_OBSERVATION_DAY_KEYS: Final[tuple[str, ...]] = ("polygonDateTime", "fireDiscoveryDateTime")

#: Columns a content digest excludes: `updated_at` is this strategy's own retrieval instant and
#: advances on every fetch regardless of content, exactly the column
#: `pipeline/direct/fire_perimeters/forward.py::VERSION_STAMP_COLUMNS` excludes for the same reason.
#: Digesting it would make every turn report "changed" and defeat the S11 rewrite rule this module
#: leans on instead of re-implementing its own watermark ladder (module docstring).
_DIGEST_EXCLUDED_COLUMNS: Final = frozenset({"updated_at"})

#: ArcGIS Online reports `editingInfo.lastEditDate = 0` on a layer with editor tracking disabled --
#: answered, not absent, but naming no edit at all. Treated as `blind` (G0's taxonomy), never as a
#: real 1970-01-01 watermark.
_NO_TRACKED_EDITS: Final = 0

#: Restated, never imported (L3 namespace rule), from
#: `pipeline/direct/fire_perimeters/rows.py::_ISO_DAY_PATTERN`: `date.fromisoformat` alone also
#: accepts shapes this regex rejects (ISO week dates like `2026-W01-1`), so `_named_day` checks
#: this FIRST. See `AGENTS.md` "The legacy-casting-rules divergence (L1)".
_ISO_DAY_PATTERN: Final = re.compile(r"\d{4}-\d{2}-\d{2}")


class FirePerimetersSnapshotTruncatedError(RuntimeError):
    """Raised when the WFIGS `_Current` walk reports a clipped answer; never retried by this strategy.

    Mirrors `pipeline/direct/fire_perimeters/source.py::FirePerimetersTruncatedError`: the record
    ceiling, the total byte budget and an undeliverable record are properties of the request and the
    feed, not of one attempt, so retrying cannot clear this and a `static_lookup` snapshot stamped on
    a clipped population would publish every perimeter it missed as extinguished.
    """


class FirePerimetersRowsWithoutSnapshotError(RuntimeError):
    """Raised when `rows()` is called with no conformed snapshot in the response: a runner contract violation.

    Modelled on `pipeline/lanes/sensors/nws.py::SensorsRowsRequestedWithNoSnapshotError`: a missing
    snapshot at `rows()` time means the runner called it without first calling `fetch`, which is
    the runner's own bug, not a retryable upstream condition -- `FirePerimetersSnapshotTruncatedError`
    is the wrong type here because a truncated WALK is a real upstream signal `fetch` raises, while
    this is an impossible-by-contract call shape.
    """


@dataclass(frozen=True, slots=True)
class GeometryRepair:
    """One perimeter the repair chain changed: which incident, and the planar area ratio it moved by.

    Restored (L3): mirrors `pipeline/direct/fire_perimeters/rows.py::GeometryRepair` exactly, so a
    caller that wants the legacy writer's per-incident repair detail (today only the legacy writer's
    terminal report and `fire_perimeters_forward_geometry_repaired` stderr event print it) has
    somewhere to read it from on this path too.
    """

    unique_fire_identifier: str
    area_change: float | None


@dataclass(frozen=True, slots=True)
class FirePerimeterSnapshot:
    """One conformed WFIGS population: every row the schema needs except `snapshot_day`, plus counts.

    Built once, inside `fetch` -- never rebuilt by `settle` or `rows`, because the geometry repair
    this carries (`support.repair_perimeter_geometries_to_wkb`) is a real DuckDB round trip over
    ~23 MB of polygon and must be paid exactly once per turn.

    `rows` carries `GEOMETRY_REPAIRED_KEY`/`GEOMETRY_REPAIRED_AREA_CHANGE_KEY` per entry (L3); they
    are not schema columns, so `rows()` strips them before building the Arrow table.
    """

    rows: tuple[dict[str, object], ...]
    fetched_at: datetime
    perimeters_seen: int
    rejected: int
    collapsed: int

    @property
    def repairs(self) -> tuple[GeometryRepair, ...]:
        """Every row the repair chain changed, in row order -- restored parity with the legacy writer's property."""
        return tuple(
            GeometryRepair(
                unique_fire_identifier=str(row["unique_fire_identifier"]),
                area_change=row.get(GEOMETRY_REPAIRED_AREA_CHANGE_KEY),  # type: ignore[arg-type]
            )
            for row in self.rows
            if row.get(GEOMETRY_REPAIRED_KEY) is True
        )


def _bbox(envelope: RegionEnvelope) -> str:
    """The region's camera envelope as an ArcGIS `bbox` value, e.g. `-125,42,-111,49`."""
    return ",".join(
        format_javascript_number(value) for value in (envelope.west, envelope.south, envelope.east, envelope.north)
    )


def _support_digest(bbox: str) -> str:
    """Binds a checkpoint to the extent the one unit was planned over (`SourceRequest.support_digest`)."""
    return sha256_hex(bbox.encode("utf-8"))


def _arcgis_error_message(error: Mapping[str, object]) -> str:
    """Extract one ArcGIS error body's message, exactly as `ingest.arcgis.parse_feature_collection` does.

    Restated rather than imported (that function raises on an error body; `probe_edge` needs the
    message text WITHOUT raising, to classify it against `BUSY_MESSAGE_PATTERN` first).
    """
    details = error.get("details")
    return str(error.get("message") or ("; ".join(details) if isinstance(details, list) else None) or "unknown")


def _named_day(perimeter: Mapping[str, object]) -> date | None:
    """The incident's own date: the first of `_OBSERVATION_DAY_KEYS` present, as its string prefix.

    MIRRORS `pipeline/direct/fire_perimeters/rows.py::publisher_named_day` exactly (restated, never
    imported, L3 namespace rule): the chain STOPS at the first key whose value is a `str`, EMPTY
    included (COALESCE semantics -- only an absent/non-`str` value continues it), and the first ten
    characters must fullmatch `_ISO_DAY_PATTERN` before `date.fromisoformat` is even called, since
    `fromisoformat` alone also accepts shapes the legacy regex rejects (ISO week dates). See
    `AGENTS.md` "The legacy-casting-rules divergence (L1)" and
    `tests/lanes/fire_perimeters/test_named_day_parity.py`.
    """
    named: str | None = None
    for key in _OBSERVATION_DAY_KEYS:
        value = perimeter.get(key)
        if isinstance(value, str):
            named = value[:10]
            break
    if named is None or _ISO_DAY_PATTERN.fullmatch(named) is None:
        return None
    try:
        return date.fromisoformat(named)
    except ValueError:
        return None


def _parse_instant(value: object) -> datetime | None:
    """Parse one of `ingest.wfigs`'s own rendered instants; `None` passes through unchanged."""
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value).astimezone(UTC)


def _conform(perimeters: Sequence[Mapping[str, object]], fetched_at: datetime) -> FirePerimeterSnapshot:
    """Build every schema row but `snapshot_day`, repairing every geometry in ONE DuckDB round trip.

    A REPEATED `uniqueFireIdentifier` WITHIN ONE WALK COLLAPSES, LAST ONE WINNING -- matching
    `geo.features`' one-row-per-`external_id` shape
    (`pipeline/direct/fire_perimeters/rows.py::fire_perimeter_population`'s own rule). The order is
    the export's order, `ORDER BY unique_fire_identifier`, applied here so slicing or sorting later
    never depends on dict insertion order.
    """
    documents: dict[str, Mapping[str, object]] = {}
    rejected = 0
    collapsed = 0
    for perimeter in perimeters:
        try:
            identity = build_fire_perimeter_identity(perimeter)
        except (MissingNativeKeyError, ValueError):
            rejected += 1
            continue
        key = identity.producer_local_id
        if key in documents:
            collapsed += 1
        documents[key] = perimeter
    ordered = sorted(documents.items())
    identities = [identifier for identifier, _ in ordered]
    geometries = [perimeter.get("geometry") for _, perimeter in ordered]
    with fire_perimeter_geometry_session() as session:
        repaired = repair_perimeter_geometries_to_wkb(session, geometries, identities)
    rows: tuple[dict[str, object], ...] = tuple(
        {
            "feature_id": f"{DIRECT_FEATURE_ID_PREFIX}:{identifier}",
            "unique_fire_identifier": identifier,
            "observed_day": _named_day(perimeter),
            "incident_name": perimeter.get("incidentName"),
            "irwin_id": perimeter.get("irwinId"),
            "fire_discovery_at": _parse_instant(perimeter.get("fireDiscoveryDateTime")),
            "polygon_at": _parse_instant(perimeter.get("polygonDateTime")),
            "gis_acres": perimeter.get("gisAcres"),
            "fire_cause": perimeter.get("fireCause"),
            "incident_type_category": perimeter.get("incidentTypeCategory"),
            "poo_state": perimeter.get("pooState"),
            "percent_contained": perimeter.get("percentContained"),
            "severity": perimeter_severity(perimeter.get("percentContained")),
            "status": PUBLISHED_STATUS,
            "data_available_at": None,
            "updated_at": fetched_at,
            "geometry_wkb": geometry.wkb,
            GEOMETRY_REPAIRED_KEY: geometry.repaired,
            GEOMETRY_REPAIRED_AREA_CHANGE_KEY: geometry.area_change,
        }
        for (identifier, perimeter), geometry in zip(ordered, repaired, strict=True)
    )
    return FirePerimeterSnapshot(
        rows=rows, fetched_at=fetched_at, perimeters_seen=len(perimeters), rejected=rejected, collapsed=collapsed
    )


def content_digest(rows: Sequence[Mapping[str, object]]) -> str:
    """Digest a population's content (every column but `updated_at`), sorted by fire identifier.

    This is the ONLY content comparison this module performs -- there is no read-back of the
    previously published version, because `Written.source_digest` hands the comparison to the
    runner's own S11 rewrite rule (module docstring). Exposed (not `_content_digest`) because
    "watermark-unchanged" is a property of THIS function being stable across two calls on an
    unchanged population, which is what the strategy's own tests exercise directly.
    """
    # Default `", "`/`": "` separators and `default=str`: the frozen per-row rendering, never compacted.
    return sha256_hex_of_lines(
        canonical_json_text(
            {key: value for key, value in row.items() if key not in _DIGEST_EXCLUDED_COLUMNS},
            sort_keys=True,
            separators=DEFAULT_SEPARATORS,
            allow_nan=True,
            default=str,
        )
        for row in sorted(rows, key=lambda row: str(row["unique_fire_identifier"]))
    )


def _snapshot_payload(responses: Sequence[SourceResponse]) -> FirePerimeterSnapshot | None:
    """The one unit's conformed snapshot, or `None` when no response carried one."""
    for response in responses:
        if isinstance(response.payload, FirePerimeterSnapshot):
            return response.payload
    return None


@dataclass(frozen=True)
class WfigsFirePerimetersStrategy:
    """One `_Current` snapshot per turn: `probe_edge` decides whether one is owed; `fetch` publishes it."""

    async def probe_edge(  # noqa: PLR0911 - one return per honest probe classification
        self, client: ProviderClient, window: ProbeWindow
    ) -> ProviderEdge:
        """Read `editingInfo.lastEditDate` off the cheap layer resource; classify every way it can fail.

        See `AGENTS.md` "`probe_edge`'s classification table (ONE small GET, never the `_Current` walk)"
        for the full answer table and the M1 note on why a transport failure/429 is never caught here.
        """
        response = await client.get(LAYER_ENDPOINT, {"f": "json"}, probe=True)
        try:
            payload = json.loads(response.body)
        except ValueError as error:
            return ProviderEdge(
                status="invalid", window=window, detail=f"the layer resource did not answer JSON: {error}"
            )
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            message = _arcgis_error_message(payload["error"])
            if BUSY_MESSAGE_PATTERN.search(message) is not None:
                return ProviderEdge(
                    status="deferred",
                    window=window,
                    detail=f"the layer resource reported a busy ArcGIS throttle: {message}",
                )
            return ProviderEdge(
                status="invalid", window=window, detail=f"the layer resource answered an error body: {message}"
            )
        if not isinstance(payload, dict):
            return ProviderEdge(
                status="invalid",
                window=window,
                detail=f"the layer resource answered a non-object body: {str(payload)[:200]}",
            )
        editing_info = payload.get("editingInfo")
        last_edit = editing_info.get("lastEditDate") if isinstance(editing_info, dict) else None
        if last_edit == _NO_TRACKED_EDITS:
            return ProviderEdge(
                status="blind", window=window, detail="editingInfo.lastEditDate reports no tracked edit"
            )
        watermark_instant = epoch_milliseconds_to_iso(last_edit)
        if watermark_instant is None:
            return ProviderEdge(
                status="invalid",
                window=window,
                detail=f"editingInfo.lastEditDate is missing or unreadable: {editing_info!r}",
            )
        watermark_day = datetime.fromisoformat(watermark_instant).date()
        return ProviderEdge(status="ok", window=window, valued_days=frozenset({watermark_day}))

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:  # noqa: ARG002 - the Protocol's lane
        """The one snapshot unit, over the region's camera envelope, answering the probed watermark day."""
        bbox = _bbox(region.default_camera_envelope)
        return [
            SourceRequest(
                unit=SNAPSHOT_UNIT,
                endpoint=QUERY_ENDPOINT,
                days=tuple(days),
                parameters=(("bbox", bbox),),
                support_digest=_support_digest(bbox),
            )
        ]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:  # noqa: ARG002 - see the module docstring, deviation 1
        """Walk the whole `_Current` feed once and conform it; refuse a clipped answer by name."""
        bbox = request.query()["bbox"]
        async with upstream_client(WFIGS_BOUNDS) as http_client:
            perimeters, outcome = await fetch_fire_perimeters_walk(http_client, bbox)
        if outcome.truncated or outcome.oversized:
            raise FirePerimetersSnapshotTruncatedError(
                f"the WFIGS walk over {bbox} did not deliver the whole feed (record ceiling, page ceiling or the "
                "total byte budget); a static_lookup snapshot stamped on a clipped population would publish "
                "every perimeter it missed as extinguished"
            )
        fetched_at = datetime.now(UTC)
        snapshot = _conform(perimeters, fetched_at)
        body = canonical_json_bytes(
            {
                "perimeters_seen": snapshot.perimeters_seen,
                "rows_conformed": len(snapshot.rows),
                "rejected": snapshot.rejected,
                "collapsed": snapshot.collapsed,
                "fetched_at": fetched_at.isoformat(),
            },
            sort_keys=True,
            separators=DEFAULT_SEPARATORS,
            allow_nan=True,
        )
        return SourceResponse(request=request, body=body, retrieved_at=fetched_at, payload=snapshot)

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:  # noqa: ARG002 - the Protocol's day and context
        """Written when the fetch conformed at least one perimeter; its digest is the S11 rewrite input."""
        snapshot = _snapshot_payload(responses)
        if snapshot is None or not snapshot.rows:
            seen = 0 if snapshot is None else snapshot.perimeters_seen
            return Unsettled(
                "unsettled",
                f"WFIGS _Current answered {seen} perimeter(s) with none conformable; no population to version",
            )
        dropped = {"rejected_feature": snapshot.rejected} if snapshot.rejected else {}
        return Written(
            expected_units=1, present_units=1, source_digest=content_digest(snapshot.rows), dropped_rows=dropped
        )

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """Stamp the conformed population with `day` (the probed watermark day) and build the base rung.

        Projects onto `FIRE_PERIMETERS_SCHEMA.column_names` explicitly (L3, re-read after ring
        review) rather than splatting the whole row dict: `GEOMETRY_REPAIRED_KEY` /
        `GEOMETRY_REPAIRED_AREA_CHANGE_KEY` ride on `snapshot.rows` for `FirePerimeterSnapshot.repairs`
        but are not schema columns, exactly the same whitelist
        `pipeline/direct/fire_perimeters/rows.py::fire_perimeters_table` applies for the identical reason.
        """
        snapshot = _snapshot_payload(responses)
        if snapshot is None:
            raise FirePerimetersRowsWithoutSnapshotError("rows() was called with no conformed snapshot in the response")
        records = [
            {**{column: row.get(column) for column in FIRE_PERIMETERS_SCHEMA.column_names}, "snapshot_day": day}
            for row in snapshot.rows
        ]
        table = pa.Table.from_pylist(records, schema=FIRE_PERIMETERS_SCHEMA.arrow_schema)
        return table.sort_by([(column, "ascending") for column in FIRE_PERIMETERS_GRAIN])


STRATEGY: Final = WfigsFirePerimetersStrategy()

__all__ = [
    "DIRECT_FEATURE_ID_PREFIX",
    "GEOMETRY_REPAIRED_AREA_CHANGE_KEY",
    "GEOMETRY_REPAIRED_KEY",
    "LAYER_ENDPOINT",
    "PUBLISHED_STATUS",
    "QUERY_ENDPOINT",
    "SNAPSHOT_UNIT",
    "STRATEGY",
    "FirePerimeterSnapshot",
    "FirePerimetersRowsWithoutSnapshotError",
    "FirePerimetersSnapshotTruncatedError",
    "GeometryRepair",
    "WfigsFirePerimetersStrategy",
    "content_digest",
]
