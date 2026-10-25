"""`drought-direct-forward`'s config-lane strategy: one USDM Tuesday release per unit.

Strategy key `drought.usdm_releases` (distinct from the reserved `drought.usdm` leaf -- a SOURCE
module with no `STRATEGY`, per `pipeline/lanes/AGENTS.md` "Reserved module names"). Ships DARK:
`lanes/drought-direct-forward.toml` declares `executor = "legacy"`, so `pipeline.direct.drought`
keeps writing unchanged; this module exists so the TOML and strategy pass their own review and are
ready for `p5-cutover`'s G6 flip (plan Phase 4/5).

**The 404-is-not-absence rule, in the runner's own vocabulary.** USDM answers a genuinely unpublished
Tuesday with an ordinary HTTP 404 (`ingest/usdm.py::fetch_drought_release`'s docstring: "a 404 is
USDM's documented 'not published yet'"). `fetch()` below CATCHES that 404 itself -- it never lets
`UpstreamHttpError(404)` escape to the runner's unit ladder (`pipeline/runner/fetch.py::
classify_fetch_error`), because that function has no case for 404: it falls through every
`SourceThrottledError`/`SourceUnavailableError`/`ProviderConfigurationError`/`UpstreamHttpError{429,5xx,
401,403}` branch to the generic `"strategy"` outcome, which `UnitFetcher.fetch_unit` turns into
`UnwrittenReason = "strategy_error"` -- an upstream- or code-fault vocabulary word, wrong for an
entirely expected weekly state. By absorbing the 404 inside `fetch()` and returning an ordinary
(successful) `SourceResponse` with `payload=None`, `settle()` is the one place that decides what an
unpublished Tuesday MEANS, using the contract's own words: `Unsettled(reason="unsettled")` (never
`exit`-coded; `pipeline/runner/exits.py`'s module docstring: "days left unwritten are in the report,
never in the exit code") when nothing proves USDM's cadence has moved past it, or `Absent(...)` when a
LATER Tuesday is already published -- `DayContext.later_published_day`, the contract's own generalised
form of the legacy adapter's `mirrored_past_proof` callback (`pipeline/direct/drought/adapter.py`;
`pipeline/direct/AGENTS.md`, "the release_series mirrored-past pattern"). Conformance precedent:
`tests/runner/fixtures/release_series.py::ReleaseSeriesStrategy.fetch` catches the identical 404 the
identical way (that fixture does not implement the mirrored-past branch).

**A known gap, why `executor` stays `"legacy"`.** USDM's per-release resource is a dated URL PATH
segment (`ingest/usdm.py::usdm_source_url`), not a query parameter. `pipeline/runner/binding.py::
ConfigProviderClient` and `ingest/provider_client.py::provider_endpoint_request` build every request
URL as the provider's declared (fixed) `path` plus a `?query` string -- there is no declared-endpoint
shape that can express a path segment that varies per request. This is the IDENTICAL gap
`lanes/_providers/nasa-firms.toml` names in its own TODO for FIRMS's path-segment API key: "the
provider client attaches nothing regardless: the strategy must place it itself" -- but `fetch()` here
is handed only `ProviderClient.get(endpoint, parameters)`, which has no seam for that either. This
lane therefore CANNOT move to `executor = "config"` until a provider-client path-segment transport is
added (out of this slice's owned files: `pipeline/runner/binding.py` and `ingest/provider_client.py`
are both framework files no swarm lane may edit). Reported to `i-coordinator`; see
`docs/lanes/drought.md` section 8 and this package's `AGENTS.md`. The tests below exercise this
strategy's own logic against a fake `ProviderClient` (never the real `ConfigProviderClient`'s URL
construction), which is the honest scope given the gap above.

**A second known gap (ring review, s-drought fix batch): G6's `--mode forward --compare` cannot run
against this lane either, and the earlier G6-parity claims below were half true.** `_source_digest`
excludes `ingested_at` so an UNCHANGED recheck never reads as a revision inside THIS strategy's own
`settle()` (S11) -- that claim holds. But G6's actual compare path is
`pipeline/runner/turn.py::_Turn._compare`, which diffs `reader.canonical_digest(stream, built)`
against `reader.canonical_digest(stream, published)`; `reader.py::ObjectStoreLaneReader.canonical_digest`
conforms the WHOLE table to the stream schema and hashes it with `digests.py::table_digest` (the
table's Arrow IPC bytes, every column, no exclusion mechanism). `ingested_at` IS a `DROUGHT_SCHEMA`
column (`warehouse/schemas/drought.py`) and `rows()` below stamps it with the CURRENT fetch's
`retrieved_at` -- so comparing a freshly built table against ANY already-published day (written by the
legacy lane, or by this lane on an earlier turn, with that write's OWN `ingested_at`) deltas on that
column alone, every time, for every already-settled day. `_source_digest` and `canonical_digest` are
two DIFFERENT digests over two DIFFERENT inputs (release content minus the instant, vs. the whole
conformed table including it) serving two different callers; excluding the instant from one says
nothing about the other. This is NOT drought-specific -- it is the `direct:`-row convention's general
shape: any lane whose schema carries a retrieval-instant column (`ingested_at`, `fetched_at`, ...) hits
the identical false delta the moment it reaches G6 compare. The fix lives OUTSIDE this slice's owned
files (`pipeline/runner/turn.py`, `reader.py`, `digests.py` are framework files no swarm lane may
edit): `_compare`/`canonical_digest` need to become volatile-column-aware (e.g. a schema-declared
exclusion set, or preferring a stored `source_digest` over a raw table hash where one exists) before
G6 can run against this lane -- or against ANY other lane sharing the same schema shape. Recorded as a
G6 prerequisite with `i-coordinator`; see `docs/lanes/drought.md` section 8 and this package's
`AGENTS.md` for the same disclosure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.http import HTTP_NOT_FOUND, UpstreamHttpError
from agri_data_service.ingest.usdm import DroughtRelease, parse_drought_release
from agri_data_service.pipeline.lanes._shared.digest import COMPACT_SEPARATORS, canonical_json_bytes, sha256_hex
from agri_data_service.pipeline.lanes.drought.geometry import drought_geometry_session, repair_drought_areas_to_wkb
from agri_data_service.pipeline.lanes.drought.products import release_weeks
from agri_data_service.pipeline.runner.contract import Absent, SourceRequest, SourceResponse, Unsettled, Written
from agri_data_service.warehouse.schemas.drought import DROUGHT_SCHEMA, DROUGHT_STREAM

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.pipeline.runner.contract import DayContext, ProviderClient, Settlement

#: `date.weekday()` where Monday is 0 (`ingest/usdm.py::TUESDAY`; `products.py::USDM_RELEASE_WEEKDAY`).
_TUESDAY: Final = 1
#: Matches `lanes/_providers/usdm.toml`'s `[endpoints.release-json]` declaration.
RELEASE_ENDPOINT: Final = "release-json"
#: The one parameter a release unit settles: the whole release, named rather than left implicit.
VALID_DATE_PARAMETER: Final = "valid_date"
#: A 404 carries no retrieval instant of its own (`ProviderResponse` is never constructed for one);
#: matches `tests/runner/fixtures/release_series.py::NOT_PUBLISHED_AT`'s identical sentinel choice.
NOT_PUBLISHED_AT: Final = datetime(1970, 1, 1, tzinfo=UTC)
#: Matches `pipeline/direct/drought/rows.py::DIRECT_AREA_ID_PREFIX`/`direct_area_id` byte-for-byte
#: (restated, not imported -- this package may never import `pipeline/direct/**`): this COLUMN alone
#: contributes no delta at G6 compare when the executor flips from legacy to config. CORRECTED (ring
#: review, s-drought fix batch): that is NOT the same as "the G6 compare gate sees no delta" overall
#: -- `ingested_at` still will, on every already-published day, regardless of this column's fidelity.
#: See this module's docstring, "A second known gap", for the compare-path mechanism and why the fix
#: is out of scope here.
_DIRECT_AREA_ID_PREFIX: Final = "direct"


def _direct_area_id(valid_date: str, drought_intensity_class: int) -> str:
    """Build the deterministic, `direct:`-namespaced id a direct-fetched row carries in place of a real one.

    There is no Postgres row behind a direct fetch (`pipeline/direct/AGENTS.md`, "The `direct:` area_id,
    and why it is not a lineage column"): deterministic, so idempotent republication is byte-identical,
    and namespaced, so a reader checking only the column's shape cannot mistake it for a real
    `geo.drought_areas.id` foreign key.
    """
    return f"{_DIRECT_AREA_ID_PREFIX}:{valid_date}:{drought_intensity_class}"


def _source_digest(release: DroughtRelease) -> str:
    """SHA-256 of the release's content, excluding the retrieval instant (S11).

    Every row carries `ingested_at`, which changes on every refetch; without excluding it here every
    recheck of an unchanged release would read as a revision and rewrite the day for no reason
    (`Written.source_digest`'s own docstring: "a strategy whose rows carry a retrieval instant must set
    it, or every refetch reads as a revision").

    CORRECTED SCOPE (ring review, s-drought fix batch): this governs ONLY that one S11 rewrite
    decision, internal to this strategy's own `settle()`. It is a different digest over a different
    input than `pipeline/runner/turn.py::_compare`'s `reader.canonical_digest`, which hashes the WHOLE
    written table -- `ingested_at` included -- with no exclusion. Excluding the instant here does
    NOT make this lane's output immune to a false delta at G6 compare; see this module's docstring,
    "A second known gap".
    """
    canonical = canonical_json_bytes(
        {
            "valid_date": release.valid_date,
            "source_url": release.source_url,
            "areas": [
                {"dm_category": area.drought_intensity_class, "geometry": area.geometry}
                for area in sorted(release.areas, key=lambda area: area.drought_intensity_class)
            ],
        },
        sort_keys=True,
        separators=COMPACT_SEPARATORS,
        allow_nan=True,
    )
    return sha256_hex(canonical)


def _release_payload(response: SourceResponse) -> DroughtRelease | None:
    """Narrow `SourceResponse.payload` back to its real type: `None` for USDM's "not published yet"."""
    payload = response.payload
    if payload is None:
        return None
    if not isinstance(payload, DroughtRelease):
        raise TypeError(f"drought release unit carried a {type(payload).__name__} payload, not a DroughtRelease")
    return payload


@dataclass(frozen=True, slots=True)
class UsdmReleaseSeriesStrategy:
    """One unit per USDM release Tuesday: the whole national GeoJSON file, never fetched in parts."""

    def release_days(self, first: date, last: date) -> Sequence[date]:
        """Every USDM release Tuesday in `[first, last]`; a `release_series` owes only these (S19).

        Delegates to `products.py::release_weeks`, the SAME canonical Tuesday walk the legacy
        `forward.py`/`backfill.py` and `pipeline/validation/drought.py` all trust, so the runner's
        `_release_filter` (`pipeline/runner/turn.py`) never treats a non-Tuesday day as owed.
        """
        return release_weeks(first, last)

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:  # noqa: ARG002 - the Protocol's lane and region
        """One request per release day. USDM is a single national source: neither `lane` nor `region`
        varies the request shape (unlike a gridded or regionally-chunked lane).

        Defends the "valid Tuesday" settle semantic (spec §7 drought row) even though every `day`
        reaching here should already be one: the runner only ever asks for a release day through
        `_release_filter` -> `release_days()` above, which delegates to the same `release_weeks()`
        Tuesday walk. `ingest/usdm.py::fetch_drought_release`'s own `_require_tuesday` is not called
        (this strategy never calls that function), so this is the one place left to catch a future
        regression in the runner's own filtering rather than silently requesting a non-release day.
        DELIBERATELY a hard failure (`plan_requests` raising is NOT caught per-day the way `settle`/
        `rows` are: `pipeline/runner/turn.py::_fan_out` calls it with no try/except, so this fails the
        WHOLE turn, exit 70) -- the right severity for an invariant violation rather than one day's
        `strategy_error`.
        """
        non_tuesdays = [day for day in days if day.weekday() != _TUESDAY]
        if non_tuesdays:
            raise ValueError(f"drought release_series plan_requests was asked for non-Tuesday day(s): {non_tuesdays}")
        return [
            SourceRequest(
                unit=f"release:{day.isoformat()}",
                endpoint=RELEASE_ENDPOINT,
                days=(day,),
                parameters=((VALID_DATE_PARAMETER, day.isoformat()),),
                requested_parameters=frozenset({VALID_DATE_PARAMETER}),
            )
            for day in days
        ]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """A 404 is USDM's own "not published yet" (`ingest/usdm.py::fetch_drought_release`); never a failure.

        See this module's docstring for why the 404 is absorbed HERE rather than let through to
        `pipeline/runner/fetch.py::classify_fetch_error`.
        """
        try:
            response = await client.get(request.endpoint, request.query())
        except UpstreamHttpError as error:
            if error.status != HTTP_NOT_FOUND:
                raise
            return SourceResponse(
                request=request,
                body=b"",
                retrieved_at=NOT_PUBLISHED_AT,
                complete_parameters=request.requested_parameters,
                payload=None,
            )
        valid_date = request.days[0].isoformat()
        release = parse_drought_release(valid_date, json.loads(response.body))
        return SourceResponse(
            request=request,
            body=response.body,
            retrieved_at=response.retrieved_at,
            complete_parameters=request.requested_parameters,
            payload=release,
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:  # noqa: ARG002 - day is read via the release's own valid_date, never re-derived
        """Published writes the whole release; unpublished is the release_series mirrored-past pattern.

        One unit per day (`plan_requests` above), and `fetch()` never raises for USDM's documented
        404, so `responses` holds exactly one answer whenever the runner calls this at all
        (`pipeline/runner/turn.py::_settle_answered`: a day short of its only unit is reported
        `unwritten` directly and never reaches `settle`).
        """
        release = _release_payload(responses[0])
        if release is None:
            return self._unpublished(context)
        return Written(expected_units=1, present_units=1, source_digest=_source_digest(release))

    def _unpublished(self, context: DayContext) -> Settlement:
        """USDM's 404 is "not yet", never "never" (`pipeline/direct/AGENTS.md`, "the release_series
        mirrored-past pattern"): a LATER Tuesday already published proves the cadence moved past this
        one, read off the SAME census snapshot the turn already paid for
        (`DayContext.later_published_day`, the contract's generalised form of the legacy adapter's
        `mirrored_past_proof` callback). Otherwise this Tuesday stays unsettled and the next turn asks
        again -- in practice a safety margin rather than a live path: `pipeline/parquet/lane_registry.py`'s
        `DROUGHT_STREAM` registration measured 209/209 continuous releases 2022-08-09..2026-08-18.
        """
        proof_day = context.later_published_day(DROUGHT_STREAM)
        if proof_day is None:
            return Unsettled("unsettled", "USDM's archive has no release for this Tuesday yet (404 is never absence)")
        return Absent(
            reason="USDM's archive has no release for this Tuesday",
            proof=(
                f"a later release ({proof_day.isoformat()}) is already published, proving USDM's "
                "weekly cadence has moved past this Tuesday"
            ),
        )

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:  # noqa: ARG002 - day is read via the release's own valid_date, never re-derived
        """One row per drought class, WKB-repaired through DuckDB spatial; never a partial release.

        `settle()` already refused an unpublished day (never reaches here), so `release` is always the
        parsed payload when the runner calls this.
        """
        release = _release_payload(responses[0])
        if release is None:  # pragma: no cover - settle() already turned this into Unsettled/Absent
            raise ValueError("rows() called for an unpublished USDM release; settle() should have refused it")
        ingested_at = responses[0].retrieved_at
        with drought_geometry_session() as session:
            repaired = repair_drought_areas_to_wkb(session, release.areas)
        records = [
            {
                "area_id": _direct_area_id(release.valid_date, area.drought_intensity_class),
                "valid_date": release.release_day,
                "dm_category": area.drought_intensity_class,
                "source_url": release.source_url,
                "ingested_at": ingested_at,
                "geom": repaired[area.drought_intensity_class],
            }
            for area in release.areas
        ]
        return pa.Table.from_pylist(records, schema=DROUGHT_SCHEMA.arrow_schema)


#: The single instance the lane TOML's `strategy = "drought.usdm_releases"` resolves to (S14).
STRATEGY: Final = UsdmReleaseSeriesStrategy()


__all__ = [
    "NOT_PUBLISHED_AT",
    "RELEASE_ENDPOINT",
    "STRATEGY",
    "VALID_DATE_PARAMETER",
    "UsdmReleaseSeriesStrategy",
]
