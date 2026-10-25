"""`fire-perimeters-direct-forward`: one WFIGS `_Current` snapshot per turn, probed before it is fetched.

Strategy key `fire_perimeters.wfigs` (spec S14, §7). DARK: the shipped lane TOML still carries
`executor = "legacy"` (`lanes/fire-perimeters-direct-forward.toml`), so
`pipeline/direct/fire_perimeters/forward.py` is what the executor actually drives today, hourly at
`:10` (`pipeline/direct/AGENTS.md:1728`). This module is authored and reviewed ahead of the G6
cut-over (plan Phase 5); nothing here runs in production until that flip.

WHY THIS STRATEGY IS SMALLER THAN THE LEGACY PACKAGE IT WILL REPLACE. The legacy
`pipeline/direct/fire_perimeters/watermark.py` re-implements, by hand, exactly what the generic
runner now does for every `static_lookup` lane: "the probe returns the source watermark as the one
valued day; the lane owes a snapshot dated there when the served snapshot is older, and nothing when
it is current" (`pipeline/runner/AGENTS.md` "Static lookup"), and S11's rewrite rule ("write when ...
the source receipt digest changed") is what used to need `watermark.py`'s own
`PublishedLadder`/`read_published_version`/row-count short-circuit. This module therefore carries no
ladder, no read-back of the previously published version, and no content-digest comparison against
storage: `probe_edge` answers ONE question (what does WFIGS' own `editingInfo.lastEditDate` say right
now) and `settle`'s `Written.source_digest` is the S11 input the runner compares against the last
RECEIPT, not against a re-read partition. The watermark FIELD spec §7 names
(`editingInfo.lastEditDate`) is a probe-time optimisation that avoids the ~11 MB `_Current` walk when
nothing has changed; it is not, and does not need to be, a second copy of the content-comparison the
legacy module built because PostgreSQL gave it no change clock to read (that module's own docstring,
"WFIGS `_Current` publishes no change timestamp for an attribute revision at all").

TWO DELIBERATE DEVIATIONS, left for the per-lane adversarial review (plan Phase 4) and the Phase 6 QA
round to weigh before G6:

1. **`fetch` does not send through the `client: ProviderClient` it is handed.** The adaptive,
   byte-bounded, oversized-record-skipping page walk (`ingest.wfigs.fetch_fire_perimeters_walk`) does
   not know its own page count ahead of `plan_requests` time, which is the model every other unit in
   this codebase assumes ("A request unit is the retry grain... one failed unit never discards its
   siblings", `pipeline/runner/contract.py`). `lanes/AGENTS.md` "Extracted source leaves are NOT
   strategies" names this exact tension for the modules `p4-extract` already moved and states "the
   s-* slices decide per lane what migrates onto the provider client" -- this slice's decision is NOT
   YET, for the same reason those extracted leaves stayed on `upstream_client`. The whole walk
   (`ingest.http.upstream_client` + `ingest.wfigs.fetch_fire_perimeters_walk`) runs inside `fetch` on
   its OWN already-adequate retry ladder (`ingest.wfigs.WFIGS_RETRY`, widened 2026-08-10 after the
   production throttle incident); layering the runner's per-unit ladder on top would double the
   backoff rather than improve it. `probe_edge`, by contrast, IS one send and threads the real
   `client` cleanly.
2. **Geometry repair is restated, not imported, from `pipeline/direct/fire_perimeters/support.py`.**
   `pipeline/lanes/fire_perimeters/support.py` carries the identical `ST_MakeValid` /
   `ST_CollectionExtract` chain `evacuation_zones/support.py` already restates (see that module's own
   docstring for why: a strategy may import its own `pipeline/lanes/<layer>/` package and never
   `pipeline/direct/**`, which Phase 7 deletes whole).

A SNAPSHOT NEVER PARTITIONS BY DAY. `plan_requests` ignores the day(s) the runner asks about beyond
passing them through on the one unit: a `static_lookup`'s only question is "what does `_Current` look
like right now", and `probe_edge` already decided a newer version is owed before `plan_requests` is
ever called (`pipeline/runner/AGENTS.md`'s probe gate). `rows` stamps every row with whichever day the
runner resolved the turn's `day` to (the probed watermark day), exactly as
`pipeline/direct/fire_perimeters/rows.py::fire_perimeters_table` stamps `snapshot_day` from the
watermark rather than from any row's own timestamp.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.identity import MissingNativeKeyError, build_fire_perimeter_identity
from agri_data_service.ingest.policy import format_javascript_number
from agri_data_service.ingest.wfigs import (
    WFIGS_BOUNDS,
    epoch_milliseconds_to_iso,
    fetch_fire_perimeters_walk,
    perimeter_severity,
)
from agri_data_service.pipeline.lanes.fire_perimeters.support import (
    fire_perimeter_geometry_session,
    repair_perimeter_geometries_to_wkb,
)
from agri_data_service.pipeline.runner.contract import (
    ProviderEdge,
    SourceRequest,
    SourceResponse,
    SourceThrottledError,
    SourceUnavailableError,
    Unsettled,
    Written,
)
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

#: The observation-day COALESCE, narrowed to the two keys `ingest.wfigs.parse_perimeter_collection`
#: ever actually produces. The legacy `rows.py::publisher_named_day` restates all four of
#: `geo.feature_observation_day`'s keys because it reads the stored PostgreSQL jsonb document, which
#: could structurally grow `observedAt`/`updatedAt`; this module's input is this package's OWN,
#: narrower dict shape (`ingest.wfigs.parse_perimeter_collection`'s output), which never carries
#: those two keys at all, so restating them here would assert a possibility that cannot arise.
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


class FirePerimetersSnapshotTruncatedError(RuntimeError):
    """Raised when the WFIGS `_Current` walk reports a clipped answer; never retried by this strategy.

    Mirrors `pipeline/direct/fire_perimeters/source.py::FirePerimetersTruncatedError`: the record
    ceiling, the total byte budget and an undeliverable record are properties of the request and the
    feed, not of one attempt, so retrying cannot clear this and a `static_lookup` snapshot stamped on
    a clipped population would publish every perimeter it missed as extinguished.
    """


@dataclass(frozen=True, slots=True)
class FirePerimeterSnapshot:
    """One conformed WFIGS population: every row the schema needs except `snapshot_day`, plus counts.

    Built once, inside `fetch` -- never rebuilt by `settle` or `rows`, because the geometry repair
    this carries (`support.repair_perimeter_geometries_to_wkb`) is a real DuckDB round trip over
    ~23 MB of polygon and must be paid exactly once per turn.
    """

    rows: tuple[dict[str, object], ...]
    fetched_at: datetime
    perimeters_seen: int
    rejected: int
    collapsed: int


def _bbox(envelope: RegionEnvelope) -> str:
    """The region's camera envelope as an ArcGIS `bbox` value, e.g. `-125,42,-111,49`."""
    return ",".join(format_javascript_number(value) for value in (envelope.west, envelope.south, envelope.east, envelope.north))


