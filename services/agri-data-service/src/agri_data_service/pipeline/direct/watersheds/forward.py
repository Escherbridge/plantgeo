"""Publish the current NHDPlus_HR WBDHU12 snapshot directly, bypassing PostgreSQL entirely.

Replaced `pipeline/lanes/watersheds.py::export_watersheds_release` (the former `_fill_watersheds`
adapter) AND the Postgres watermark resolver that used to sit beside it, both of which read
`geo.features`; this module is what made them, `ingest/watersheds.py::run_watersheds_ingestion_job`
and the `postgres-watersheds` lane that ran it deletable, and ALL OF THEM WERE DELETED ON 2026-09-06
(`conductor/tracks/environmental_postgres_retirement_20260904/evidence/removal-packet-watersheds-evacuation-zones-20260906.md`).
Once nothing writes `geo.features` for this layer any more, a Postgres-backed watermark reads stale
or empty forever, so this writer computes its OWN watermark straight from the source's own
`loaddate` (`source.py`), never from Postgres.

BOTH REGISTRY FIELDS WERE SWAPPED ON 2026-09-06, in one edit, because neither could move alone:
`LANE_REGISTRY[WATERSHEDS_STREAM].adapter` is now a source-direct refusal naming this package, and
its `watermark` is `watermark.py`, which calls the same `source.py` fetch this module does. The
registered pair is therefore no longer a Postgres fallback to be careful of; the substitution below
survives only to spare this turn a second whole-lane fetch.

ONE FETCH, NOT THREE. `pipeline/parquet/gap_fill.py::_fill_static_day` brackets a static lane's
export -- reading the watermark before AND after the write, to catch a source change landing
between SELECT and PUT -- and pays for that with two extra reads of whatever the watermark resolver
reads. For every other static lane in this warehouse the watermark is one bounded SQL query. Here
it is the EXACT SAME ~9,400-basin, ~47-request NHDPlus_HR walk the export itself performs: WBD's
id-only query never returns `loaddate` (`source.py`'s module docstring), so there is no cheaper
attribute-only probe to read instead. Paying for that walk three times a turn -- against a national
reference layer measured to hold exactly ONE load day in its whole history
(`pipeline/parquet/lane_registry.py`'s `WATERSHEDS_STREAM` registration) -- would triple a
genuinely expensive fetch to close a race window this source has never been observed to open.

`_WatershedsSnapshotCache` fetches ONCE and hands the SAME `WatershedsSnapshotSource` to the
watermark resolver (called before and after the write, inside `_fill_static_day`) and to the write
adapter. `_fill_static_day`'s bracket check therefore always compares an instant against itself and
completes in exactly one attempt: correct on the interface's own contract (it really did check
whether the source's watermark moved between the two reads it took), just cheap, because both reads
are the same read. This is a deliberate, documented simplification, not an oversight -- a genuinely
independent second fetch belongs here the day an owner decides this source's cadence justifies its
cost, which the measured one-load-day history gives no reason to expect soon.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.parquet.lane_contract import newest_data_day, newest_marker_day, resolve_static_lane
from agri_data_service.ingest.mtbs import inline_bbox_value
from agri_data_service.ingest.policy import UNCONFIGURED_BBOX_REASON, resolve_bounded_bbox
from agri_data_service.pipeline.direct import (
    BBOX_UNCONFIGURED,
    LANE_DAY_OUTCOMES,
    NO_WINDOW,
    REFUSE_WHOLE_RELEASE,
    SKIP_AND_COUNT,
    SKIP_TURN_ON_UNCONFIGURED_BBOX,
    UNCHANGED,
    DirectWriterContract,
)
from agri_data_service.pipeline.direct.watersheds.adapter import (
    WATERSHEDS_DIRECT_KIND,
    DirectWatershedsAdapter,
    DirectWatershedsError,
)
from agri_data_service.pipeline.direct.watersheds.products import watersheds_lane_registration
from agri_data_service.pipeline.direct.watersheds.source import (
    WatershedsSnapshotSource,
    WatershedsSourceError,
    fetch_watersheds_snapshot,
)
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.gap_fill import (
    _lane_day_lock_key,
    fill_one_lane_day,
    postgres_lane_day_lock,
    unlocked_lane_day,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore, oldest_export_instant
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
    from agri_data_service.pipeline.parquet.lane_registry import LaneWatermarkResolver

WATERSHEDS_FORWARD_RUN_ID_PREFIX: Final = "watersheds-forward:"
#: A `static_lookup` lane publishes at most one version per turn; unlike drought/climate/soil's
#: window walks, there is no meaning to asking for more than one. Restated as a validated bound
#: rather than dropped outright, so the CLI stays shaped like every other `pipeline/direct/*`
#: forward writer (this track's brief: `python -m ... --max-days 1`).
WATERSHEDS_MAX_DAYS: Final = 1

#: What this writer promises about its own failure policy, CLI surface and reported words; see
#: `pipeline/direct/__init__.py` for the axes and `tests/direct/test_direct_writer_contract.py` for
#: the table all eleven are read as.
#:
#: IDENTICAL TO `fire_perimeters`' ON BOTH DEFECT AXES, which is the point worth recording: the
#: `rejected_basins: 0` this lane publishes beside `accepted_basins: 9396` counts an IDENTITY defect
#: (`source.py:_accept` -- no properties/geometry object, or an unparseable huc12), the same defect
#: fire-perimeters counts as `rejected` and also publishes. A HUC12 whose geometry converts to empty
#: refuses this whole population (`support.py:130`), the same defect fire-perimeters refuses on. The
#: two lanes never disagreed; their live data did.
#:
#: THE MISSING RETRY SERIES IS DECLARED, NOT OVERLOOKED. Ten of the eleven writers expose
#: `--retry-attempts`/`--retry-base-seconds`/`--retry-max-seconds`; this one fetches exactly once.
WRITER_CONTRACT: Final = DirectWriterContract(
    slug="watersheds",
    identity_defect=SKIP_AND_COUNT,
    geometry_defect=REFUSE_WHOLE_RELEASE,
    unconfigured_bbox=SKIP_TURN_ON_UNCONFIGURED_BBOX,
    turn_outcomes=LANE_DAY_OUTCOMES | {BBOX_UNCONFIGURED, UNCHANGED, NO_WINDOW},
    flags_absent_on_purpose={
        "--product": "one stream, WATERSHEDS_STREAM; see products.py. NHDPlus_HR WBDHU12 is one "
        "national basin index, so a product selector here could only ever take one value.",
        "--time-budget-seconds": "a `static_lookup` turn publishes AT MOST ONE version, so there is no "
        "multi-day walk for a clock to interrupt part-way. A budget here could only abort a single "
        "indivisible fetch-and-publish, turning a slow turn into a failed one for no gain.",
        "--retry-attempts": "one turn is one ~9,400-basin, ~47-request NHDPlus_HR walk, and the whole "
        "walk is the retry unit -- there is no cheaper attribute-only probe to re-poll (source.py). "
        "Against a national reference layer measured to hold exactly ONE load day in its entire "
        "history, a failed turn costs nothing that the next cron tick does not recover, so paying for "
        "the walk twice inside one turn buys a shorter recovery on a lane with no recovery pressure.",
        "--retry-base-seconds": "no retry series exists to space out; see `--retry-attempts`.",
        "--retry-max-seconds": "no retry series exists to cap; see `--retry-attempts`.",
        "--contention-timeout-seconds": "this writer never WAITS on the lane-day lock: a contended turn "
        "reports `contended` immediately and exits, so there is no wait for a timeout to bound. A knob "
        "accepted and then ignored is worse than an absent one.",
        "--max-records": "the population is every HUC12 in the extent; a cap would publish a partial "
        "national basin index that reads as a complete one.",
        "--max-records-per-day": "same reason as `--max-records`, and a version-stamped lane's unit is "
        "a snapshot rather than a day.",
    },
    policy_basis="`support.py` applies NO repair chain -- no ST_MakeValid, no CollectionExtract, no "
    "Multi -- unlike `drought/`, because this lane's own Postgres write path applied none either "
    "(`sql/ingest/insert_geometry_versions.sql:210` is a bare ST_GeomFromGeoJSON). Mirroring the bare "
    "conversion is what keeps a direct-fetched basin identical to the row it must reproduce. A repair "
    "chain belongs here the day a self-intersecting WBDHU12 polygon is actually measured.",
)


class WatershedsForwardConfigError(ValueError):
    """Raised when a turn is asked for a shape this lane's nature cannot support."""


