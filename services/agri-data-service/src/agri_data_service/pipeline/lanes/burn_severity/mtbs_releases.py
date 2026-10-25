"""`burn-severity-direct-forward`, historical side: one governed MTBS release day per unit, every cohort paged whole.

Strategy key `burn_severity.mtbs_releases` (spec S14, §7). Imports `execution.contracts`, `ingest`, `warehouse`,
this package and `pipeline/runner/contract.py` only. The Friday current capture is NOT here and has no successor
(CA7): `pipeline/lanes/burn_severity/AGENTS.md`, "CA7: no capture successor", says which contract surface refuses it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from agri_data_service.execution.contracts import MAX_SOURCE_GEOJSON_FEATURES
from agri_data_service.ingest.http import UpstreamPayloadTooLargeError
from agri_data_service.ingest.mtbs import (
    DEFAULT_PAGE_SIZE,
    MAX_RELEASE_PAGES,
    MIN_PAGE_SIZE,
    MtbsDuplicateFeatureError,
    MtbsReleaseTooLargeError,
    MtbsTruncatedCaptureError,
    build_mtbs_record,
    native_fire_identifier,
    page_is_truncated,
    release_observation_window,
    resolve_data_available_at,
    validate_release_window,
)
from agri_data_service.pipeline.lanes.burn_severity.products import (
    governed_release_days,
    release_days_by_ignition_year,
)
from agri_data_service.pipeline.lanes.burn_severity.release_rows import release_day_table
from agri_data_service.pipeline.runner.contract import (
    Absent,
    SourceRequest,
    SourceResponse,
    SourceUnavailableError,
    Written,
)
from agri_data_service.warehouse.mtbs_snapshots import MTBS_SNAPSHOT_FIRST_DAY

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.ingest.mtbs import MtbsBurnSeverityRecord
    from agri_data_service.pipeline.runner.contract import (
        DayContext,
        ProviderClient,
        ProviderResponse,
        Settlement,
    )

#: The `lanes/_providers/mtbs-edw.toml` endpoint: EDW MapServer layer 63, "Burned Area Boundaries (All Years)".
ENDPOINT: Final = "burned-area-query"
#: The region manifest's `sub_envelopes` key this lane pulls inside (`foundation/region/pnw.json`).
ENVELOPE_PURPOSE: Final = "burn_severity"
#: The envelope filter's query parameters, carried on every unit so `fetch` never re-reads the region.
ENVELOPE_KEYS: Final = frozenset({"geometry", "geometryType", "inSR", "spatialRel"})
#: An ArcGIS error document at or above this `code` is the host refusing an oversized page, not a bad query.
ARCGIS_SERVER_ERROR_MINIMUM: Final = 500
UNIT_PREFIX: Final = "mtbs-release"

Parameters = tuple[tuple[str, str], ...]


class BurnSeverityReleaseError(RuntimeError):
    """A release day this strategy will not ask MTBS for, or an answer it cannot attribute to one."""


class OversizedPageError(Exception):
    """The host refused a page as too large (an ArcGIS error document with a 5xx `code`): narrow and re-ask."""


@dataclass(frozen=True, slots=True)
class CohortAnswer:
    """One ignition-year cohort paged to the service's own count over the region's envelope."""

    ignition_year: int
    count: int
    records: tuple[MtbsBurnSeverityRecord, ...]


@dataclass(frozen=True, slots=True)
class ReleaseDayAnswer:
    """Every cohort one governed release day publishes: the union is all-or-nothing."""

    day: date
    cohorts: tuple[CohortAnswer, ...]

    @property
    def records(self) -> tuple[MtbsBurnSeverityRecord, ...]:
        """Every burned-area boundary of every cohort, cohort order kept."""
        return tuple(record for cohort in self.cohorts for record in cohort.records)

    @property
    def expected_units(self) -> int:
        """The service's own counts, summed: what a complete day must hold."""
        return sum(cohort.count for cohort in self.cohorts)


