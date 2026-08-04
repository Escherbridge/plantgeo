"""Complete, paged capture of MTBS burn severity, dated by its publication date and never by its ignitions."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agri_data_service.config import settings
from agri_data_service.execution.contracts import (
    MAX_SOURCE_GEOJSON_BYTES,
    MAX_SOURCE_GEOJSON_FEATURES,
    canonical_json_bytes,
)
from agri_data_service.execution.source_ingestion import (
    SourceDefinition,
    SourceIngestionPlan,
    SourceReleasePlan,
)
from agri_data_service.ingest.identity import MTBS_PRODUCER, build_burn_severity_identity

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

BoundingBox = tuple[float, float, float, float]
Sleep = Callable[[float], Awaitable[None]]
BurnSeverityClass = Literal["unburned", "low", "moderate", "high", "increased_greenness"]

# The authoritative MTBS burned-area boundary service. This is NOT the endpoint the retired
# TypeScript used: `MTBS_Polygons_v1` on the NIFC ArcGIS Online org answers HTTP 400 "Invalid URL"
# and does not appear in that org's 862-service catalogue. See `ingest/AGENTS.md` "mtbs.py".
MTBS_FEATURE_SERVICE_QUERY_URL: Final = "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MTBS_01/MapServer/63/query"
MTBS_FEATURE_SERVICE_HOST: Final = "apps.fs.usda.gov"
MTBS_LAYER_NAME: Final = "Burned Area Boundaries (All Years)"

# Matches `src/__tests__/services/ingestion-jobs.test.ts:3` (west, south, east, north).
PACIFIC_NORTHWEST_BBOX: Final[BoundingBox] = (-125.0, 42.0, -111.0, 49.0)

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
IGNITION_YEAR_FIELD_NAMES: Final = ("year", "Ig_Year")
FIRE_NAME_FIELD_NAMES: Final = ("fire_name", "Fire_Name", "Incid_Name")
FIRE_TYPE_FIELD_NAMES: Final = ("fire_type", "Incid_Type")
ASSESSMENT_TYPE_FIELD_NAMES: Final = ("asmnt_type", "Asmnt_Type")
ACRES_FIELD_NAMES: Final = ("acres", "BurnBndAc")
# The per-fire mapping fingerprint. MTBS re-maps fires between releases under a stable Fire_ID, so
# a Type-2 chain must version on these rather than on geometry floats.
MAPPING_REVISION_FIELD_NAMES: Final = ("map_id", "asmnt_type", "pre_id", "post_id", "perim_id")
SEVERITY_THRESHOLD_FIELD_NAMES: Final = (
    "dnbr_offst",
    "dnbr_stddv",
    "nodata_threshold",
    "greenness_threshold",
    "low_threshold",
    "moderate_threshold",
    "high_threshold",
)

POLYGON_GEOMETRY_TYPES: Final = frozenset({"Polygon", "MultiPolygon"})

# Fire year -> the publication date of the release by which that year's mapping was complete.
#
# MTBS does NOT publish one release per fire year. It publishes QUARTERLY (the program states
# "early February, May, August and November", https://www.mtbs.gov/data-availability), and a single
# fire year accretes across several quarterly releases spanning two to four calendar years. The only
# honest single date for a cohort is therefore the date of the LAST release that added fires from
# that year -- late by construction, which under-claims knowledge but can never leak hindsight.
#
# A year still being mapped has no such date and MUST raise. Every entry is a dated release
# announcement from https://www.mtbs.gov/announcements, cross-checked against the USGS ScienceBase
# revision history for DOI 10.5066/P9IED7RZ
# (https://www.sciencebase.gov/catalog/item/5e541969e4b0ff554f753113).
MTBS_ANNUAL_RELEASE_DATES: Mapping[int, date] = MappingProxyType(
    {
        # MTBS Data Release, 24 November 2020: released "the remaining 716 fire mappings for 2018,
        # bringing the total release for 2018 fires to 1,129". The word "remaining" is an explicit
        # completion statement -- the strongest evidence in this table.
        2018: date(2020, 11, 24),
        # "2020 Data Release", 28 April 2022, added 397 fires and closed the cohort opened by the
        # "2020 Interim Data Release" of 15 February 2022 (417 fires). The next release, 10 August
        # 2022, had moved on to fire year 2021. 417 + 397 = 814 equals the live national perimeter
        # count for 2020 exactly.
        2020: date(2022, 4, 28),
        # Last of four 2021 quarterly releases: 10 August 2022 (154), 11 January 2023 (393),
        # 7 April 2023 (222) and 9 August 2023 (257). The next release, 26 October 2023, had moved
        # on to fire year 2022. The announced total of 1,026 is within six of the live national
        # perimeter count of 1,020.
        2021: date(2023, 8, 9),
        # Last of four 2022 quarterly releases: 26 October 2023 (209), 24 January 2024 (297),
        # 1 May 2024 (325) and 22 August 2024 (348). The next release, 31 October 2024, had moved on
        # to fire years 2023 and 2024. The announced total of 1,179 is within two of the live
        # national perimeter count of 1,177. Corroborated by ScienceBase revision 9.0, 22 August 2024.
        2022: date(2024, 8, 22),
    }
)

MTBS_SOURCE_KEY: Final = "mtbs-burn-severity"
MTBS_SOURCE_NAME: Final = "MTBS Burned Area Boundaries"
MTBS_SOURCE_OWNER: Final = "U.S. Geological Survey EROS and USDA Forest Service GTAC"
# `license_snapshot` on `agri.source_release` is varchar(255), copied verbatim from this string by
# `publish_source_release`, and a governed source is immutable once written. Both instruments named.
MTBS_LICENSE_NAME: Final = (
    "USGS/USFS MTBS data release; U.S. federal public domain (FGDC access constraints None, use "
    "constraints: no restrictions beyond reasonable and proper acknowledgement of sources). "
    "Obtained via the USDA FS EDW ArcGIS Server; hosting terms are separate."
)
MTBS_LICENSE_URL: Final = "https://doi.org/10.5066/P9IED7RZ"
MTBS_CITATION: Final = (
    "U.S. Geological Survey, USDA Forest Service, Nelson, K., 2021, Monitoring Trends in Burn "
    "Severity (ver. 12.0, April 2025): U.S. Geological Survey data release, "
    "https://doi.org/10.5066/P9IED7RZ."
)
MTBS_PURPOSE: Final = (
    "Burned-area boundaries for every large wildland fire mapped by the Monitoring Trends in Burn "
    "Severity program (fires over 1,000 acres in the western U.S. and over 500 acres in the "
    "eastern U.S., 1984 onward), captured one ignition-year cohort at a time so each capture "
    "carries a single honest publication date. The underlying product is a U.S. federal public "
    "domain data release of the USGS EROS and USDA Forest Service GTAC partnership, distributed "
    "under FGDC use constraints of 'no restrictions on use, except for reasonable and proper "
    "acknowledgement of information sources' and access constraints of 'None'. We obtain it "
    "through the USDA Forest Service Enterprise Data Warehouse ArcGIS Server at "
    f"{MTBS_FEATURE_SERVICE_HOST}, a separate hosting instrument from the data licence itself; an "
    "Esri-hosted ArcGIS Online mirror of the same layer exists and is deliberately not used, so no "
    "ArcGIS Online terms of service attach to this capture. `allowed_client_exposure` stays false: "
    "nothing MTBS-derived may reach a public CDN without a fresh licensing review."
)
MTBS_RELEASE_SCHEMA_VERSION: Final = "edw-mtbs-01-burned-area-boundaries-v1"

# `publish_source_release` stores an artifact with `storage_class="database_inline"`, so this is the
# ceiling for that path only -- not for a local capture, which this lane writes to disk regardless.
INLINE_PUBLICATION_BYTE_LIMIT: Final = MAX_SOURCE_GEOJSON_BYTES

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
BBOX_ORDINATE_COUNT: Final = 4
BBOX_OPTION: Final = "--bbox"


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


class SourceReview(BaseModel):
    """The operator-supplied governance review that a `SourceDefinition` may not fabricate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reviewed_at: datetime
    reviewed_by: str = Field(min_length=1, max_length=255)