@dataclass(frozen=True, slots=True)
class WatershedsForwardConfig:
    """Bound one turn: how many versions to consider (always exactly one), which extent, which run."""

    max_days: int = WATERSHEDS_MAX_DAYS
    bbox: str | None = None
    run_id: str | None = None
    today: date | None = None


@dataclass(slots=True)
class _WatershedsSnapshotCache:
    """Memoise the one NHDPlus_HR fetch this turn performs; see the module docstring, "One fetch, not three"."""

    bbox: str
    _snapshot: WatershedsSnapshotSource | None = field(default=None, init=False)

    async def load(self) -> WatershedsSnapshotSource:
        if self._snapshot is None:
            self._snapshot = await fetch_watersheds_snapshot(bbox=self.bbox)
        return self._snapshot


def emit(payload: Mapping[str, object]) -> None:
    """Write one stable JSON progress record to stderr, leaving stdout for the terminal report."""
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


def _validate_config(config: WatershedsForwardConfig) -> None:
    """Fail closed before a socket is ever opened."""
    if config.max_days != WATERSHEDS_MAX_DAYS:
        raise WatershedsForwardConfigError(
            f"--max-days must be exactly {WATERSHEDS_MAX_DAYS}; watersheds is a static_lookup lane and "
            "publishes at most one version per turn, so any other value asks for a shape this lane's "
            "nature cannot support"
        )