def envelope_parameters(region: Region) -> Parameters:
    """The region's burn-severity envelope as the ArcGIS envelope filter (`federation.md` §1: never a literal)."""
    envelope = region.sub_envelopes.get(ENVELOPE_PURPOSE)
    if envelope is None:
        raise BurnSeverityReleaseError(f"the active region declares no {ENVELOPE_PURPOSE!r} sub-envelope")
    return (
        ("geometry", f"{envelope.west},{envelope.south},{envelope.east},{envelope.north}"),
        ("geometryType", "esriGeometryEnvelope"),
        ("inSR", "4326"),
        ("spatialRel", "esriSpatialRelIntersects"),
    )


def count_parameters(ignition_year: int, envelope: Parameters) -> Parameters:
    """`returnCountOnly`: the cohort's authoritative size, the completeness gate every page is held to."""
    return (("where", f"year = {ignition_year}"), *envelope, ("returnCountOnly", "true"), ("f", "json"))


def page_parameters(ignition_year: int, envelope: Parameters, *, offset: int, size: int) -> Parameters:
    """One deterministically ordered GeoJSON page (`orderByFields=fire_id` is mandatory for ArcGIS paging)."""
    return (
        ("where", f"year = {ignition_year}"),
        *envelope,
        ("outSR", "4326"),
        ("outFields", "*"),
        ("orderByFields", "fire_id"),
        ("resultOffset", str(offset)),
        ("resultRecordCount", str(size)),
        ("returnGeometry", "true"),
        ("f", "geojson"),
    )


def release_unit(day: date) -> str:
    """The unit id: one governed release day is one retry unit."""
    return f"{UNIT_PREFIX}:{day.isoformat()}"


async def _query(client: ProviderClient, parameters: Parameters) -> tuple[dict[str, Any], ProviderResponse]:
    """One send; an ArcGIS error document with a 5xx code is `OversizedPageError`, any other a refusal."""
    response = await client.get(ENDPOINT, dict(parameters))
    try:
        payload = json.loads(response.body)
    except ValueError as decode_error:
        raise MtbsTruncatedCaptureError("MTBS answered a body that is not JSON") from decode_error
    if not isinstance(payload, dict):
        raise MtbsTruncatedCaptureError("MTBS answered a JSON document that is not an object")
    error = payload.get("error")
    if error:
        code = error.get("code") if isinstance(error, dict) else None
        if isinstance(code, int) and not isinstance(code, bool) and code >= ARCGIS_SERVER_ERROR_MINIMUM:
            raise OversizedPageError(f"MTBS answered an ArcGIS error document with code {code}")
        raise MtbsTruncatedCaptureError(f"MTBS answered an ArcGIS error document with code {code!r}")
    return payload, response