class MtbsReleaseCapture(BaseModel):
    """What one ignition-year capture wrote to disk and the counts that prove it was complete."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ignition_year: int
    release_identifier: str
    data_available_at: datetime
    observed_from: datetime
    observed_to: datetime
    authoritative_count: int
    paged_feature_count: int
    record_count: int
    payload_path: Path
    payload_bytes: int
    payload_checksum: str
    # True means "publish this to object storage, never as a `database_inline` artifact".
    requires_object_storage: bool
    sidecar_path: Path | None = None
    sidecar_skipped_reason: str | None = None


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
    raw_properties = feature.get("properties")
    properties: Mapping[str, object] = raw_properties if isinstance(raw_properties, dict) else {}
    identity = build_burn_severity_identity(_identity_properties(properties))
    release_identifier = build_release_identifier(ignition_year)
    return MtbsBurnSeverityRecord(
        natural_key=identity.natural_key,
        producer=identity.producer,
        producer_local_id=identity.producer_local_id,
        geometry=_require_polygon_geometry(feature),
        release_identifier=release_identifier,
        mapping_revision=build_mapping_revision(properties, release_identifier),
        data_available_at=resolve_data_available_at(ignition_year),
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


def build_mtbs_source_definition(review: SourceReview) -> SourceDefinition:
    """Build the governed MTBS source identity; the review fields are the operator's, not this module's."""
    return SourceDefinition(
        key=MTBS_SOURCE_KEY,
        name=MTBS_SOURCE_NAME,
        owner=MTBS_SOURCE_OWNER,
        purpose=MTBS_PURPOSE,
        license_name=MTBS_LICENSE_NAME,
        license_url=MTBS_LICENSE_URL,
        citation=MTBS_CITATION,
        base_url=MTBS_FEATURE_SERVICE_QUERY_URL,
        # Every annual release is kept forever: the plan's retention rule for MTBS.
        retention_days=None,
        reviewed_at=review.reviewed_at,
        reviewed_by=review.reviewed_by,
    )


def build_release_query_parameters(ignition_year: int, bounding_box: BoundingBox, page_size: int) -> dict[str, Any]:
    """Record exactly how a cohort was requested, without the credentials the contract forbids."""
    west, south, east, north = bounding_box
    return {
        "layer_url": MTBS_FEATURE_SERVICE_QUERY_URL,
        "layer_name": MTBS_LAYER_NAME,
        "where": _release_where_clause(ignition_year),
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outSR": 4326,
        "outFields": "*",
        "orderByFields": "fire_id",
        "resultRecordCount": page_size,
    }


def build_source_ingestion_plan(
    ignition_year: int,
    bounding_box: BoundingBox,
    *,
    review: SourceReview,
    observation_window: tuple[datetime, datetime],
    page_size: int = DEFAULT_PAGE_SIZE,
) -> SourceIngestionPlan:
    """Build the reviewed sidecar a later lane hands to the warehouse; this lane persists nothing."""
    observed_from, observed_to = observation_window
    release_identifier = build_release_identifier(ignition_year)
    data_available_at = resolve_data_available_at(ignition_year)
    return SourceIngestionPlan(
        source=build_mtbs_source_definition(review),
        release=SourceReleasePlan(
            source_version=release_identifier,
            schema_version=MTBS_RELEASE_SCHEMA_VERSION,
            data_available_at=data_available_at,
            observed_from=observed_from,
            observed_to=observed_to,
            query_parameters=build_release_query_parameters(ignition_year, bounding_box, page_size),
        ),
        # The release set identifies one captured extent of one release: a second bounding box over
        # the same release is different immutable content and must not claim the same key.
        release_set_key=f"{release_identifier}-bbox-{bounding_box_token(bounding_box)}",
        release_set_as_of=data_available_at,
        description=(
            f"MTBS burned-area boundaries for fire year {ignition_year}, captured over the bounding "
            f"box {bounding_box}. `data_available_at` is the publication date of the release that "
            "completed this fire year, never an ignition date."
        ),
    )


def bounding_box_token(bounding_box: BoundingBox) -> str:
    """Fingerprint a capture extent so two extents of one release cannot overwrite each other."""
    rendered = ",".join(f"{ordinate:.4f}" for ordinate in bounding_box)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:12]


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
        response = await client.get(MTBS_FEATURE_SERVICE_QUERY_URL, params=parameters)
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


def build_release_payload(features: Sequence[Mapping[str, Any]]) -> bytes:
    """Serialise the captured cohort deterministically so its checksum is stable across runs."""
    return canonical_json_bytes({"type": "FeatureCollection", "features": list(features)})


def requires_object_storage(payload: bytes) -> bool:
    """Report whether a release is too large to be published as a `database_inline` artifact.

    A real PNW MTBS cohort is roughly 20 MB of full-resolution polygon, four times the inline
    budget, so this is the normal answer rather than an error. The capture is still written: this
    lane emits and does not persist, and the plan routes anything over 1 MB to object storage.
    Refusing to capture here would only mean no MTBS data at all. See `ingest/AGENTS.md` "mtbs.py".
    """
    return len(payload) > INLINE_PUBLICATION_BYTE_LIMIT


def release_observation_window(records: Sequence[MtbsBurnSeverityRecord]) -> tuple[datetime, datetime]:
    """Return the cohort's first and last ignition -- when it happened, never when we could know."""
    if not records:
        raise MtbsFeatureShapeError("an observation window needs at least one normalised record")
    ignition_dates = [record.ignition_date for record in records]
    return _midnight_utc(min(ignition_dates)), _midnight_utc(max(ignition_dates))