def _existing_coverage(store: ObjectStore) -> tuple[date | None, datetime | None, date | None]:
    """Read what the object store already holds at the base rung -- no network, no NHDPlus_HR call.

    Mirrors `pipeline/parquet/gap_fill.py::_static_lane_census`'s own whole-stream listing and
    `newest_data_day`/`newest_marker_day`/`oldest_export_instant` calls, scoped to this one lane.
    """
    listed = store.list_partition_objects(WATERSHEDS_STREAM, WATERSHEDS_DIRECT_KIND, LANE_BASE_ZOOM_TIER)
    keys = tuple(entry.relative_path for entry in listed)
    newest_day = newest_data_day(
        layer=WATERSHEDS_STREAM, kind=WATERSHEDS_DIRECT_KIND, zoom=LANE_BASE_ZOOM_TIER, keys=keys
    )
    newest_instant = (
        None
        if newest_day is None
        else oldest_export_instant(
            listed, layer=WATERSHEDS_STREAM, kind=WATERSHEDS_DIRECT_KIND, zoom=LANE_BASE_ZOOM_TIER, day=newest_day
        )
    )
    marker_day = newest_marker_day(
        layer=WATERSHEDS_STREAM, kind=WATERSHEDS_DIRECT_KIND, zoom=LANE_BASE_ZOOM_TIER, keys=keys
    )
    return newest_day, newest_instant, marker_day


