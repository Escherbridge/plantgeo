"""Complete, paged capture of MTBS burn severity, dated by its publication date and never by its ignitions."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agri_data_service.execution.contracts import MAX_SOURCE_GEOJSON_FEATURES
from agri_data_service.foundation.geography.bounding_box import (
    BoundingBox as BoundingBox,  # noqa: PLC0414 - public compatibility re-export
)
from agri_data_service.foundation.region import load_region
from agri_data_service.ingest.http import UpstreamBounds
from agri_data_service.ingest.identity import (
    MTBS_PRODUCER,
    build_burn_severity_identity,
)
from agri_data_service.ingest.layer_binding import LayerBinding
from agri_data_service.warehouse.mtbs_releases import (
    MTBS_ANNUAL_RELEASE_DATES as MTBS_ANNUAL_RELEASE_DATES,  # noqa: PLC0414 - public compatibility reexport
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


Sleep = Callable[[float], Awaitable[None]]
BurnSeverityClass = Literal["unburned", "low", "moderate", "high", "increased_greenness"]

# The authoritative MTBS burned-area boundary service. This is NOT the endpoint the retired
# TypeScript used: `MTBS_Polygons_v1` on the NIFC ArcGIS Online org answers HTTP 400 "Invalid URL"
# and does not appear in that org's 862-service catalogue. See `ingest/AGENTS.md` "mtbs.py".
MTBS_FEATURE_SERVICE_QUERY_URL: Final = "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MTBS_01/MapServer/63/query"
MTBS_FEATURE_SERVICE_HOST: Final = "apps.fs.usda.gov"


def burn_severity_bounding_box() -> BoundingBox:
    """Read the burn-severity deployment envelope from the region manifest, once per call.

    Matches `src/__tests__/services/ingestion-jobs.test.ts:3` (west, south, east, north). This is a
    function and not a module constant because `federation.md` §1 forbids a module-level footprint:
    the manifest is read per call, so `PLANTGEO_REGION` set after this module is imported is still
    honoured, and a caller that depends on the region says so at the call site.
    """
    envelope = load_region().sub_envelopes["burn_severity"]
    return (envelope.west, envelope.south, envelope.east, envelope.north)


# Verbatim from the retired `src/lib/server/services/mtbs.ts:13-19`. Never renumbered, and never
# consulted with a default: an absent or unrecognised code raises rather than becoming "unburned".
_SEVERITY_CLASSES: dict[int, BurnSeverityClass] = {
    1: "unburned",
    2: "low",
    3: "moderate",
    4: "high",
    5: "increased_greenness",
}
SEVERITY_CLASS_BY_CODE: Mapping[int, BurnSeverityClass] = MappingProxyType(_SEVERITY_CLASSES)

# The live service publishes lowercase field names; the retired TypeScript assumed the capitalised
# direct-download shapefile schema. Both spellings are accepted so a schema flip cannot go unnoticed.
NATIVE_FIRE_IDENTIFIER_FIELD_NAMES: Final = ("fire_id", "Fire_ID")
SEVERITY_CODE_FIELD_NAMES: Final = ("severity", "Severity")
IGNITION_DATE_FIELD_NAMES: Final = ("ig_date", "Ig_Date")
FIRE_NAME_FIELD_NAMES: Final = ("fire_name", "Fire_Name", "Incid_Name")
FIRE_TYPE_FIELD_NAMES: Final = ("fire_type", "Incid_Type")
ASSESSMENT_TYPE_FIELD_NAMES: Final = ("asmnt_type", "Asmnt_Type")
ACRES_FIELD_NAMES: Final = ("acres", "BurnBndAc")
# The per-fire mapping fingerprint. MTBS re-maps fires between releases under a stable Fire_ID, so
# a Type-2 chain must version on these rather than on geometry floats.
MAPPING_REVISION_FIELD_NAMES: Final = ("map_id", "asmnt_type", "pre_id", "post_id", "perim_id")

POLYGON_GEOMETRY_TYPES: Final = frozenset({"Polygon", "MultiPolygon"})


# The service advertises `maxRecordCount: 2000`, but an MTBS perimeter is a full-resolution polygon
# and the host refuses an oversized response with HTTP 500 rather than truncating it. Measured on
# the 2020 PNW cohort: 50 rows is a 10.7 MB answer and succeeds, 100 rows fails. Polygon complexity
# varies by cohort, so `_fetch_page_within_service_limits` halves the window on refusal as well.
DEFAULT_PAGE_SIZE: Final = 50
MIN_PAGE_SIZE: Final = 5
MAX_PAGE_SIZE: Final = 2000
MAX_RELEASE_PAGES: Final = 400
REQUEST_TIMEOUT_SECONDS: Final = 120.0
USER_AGENT: Final = "PlantGeo-agri-ingest-mtbs/1.0"

# Sent per request rather than only on a module-owned client, so a caller that hands in the shared
# `ingest.http.upstream_client` still identifies itself and still asks for GeoJSON.
MTBS_REQUEST_HEADERS: Mapping[str, str] = MappingProxyType(
    {"User-Agent": USER_AGENT, "Accept": "application/geo+json, application/json"}
)
# The ceiling has to clear one PAGE, not one cohort: a 50-row page of full-resolution perimeter
# measured 10.7 MB, and the host answers an oversized window with HTTP 500 rather than truncating,
# which `_fetch_page_within_service_limits` handles by halving the window down to five rows.
MTBS_BOUNDS: Final = UpstreamBounds(max_bytes=24 * 1024 * 1024, timeout_seconds=REQUEST_TIMEOUT_SECONDS)

HTTP_TOO_MANY_REQUESTS: Final = 429
HTTP_SERVER_ERROR_MIN: Final = 500
HTTP_SERVER_ERROR_MAX: Final = 600
MAX_REQUEST_ATTEMPTS: Final = 4
MAX_RETRY_DELAY_SECONDS: Final = 60.0

# A release date must lead its cohort's last ignition by at least this much. MTBS has never mapped a
# fire year in under a year; anything shorter means the table has been fed an ignition-shaped date.
MIN_RELEASE_LEAD = timedelta(days=180)
# Risk 4's batch-runner tripwire: a release that claims to have been published seconds ago is a
# `now()` fallback wearing a release date.
MIN_PUBLICATION_AGE = timedelta(seconds=60)

MIN_IGNITION_YEAR: Final = 1984
MAX_IGNITION_YEAR: Final = 2200
IGNITION_DATE_DIGITS: Final = 8


class MtbsIngestError(Exception):
    """Base class for every MTBS capture failure that must stop the run rather than degrade it."""


class MtbsReleaseNotPublishedError(MtbsIngestError):
    """Raised when a fire year has no established release publication date; never fall back."""


class MtbsReleaseWindowError(MtbsIngestError):
    """Raised when a release date fails the hindsight tripwires that guard `data_available_at`."""


class MtbsTruncatedCaptureError(MtbsIngestError):
    """Raised when a paged capture cannot be proven complete against the service's own count."""


