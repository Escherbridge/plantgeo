"""`fire-perimeters-direct-forward`: one WFIGS `_Current` snapshot per turn, probed before it is fetched.

Strategy key `fire_perimeters.wfigs` (spec S14, §7). DARK: the shipped lane TOML still carries
`executor = "legacy"` (`lanes/fire-perimeters-direct-forward.toml`), so
`pipeline/direct/fire_perimeters/forward.py` is what the executor actually drives today, hourly at
`:10` (`pipeline/direct/AGENTS.md:1728`). This module is authored and reviewed ahead of the G6
cut-over (plan Phase 5); nothing here runs in production until that flip.

WHY THIS STRATEGY IS SMALLER THAN THE LEGACY PACKAGE IT WILL REPLACE, AND WHERE THAT COSTS SOMETHING
(H1, re-read after ring review). The legacy `pipeline/direct/fire_perimeters/watermark.py`
re-implements, by hand, exactly what the generic runner does for every `static_lookup` lane on a
turn that REACHES `settle`: S11's rewrite rule ("write when ... the source receipt digest changed")
replaces `watermark.py`'s own `PublishedLadder`/`read_published_version`/row-count short-circuit.
`probe_edge` answers ONE cheap question (what does WFIGS' own `editingInfo.lastEditDate` say right
now) and `settle`'s `Written.source_digest` is the S11 input the runner compares against the last
RECEIPT, not against a re-read partition.

BUT S11 IS NEVER CONSULTED ON A FORWARD TURN THE DAY-LEVEL GATE ALREADY CALLS CURRENT, AND THAT GATE
IS COARSER THAN THE INSTANT THE LEGACY WATERMARK COMPARED. `pipeline/runner/turn.py::_static_forward`
decides `current = all(day >= watermark for day in served.values())` -- a bare `date` comparison --
and returns before `plan_requests`/`fetch`/`settle` ever run when it is. If WFIGS reports a SECOND,
genuinely different edit on the SAME UTC day `editingInfo.lastEditDate` already named (an active fire
updates its perimeter more than once before midnight UTC), `probe_edge` reports the identical
`valued_days` both times, the gate reads "already current", and the second edit's content is masked
until the UTC day rolls over -- for up to ~24 h on an hourly cron, regardless of what S11 would have
decided had it been reached. This is a property of the runner's generic `static_lookup` path, not
something this strategy or its TOML can fix; it is the exact gap `docs/lanes/watersheds.md` §8 item 1
already named for the sibling static-lookup lane this track ships beside, and it is pinned here as
the same class of residual (owner register row `static_freshness_contract`) rather than quietly
inherited. See `docs/lanes/fire-perimeters.md` §8 and `AGENTS.md`'s "Known residuals (H1/H2/H3)" for
the full writeup and `tests/lanes/fire_perimeters/test_wfigs.py`'s same-UTC-day pinning test for the
mechanism made concrete at this module's own boundary (`probe_edge`'s day-granularity return value).

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

COMPARE PARITY WITH THE LEGACY WRITER IS NOT ACHIEVABLE TODAY (H2, re-read after ring review). An
earlier revision of this module's own report claimed `--mode forward --compare` would show zero
unexplained delta against the legacy writer because `feature_id`/`status`/`data_available_at` are
kept byte-identical; that claim is WRONG and is corrected rather than repeated here. Two separate
problems survive those three constants matching: (1) a full-row digest still includes `updated_at`,
which is each writer's OWN capture instant, so even an identical population diffs on every row
between any two independent fetches; (2) the two writers resolve DIFFERENT days for the same version
-- this strategy's day comes from `editingInfo.lastEditDate` (§7's named watermark), while the legacy
writer's comes from `source.fetched_at.date()` (the instant ITS turn happened to run), and those two
clocks need not agree, especially across a UTC-midnight boundary. Compare parity REQUIRES a contract
fix neither writer owns alone (a volatile-column-aware compare plus day alignment) and MUST NOT be
attempted for this lane before that fix lands; see `AGENTS.md`'s "Known residuals (H1/H2/H3)".

THE WATERMARK FIELD ITSELF HAS NEVER BEEN READ IN PRODUCTION (H3). `lanes/_providers/nifc-wfigs.toml`
states this is unverified against the live service. If `editingInfo.lastEditDate` turns out not to
behave as assumed here, `probe_edge` answers `invalid`/`blind` forever, the gate holds the window
every turn (O-R3-1: "a probe that cannot answer never fails the run"), and the lane exits 0 while
publishing nothing, permanently, with no alarm distinguishing that from a genuinely quiet WFIGS day.
See `docs/lanes/fire-perimeters.md` §8 for the G5-blocking live-probe checklist item and the
content-digest fallback this degrades to if the field proves unusable.

TWO SMALL, DELIBERATE DIVERGENCES FROM THE LEGACY CASTING RULES, BOTH ARGUED UNREACHABLE RATHER THAN
FIXED (L1, re-read after ring review). `_named_day` continues to the NEXT key when a key's value is
an empty string, where `pipeline/direct/fire_perimeters/rows.py::publisher_named_day` STOPS the
chain on any string, empty included (its own docstring: "A KEY PRESENT-BUT-UNUSABLE STOPS THE CHAIN;
ONLY AN ABSENT ONE CONTINUES IT"). And this module has no casting helpers that raise on an
unexpected type, where legacy's `_optional_text`/`_optional_float` do. Neither divergence is fixed
because neither can be exercised: legacy's helpers read PostgreSQL's untyped-at-rest jsonb document,
where an empty string or a wrong-shaped value are real possibilities worth stopping for; this
module's `perimeter` dicts come from `ingest.wfigs.parse_perimeter_collection`, which already runs
every field through `ingest.arcgis.optional_text`/`optional_number`/`epoch_milliseconds_to_iso` --
none of which can emit an empty string instead of `None`, and all of which already coerce to the
declared type rather than passing a wrong one through. Restating legacy's guards here would be
dead code defending against a shape this module's own upstream producer cannot produce; if that
producer's contract ever widens, the right fix is at that producer, not a second guard here.
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
from agri_data_service.ingest.upstream_retry import BUSY_MESSAGE_PATTERN
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

#: Restored (L3, re-read after ring review): the trigger's `geometry_repaired: true` stamp, named
#: identically to `pipeline/direct/fire_perimeters/rows.py:71-73`'s `GEOMETRY_REPAIRED_KEY` /
#: `GEOMETRY_REPAIRED_AREA_CHANGE_KEY`. Carried on `FirePerimeterSnapshot.rows` so the fact is
#: computed and inspectable (`FirePerimeterSnapshot.repairs`, tested directly), but NOT a schema
#: column -- `rows()` below projects onto `FIRE_PERIMETERS_SCHEMA.column_names` before building the
#: Arrow table, exactly as the legacy `fire_perimeters_table` does, for the identical reason: the L1
#: schema is frozen. Unlike the legacy writer's terminal report and
#: `fire_perimeters_forward_geometry_repaired` stderr event, nothing in the generic runner's S5
#: report surfaces this per-incident detail today -- `AGENTS.md`'s "Known residuals (H1/H2/H3)" and
#: "What this slice did not port" name that gap rather than inventing a misleading home for it (e.g.
#: `Written.dropped_rows`, which means ROWS NEVER WRITTEN, not rows written-but-repaired).
GEOMETRY_REPAIRED_KEY: Final = "geometry_repaired"
GEOMETRY_REPAIRED_AREA_CHANGE_KEY: Final = "geometry_repaired_area_change"

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
    return hashlib.sha256(bbox.encode("utf-8")).hexdigest()


def _arcgis_error_message(error: Mapping[str, object]) -> str:
    """Extract one ArcGIS error body's message, exactly as `ingest.arcgis.parse_feature_collection` does.

    Restated rather than imported (that function raises on an error body; `probe_edge` needs the
    message text WITHOUT raising, to classify it against `BUSY_MESSAGE_PATTERN` first).
    """
    details = error.get("details")
    return str(error.get("message") or ("; ".join(details) if isinstance(details, list) else None) or "unknown")


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

    async def probe_edge(  # noqa: PLR0911 - one return per honest probe classification
        self, client: ProviderClient, window: ProbeWindow
    ) -> ProviderEdge:
        """Read `editingInfo.lastEditDate` off the cheap layer resource; classify every way it can fail.

        ONE small GET, never the ~11 MB `_Current` walk: this is the probe-time saving `plan_requests`
        /`fetch` do not need to re-earn. The answers THIS function returns directly:

        - a non-JSON body, a 200 body carrying an ArcGIS error that does NOT read as a busy
          throttle, a non-object body, or an `editingInfo.lastEditDate` that is missing, null or
          not a finite epoch-millisecond number -> `invalid`;
        - a 200 body carrying an ArcGIS error whose message matches
          `ingest.upstream_retry.BUSY_MESSAGE_PATTERN` ("too many requests|busy|try again") ->
          `deferred`: ArcGIS reports throttling INSIDE an HTTP 200 body
          (`lanes/_providers/nifc-wfigs.toml`'s own "busy HTTP-200 `error` body" note -- the exact
          production incident `BUSY_MESSAGE_PATTERN` exists for), so this can never raise
          `SourceThrottledError` and must be classified here instead of folded into `invalid`;
        - editor tracking disabled (`lastEditDate == 0`, `_NO_TRACKED_EDITS`) -> `blind`: answered,
          but names no edit at all;
        - a readable instant -> `ok`, with that instant's UTC date as the one valued day.

        A transport failure or an HTTP 429 (`SourceUnavailableError` / `SourceThrottledError`) is
        NOT caught here (M1, re-read after ring review): it PROPAGATES to
        `pipeline/runner/turn.py::_spend_probe`, which owns the Retry-After cooldown
        (`exceeds_ladder`/`hold_stated_wait`) for every probing strategy. Catching either exception
        in this function would quietly bypass that ladder and answer `unavailable`/`deferred`
        without ever persisting a stated wait.
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
        """Stamp the conformed population with `day` (the probed watermark day) and build the base rung.

        Projects onto `FIRE_PERIMETERS_SCHEMA.column_names` explicitly (L3, re-read after ring
        review) rather than splatting the whole row dict: `GEOMETRY_REPAIRED_KEY` /
        `GEOMETRY_REPAIRED_AREA_CHANGE_KEY` ride on `snapshot.rows` for `FirePerimeterSnapshot.repairs`
        but are not schema columns, exactly the same whitelist
        `pipeline/direct/fire_perimeters/rows.py::fire_perimeters_table` applies for the identical reason.
        """
        snapshot = _snapshot_payload(responses)
        if snapshot is None:
            raise FirePerimetersSnapshotTruncatedError("rows() was called with no conformed snapshot in the response")
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
    "FirePerimetersSnapshotTruncatedError",
    "GeometryRepair",
    "WfigsFirePerimetersStrategy",
    "content_digest",
]