def _direct_watermark_resolver(cache: _WatershedsSnapshotCache) -> LaneWatermarkResolver:
    """Build the closure `fill_one_lane_day` calls before and after the write, both hitting `cache`."""

    async def resolve(
        session: AsyncSession,  # noqa: ARG001 - uniform resolver shape; this lane's clock is the source, not Postgres
        store: ObjectStore,  # noqa: ARG001 - uniform resolver shape; the watermark never reads the object store
        *,
        today: date,  # noqa: ARG001 - the source's own change time, never this run's date
    ) -> SourceWatermark:
        snapshot = await cache.load()
        return snapshot.watermark

    return resolve


def _noop_report(
    run_id: str, *, published: bool, outcome: str, state: str | None, detail: str | None, **extra: object
) -> dict[str, object]:
    """Render a turn that published nothing, carrying BOTH the shared word and the static-lane state.

    `state` is a `StaticLaneState` and answers "where does this lane stand against its source
    watermark"; `outcome` is a `DIRECT_TURN_OUTCOMES` word and answers "what did this turn do". They
    are different questions, and until this key existed a monitor reading all eleven writers had to
    special-case the two static lanes because their no-op reports carried no `outcome` at all. Both
    are kept: the state is strictly more informative and the word is what generalises.
    """
    return {
        "status": "completed",
        "run_id": run_id,
        "outcome": outcome,
        "layer": WATERSHEDS_STREAM,
        "namespace": f"layer={WATERSHEDS_STREAM}/kind={WATERSHEDS_DIRECT_KIND}/",
        "published": published,
        "state": state,
        "detail": detail,
        **extra,
    }


async def run_watersheds_forward(config: WatershedsForwardConfig) -> dict[str, object]:
    """Publish the current NHDPlus_HR snapshot if the object store does not already hold its version.

    Never opens the object store at all when `INGEST_BBOX` is unconfigured -- the identical
    before-any-network no-op the deleted `ingest/watersheds.py::run_watersheds_ingestion_job`
    returned, and that `pipeline/direct/drought/forward.py`'s before-the-floor case still returns.
    """
    _validate_config(config)
    run_id = config.run_id or f"{WATERSHEDS_FORWARD_RUN_ID_PREFIX}{uuid.uuid4()}"
    today = config.today or datetime.now(UTC).date()
    bbox = resolve_bounded_bbox(config.bbox)
    if bbox is None:
        report = _noop_report(
            run_id,
            published=False,
            outcome=BBOX_UNCONFIGURED,
            state="watermark_unread",
            detail=UNCONFIGURED_BBOX_REASON,
        )
        emit({"event": "watersheds_forward_noop", **report})
        return report

    store = ObjectStore.from_settings()
    cache = _WatershedsSnapshotCache(bbox=bbox)
    try:
        snapshot = await cache.load()
    except WatershedsSourceError as error:
        raise DirectWatershedsError(f"watersheds snapshot fetch: {error}") from error

    newest_day, newest_instant, marker_day = _existing_coverage(store)
    verdict = resolve_static_lane(
        watermark=snapshot.watermark,
        newest_data_day=newest_day,
        newest_data_instant=newest_instant,
        newest_marker_day=marker_day,
        today=today,
    )
    if verdict.state != "stale":
        report = _noop_report(
            run_id,
            published=False,
            # `current` means the published version already matches the source watermark, which is
            # exactly what `UNCHANGED` names. `source_empty` and `watermark_unread` both mean there
            # is no version to publish at all, which is `NO_WINDOW`; the precise distinction between
            # them survives untouched in `state` beside it.
            outcome=UNCHANGED if verdict.state == "current" else NO_WINDOW,
            state=verdict.state,
            detail=verdict.detail,
            accepted_basins=len(snapshot.accepted),
            rejected_basins=snapshot.rejected_count,
        )
        emit({"event": "watersheds_forward_noop", **report})
        return report

    version_day = verdict.version_day
    if version_day is None:  # pragma: no cover - `resolve_static_lane` never pairs `stale` with `None`
        raise DirectWatershedsError("resolve_static_lane returned 'stale' with no version day; contract violation")

    direct_lane = replace(
        watersheds_lane_registration(),
        adapter=DirectWatershedsAdapter(fetch_source=cache.load),
        watermark=_direct_watermark_resolver(cache),
    )
    loader_database_url = settings.require_local_source_loader_database_url()
    async with (
        local_source_loader_session(loader_database_url) as session,
        postgres_lane_day_lock(session, _lane_day_lock_key(direct_lane, version_day)) as granted,
    ):
        if not granted:
            report = _noop_report(
                run_id,
                published=False,
                outcome="contended",
                state="contended",
                detail=f"{version_day.isoformat()}: another run holds this lane-day",
            )
            emit({"event": "watersheds_forward_contention", **report})
            return report
        outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
            session,
            store,
            direct_lane,
            day=version_day,
            run_id=run_id,
            now=lambda: datetime.now(UTC),
            today=today,
            lane_day_lock=unlocked_lane_day,
            extend_availability=False,
        )

    if outcome not in {"written", "absent"}:
        raise DirectWatershedsError(
            f"watersheds {version_day.isoformat()} did not publish cleanly: outcome={outcome}, detail={detail}"
        )
    report = {
        "status": "completed",
        "run_id": run_id,
        "layer": WATERSHEDS_STREAM,
        "namespace": f"layer={WATERSHEDS_STREAM}/kind={WATERSHEDS_DIRECT_KIND}/",
        "published": outcome == "written",
        "version_day": version_day.isoformat(),
        "outcome": outcome,
        "parts": parts,
        "rows_across_write": rows,
        "written_bytes": written_bytes,
        "accepted_basins": len(snapshot.accepted),
        "rejected_basins": snapshot.rejected_count,
        "watermark_basis": snapshot.watermark.basis,
        "detail": detail,
    }
    emit({"event": "watersheds_forward_release_complete", "run_id": run_id, **report})
    return report