class MtbsDuplicateFeatureError(MtbsIngestError):
    """Raised when a Fire_ID repeats across pages, which proves the paging order was unstable."""


class MtbsReleaseTooLargeError(MtbsIngestError):
    """Raised when a cohort exceeds the bounded-payload contract a capture must stay inside."""


class MtbsUnknownSeverityCodeError(MtbsIngestError):
    """Raised when a severity code is unrecognised; "unburned" is a real class and never a default."""


class MtbsFeatureShapeError(MtbsIngestError):
    """Raised when an upstream feature is missing a field the warehouse record cannot be built without."""


class MtbsSeverityThresholds(BaseModel):
    """The per-fire dNBR thresholds MTBS publishes in place of a polygon-level severity class."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dnbr_offset: int | None = None
    dnbr_standard_deviation: int | None = None
    nodata_threshold: int | None = None
    greenness_threshold: int | None = None
    low_threshold: int | None = None
    moderate_threshold: int | None = None
    high_threshold: int | None = None


class MtbsBurnSeverityRecord(BaseModel):
    """One MTBS burned-area boundary, keyed by its native Fire_ID and dated by its release."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    natural_key: str
    producer: str
    producer_local_id: str
    geom_kind: Literal["polygon"] = "polygon"
    geometry: dict[str, Any]
    release_identifier: str
    mapping_revision: str
    data_available_at: datetime
    ignition_date: date
    ignition_year: int
    fire_name: str | None = None
    fire_type: str | None = None
    assessment_type: str | None = None
    acres: float | None = None
    # `None` means the source publishes no polygon-level severity class, not "unburned". MTBS
    # distributes severity as a thematic raster; `severity_thresholds` carries what the polygon
    # layer actually knows. A present but unrecognised code raises instead of landing here.
    severity_class: BurnSeverityClass | None = None
    severity_thresholds: MtbsSeverityThresholds = Field(default_factory=MtbsSeverityThresholds)


