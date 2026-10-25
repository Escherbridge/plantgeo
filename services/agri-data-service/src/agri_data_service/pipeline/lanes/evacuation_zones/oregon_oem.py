"""The config-lane strategy (S14) for evacuation-zones: a `static_lookup` watermarked by CONTENT.

`evacuation-zones-direct-forward.toml` names `"evacuation_zones.oregon_oem"`. This module is the
runner-side successor of `pipeline/direct/evacuation_zones/forward.py` -- it reads the SAME landed
leaves (`watermark.py`, `rows.py`, `source.py`, `products.py`) p4-extract moved here, over the
`pipeline/runner/contract.py` Protocols instead of the legacy `fill_one_lane_day` driver.

## Why `probe_edge` does the whole statewide capture

Every other `static_lookup` this track ships (watersheds' `max(loaddate)`, fire-perimeters'
`editingInfo.lastEditDate`) reads one cheap metadata field as its watermark, then `fetch`s the full
population only once the runner decides a new version is owed. Oregon OEM publishes no such field:
`created_date` never moves when a level is raised, and `last_edited_date` is re-stamped on unchanged
areas every few minutes (`watermark.py` module docstring; `rows.py` module docstring). The only
honest change test is a CONTENT DIGEST of the whole captured population, which means deciding the
watermark here REQUIRES the full capture `fetch` would otherwise make. `probe_edge` therefore does
that capture, decides the day from it (sticky at the published day when the digest agrees, otherwise
today's UTC capture date -- the same rule `forward.py` inlines rather than `watermark.py`'s
`watermark_for_capture`, see "Why this diverges from `watermark.py`'s `day=None`" below), and caches
it on `self._captured` so `fetch` (called only when the runner decides a new version is owed) never
re-asks Oregon a second time for the same statewide state a few seconds later -- the exact "two
captures of a fast-moving multi-fire event minutes apart" race `forward.py`'s own docstring explains
why a version's publish attempts never refetch.

## Why `fetch` never calls `client.get()`

Oregon OEM's real contract is `source.py::fetch_evacuation_zones_snapshot`'s adaptive, byte- and
page-bounded, oversized-record-governed walk (`ingest/evacuation_zones.py::fetch_evacuation_zones`),
already GL-2 metered through `ingest/http.py::upstream_client` -- not a single `SourceRequest` the
generic `ProviderClient.get(endpoint, parameters)` one-logical-attempt shape can express.
`source.py`'s own module docstring names the reason not to re-derive that walk: "Re-implementing it
would fork the 64 MiB per-run transfer budget, the 20-page ceiling and the `INGEST_MAX_SOURCE_RECORDS`
record ceiling." This strategy therefore calls the landed, already-metered function directly. The
residual cost named in `lanes/evacuation-zones-direct-forward.toml`'s `[budget]` comment: the
runner's own per-turn request ledger (`weighted_calls`/`requests`/`fetch_attempts` in the S5 report)
does not see these sends, because they never pass through the `BudgetedClient` wrapping `client`.
Oregon OEM is unweighted and keyless (no dollar budget at risk); `source.py`'s own ceilings are the
real cap. `client` is accepted on every Protocol method (and briefly handed to a restored checkpoint
replay, which this strategy also never reaches -- see "Checkpoints" below) purely to satisfy the
`IngestStrategy`/`EdgeProbingStrategy` Protocol shapes; it is never called.

## Why this diverges from `watermark.py`'s `day=None`

`watermark.py::watermark_for_capture` answers an EMPTY capture with `day=None` ("source_empty"),
because that module backs the legacy, absence-blind `resolve_static_lane` census, which predates
`Absent` settlements and treats a watermark with no day as a failed read to retry (its own docstring,
point 1). This strategy is the WRITE path, and the runner's `Absent` Settlement (`contract.py`) is
exactly the governed-absence machinery a quiet statewide day needs: a day the capture returns zero
areas for is a VALUED day (today's UTC capture date, or the published day when it repeats a quiet
spell already recorded), never a blind one. `forward.py` already made this same choice inline
(`version_day = source.fetched_at.date()` unconditionally, never routed through
`watermark_for_capture`); this module restates that choice because
`pipeline/lanes/evacuation_zones/**` may not import `pipeline/direct/**` (this package's `AGENTS.md`).

## How the quiet-day and ladder-absence semantics land on THIS runner's absence machinery

`settle()` returns `Absent(reason=..., proof=...)` when the capture holds zero areas; it does nothing
else. The ladder write (every zoom tier, coarse rungs first, base last) is `writer.py::ObjectStoreLaneWriter.write_absence`
-> `pipeline/parquet/derivation.py::govern_day_absent`, not this package -- the generic runner writes
the WHOLE ladder for every `Absent` settlement, so this strategy needs no `_ABSENCE_LADDER_TIERS`
loop of its own (contrast `pipeline/direct/evacuation_zones/adapter.py::_govern_quiet_day`, which has
to do that by hand because the legacy driver has no generic absence writer). The "same-day
stand-down" refusal (`test_evacuation_zones_direct_adapter.py::
test_a_same_day_stand_down_is_refused_rather_than_deleting_a_published_snapshot`) is likewise generic:
`writer.py::_decide_unverified_rewrite`'s `Absent` branch refuses whenever `census.status(stream, day)
== "data"`, returning `"data_never_retracted_by_absence"` and never writing -- this strategy does not
re-implement that pre-flight at all; `turn.py::_Turn._write_absence` logs
`plantgeo_lane_turn_absence_over_data_refused` for exactly that case. A quiet day that repeats an
already-governed absence reads `"absence_unchanged"` the same way, also for free.

## Checkpoints

This strategy's `SourceRequest` carries no `requested_parameters`, so `SourceResponse.checkpoint_eligible`
is always `False` (`contract.py`: `bool(requested) and requested <= complete_parameters`, and
`bool(frozenset())` is `False`). `checkpoints.py::TurnCheckpoints.retain` therefore never persists a
capture, and `.restore` never replays one -- deliberately: a replayed capture would resurrect a stale
digest decision for a lane whose only correctness property is "the version decided this turn is the
state this turn actually saw."
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from agri_data_service.pipeline.lanes.evacuation_zones.products import bbox_unconfigured_reason, resolve_coverage_bbox
from agri_data_service.pipeline.lanes.evacuation_zones.rows import (
    apply_carried_forward_updated_at,
    content_digest,
    evacuation_zones_table,
    row_content_digests,
)
from agri_data_service.pipeline.lanes.evacuation_zones.source import (
    EvacuationZonesSourceError,
    EvacuationZonesTruncatedError,
    fetch_evacuation_zones_snapshot,
)
from agri_data_service.pipeline.lanes.evacuation_zones.watermark import (
    EvacuationZonesWatermarkError,
    read_published_snapshot,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.runner.contract import Absent, ProviderEdge, SourceRequest, SourceResponse, Written

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date, datetime

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.pipeline.lanes.evacuation_zones.source import EvacuationZonesSource
    from agri_data_service.pipeline.lanes.evacuation_zones.watermark import PublishedSnapshot
    from agri_data_service.pipeline.runner.contract import DayContext, ProbeWindow, ProviderClient, Settlement

#: The one provider endpoint this strategy's `SourceRequest`s NAME (`lanes/_providers/oregon-oem.toml`
#: `[endpoints.query]`), required so `SourceRequest.credential_free_url`/the checkpoint identity can
#: resolve it -- never actually sent through; see the module docstring "Why `fetch` never calls
#: `client.get()`".
ENDPOINT: Final = "query"
#: One logical unit per turn: Oregon publishes one statewide current-state set, never a chunked one.
UNIT: Final = "statewide-snapshot"

#: This strategy's OWN capture retry series: the real production turn (contrast
#: `watermark.py::WATERMARK_RETRY_*`, which is the lighter read-only census path's). Restated rather
#: than imported from `pipeline/direct/evacuation_zones/forward.py::EVACUATION_ZONES_DEFAULT_RETRY_*`
#: because this package may not import `pipeline/direct/**` (`AGENTS.md`); the values are the same.
#:
#: BUDGET DEPENDENCY: this series runs entirely inside one `fetch()`/`probe_edge()` call, and
#: `fetch()`'s is wrapped in a hard `asyncio.wait_for` by the runner
#: (`pipeline/runner/fetch.py::UnitFetcher.fetch_unit`, `timeout = min(PER_UNIT_FETCH_CAP_SECONDS,
#: remaining-of-the-turn's-fetch-share)`) -- a series that does not fit inside that window is
#: cancelled mid-retry and then RETRIED WHOLESALE by the runner's own outer ladder, restarting this
#: series from attempt 1 every time ("retry-of-retries"). See
#: `lanes/evacuation-zones-direct-forward.toml`'s `[budget]` comment for the arithmetic that sizes
#: `turn_timeout_seconds` to this series with margin; the two must be re-derived together.
CAPTURE_RETRY_ATTEMPTS: Final = 5
CAPTURE_RETRY_BASE_SECONDS: Final = 5.0
CAPTURE_RETRY_MAX_SECONDS: Final = 60.0

#: How much of a digest a basis string quotes; mirrors `watermark.py::_DIGEST_EXCERPT`.
_DIGEST_EXCERPT: Final = 12


class OregonOemStrategyError(RuntimeError):
    """Raised when this strategy is asked to fetch with no bbox configured and no cached capture,
    when a captured watermark is stamped behind an already-published version, or when a
    `SourceResponse.payload` is not the `_ProbedCapture` this module itself always produces."""


@dataclass(slots=True)
class _ProbedCapture:
    """One statewide capture, its content digest, and what was published when it was taken."""

    source: EvacuationZonesSource
    digest: str
    published: PublishedSnapshot | None


def _require_capture(payload: object) -> _ProbedCapture:
    """Narrow `SourceResponse.payload` (typed `object` in `contract.py`) back to `_ProbedCapture`.

    `payload` is `object` by Protocol, so an unchecked `capture: _ProbedCapture = responses[0].payload`
    is an unsound downcast mypy strict flags at every call site (the watersheds-review pattern: an
    `isinstance` guard with a loud raise, never a cast). The only way this strategy's own `payload`
    is ever anything else is a caller (a checkpoint replay, a test) handing `settle`/`rows` a response
    this module did not build itself -- `turn.py::_settle`/`_tables_by_stream` catch any exception
    `settle`/`rows` raise and classify the day `strategy_error`, so failing loudly here is the correct,
    already-handled outcome rather than a crash.
    """
    if not isinstance(payload, _ProbedCapture):
        raise OregonOemStrategyError(f"expected a _ProbedCapture payload, got {type(payload).__name__}")
    return payload


def _unchanged_row_stamps(table: pa.Table, published: PublishedSnapshot | None) -> dict[str, datetime]:
    """Return `feature_updated_at` for exactly the rows whose source content did NOT move.

    Restates `pipeline/direct/evacuation_zones/forward.py::_unchanged_row_stamps` (a private helper
    there, and that package is off-limits to import from here): reproduces
    `sql/ingest/refresh_features.sql`'s row-scoped UPDATE, where a statewide re-stamp would not.
    """
    if published is None:
        return {}
    current = row_content_digests(table)
    return {
        natural_key: stamp
        for natural_key, stamp in published.updated_at.items()
        if published.row_digests.get(natural_key) == current.get(natural_key)
    }


def _descriptor_bytes(capture: _ProbedCapture) -> bytes:
    """A deterministic byte rendering of one capture, for `SourceResponse.body`/`.digest` only.

    Never parsed back: `settle`/`rows` read `SourceResponse.payload` (the `_ProbedCapture` itself),
    which is opaque to the runner. This exists so `digests.py::responses_digest` (the absence
    receipt's `source_digest`) and checkpoint bookkeeping see a stable value per capture.
    """
    return json.dumps(
        {
            "bbox": capture.source.bbox,
            "captured_digest": capture.digest,
            "fetched_at": capture.source.fetched_at.isoformat(),
            "zone_count": capture.source.zone_count,
        },
        sort_keys=True,
    ).encode("utf-8")


def _quiet_day_absence(capture: _ProbedCapture) -> Absent:
    """A statewide stand-down: a real, valued answer, never a failed read (module docstring)."""
    source = capture.source
    return Absent(
        reason="Oregon OEM published no evacuation areas within this run's bounds",
        proof=(
            f"a statewide capture at {source.fetched_at.isoformat()} within bbox {source.bbox} returned zero "
            f"areas (digest {capture.digest[:_DIGEST_EXCERPT]}); the digest of an empty population is stable "
            "across polls, so repeated quiet captures compare equal and this is a real statewide stand-down, "
            "not a failed read"
        ),
    )


@dataclass(slots=True)
class OregonOemEvacuationZonesStrategy:
    """Oregon OEM's `Fire_Evacuation_Areas_Public` as a content-watermarked `static_lookup`.

    NOT frozen, unlike this package's sibling strategies: `_captured` is deliberately mutable,
    per-turn cache state, the one escape this strategy makes from "thin strategy, fat runner" (D2),
    and the module docstring above states exactly why it is structurally required rather than an
    optimisation. `store_factory` defaults to the real bucket and exists so a test can hand this
    strategy an `ObjectStore(RecordingBackend())` without monkeypatching a classmethod.

    SAFETY DEPENDS ON ONE UNSTATED-ELSEWHERE INVARIANT this class does not itself enforce: one turn
    runs in its own subprocess, so a fresh `python -m agri_data_service.pipeline.runner` invocation
    means a fresh module import and a fresh `STRATEGY` instance, never a `_captured` left over from an
    earlier turn (`execution/job_executor_service.py:5064`'s `asyncio.create_subprocess_exec`, spawning
    the per-turn CLI call `execution/AGENTS.md:359` names). A future binding that ran turns inside one
    long-lived process would need to reset `_captured` between them, which nothing here does.
    """

    store_factory: Callable[[], ObjectStore] = ObjectStore.from_settings
    _captured: _ProbedCapture | None = field(default=None, init=False, repr=False)

    async def probe_edge(self, client: ProviderClient, window: ProbeWindow) -> ProviderEdge:  # noqa: ARG002 - never sent through; see the module docstring
        """Capture Oregon's current statewide set once, decide this turn's watermark day from it, and cache it.

        `_watermark_day` is computed, and can raise (see its own docstring for the clock-skew guard
        and exactly which outcome that produces), BEFORE `self._captured` is set: a turn this strategy
        refuses to decide a day for should not leave a capture cached for a `fetch()` call that, for
        the normal (non-CA17) path, will never happen this turn anyway.
        """
        bbox = resolve_coverage_bbox()
        if bbox is None:
            return ProviderEdge(status="invalid", window=window, detail=bbox_unconfigured_reason())
        try:
            capture = await self._capture(bbox)
        except (EvacuationZonesSourceError, EvacuationZonesTruncatedError, EvacuationZonesWatermarkError) as error:
            return ProviderEdge(status="unavailable", window=window, detail=f"{type(error).__name__}: {error}")
        day = _watermark_day(capture)
        self._captured = capture
        return ProviderEdge(status="ok", window=window, valued_days=frozenset({day}))

    def plan_requests(
        self,
        days: Sequence[date],
        lane: LaneConfig,  # noqa: ARG002 - the Protocol's lane; this lane has nothing per-config to read
        region: Region,  # noqa: ARG002 - the Protocol's region; coverage is Oregon's own feed, not a lattice
    ) -> list[SourceRequest]:
        """The one snapshot unit, answering whichever single day `probe_edge` decided was owed."""
        return [SourceRequest(unit=UNIT, endpoint=ENDPOINT, days=tuple(days))]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:  # noqa: ARG002 - see the module docstring
        """Reuse `probe_edge`'s cached capture, or take a fresh one (CA17 `--republish-current` skips the probe)."""
        capture = self._captured
        if capture is None:
            bbox = resolve_coverage_bbox()
            if bbox is None:
                raise OregonOemStrategyError(bbox_unconfigured_reason())
            capture = await self._capture(bbox)
        return SourceResponse(
            request=request,
            body=_descriptor_bytes(capture),
            retrieved_at=capture.source.fetched_at,
            payload=capture,
        )

    def settle(
        self,
        day: date,  # noqa: ARG002 - the day is `probe_edge`'s own decision; nothing here depends on it again
        responses: Sequence[SourceResponse],
        context: DayContext,  # noqa: ARG002 - the watermark decision already happened in `probe_edge`
    ) -> Settlement:
        """A capture with zero areas is a governed absence; otherwise every captured area is present."""
        capture = _require_capture(responses[0].payload)
        if capture.source.zone_count == 0:
            return _quiet_day_absence(capture)
        return Written(
            expected_units=capture.source.zone_count,
            present_units=capture.source.zone_count,
            source_digest=capture.digest,
        )

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """Build this version's rows, carrying `feature_updated_at` forward for every unchanged area."""
        capture = _require_capture(responses[0].payload)
        table = evacuation_zones_table(capture.source, snapshot_day=day)
        carried = _unchanged_row_stamps(table, capture.published)
        return apply_carried_forward_updated_at(table, carried)

    async def _capture(self, bbox: str) -> _ProbedCapture:
        """Read the published snapshot, capture Oregon's current state once, and digest it.

        Reading `published` before the upstream walk costs nothing extra (FR-7: a lane never runs
        twice, so nothing else can publish a new version of this lane between the two reads) and
        means a change landing mid-walk is simply seen by the digest comparison below, never raced
        against a published-state read that happened to run concurrently with it.
        """
        store = self.store_factory()
        published = await asyncio.to_thread(read_published_snapshot, store)
        source = await fetch_evacuation_zones_snapshot(
            bbox,
            retry_attempts=CAPTURE_RETRY_ATTEMPTS,
            retry_base_seconds=CAPTURE_RETRY_BASE_SECONDS,
            retry_max_seconds=CAPTURE_RETRY_MAX_SECONDS,
        )
        # A scratch day: `content_digest` excludes `snapshot_day` from `CONTENT_DIGEST_COLUMNS`
        # (`rows.py`), so the day stamped here never affects the digest this capture is taken for.
        scratch = evacuation_zones_table(source, snapshot_day=source.fetched_at.date())
        return _ProbedCapture(source=source, digest=content_digest(scratch), published=published)


def _watermark_day(capture: _ProbedCapture) -> date:
    """Sticky at the published day when nothing changed; otherwise today's UTC capture date.

    The rule `forward.py` inlines (module docstring, "Why this diverges from `watermark.py`'s
    `day=None`"), restated here because that module is off-limits to import from this package.

    CLOCK-SKEW GUARD, restated from `forward.py:252-261` (also off-limits to import): a published
    day AHEAD of this capture's own day means some version was stamped ahead of this capture -- a
    skewed host clock, or a hand-written future version -- and a day behind it would be invisible to
    every later turn, which would then re-decide the SAME behind-schedule day and rewrite nothing,
    forever (`served >= watermark` reading "current" with no calendar day ever catching up). Checked
    UNCONDITIONALLY, before the sticky comparison, exactly as `forward.py` orders it.

    The raise is NOT caught by `probe_edge`'s own `except (EvacuationZonesSourceError,
    EvacuationZonesTruncatedError, EvacuationZonesWatermarkError)` -- `OregonOemStrategyError` is
    none of those -- so it propagates out of `probe_edge` to `turn.py::_Turn._spend_probe`'s generic
    `except Exception` handler. `fetch.py::classify_fetch_error` has no typed case for it, so it
    falls through to `"strategy"`, which `_spend_probe` maps to `status="unavailable"` (never the
    `"config"` branch that re-raises and ends the turn). That is a SOFTER outcome than legacy's
    uncaught `DirectEvacuationZonesError` (which failed the whole process): today is reported
    `unwritten`/`upstream_unavailable` and the turn exits 0, to be retried next tick -- and it will
    raise again every tick until an operator fixes the host clock or retracts the future version,
    which is the same pair of remedies the message below names.
    """
    published = capture.published
    capture_day = capture.source.fetched_at.date()
    if published is not None and published.day > capture_day:
        raise OregonOemStrategyError(
            f"the newest published version {published.day.isoformat()} is later than this capture's "
            f"own day {capture_day.isoformat()}; a version behind it would be invisible to every "
            "later turn and this one would rewrite nothing, forever. Check the host clock, or "
            "retract the future version"
        )
    if published is not None and published.digest == capture.digest:
        return published.day
    return capture_day


#: S14: `strategy = "evacuation_zones.oregon_oem"` resolves to this module's `STRATEGY`.
STRATEGY: Final = OregonOemEvacuationZonesStrategy()

__all__ = [
    "CAPTURE_RETRY_ATTEMPTS",
    "CAPTURE_RETRY_BASE_SECONDS",
    "CAPTURE_RETRY_MAX_SECONDS",
    "ENDPOINT",
    "STRATEGY",
    "UNIT",
    "OregonOemEvacuationZonesStrategy",
    "OregonOemStrategyError",
]