def parser() -> argparse.ArgumentParser:
    """Build the bounded, forward-only watersheds lane operator. No `--product`: this lane has one."""
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--max-days", type=int, default=WATERSHEDS_MAX_DAYS)
    built.add_argument("--bbox", default=None, help="Override INGEST_BBOX for this turn only.")
    built.add_argument("--run-id", default=None)
    return built


def parse_args(argv: Sequence[str] | None = None) -> WatershedsForwardConfig:
    """Validate every operator input at the boundary and hand back one bounded turn.

    `inline_bbox_value` rewrites `--bbox -125,42,...` to `--bbox=-125,42,...` before argparse ever
    sees it, matching `ingest/mtbs.py::main` and `burn_severity/forward.py`. Without it argparse
    reads the leading `-125` as a second flag rather than this option's value and the documented
    operator command dies with "argument --bbox: expected one argument". The helper lives in
    `ingest/mtbs.py` because that is where the trap was first paid for; every `--bbox` writer in
    `pipeline/direct` imports the one implementation rather than restating the rewrite.
    """
    raw = list(argv) if argv is not None else sys.argv[1:]
    built = parser()
    arguments = built.parse_args(inline_bbox_value(raw))
    config = WatershedsForwardConfig(max_days=arguments.max_days, bbox=arguments.bbox, run_id=arguments.run_id)
    try:
        _validate_config(config)
    except WatershedsForwardConfigError as error:
        built.error(str(error))
    return config


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded turn and emit exactly one terminal report on stdout."""
    config = parse_args(argv)
    try:
        report = await run_watersheds_forward(config)
    except Exception as error:  # the one terminal failure report a caller parses
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


__all__ = [
    "WATERSHEDS_FORWARD_RUN_ID_PREFIX",
    "WATERSHEDS_MAX_DAYS",
    "WatershedsForwardConfig",
    "WatershedsForwardConfigError",
    "main",
    "parse_args",
    "parser",
    "run_watersheds_forward",
]