class PageWindow(BaseModel):
    """One page of a paged capture: where it starts and the most rows it may return."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    offset: int = Field(ge=0)
    size: int = Field(ge=1, le=MAX_PAGE_SIZE)


def _first_present(properties: Mapping[str, object], field_names: Sequence[str]) -> object:
    """Return the first upstream value present under any accepted spelling of a field."""
    for field_name in field_names:
        if field_name in properties:
            return properties[field_name]
    return None


def _identity_properties(properties: Mapping[str, object]) -> Mapping[str, object]:
    """Present the MTBS native key under the field name lane A's identity builder reads."""
    for field_name in NATIVE_FIRE_IDENTIFIER_FIELD_NAMES:
        if field_name in properties:
            return {"Fire_ID": properties[field_name]}
    return {}


def native_fire_identifier(properties: Mapping[str, object]) -> str:
    """Return the MTBS native Fire_ID via lane A's builder, which rejects a missing or blank one."""
    return build_burn_severity_identity(_identity_properties(properties)).producer_local_id


def resolve_data_available_at(ignition_year: int) -> datetime:
    """Annual release publication date; never Ig_Date, which leaks the mapping lag as hindsight."""
    release_date = MTBS_ANNUAL_RELEASE_DATES.get(ignition_year)
    if release_date is None:
        raise MtbsReleaseNotPublishedError(
            f"MTBS fire year {ignition_year} has no established release publication date in "
            f"MTBS_ANNUAL_RELEASE_DATES (known years: "
            f"{', '.join(str(year) for year in sorted(MTBS_ANNUAL_RELEASE_DATES))}). There is no "
            "fallback: an ignition date, `now()`, or an assumed mapping lag would each silently "
            "backdate what the warehouse claims it could have known."
        )
    return datetime(release_date.year, release_date.month, release_date.day, tzinfo=UTC)


def build_release_identifier(ignition_year: int) -> str:
    """Name the Type-2 release cohort by its fire year and its publication date."""
    release_date = resolve_data_available_at(ignition_year).date()
    return f"{MTBS_PRODUCER}-{ignition_year}-release-{release_date.isoformat()}"


def validate_release_window(
    data_available_at: datetime,
    observed_to: datetime,
    *,
    now: datetime | None = None,
) -> None:
    """Trip on a `data_available_at` that could be an ignition date or a `now()` fallback in disguise."""
    if data_available_at <= observed_to:
        raise MtbsReleaseWindowError(
            f"data_available_at {data_available_at.isoformat()} does not follow the cohort's last "
            f"ignition {observed_to.isoformat()}; a release cannot precede the fires it maps"
        )
    lead = data_available_at - observed_to
    if lead < MIN_RELEASE_LEAD:
        raise MtbsReleaseWindowError(
            f"data_available_at {data_available_at.isoformat()} leads the cohort's last ignition by "
            f"{lead.days} days, under the {MIN_RELEASE_LEAD.days}-day floor; MTBS has never mapped a "
            "fire year that fast, so this is an ignition-shaped date"
        )
    moment = now or datetime.now(UTC)
    if abs(moment - data_available_at) < MIN_PUBLICATION_AGE:
        raise MtbsReleaseWindowError(
            f"data_available_at {data_available_at.isoformat()} is within "
            f"{MIN_PUBLICATION_AGE.total_seconds():.0f}s of now; that is a `now()` fallback, not a "
            "release publication date"
        )