async def _page(
    client: ProviderClient, ignition_year: int, envelope: Parameters, *, offset: int, size: int
) -> tuple[list[dict[str, Any]], bool, int, ProviderResponse]:
    """One page, halving the window down to `MIN_PAGE_SIZE` only on an oversized-page signal.

    The two signals are the ArcGIS error document (`OversizedPageError`) and the client's byte cap
    (`UpstreamPayloadTooLargeError`). A 5xx status or a transport failure is an outage: it propagates at
    once to the runner's ladder, never narrowing a page.
    """
    while True:
        try:
            payload, response = await _query(client, page_parameters(ignition_year, envelope, offset=offset, size=size))
        except (OversizedPageError, UpstreamPayloadTooLargeError) as error:
            if size > MIN_PAGE_SIZE:
                size = max(MIN_PAGE_SIZE, size // 2)
                continue
            if isinstance(error, OversizedPageError):
                raise SourceUnavailableError(
                    f"MTBS still refused a {MIN_PAGE_SIZE}-row page at offset {offset} as oversized"
                ) from error
            raise
        features = payload.get("features") or []
        if not isinstance(features, list):
            raise MtbsTruncatedCaptureError(f"MTBS page at offset {offset} returned a non-list `features` member")
        kept = [feature for feature in features if isinstance(feature, dict)]
        return kept, page_is_truncated(payload), size, response


async def _page_cohort(
    client: ProviderClient, ignition_year: int, envelope: Parameters
) -> tuple[list[dict[str, Any]], datetime | None]:
    """Page a cohort to completion behind the service flag and the short page; the caller holds it to the count."""
    features: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset, size, previous_truncated = 0, DEFAULT_PAGE_SIZE, False
    retrieved_at: datetime | None = None
    for _ in range(MAX_RELEASE_PAGES):
        page, truncated, size, response = await _page(client, ignition_year, envelope, offset=offset, size=size)
        retrieved_at = response.retrieved_at
        if not page:
            if previous_truncated:
                raise MtbsTruncatedCaptureError(
                    f"MTBS fire year {ignition_year} flagged exceededTransferLimit at offset {offset}, "
                    "then sent nothing"
                )
            break
        for feature in page:
            properties = feature.get("properties")
            fire_id = native_fire_identifier(properties if isinstance(properties, dict) else {})
            if fire_id in seen:
                raise MtbsDuplicateFeatureError(f"MTBS Fire_ID {fire_id!r} repeated across fire year {ignition_year}")
            seen.add(fire_id)
        features.extend(page)
        offset += len(page)
        previous_truncated = truncated
        # Legacy's stop rule: a short page AND no service flag. A page ending exactly on the count still asks
        # one more page, because that page is what catches a fire added between the count and the paging.
        if len(page) < size and not truncated:
            break
    else:
        raise MtbsTruncatedCaptureError(
            f"MTBS fire year {ignition_year} did not finish within {MAX_RELEASE_PAGES} pages"
        )
    return features, retrieved_at


async def _fetch_cohort(
    client: ProviderClient, ignition_year: int, envelope: Parameters
) -> tuple[CohortAnswer, list[dict[str, Any]], datetime]:
    """Count, page and normalise one cohort; a short capture never reaches `settle`."""
    payload, response = await _query(client, count_parameters(ignition_year, envelope))
    count = payload.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise MtbsTruncatedCaptureError(f"MTBS returnCountOnly gave {count!r} for fire year {ignition_year}")
    if count > MAX_SOURCE_GEOJSON_FEATURES:
        raise MtbsReleaseTooLargeError(
            f"MTBS fire year {ignition_year} holds {count} features, above the {MAX_SOURCE_GEOJSON_FEATURES} contract"
        )
    features: list[dict[str, Any]] = []
    retrieved_at = response.retrieved_at
    if count:
        features, paged_at = await _page_cohort(client, ignition_year, envelope)
        retrieved_at = paged_at or retrieved_at
    if len(features) != count:
        raise MtbsTruncatedCaptureError(
            f"MTBS fire year {ignition_year} paged {len(features)} features but the service counts {count}"
        )
    records = tuple(build_mtbs_record(feature, ignition_year) for feature in features)
    return CohortAnswer(ignition_year=ignition_year, count=count, records=records), features, retrieved_at


def _check_cohort_release(cohort: CohortAnswer, day: date) -> None:
    """The governed release must follow the cohort's last ignition by the floor, and resolve to this very day."""
    available_at = resolve_data_available_at(cohort.ignition_year)
    if cohort.records:
        _observed_from, observed_to = release_observation_window(cohort.records)
        validate_release_window(available_at, observed_to)
    if available_at.date() != day:
        raise BurnSeverityReleaseError(
            f"fire year {cohort.ignition_year} resolves to release day {available_at.date().isoformat()}, "
            f"not {day.isoformat()}; MTBS_ANNUAL_RELEASE_DATES changed during the turn"
        )


def _answer(day: date, responses: Sequence[SourceResponse]) -> ReleaseDayAnswer:
    """The day's one release answer, or a refusal naming why it cannot be attributed to `day`."""
    answer = responses[0].payload if len(responses) == 1 else None
    if not isinstance(answer, ReleaseDayAnswer):
        raise BurnSeverityReleaseError(f"release day {day.isoformat()} needs exactly one answered release unit")
    if answer.day != day:
        raise BurnSeverityReleaseError(f"release unit answered {answer.day.isoformat()}, not {day.isoformat()}")
    return answer


@dataclass(frozen=True, slots=True)
class MtbsReleaseStrategy:
    """Historical MTBS releases: a `release_series` owing only governed release days, each written whole or not."""

    def release_days(self, first: date, last: date) -> tuple[date, ...]:
        """Every governed release day in `[first, last]`; no other day is ever owed."""
        return tuple(day for day in governed_release_days() if first <= day <= last)

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:  # noqa: ARG002 - the Protocol's lane
        """One unit per governed release day; its parameters are its first send, the day's first cohort count."""
        cohorts = release_days_by_ignition_year()
        envelope = envelope_parameters(region)
        return [
            SourceRequest(
                unit=release_unit(day),
                endpoint=ENDPOINT,
                days=(day,),
                parameters=count_parameters(cohorts[day][0], envelope),
            )
            for day in days
            if day in cohorts
        ]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """Every cohort of the day, counted and paged; refused before any send inside the current-snapshot era."""
        (day,) = request.days
        if day >= MTBS_SNAPSHOT_FIRST_DAY:
            raise BurnSeverityReleaseError(
                f"release day {day.isoformat()} is on or after {MTBS_SNAPSHOT_FIRST_DAY.isoformat()}: serving admits "
                "such a burn-severity day only as a verified current snapshot "
                "(parquet_ops/serving.py::_snapshot_scope), "
                "so a release written here would be refused at read"
            )
        years = release_days_by_ignition_year().get(day)
        if not years:
            raise BurnSeverityReleaseError(f"{day.isoformat()} is not a governed MTBS release day")
        envelope = tuple(item for item in request.parameters if item[0] in ENVELOPE_KEYS)
        cohorts: list[CohortAnswer] = []
        bodies: list[dict[str, object]] = []
        retrieved: list[datetime] = []
        for year in years:
            cohort, features, retrieved_at = await _fetch_cohort(client, year, envelope)
            _check_cohort_release(cohort, day)
            cohorts.append(cohort)
            bodies.append({"ignition_year": year, "count": cohort.count, "features": features})
            retrieved.append(retrieved_at)
        body = json.dumps(
            {"release_day": day.isoformat(), "cohorts": bodies}, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return SourceResponse(
            request=request,
            body=body,
            retrieved_at=max(retrieved),
            payload=ReleaseDayAnswer(day=day, cohorts=tuple(cohorts)),
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:  # noqa: ARG002 - the Protocol's context
        """A complete union is `Written`; a governed release with no fire inside the envelope is an honest absence."""
        answer = _answer(day, responses)
        if not answer.records:
            years = ", ".join(str(cohort.ignition_year) for cohort in answer.cohorts)
            return Absent(
                reason="no MTBS fire of this governed release lies inside the region's burn-severity envelope",
                proof=f"returnCountOnly answered 0 for fire year(s) {years} on governed release day {day.isoformat()}",
            )
        return Written(
            expected_units=answer.expected_units,
            present_units=len(answer.records),
            source_digest=responses[0].digest,
        )

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """The base rung: every fire of the union, its polygon repaired to WKB."""
        return release_day_table(_answer(day, responses).records, observed_day=day)


STRATEGY: Final = MtbsReleaseStrategy()

__all__ = [
    "ENDPOINT",
    "ENVELOPE_PURPOSE",
    "STRATEGY",
    "BurnSeverityReleaseError",
    "CohortAnswer",
    "MtbsReleaseStrategy",
    "OversizedPageError",
    "ReleaseDayAnswer",
    "count_parameters",
    "envelope_parameters",
    "page_parameters",
    "release_unit",
]