async def capture_release(
    ignition_year: int,
    *,
    bounding_box: BoundingBox,
    output_root: Path,
    client: httpx.AsyncClient,
    review: SourceReview | None = None,
) -> MtbsReleaseCapture:
    """Capture one ignition-year cohort and emit its payload and sidecar; nothing here writes to a database."""
    data_available_at = resolve_data_available_at(ignition_year)
    release_identifier = build_release_identifier(ignition_year)
    features, authoritative_count = await fetch_release_features(
        ignition_year,
        bounding_box,
        client=client,
    )
    records = [build_mtbs_record(feature, ignition_year) for feature in features]
    observed_from, observed_to = release_observation_window(records)
    validate_release_window(data_available_at, observed_to)

    payload = build_release_payload(features)
    release_root = output_root / "mtbs" / release_identifier / f"bbox-{bounding_box_token(bounding_box)}"
    release_root.mkdir(parents=True, exist_ok=True)
    payload_path = release_root / f"{release_identifier}.geojson"
    payload_path.write_bytes(payload)

    sidecar_path: Path | None = None
    sidecar_skipped_reason: str | None = None
    if review is None:
        sidecar_skipped_reason = (
            "no operator review supplied; `SourceDefinition.reviewed_at`/`reviewed_by` are governance "
            "facts this module may not fabricate. Re-run with --reviewed-by to emit the sidecar."
        )
    else:
        plan = build_source_ingestion_plan(
            ignition_year,
            bounding_box,
            review=review,
            observation_window=(observed_from, observed_to),
        )
        sidecar_path = release_root / f"{release_identifier}-ingestion-plan.json"
        sidecar_path.write_bytes(canonical_json_bytes(plan.model_dump(mode="json")))

    return MtbsReleaseCapture(
        ignition_year=ignition_year,
        release_identifier=release_identifier,
        data_available_at=data_available_at,
        observed_from=observed_from,
        observed_to=observed_to,
        authoritative_count=authoritative_count,
        paged_feature_count=len(features),
        record_count=len(records),
        payload_path=payload_path,
        payload_bytes=len(payload),
        payload_checksum=hashlib.sha256(payload).hexdigest(),
        requires_object_storage=requires_object_storage(payload),
        sidecar_path=sidecar_path,
        sidecar_skipped_reason=sidecar_skipped_reason,
    )