def parse_mtbs_ignition_date(value: object) -> date:
    """Parse the MTBS ignition date from its integer `YYYYMMDD` form or an ISO-8601 string."""
    if isinstance(value, bool) or value is None:
        raise MtbsFeatureShapeError("ignition date is required and must not be blank")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise MtbsFeatureShapeError("ignition date must be an integer YYYYMMDD or an ISO-8601 string")
    if not text:
        raise MtbsFeatureShapeError("ignition date is required and must not be blank")
    if len(text) == IGNITION_DATE_DIGITS and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError as error:
        raise MtbsFeatureShapeError(f"ignition date {text!r} is not a real calendar date") from error


def resolve_burn_severity_class(properties: Mapping[str, object]) -> BurnSeverityClass | None:
    """Decode a published severity code, or report honestly that the layer publishes none."""
    for field_name in SEVERITY_CODE_FIELD_NAMES:
        if field_name not in properties:
            continue
        value = properties[field_name]
        if value is None:
            # An explicit null is "this fire has no classified severity", not code 1.
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise MtbsUnknownSeverityCodeError(
                f"severity code {value!r} is not one of the published MTBS codes "
                f"{sorted(SEVERITY_CLASS_BY_CODE)}; refusing to substitute a class for it"
            )
        severity_class = SEVERITY_CLASS_BY_CODE.get(value)
        if severity_class is None:
            raise MtbsUnknownSeverityCodeError(
                f"severity code {value!r} is not one of the published MTBS codes "
                f"{sorted(SEVERITY_CLASS_BY_CODE)}; 'unburned' is a real class and never a default"
            )
        return severity_class
    return None


def build_severity_thresholds(properties: Mapping[str, object]) -> MtbsSeverityThresholds:
    """Carry the per-fire dNBR thresholds, which are the severity information this layer really has."""

    def threshold(field_name: str) -> int | None:
        value = properties.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return int(value)

    return MtbsSeverityThresholds(
        dnbr_offset=threshold("dnbr_offst"),
        dnbr_standard_deviation=threshold("dnbr_stddv"),
        nodata_threshold=threshold("nodata_threshold"),
        greenness_threshold=threshold("greenness_threshold"),
        low_threshold=threshold("low_threshold"),
        moderate_threshold=threshold("moderate_threshold"),
        high_threshold=threshold("high_threshold"),
    )


def build_mapping_revision(properties: Mapping[str, object], release_identifier: str) -> str:
    """Fingerprint one fire's mapping so a re-map versions the Type-2 chain without touching geometry."""
    parts = ["" if properties.get(name) is None else str(properties[name]) for name in MAPPING_REVISION_FIELD_NAMES]
    return "|".join([release_identifier, *parts])


def _optional_text(properties: Mapping[str, object], field_names: Sequence[str]) -> str | None:
    """Return an optional upstream string, normalising a blank to absent."""
    value = _first_present(properties, field_names)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _optional_number(properties: Mapping[str, object], field_names: Sequence[str]) -> float | None:
    """Return an optional upstream number, rejecting booleans that would coerce to 0 or 1."""
    value = _first_present(properties, field_names)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _require_polygon_geometry(feature: Mapping[str, object]) -> dict[str, Any]:
    """Return the feature's polygon geometry, rejecting the point and null shapes this lane cannot store."""
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        raise MtbsFeatureShapeError("MTBS feature is missing its geometry")
    geometry_type = geometry.get("type")
    if geometry_type not in POLYGON_GEOMETRY_TYPES:
        raise MtbsFeatureShapeError(
            f"MTBS geometry type {geometry_type!r} is not one of {sorted(POLYGON_GEOMETRY_TYPES)}"
        )
    return geometry


def build_mtbs_record(feature: Mapping[str, object], ignition_year: int) -> MtbsBurnSeverityRecord:
    """Normalise one upstream burned-area boundary into the warehouse record for its release."""
    return _normalise_mtbs_record(
        feature, ignition_year, build_release_identifier(ignition_year), resolve_data_available_at(ignition_year)
    )


