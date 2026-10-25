"""`watersheds-direct-forward`: NHDPlus HR WBDHU12, the whole configured extent as one version.

Strategy key `watersheds.nhdplus` (spec S14, S4.2; track `config_driven_ingestion_20260926`, spec
S7 row for `watersheds-direct-forward`). Wraps `pipeline/lanes/watersheds/{source,watermark}.py`'s
existing fetch/watermark surface (landed by `p4-extract`) in the runner contract
(`pipeline/runner/contract.py`); see `pipeline/lanes/watersheds/AGENTS.md` for the import rule
this leaf answers to, and `pipeline/lanes/AGENTS.md` "Extracted source leaves are NOT strategies"
for why this behaviour lives in its own module rather than on `source.py`/`watermark.py`.

WAVE O, FETCHES BYPASS THE GOVERNED PROVIDER CLIENT ON PURPOSE. NHDPlus_HR's own query shape is
two-phase and dynamically sized -- an id-only phase, then geometry fetched in batches of 200,
addressed by named OBJECTID rather than offset (`docs/lanes/watersheds.md` "Query mechanism") --
so the real batch count is not knowable until the id phase has already answered. `plan_requests`
is synchronous (the Protocol forbids I/O there), so it cannot pre-plan one `SourceRequest` per
batch without a network call it is not allowed to make. This strategy therefore plans exactly ONE
unit per turn and fetches it through `source.py`'s own `upstream_client`-backed
`fetch_watersheds_snapshot`, which already owns that whole walk -- matching
`pipeline/direct/watersheds/forward.py`'s identical choice for the legacy writer ("one turn is one
~9,400-basin, ~47-request NHDPlus_HR walk, and the whole walk is the retry unit"). This is the
allowed alternative Wave O names: "fetches only through the provider client OR the existing source
module's upstream_client". `usgs-nhdplus` is `weighted = false`, so the runner's own budget ledger
(`requests`/`weighted_calls`) only ever counts this ONE logical unit; the real ~47-request network
cost is invisible to it, by design, because it never goes through `BudgetedClient`.

TWO FULL WALKS ON A STALE TURN, NOT ONE. `pipeline/runner/turn.py::_Turn._static_forward` always
calls `probe_edge` first; only when it reports a watermark NEWER than what is served does it call
`plan_requests`/`fetch`. Both paths pay for the SAME ~9,400-basin walk, because there is no cheaper
attribute-only watermark probe (`source.py`'s own module docstring: WBD's id-only query never
returns `loaddate`). Unlike the legacy writer's `_WatershedsSnapshotCache` (which shares one fetch
across its own before-and-after watermark bracket inside ONE write), this strategy does NOT try to
share a fetch between `probe_edge` and `fetch`: those are two different runner phases, called on a
process-wide `STRATEGY` singleton, and a per-turn cache on a singleton risks leaking a stale
snapshot into a later, unrelated turn. The measured cost of being wrong about that risk is zero
today -- this layer has exactly ONE load day in its entire history
(`docs/lanes/watersheds.md` section 2) -- so the doubled walk is paid at most once a day, and only
on the rare day WBD actually republishes.

A SINGLE FLAKY BATCH CAN CANCEL AND RESTART THE WHOLE WALK, NOT JUST THAT BATCH.
`pipeline/runner/fetch.py::UnitFetcher.fetch_unit` wraps this strategy's ENTIRE ~47-batch `fetch()`
call in one `asyncio.wait_for(..., timeout=min(PER_UNIT_FETCH_CAP_SECONDS, remaining))`
(`fetch.py:243`; the cap itself is `fetch.py:68`, `PER_UNIT_FETCH_CAP_SECONDS = 330.0`), because the
runner treats this strategy's one planned unit as the retry grain -- the same choice
`pipeline/direct/watersheds/forward.py` makes for its own retry series (see "ONE REQUEST, WHOLE
WALK" above). Every one of the ~47 sequential batches inside that one `fetch()` call already
carries its OWN `ingest/http.py::fetch_bounded` transport-retry series
(`TRANSPORT_RETRY_ATTEMPTS = 3` at up to that page's own timeout, `WBDHU12_BOUNDS` = 120 s here) --
so a SINGLE flaky batch can, by itself, push the whole `fetch()` call past 330 s. When that
happens, the runner's `wait_for` CANCELS the coroutine mid-walk, discarding every batch already
fetched, and converts the timeout into `UpstreamTimeoutError`. The runner's own OUTER ladder then
retries the WHOLE unit -- not the one slow batch -- from batch 1, up to
`SERVER_ERROR_ATTEMPTS = 3` (`fetch.py:56`) attempts total before giving up as
`upstream_unavailable`. THIS BITES ONLY ON THE FETCH/REPUBLISH PATH: `probe_edge` is never wrapped
in `asyncio.wait_for` at all (`turn.py::_Turn._spend_probe` has no such cap), so a slow probe just
runs long, uncapped; a slow FETCH gets cancelled and restarted from zero, up to three times.
`lanes/watersheds-direct-forward.toml`'s `turn_timeout_seconds` is sized to survive one full
cancel-retry cycle on top of an estimated probe walk; see that TOML's own `[budget]` comment for
the arithmetic. The structural fix -- a per-lane override of `PER_UNIT_FETCH_CAP_SECONDS` for a
strategy whose own planned unit is known to be this heavy -- belongs at the contract level
(`foundation/lane_config/models.py::LaneBudget`), not as a workaround in this lane's TOML; track it
under the owner's cross-lane `static_freshness_contract` row once one exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
import structlog

from agri_data_service.ingest.policy import resolve_bounded_bbox
from agri_data_service.ingest.watersheds import WATERSHEDS_PROPERTY_SOURCE
from agri_data_service.pipeline.lanes._shared.digest import COMPACT_SEPARATORS, canonical_json_bytes, sha256_hex
from agri_data_service.pipeline.lanes.watersheds.geometry import (
    convert_watershed_geometries_to_wkb,
    watersheds_geometry_session,
)
from agri_data_service.pipeline.lanes.watersheds.source import (
    WatershedsSnapshotSource,
    WatershedsSourceError,
    fetch_watersheds_snapshot,
)
from agri_data_service.pipeline.lanes.watersheds.watermark import read_watersheds_source_watermark
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.pipeline.runner.contract import ProviderEdge, SourceRequest, SourceResponse, Unsettled, Written
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.pipeline.lanes.watersheds.source import WatershedRecord
    from agri_data_service.pipeline.runner.contract import DayContext, ProbeWindow, ProviderClient, Settlement

logger = structlog.get_logger()

#: The provider endpoint this lane's `[source]` names (`lanes/_providers/usgs-nhdplus.toml`); used
#: only to label the one planned unit, since the actual send never goes through the provider client.
ENDPOINT: Final = "wbd-hu12-query"

#: MUST equal `pipeline/direct/watersheds/rows.py::DIRECT_FEATURE_ID_PREFIX`'s value exactly, or
#: the G6 `--compare` parity check sees a delta on every row: the legacy writer's already-published
#: rows carry this prefix. Not imported from that module -- strategies never import
#: `pipeline/direct/**` (see the module docstring) -- so keep this value synced by hand if that
#: module's prefix ever changes.
DIRECT_FEATURE_ID_PREFIX: Final = "direct"


def direct_feature_id(huc12: str) -> str:
    """The deterministic, non-UUID feature id this write carries in place of a real `geo.features.id`."""
    return f"{DIRECT_FEATURE_ID_PREFIX}:{huc12}"


def _snapshot_digest_body(accepted: Sequence[WatershedRecord]) -> bytes:
    """Canonical per-basin fact tuple, sorted by huc12: every scalar field plus a geometry fingerprint.

    Hashes each basin's geometry rather than embedding it: the whole extent's raw GeoJSON is
    roughly 500 MB (9,396 rows x the ~56,780 B/row measured in `docs/lanes/watersheds.md` section
    5, trap 7), and this body only needs to CHANGE when the geometry changes, never to reproduce
    it -- `rows()` reads the real geometry straight from `responses[0].payload`, never from here.
    """
    facts = [
        (
            record.huc12,
            record.name,
            record.areasqkm,
            record.tohuc,
            record.states,
            record.hutype,
            None if record.observed_at is None else record.observed_at.isoformat(),
            sha256_hex(
                canonical_json_bytes(record.geometry, sort_keys=True, separators=COMPACT_SEPARATORS, allow_nan=True)
            ),
        )
        for record in sorted(accepted, key=lambda record: record.huc12)
    ]
    # The outer dump never sorted keys; `sort_keys=False` keeps the frozen digest bytes, never normalise it.
    return canonical_json_bytes(facts, sort_keys=False, separators=COMPACT_SEPARATORS, allow_nan=True)


@dataclass(frozen=True, slots=True)
class WatershedsNhdplusStrategy:
    """One snapshot unit per turn; `probe_edge` reads the same watermark `watermark.py` already answers."""

    async def probe_edge(self, client: ProviderClient, window: ProbeWindow) -> ProviderEdge:
        """The source watermark day is the one valued day: the version a static lookup owes is dated there."""
        del client  # the fetch goes through source.py's own upstream_client; see the module docstring
        try:
            watermark = await read_watersheds_source_watermark()
        except WatershedsSourceError as error:
            # Basins exist but carry no parseable vintage at all: a genuine data-integrity fault, not
            # merely "could not ask" -- `invalid` names that distinction `unavailable` would blur.
            return ProviderEdge(status="invalid", window=window, detail=str(error))
        valued_days = frozenset() if watermark.day is None else frozenset({watermark.day})
        return ProviderEdge(status="ok", window=window, valued_days=valued_days, detail=watermark.basis)

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:  # noqa: ARG002 - the Protocol's lane and region
        """The one snapshot unit covering every day asked (always exactly one: the watermark day)."""
        bbox = resolve_bounded_bbox(None)
        if bbox is None:
            # `probe_edge` already succeeded this same turn with a resolved bbox (`_static_forward`
            # never reaches `plan_requests` otherwise), so this branch is not expected to run in
            # practice; it is kept so an unset INGEST_BBOX reads as `strategy_error` rather than a
            # raised exception that would abort the whole turn.
            return []
        return [
            SourceRequest(
                unit=f"snapshot:{bbox}",
                endpoint=ENDPOINT,
                days=tuple(days),
                parameters=(("bbox", bbox),),
            )
        ]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """ONE whole-extent fetch, through `source.py`'s own upstream_client (see the module docstring)."""
        del client
        bbox = request.query()["bbox"]
        snapshot = await fetch_watersheds_snapshot(bbox=bbox)
        if snapshot.rejected_count:
            logger.warning(
                "watersheds_nhdplus_features_rejected",
                rejected=snapshot.rejected_count,
                accepted=len(snapshot.accepted),
            )
        return SourceResponse(
            request=request,
            body=_snapshot_digest_body(snapshot.accepted),
            retrieved_at=snapshot.fetched_at,
            payload=snapshot,
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:  # noqa: ARG002 - the Protocol's day and context
        """Every accepted basin is present; identity-level rejects are counted, never refused (SKIP_AND_COUNT)."""
        if not responses:
            return Unsettled("upstream_unavailable", "no response for the watermark day's snapshot unit")
        snapshot = responses[0].payload
        if not isinstance(snapshot, WatershedsSnapshotSource):
            return Unsettled("strategy_error", f"fetch returned {type(snapshot).__name__}, not a watershed snapshot")
        accepted = snapshot.accepted
        if not accepted:
            # `probe_edge` already proved a watermark day with rows (`_static_forward` would not have
            # reached here otherwise); an empty read of it now is a FAILED READ to retry, never a
            # settled absence -- the identical asymmetry `foundation/parquet/lane_contract.py::
            # resolve_static_lane` documents for the legacy writer.
            return Unsettled(
                "upstream_unavailable",
                "the snapshot fetch returned no accepted basin, but the probe already proved a non-empty watermark",
            )
        dropped = {"rejected_feature": snapshot.rejected_count} if snapshot.rejected_count else {}
        return Written(expected_units=len(accepted), present_units=len(accepted), dropped_rows=dropped)

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """One row per accepted basin, WKB-converted through DuckDB spatial, stamped with this version day."""
        snapshot = responses[0].payload
        if not isinstance(snapshot, WatershedsSnapshotSource):
            raise TypeError(f"fetch returned {type(snapshot).__name__}, not a watershed snapshot")
        accepted = snapshot.accepted
        with watersheds_geometry_session() as session:
            wkb_by_huc12 = convert_watershed_geometries_to_wkb(
                session, [(record.huc12, record.geometry) for record in accepted]
            )
        table = pa.Table.from_pylist(
            [
                {
                    "huc12": record.huc12,
                    "name": record.name,
                    "areasqkm": record.areasqkm,
                    "tohuc": record.tohuc,
                    "states": record.states,
                    "hutype": record.hutype,
                    "source": WATERSHEDS_PROPERTY_SOURCE,
                    "observed_at": record.observed_at,
                    "data_available_at": None,
                    "release_day": day,
                    "feature_id": direct_feature_id(record.huc12),
                    "geom": wkb_by_huc12[record.huc12],
                }
                for record in accepted
            ],
            schema=WATERSHEDS_SCHEMA.arrow_schema,
        )
        return conform_to_stream_schema(table, WATERSHEDS_SCHEMA)


STRATEGY: Final = WatershedsNhdplusStrategy()

__all__ = [
    "DIRECT_FEATURE_ID_PREFIX",
    "ENDPOINT",
    "STRATEGY",
    "WatershedsNhdplusStrategy",
    "direct_feature_id",
]