def _support_digest(bbox: str) -> str:
    """Binds a checkpoint to the extent the one unit was planned over (`SourceRequest.support_digest`)."""
    return hashlib.sha256(bbox.encode("utf-8")).hexdigest()


def _named_day(perimeter: Mapping[str, object]) -> date | None:
    """The incident's own date: the first of `_OBSERVATION_DAY_KEYS` present, as its string prefix.

    THE FIRST TEN CHARACTERS, NEVER A PARSED INSTANT -- the same rule
    `pipeline/direct/fire_perimeters/rows.py::publisher_named_day` restates from
    `geo.feature_observation_day`, for the reason that function's own comment gives: an
    instant-based conversion moved 6,279 of 16,743 production water-gauge rows onto the day after
    the one they name. WFIGS timestamps are rendered UTC (`ingest.wfigs.epoch_milliseconds_to_iso`),
    so the two agree here, and the string prefix is still what is implemented.
    """
    for key in _OBSERVATION_DAY_KEYS:
        value = perimeter.get(key)
        if isinstance(value, str) and value:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
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
    canonical = "\n".join(
        json.dumps(
            {key: value for key, value in row.items() if key not in _DIGEST_EXCLUDED_COLUMNS},
            sort_keys=True,
            default=str,
        )
        for row in sorted(rows, key=lambda row: str(row["unique_fire_identifier"]))
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _snapshot_payload(responses: Sequence[SourceResponse]) -> FirePerimeterSnapshot | None:
    """The one unit's conformed snapshot, or `None` when no response carried one."""
    for response in responses:
        if isinstance(response.payload, FirePerimeterSnapshot):
            return response.payload
    return None


@dataclass(frozen=True)
class WfigsFirePerimetersStrategy:
    """One `_Current` snapshot per turn: `probe_edge` decides whether one is owed; `fetch` publishes it."""

    async def probe_edge(self, client: ProviderClient, window: ProbeWindow) -> ProviderEdge:
        """Read `editingInfo.lastEditDate` off the cheap layer resource; classify every way it can fail.

        ONE small GET, never the ~11 MB `_Current` walk: this is the probe-time saving `plan_requests`
        /`fetch` do not need to re-earn. The five answers:

        - a transport failure after the client's own retries -> `unavailable`;
        - a 429 / provider throttle -> `deferred`;
        - a non-JSON body, a body with no readable object, or an `editingInfo.lastEditDate` that is
          missing, null or not a finite epoch-millisecond number -> `invalid`;
        - editor tracking disabled (`lastEditDate == 0`, `_NO_TRACKED_EDITS`) -> `blind`: answered,
          but names no edit at all;
        - a readable instant -> `ok`, with that instant's UTC date as the one valued day.
        """
        try:
            response = await client.get(LAYER_ENDPOINT, {"f": "json"}, probe=True)
        except SourceThrottledError as error:
            return ProviderEdge(status="deferred", window=window, detail=str(error))
        except SourceUnavailableError as error:
            return ProviderEdge(status="unavailable", window=window, detail=str(error))
        try:
            payload = json.loads(response.body)
        except ValueError as error:
            return ProviderEdge(status="invalid", window=window, detail=f"the layer resource did not answer JSON: {error}")
        if not isinstance(payload, dict) or "error" in payload:
            return ProviderEdge(
                status="invalid", window=window, detail=f"the layer resource answered an error body: {str(payload)[:200]}"
            )
        editing_info = payload.get("editingInfo")
        last_edit = editing_info.get("lastEditDate") if isinstance(editing_info, dict) else None
        if last_edit == _NO_TRACKED_EDITS:
            return ProviderEdge(status="blind", window=window, detail="editingInfo.lastEditDate reports no tracked edit")
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
        body = json.dumps(
            {
                "perimeters_seen": snapshot.perimeters_seen,
                "rows_conformed": len(snapshot.rows),
                "rejected": snapshot.rejected,
                "collapsed": snapshot.collapsed,
                "fetched_at": fetched_at.isoformat(),
            },
            sort_keys=True,
        ).encode("utf-8")
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
        """Stamp the conformed population with `day` (the probed watermark day) and build the base rung."""
        snapshot = _snapshot_payload(responses)
        if snapshot is None:
            raise FirePerimetersSnapshotTruncatedError("rows() was called with no conformed snapshot in the response")
        records = [{**row, "snapshot_day": day} for row in snapshot.rows]
        table = pa.Table.from_pylist(records, schema=FIRE_PERIMETERS_SCHEMA.arrow_schema)
        return table.sort_by([(column, "ascending") for column in FIRE_PERIMETERS_GRAIN])


STRATEGY: Final = WfigsFirePerimetersStrategy()

__all__ = [
    "DIRECT_FEATURE_ID_PREFIX",
    "LAYER_ENDPOINT",
    "PUBLISHED_STATUS",
    "QUERY_ENDPOINT",
    "SNAPSHOT_UNIT",
    "FirePerimeterSnapshot",
    "FirePerimetersSnapshotTruncatedError",
    "STRATEGY",
    "WfigsFirePerimetersStrategy",
    "content_digest",
]