def build_mtbs_snapshot_record(
    feature: Mapping[str, object], ignition_year: int, *, manifest_sha256: str, available_at: datetime
) -> MtbsBurnSeverityRecord:
    """Normalize a current capture with explicit immutable provenance, never a completed-cohort date."""
    if len(manifest_sha256) != hashlib.sha256().digest_size * 2 or any(
        char not in "0123456789abcdef" for char in manifest_sha256
    ):
        raise ValueError("snapshot manifest requires a lowercase SHA-256")
    if available_at.utcoffset() != timedelta(0):
        raise ValueError("snapshot availability must be UTC-aware")
    record = _normalise_mtbs_record(feature, ignition_year, f"mtbs-current-snapshot:{manifest_sha256}", available_at)
    if record.ignition_date.year != ignition_year or record.ignition_date >= available_at.date():
        raise ValueError("snapshot ignition lies outside its year or before-availability contract")
    return record


def _normalise_mtbs_record(
    feature: Mapping[str, object], ignition_year: int, release_identifier: str, available_at: datetime
) -> MtbsBurnSeverityRecord:
    raw_properties = feature.get("properties")
    properties: Mapping[str, object] = raw_properties if isinstance(raw_properties, dict) else {}
    identity = build_burn_severity_identity(_identity_properties(properties))
    return MtbsBurnSeverityRecord(
        natural_key=identity.natural_key,
        producer=identity.producer,
        producer_local_id=identity.producer_local_id,
        geometry=_require_polygon_geometry(feature),
        release_identifier=release_identifier,
        mapping_revision=build_mapping_revision(properties, release_identifier),
        data_available_at=available_at,
        ignition_date=parse_mtbs_ignition_date(_first_present(properties, IGNITION_DATE_FIELD_NAMES)),
        ignition_year=ignition_year,
        fire_name=_optional_text(properties, FIRE_NAME_FIELD_NAMES),
        fire_type=_optional_text(properties, FIRE_TYPE_FIELD_NAMES),
        assessment_type=_optional_text(properties, ASSESSMENT_TYPE_FIELD_NAMES),
        acres=_optional_number(properties, ACRES_FIELD_NAMES),
        severity_class=resolve_burn_severity_class(properties),
        severity_thresholds=build_severity_thresholds(properties),
    )