async def ingest_mtbs(
    ignition_years: Sequence[int],
    *,
    bounding_box: BoundingBox = PACIFIC_NORTHWEST_BBOX,
    output_root: Path | None = None,
    review: SourceReview | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[MtbsReleaseCapture]:
    """Capture every requested MTBS release. The CLI verb belongs to `cli.py`; this is its callable."""
    if not ignition_years:
        raise ValueError("ingest_mtbs requires at least one ignition year")
    root = output_root if output_root is not None else settings.local_execution_root
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json, application/json"},
            follow_redirects=False,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    try:
        return [
            await capture_release(
                ignition_year,
                bounding_box=bounding_box,
                output_root=root,
                client=client,
                review=review,
            )
            for ignition_year in ignition_years
        ]
    finally:
        if owns_client:
            await client.aclose()


def parse_bounding_box(value: str) -> BoundingBox:
    """Parse a `west,south,east,north` bounding box, rejecting an inverted or malformed envelope."""
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != BBOX_ORDINATE_COUNT:
        raise argparse.ArgumentTypeError("bbox must be west,south,east,north")
    try:
        west, south, east, north = (float(part) for part in parts)
    except ValueError as error:
        raise argparse.ArgumentTypeError("bbox ordinates must be numbers") from error
    if west >= east or south >= north:
        raise argparse.ArgumentTypeError("bbox must satisfy west < east and south < north")
    return west, south, east, north


def parse_reviewed_at(value: str) -> datetime:
    """Parse the operator's review timestamp, which must carry an explicit UTC offset."""
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError("--reviewed-at must be an ISO-8601 instant") from error
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise argparse.ArgumentTypeError("--reviewed-at must include a timezone")
    return moment.astimezone(UTC)


def inline_bbox_value(argv: Sequence[str]) -> list[str]:
    """Rewrite `--bbox -125,42,...` to `--bbox=-125,42,...`; argparse reads a leading `-` as a flag."""
    items = list(argv)
    normalised: list[str] = []
    index = 0
    while index < len(items):
        if items[index] == BBOX_OPTION and index + 1 < len(items):
            normalised.append(f"{BBOX_OPTION}={items[index + 1]}")
            index += 2
            continue
        normalised.append(items[index])
        index += 1
    return normalised


def build_argument_parser() -> argparse.ArgumentParser:
    """Describe the capture verb that `cli.py` will wrap as `ingest-mtbs`."""
    parser = argparse.ArgumentParser(
        prog="agri-ingest-mtbs",
        description="Capture MTBS burned-area boundaries one annual release at a time.",
    )
    parser.add_argument("--bbox", type=parse_bounding_box, default=PACIFIC_NORTHWEST_BBOX)
    parser.add_argument("--release-year", type=int, action="append", dest="release_years")
    parser.add_argument("--all-releases", action="store_true")
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--reviewed-by", default=None)
    parser.add_argument("--reviewed-at", type=parse_reviewed_at, default=None)
    return parser


def resolve_requested_years(release_years: Sequence[int] | None, *, all_releases: bool) -> list[int]:
    """Choose the cohorts to capture, defaulting to every year with an established release date."""
    if all_releases:
        return sorted(MTBS_ANNUAL_RELEASE_DATES)
    if release_years:
        return sorted(set(release_years))
    raise SystemExit("specify --release-year YEAR (repeatable) or --all-releases")


def build_review(reviewed_by: str | None, reviewed_at: datetime | None) -> SourceReview | None:
    """Build the governance review only from what the operator actually supplied."""
    if reviewed_by is None:
        return None
    return SourceReview(reviewed_at=reviewed_at or datetime.now(UTC), reviewed_by=reviewed_by)


def main() -> None:
    """Capture reviewed MTBS releases to the local run root without opening a database connection."""
    parser = build_argument_parser()
    args = parser.parse_args(inline_bbox_value(sys.argv[1:]))
    years = resolve_requested_years(args.release_years, all_releases=args.all_releases)
    captures = asyncio.run(
        ingest_mtbs(
            years,
            bounding_box=args.bbox,
            output_root=args.output_root,
            review=build_review(args.reviewed_by, args.reviewed_at),
        )
    )
    summary = {
        "bbox": list(args.bbox),
        "releases": [
            {
                "ignition_year": capture.ignition_year,
                "release_identifier": capture.release_identifier,
                "data_available_at": capture.data_available_at.isoformat(),
                "observed_from": capture.observed_from.isoformat(),
                "observed_to": capture.observed_to.isoformat(),
                "authoritative_count": capture.authoritative_count,
                "paged_feature_count": capture.paged_feature_count,
                "counts_agree": capture.authoritative_count == capture.paged_feature_count,
                "payload_path": str(capture.payload_path),
                "payload_bytes": capture.payload_bytes,
                "payload_checksum": capture.payload_checksum,
                "requires_object_storage": capture.requires_object_storage,
                "sidecar_path": None if capture.sidecar_path is None else str(capture.sidecar_path),
                "sidecar_skipped_reason": capture.sidecar_skipped_reason,
            }
            for capture in captures
        ],
        "total_authoritative_count": sum(capture.authoritative_count for capture in captures),
        "total_paged_feature_count": sum(capture.paged_feature_count for capture in captures),
    }
    print(json.dumps(summary, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