def _midnight_utc(day: date) -> datetime:
    """Anchor a calendar day at midnight UTC so every emitted timestamp is timezone-aware."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _release_where_clause(ignition_year: int) -> str:
    """Partition the service by ignition-year cohort so each capture carries one honest release date."""
    if not MIN_IGNITION_YEAR <= ignition_year <= MAX_IGNITION_YEAR:
        raise ValueError(f"ignition_year must be between {MIN_IGNITION_YEAR} and {MAX_IGNITION_YEAR}")
    return f"year = {ignition_year}"


def _bbox_parameters(bounding_box: BoundingBox) -> dict[str, str]:
    """Render the envelope filter the way the retired TypeScript did, so the spatial result matches."""
    west, south, east, north = bounding_box
    return {
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def _reject_service_error(payload: Mapping[str, Any]) -> None:
    """Surface an ArcGIS error document instead of treating it as an empty result set."""
    error = payload.get("error")
    if error:
        raise MtbsTruncatedCaptureError(f"MTBS feature service returned an error document: {error!r}")


def page_is_truncated(payload: Mapping[str, Any]) -> bool:
    """Read `exceededTransferLimit` from either the GeoJSON or the Esri JSON envelope."""
    geojson_properties = payload.get("properties")
    if isinstance(geojson_properties, dict) and geojson_properties.get("exceededTransferLimit"):
        return True
    return bool(payload.get("exceededTransferLimit"))


async def _get_query(
    client: httpx.AsyncClient,
    parameters: dict[str, str],
    *,
    sleep: Sleep = asyncio.sleep,
) -> dict[str, Any]:
    """Issue one bounded query, retrying the rate-limit and server-error answers this host emits."""
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        response = await client.get(
            MTBS_FEATURE_SERVICE_QUERY_URL,
            params=parameters,
            headers=dict(MTBS_REQUEST_HEADERS),
        )
        retryable = response.status_code == HTTP_TOO_MANY_REQUESTS or (
            HTTP_SERVER_ERROR_MIN <= response.status_code < HTTP_SERVER_ERROR_MAX
        )
        if retryable and attempt + 1 < MAX_REQUEST_ATTEMPTS:
            retry_after = response.headers.get("retry-after")
            delay = (
                min(float(retry_after), MAX_RETRY_DELAY_SECONDS)
                if retry_after and retry_after.isdigit()
                else min(2.0**attempt, MAX_RETRY_DELAY_SECONDS)
            )
            await sleep(delay)
            continue
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise MtbsTruncatedCaptureError("MTBS feature service returned a non-object response")
        _reject_service_error(payload)
        return payload
    raise MtbsTruncatedCaptureError(f"MTBS feature service failed {MAX_REQUEST_ATTEMPTS} attempts")


async def fetch_release_count(
    ignition_year: int,
    bounding_box: BoundingBox,
    *,
    client: httpx.AsyncClient,
    sleep: Sleep = asyncio.sleep,
) -> int:
    """Ask the service how many features the cohort holds; this count is the completeness gate."""
    parameters = {
        "where": _release_where_clause(ignition_year),
        **_bbox_parameters(bounding_box),
        "returnCountOnly": "true",
        "f": "json",
    }
    payload = await _get_query(client, parameters, sleep=sleep)
    count = payload.get("count")
    if isinstance(count, bool) or not isinstance(count, int):
        raise MtbsTruncatedCaptureError(
            f"MTBS returnCountOnly gave {count!r} for fire year {ignition_year}; without an "
            "authoritative count a paged capture cannot be proven complete"
        )
    return count


async def fetch_release_page(
    ignition_year: int,
    bounding_box: BoundingBox,
    *,
    client: httpx.AsyncClient,
    window: PageWindow,
    sleep: Sleep = asyncio.sleep,
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch one deterministically ordered page and report whether the service flagged more behind it."""
    parameters = {
        "where": _release_where_clause(ignition_year),
        **_bbox_parameters(bounding_box),
        "outSR": "4326",
        # `outFields=*` rather than a hand-picked subset: this lane persists the raw payload, and a
        # column dropped at fetch time cannot be recovered from the capture afterwards.
        "outFields": "*",
        # Mandatory. ArcGIS paging without a deterministic sort may repeat or skip rows between
        # pages, and no other service in this repo pages this host family.
        "orderByFields": "fire_id",
        "resultOffset": str(window.offset),
        "resultRecordCount": str(window.size),
        "returnGeometry": "true",
        "f": "geojson",
    }
    payload = await _get_query(client, parameters, sleep=sleep)
    raw_features = payload.get("features") or []
    if not isinstance(raw_features, list):
        raise MtbsTruncatedCaptureError(f"MTBS page at offset {window.offset} returned a non-list `features` member")
    return [feature for feature in raw_features if isinstance(feature, dict)], page_is_truncated(payload)


async def _fetch_page_within_service_limits(
    ignition_year: int,
    bounding_box: BoundingBox,
    *,
    client: httpx.AsyncClient,
    window: PageWindow,
    sleep: Sleep = asyncio.sleep,
) -> tuple[list[dict[str, Any]], bool, PageWindow]:
    """Fetch one page, narrowing the window when the host refuses an oversized polygon response.

    Returns the window that actually worked so the caller can keep using it. A refusal here is a
    response-size limit, not a data limit -- shrinking is the only way to page the cohort to
    completion, and abandoning the page would silently truncate exactly what this lane exists to fix.
    """
    attempted = window
    while True:
        try:
            page, truncated = await fetch_release_page(
                ignition_year,
                bounding_box,
                client=client,
                window=attempted,
                sleep=sleep,
            )
        except httpx.HTTPStatusError:
            if attempted.size <= MIN_PAGE_SIZE:
                raise
            attempted = PageWindow(offset=attempted.offset, size=max(MIN_PAGE_SIZE, attempted.size // 2))
            continue
        return page, truncated, attempted


async def fetch_release_features(
    ignition_year: int,
    bounding_box: BoundingBox,
    *,
    client: httpx.AsyncClient,
    page_size: int = DEFAULT_PAGE_SIZE,
    sleep: Sleep = asyncio.sleep,
) -> tuple[list[dict[str, Any]], int]:
    """Page one cohort to completion behind all three truncation signals, gated on the service's count."""
    authoritative_count = await fetch_release_count(ignition_year, bounding_box, client=client, sleep=sleep)
    if authoritative_count > MAX_SOURCE_GEOJSON_FEATURES:
        raise MtbsReleaseTooLargeError(
            f"MTBS fire year {ignition_year} holds {authoritative_count} features over this bounding "
            f"box, above the {MAX_SOURCE_GEOJSON_FEATURES}-feature payload contract; narrow the box"
        )
    features: list[dict[str, Any]] = []
    seen_fire_identifiers: set[str] = set()
    offset = 0
    previous_page_truncated = False
    for _ in range(MAX_RELEASE_PAGES):
        page, truncated, window = await _fetch_page_within_service_limits(
            ignition_year,
            bounding_box,
            client=client,
            window=PageWindow(offset=offset, size=page_size),
            sleep=sleep,
        )
        # A narrowed window sticks: the next page of the same cohort is no smaller.
        page_size = window.size
        if not page:
            if previous_page_truncated:
                raise MtbsTruncatedCaptureError(
                    f"MTBS fire year {ignition_year} flagged exceededTransferLimit at offset "
                    f"{offset} but then returned no further features"
                )
            break
        for feature in page:
            raw_properties = feature.get("properties")
            fire_identifier = native_fire_identifier(raw_properties if isinstance(raw_properties, dict) else {})
            if fire_identifier in seen_fire_identifiers:
                raise MtbsDuplicateFeatureError(
                    f"MTBS Fire_ID {fire_identifier!r} repeated across pages for fire year "
                    f"{ignition_year}; the paging order was not stable"
                )
            seen_fire_identifiers.add(fire_identifier)
        features.extend(page)
        offset += len(page)
        previous_page_truncated = truncated
        # Stop only when the short-page heuristic AND the service's own flag both say stop.
        if len(page) < page_size and not truncated:
            break
    else:
        raise MtbsTruncatedCaptureError(
            f"MTBS fire year {ignition_year} did not finish paging within {MAX_RELEASE_PAGES} pages"
        )
    if len(features) != authoritative_count:
        raise MtbsTruncatedCaptureError(
            f"MTBS fire year {ignition_year} paged {len(features)} features but the service counts "
            f"{authoritative_count}; the capture is incomplete"
        )
    return features, authoritative_count


def release_observation_window(records: Sequence[MtbsBurnSeverityRecord]) -> tuple[datetime, datetime]:
    """Return the cohort's first and last ignition -- when it happened, never when we could know."""
    if not records:
        raise MtbsFeatureShapeError("an observation window needs at least one normalised record")
    ignition_dates = [record.ignition_date for record in records]
    return _midnight_utc(min(ignition_dates)), _midnight_utc(max(ignition_dates))


# --- Layer binding and release-year resolution -----------------------------------------------
#
# The geo.features job path (`_run_mtbs_job`/`build_mtbs_source`) was removed 2026-10-26 with zero
# callers; burn severity is published to Parquet by `pipeline/direct/burn_severity/`.
BURN_SEVERITY_LAYER: Final = LayerBinding(
    variable="BURN_SEVERITY_LAYER_ID",
    default="burn-severity",
    channel="layer:burn-severity",
)
MTBS_CHANNEL: Final = BURN_SEVERITY_LAYER.channel
BURN_SEVERITY_LAYER_VARIABLE: Final = BURN_SEVERITY_LAYER.variable
DEFAULT_BURN_SEVERITY_LAYER_NAME: Final = BURN_SEVERITY_LAYER.default


def resolve_burn_severity_layer_name() -> str:
    """Read BURN_SEVERITY_LAYER_ID at call time so a cron environment change needs no restart."""
    return BURN_SEVERITY_LAYER.resolve()


def resolve_release_years(release_years: Sequence[int] | None) -> list[int]:
    """Choose the cohorts one job run ingests, defaulting to every year with an established release date.

    Unlike the retired capture CLI's resolver (which refused to default), this is the hourly-shaped
    verb every other producer exposes and has to do something sensible with no arguments at all. An
    explicitly requested year with no release date still raises through `resolve_data_available_at`.
    """
    if not release_years:
        return sorted(MTBS_ANNUAL_RELEASE_DATES)
    return sorted(set(release_years))
