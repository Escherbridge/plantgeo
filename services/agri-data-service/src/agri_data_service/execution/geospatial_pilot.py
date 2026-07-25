"""Idempotent local writer for the open Boise intervention-evidence pilot."""

import argparse
import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.execution.contracts import canonical_json_bytes
from agri_data_service.execution.geospatial_capture import (
    GeospatialCaptureManifest,
    GeospatialCapturePlan,
    GeospatialCaptureReceipt,
    GeospatialCaptureSource,
    geospatial_capture_root,
    load_existing_geospatial_capture,
    load_geospatial_capture_plan,
)
from agri_data_service.models.geospatial import (
    AnalysisSubject,
    InterventionAnalysisRun,
    InterventionEvidenceInput,
    InterventionEvidenceLineage,
    NormalizedSourceFeature,
)
from agri_data_service.models.provenance import (
    Artifact,
    DataSource,
    ReleaseSet,
    ReleaseSetItem,
    ReleaseSetState,
    ReleaseValidationState,
    SourceRelease,
    SourceReviewState,
)

NORMALIZATION_METHOD_KEY = "geojson-wgs84-source-feature"
NORMALIZATION_METHOD_VERSION = "1"
ANALYSIS_METHOD_KEY = "postgis-open-intervention-context"
ANALYSIS_METHOD_VERSION = "1"
PILOT_LOGICAL_KEY = "boise-hillside-hollow-open-v1"
BOUNDING_BOX_COORDINATE_COUNT = 4
BOUNDING_BOX_TOLERANCE = 1e-7
POSITION_COORDINATE_COUNT = 2
EXPECTED_GEOMETRY_DIMENSIONS = 2
WUI_VINTAGE_LATEST_POSSIBLE_TIME = datetime(2021, 1, 1, tzinfo=UTC)
WUI_VINTAGE_REFERENCE_CONVENTION = (
    "Conservative minimum-age lower bound measured from the latest possible "
    "exclusive upper bound of the WUI product's stated 2020 classification "
    "vintage (2021-01-01T00:00:00Z); the product does not provide an "
    "observation date."
)
WUI_VINTAGE_CONFIDENCE_BASIS = (
    "WUI 2020-vintage minimum age uses the exclusive upper bound "
    "2021-01-01T00:00:00Z because the product has no observation date; "
    "not current-condition evidence."
)
DERIVED_VALUES_SQL = """
WITH city AS (
    SELECT geometry FROM agri.normalized_source_feature WHERE id = :city_id
),
property AS (
    SELECT geometry FROM agri.normalized_source_feature WHERE id = :property_id
),
wui AS (
    SELECT id, geometry, attributes_json ->> 'wuiclass2020' AS class_name
    FROM agri.normalized_source_feature
    WHERE source_release_id = :wui_release_id
),
intersecting AS (
    SELECT
        wui.*,
        ST_Area(ST_Intersection(wui.geometry, property.geometry)::geography) AS overlap_area_m2
    FROM wui, property
    WHERE ST_Intersects(wui.geometry, property.geometry)
      AND ST_Area(ST_Intersection(wui.geometry, property.geometry)::geography) > 0
),
wui_union AS (
    SELECT ST_UnaryUnion(ST_Collect(geometry)) AS geometry FROM intersecting
)
SELECT
    ST_Area(city.geometry::geography) AS city_area_m2,
    ST_Area(property.geometry::geography) AS property_area_m2,
    ST_CoveredBy(property.geometry, city.geometry) AS property_covered_by_city,
    COALESCE(
        ST_Area(
            ST_Intersection(property.geometry, wui_union.geometry)::geography
        ) / NULLIF(ST_Area(property.geometry::geography), 0),
        0
    ) AS wui_overlap_fraction,
    COALESCE(
        (SELECT string_agg(DISTINCT class_name, ',' ORDER BY class_name)
         FROM intersecting),
        'none'
    ) AS wui_classes,
    COALESCE(
        (SELECT array_agg(id ORDER BY id) FROM intersecting),
        ARRAY[]::uuid[]
    ) AS intersecting_wui_feature_ids,
    FLOOR(
        EXTRACT(
            EPOCH FROM (
                CAST(:as_of_time AS timestamptz)
                - CAST(:wui_reference_time AS timestamptz)
            )
        ) / 86400
    ) AS wui_vintage_minimum_age_days
FROM city, property, wui_union
""".strip()

SOURCE_ATTRIBUTE_ALLOWLISTS: dict[str, tuple[str, ...]] = {
    "census-tigerweb-boise-2025": (
        "STATE",
        "PLACE",
        "GEOID",
        "BASENAME",
        "NAME",
        "LSADC",
        "FUNCSTAT",
        "AREALAND",
        "AREAWATER",
        "CENTLAT",
        "CENTLON",
    ),
    "osm-hillside-to-hollow-20260723": (
        "osm_type",
        "osm_id",
        "category",
        "type",
        "name",
    ),
    "usfs-wui-2020-hillside-hollow": (
        "objectid",
        "blk20",
        "state",
        "veg2019pc",
        "hu2020",
        "huden2020",
        "wuiflag2020",
        "wuiclass2020",
    ),
}
SOURCE_NATIVE_SCALE = {
    "census-tigerweb-boise-2025": "2025 TIGERweb incorporated-place vector; not survey grade",
    "osm-hillside-to-hollow-20260723": "Contributor-mapped named reserve boundary; no stated positional accuracy",
    "usfs-wui-2020-hillside-hollow": "2020 Census block aggregation with 2019 NLCD vegetation context",
}
SOURCE_CONFIDENCE_BASIS = {
    "census-tigerweb-boise-2025": (
        "Authoritative Census incorporated-place geography; not a legal land description or survey."
    ),
    "osm-hillside-to-hollow-20260723": (
        "OpenStreetMap contributor geometry identifies a named property but is non-cadastral."
    ),
    "usfs-wui-2020-hillside-hollow": (
        "USFS national 2020 census-block WUI classification supports neighborhood exposure context only."
    ),
}
SOURCE_LICENCE_OBLIGATIONS = {
    "census-tigerweb-boise-2025": "Public-domain US government work; retain source and vintage citation.",
    "osm-hillside-to-hollow-20260723": (
        "ODbL attribution and database share-alike obligations apply; Nominatim attribution and "
        "usage-policy requirements also apply."
    ),
    "usfs-wui-2020-hillside-hollow": (
        "No fee or additional permission required; retain the Forest Service archive citation."
    ),
}
GAP_INPUTS = {
    "legal_cadastral_boundary_and_use_authority": (
        "Obtain a legal parcel/survey boundary plus current ownership, easement, and intervention authority."
    ),
    "structure_inventory_and_defensible_space_inspection": (
        "Field-verify structures, materials, access, ignition zones, vegetation, and defensible-space distances."
    ),
    "terrain_fuels_canopy_egress_and_fire_history": (
        "Add current high-resolution terrain, fuels, canopy, building, egress, and local fire-history evidence."
    ),
    "watershed_drainage_wetland_groundwater_and_infiltration": (
        "Add drainage survey, wetland review, groundwater context, and field infiltration measurements."
    ),
    "current_drought_weather_and_soil_moisture": (
        "Add explicitly redistributable current drought, precipitation, temperature, wind, humidity, "
        "and soil-moisture context. Preserve existing USDM history but exclude it until authoritative "
        "redistribution terms are archived."
    ),
    "water_right_capacity_quality_and_infrastructure": (
        "Verify legal water source/right, seasonal capacity, lab quality, storage, conveyance, and discharge."
    ),
    "aquaponics_hydroponics_feasibility": (
        "Verify code, energy backup, loading, climate control, discharge, biosecurity, and food safety."
    ),
    "silvopasture_agroforestry_feasibility": (
        "Verify soils, slope, water budget, vegetation, species, livestock/wildlife, access, and land use."
    ),
    "regulatory_and_professional_review": (
        "Confirm current fire, building, water, conservation, agricultural, and environmental requirements."
    ),
}


class PilotIngestionReceipt(BaseModel):
    """Stable summary of one validated local evidence release."""

    model_config = ConfigDict(extra="forbid")

    pilot_key: str
    capture_plan_checksum: str
    capture_receipt_set_checksum: str
    release_set_id: uuid.UUID
    release_set_logical_key: str
    release_set_manifest_checksum: str
    source_release_ids: dict[str, uuid.UUID]
    normalized_feature_count: int
    subject_ids: dict[str, uuid.UUID]
    analysis_run_id: uuid.UUID
    evidence_counts: dict[str, int]
    analysis_output_checksum: str
    publication_advanced: bool = False
    life_safety_prediction: bool = False


@dataclass(frozen=True)
class CapturedFeature:
    source: GeospatialCaptureSource
    receipt: GeospatialCaptureReceipt
    raw_bytes: bytes
    reference_bytes: bytes | None
    feature_key: str
    geometry_json: str
    attributes: dict[str, Any]
    feature_checksum: str


@dataclass(frozen=True)
class PersistedFeature:
    captured: CapturedFeature
    source_release: SourceRelease
    artifact: Artifact
    normalized: NormalizedSourceFeature


@dataclass(frozen=True)
class DerivedValues:
    city_area_m2: float
    property_area_m2: float
    property_covered_by_city: bool
    wui_overlap_fraction: float
    wui_classes: str
    intersecting_wui_feature_ids: tuple[uuid.UUID, ...]
    wui_vintage_minimum_age_days: int
    postgis_version: str


def sha256_json(value: object) -> str:
    """Hash canonical JSON."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _assert_contract_fields(record: object, expected: dict[str, Any], label: str) -> None:
    """Reject idempotent reuse unless every governed field still matches."""
    drift = [
        field_name for field_name, expected_value in expected.items() if getattr(record, field_name) != expected_value
    ]
    if drift:
        raise ValueError(f"existing {label} differs in governed fields: {', '.join(sorted(drift))}")


def load_validated_pilot_bundle(
    plan_path: Path,
    capture_base: Path,
) -> tuple[GeospatialCapturePlan, Path, GeospatialCaptureManifest, list[CapturedFeature]]:
    """Load reviewed open bytes and fail before a database connection on drift."""
    plan = load_geospatial_capture_plan(plan_path)
    if any(source.redistribution_status != "allowed" for source in plan.sources):
        raise ValueError("the intervention pilot writer accepts only explicitly open sources")
    missing_allowlists = {source.key for source in plan.sources} - SOURCE_ATTRIBUTE_ALLOWLISTS.keys()
    if missing_allowlists:
        raise ValueError(f"missing source attribute allowlists: {sorted(missing_allowlists)}")
    target = geospatial_capture_root(capture_base, plan)
    manifest, payloads = load_existing_geospatial_capture(target, plan)
    receipts = {receipt.source_key: receipt for receipt in manifest.receipts}
    features: list[CapturedFeature] = []
    for source in plan.sources:
        receipt = receipts[source.key]
        raw_bytes = payloads[source.key]
        reference_bytes = payloads.get(f"{source.key}:reference")
        payload = json.loads(raw_bytes)
        allowlist = SOURCE_ATTRIBUTE_ALLOWLISTS[source.key]
        for feature in payload["features"]:
            properties = feature["properties"]
            feature_key = str(properties[source.feature_key_property]).strip()
            attributes = {key: properties[key] for key in allowlist if key in properties}
            features.append(
                CapturedFeature(
                    source=source,
                    receipt=receipt,
                    raw_bytes=raw_bytes,
                    reference_bytes=reference_bytes,
                    feature_key=feature_key,
                    geometry_json=json.dumps(feature["geometry"], separators=(",", ":"), sort_keys=True),
                    attributes=attributes,
                    feature_checksum=sha256_json(
                        {
                            "source_key": source.key,
                            "feature_key": feature_key,
                            "geometry": feature["geometry"],
                            "attributes": attributes,
                        }
                    ),
                )
            )
    _validate_pilot_source_semantics(plan, features)
    return plan, target, manifest, features


def _validate_pilot_source_semantics(
    plan: GeospatialCapturePlan,
    features: list[CapturedFeature],
) -> None:
    """Bind source-specific meaning and the complete WUI AOI query before DB access."""
    by_source: dict[str, list[CapturedFeature]] = {
        source.key: [feature for feature in features if feature.source.key == source.key] for source in plan.sources
    }
    expected_source_keys = set(SOURCE_ATTRIBUTE_ALLOWLISTS)
    if set(by_source) != expected_source_keys:
        raise ValueError("pilot source set differs from the reviewed semantic contract")
    for source_key, source_features in by_source.items():
        required = set(SOURCE_ATTRIBUTE_ALLOWLISTS[source_key])
        for feature in source_features:
            if set(feature.attributes) != required:
                raise ValueError(f"{source_key} is missing a required governed attribute")

    census = by_source["census-tigerweb-boise-2025"][0].attributes
    if census["STATE"] != "16" or census["PLACE"] != "08830" or census["FUNCSTAT"] != "A":
        raise ValueError("Census feature is not the reviewed active Boise incorporated place")

    property_feature = by_source["osm-hillside-to-hollow-20260723"][0]
    osm = property_feature.attributes
    if (
        osm["osm_type"] != "way"
        or str(osm["osm_id"]) != "674700373"
        or osm["category"] != "leisure"
        or osm["type"] != "nature_reserve"
        or osm["name"] != "Hillside to Hollow Reserve"
        or property_feature.source.provider_version != "OSM way 674700373 v19 at 2025-04-09T18:53:39Z"
    ):
        raise ValueError("OSM feature is not the reviewed versioned non-cadastral reserve")

    wui_features = by_source["usfs-wui-2020-hillside-hollow"]
    if any(
        feature.attributes["state"] != "ID"
        or feature.attributes["wuiflag2020"] != 1
        or not feature.attributes["wuiclass2020"]
        for feature in wui_features
    ):
        raise ValueError("WUI capture contains a feature outside the reviewed Idaho WUI classification")
    wui_source = wui_features[0].source
    query = parse_qs(urlsplit(wui_source.url).query)
    property_bbox = _geometry_bbox(json.loads(property_feature.geometry_json)["coordinates"])
    query_bbox = tuple(float(value) for value in query.get("geometry", [""])[0].split(","))
    if (
        query.get("where") != ["wuiflag2020=1"]
        or query.get("geometryType") != ["esriGeometryEnvelope"]
        or query.get("spatialRel") != ["esriSpatialRelIntersects"]
        or query.get("orderByFields") != ["objectid"]
        or len(query_bbox) != BOUNDING_BOX_COORDINATE_COUNT
        or any(
            abs(left - right) > BOUNDING_BOX_TOLERANCE for left, right in zip(query_bbox, property_bbox, strict=True)
        )
    ):
        raise ValueError("WUI request is not the complete pinned property-bounding-box AOI query")


def _geometry_bbox(coordinates: object) -> tuple[float, float, float, float]:
    points: list[tuple[float, float]] = []

    def collect(value: object) -> None:
        if (
            isinstance(value, list)
            and len(value) >= POSITION_COORDINATE_COUNT
            and isinstance(value[0], (int, float))
            and isinstance(value[1], (int, float))
        ):
            points.append((float(value[0]), float(value[1])))
            return
        if isinstance(value, list):
            for child in value:
                collect(child)

    collect(coordinates)
    if not points:
        raise ValueError("property geometry has no coordinate pairs")
    longitudes = [point[0] for point in points]
    latitudes = [point[1] for point in points]
    return min(longitudes), min(latitudes), max(longitudes), max(latitudes)


async def ingest_boise_intervention_pilot(
    session: AsyncSession,
    *,
    plan_path: Path,
    capture_base: Path,
) -> PilotIngestionReceipt:
    """Write the reviewed pilot in one caller-owned transaction."""
    plan, capture_root, manifest, features = load_validated_pilot_bundle(plan_path, capture_base)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": f"geospatial-pilot:{PILOT_LOGICAL_KEY}"},
    )

    persisted: list[PersistedFeature] = []
    source_releases: dict[str, SourceRelease] = {}
    for source in plan.sources:
        source_features = [feature for feature in features if feature.source.key == source.key]
        receipt = source_features[0].receipt
        data_source = await _get_or_create_data_source(
            session,
            source,
            reviewed_at=datetime.now(UTC),
        )
        source_release = await _get_or_create_source_release(
            session,
            source=source,
            receipt=receipt,
            data_source=data_source,
            validated_at=datetime.now(UTC),
        )
        artifact = await _get_or_create_artifact(
            session,
            source=source,
            receipt=receipt,
            source_release=source_release,
            capture_root=capture_root,
            raw_bytes=source_features[0].raw_bytes,
        )
        await _get_or_create_reference_artifact(
            session,
            source=source,
            receipt=receipt,
            source_release=source_release,
            capture_root=capture_root,
            reference_bytes=source_features[0].reference_bytes,
        )
        source_releases[source.key] = source_release
        for captured in source_features:
            persisted.append(  # noqa: PERF401
                PersistedFeature(
                    captured=captured,
                    source_release=source_release,
                    artifact=artifact,
                    normalized=await _get_or_create_normalized_feature(
                        session,
                        captured=captured,
                        source_release=source_release,
                        artifact=artifact,
                        data_available_at=source_release.data_available_at,
                    ),
                )
            )

    indexed = {(item.captured.source.key, item.captured.feature_key): item for item in persisted}
    city_feature = indexed[("census-tigerweb-boise-2025", "1608830")]
    property_feature = indexed[("osm-hillside-to-hollow-20260723", "674700373")]
    wui_features = [item for item in persisted if item.captured.source.key == "usfs-wui-2020-hillside-hollow"]
    city_subject = await _get_or_create_subject(
        session,
        feature=city_feature,
        subject_key="us-id-boise-city-1608830",
        subject_kind="city",
        display_name="Boise city, Idaho",
        parent_subject=None,
    )
    property_subject = await _get_or_create_subject(
        session,
        feature=property_feature,
        subject_key="osm-way-674700373-hillside-to-hollow-reserve",
        subject_kind="property",
        display_name="Hillside to Hollow Reserve",
        parent_subject=city_subject,
    )
    release_set = await _get_or_create_release_set(
        session,
        plan=plan,
        manifest=manifest,
        source_releases=source_releases,
    )
    sql_bind_parameters = {
        "city_id": city_feature.normalized.id,
        "property_id": property_feature.normalized.id,
        "wui_release_id": wui_features[0].source_release.id,
        "as_of_time": release_set.as_of_time,
        "wui_reference_time": WUI_VINTAGE_LATEST_POSSIBLE_TIME,
    }
    bind_parameter_contract = {
        key: value.isoformat() if isinstance(value, datetime) else str(value)
        for key, value in sql_bind_parameters.items()
    }
    derived = await _derive_values(
        session,
        wui_features,
        bind_parameters=sql_bind_parameters,
    )
    intersecting_wui = sorted(
        (item for item in wui_features if item.normalized.id in derived.intersecting_wui_feature_ids),
        key=lambda item: item.captured.feature_key,
    )
    rounded_values = {
        "city_boundary_geometry_area_m2": round(derived.city_area_m2),
        "osm_reserve_geometry_area_m2_context": round(derived.property_area_m2, -2),
        "property_covered_by_city_boundary": derived.property_covered_by_city,
        "usfs_wui_2020_census_block_geometry_overlap_fraction_context": round(derived.wui_overlap_fraction, 2),
        "usfs_wui_2020_census_block_class_context": derived.wui_classes,
        "usfs_wui_vintage_minimum_age_days_at_capture": (derived.wui_vintage_minimum_age_days),
    }
    output_contract = {
        "method_key": ANALYSIS_METHOD_KEY,
        "method_version": ANALYSIS_METHOD_VERSION,
        "sql_sha256": hashlib.sha256(DERIVED_VALUES_SQL.encode()).hexdigest(),
        "bind_parameters": bind_parameter_contract,
        "wui_vintage_reference_convention": WUI_VINTAGE_REFERENCE_CONVENTION,
        "postgis_version": derived.postgis_version,
        "rounding": {
            "city_area_m2": "nearest 1 m2",
            "osm_property_area_m2": "nearest 100 m2",
            "wui_overlap_fraction": "nearest 0.01",
            "wui_vintage_minimum_age_days": ("whole elapsed days from the 2021-01-01 exclusive upper bound"),
        },
        "inputs": [
            {
                "source_key": item.captured.source.key,
                "feature_key": item.captured.feature_key,
                "feature_checksum": item.normalized.feature_checksum,
                "geometry_checksum": item.normalized.geometry_checksum,
            }
            for item in [city_feature, property_feature, *intersecting_wui]
        ],
        "values": rounded_values,
    }
    analysis_run = await _get_or_create_analysis_run(
        session,
        release_set=release_set,
        manifest=manifest,
        output_contract=output_contract,
        row_count=len(rounded_values),
    )
    evidence = await _write_evidence_set(
        session,
        release_set=release_set,
        city_subject=city_subject,
        property_subject=property_subject,
        city_feature=city_feature,
        property_feature=property_feature,
        wui_features=wui_features,
        derived=derived,
        analysis_run=analysis_run,
        rounded_values=rounded_values,
    )
    evidence_counts = {
        kind: sum(item.evidence_kind == kind for item in evidence)
        for kind in ("observed_fact", "model_derived_feature", "known_gap")
    }
    return PilotIngestionReceipt(
        pilot_key=plan.pilot_key,
        capture_plan_checksum=manifest.plan_checksum,
        capture_receipt_set_checksum=manifest.receipt_set_checksum,
        release_set_id=release_set.id,
        release_set_logical_key=release_set.logical_key,
        release_set_manifest_checksum=release_set.manifest_checksum,
        source_release_ids={key: value.id for key, value in sorted(source_releases.items())},
        normalized_feature_count=len(persisted),
        subject_ids={"city": city_subject.id, "property": property_subject.id},
        analysis_run_id=analysis_run.id,
        evidence_counts=evidence_counts,
        analysis_output_checksum=analysis_run.output_checksum,
    )


async def _get_or_create_data_source(
    session: AsyncSession,
    source: GeospatialCaptureSource,
    *,
    reviewed_at: datetime,
) -> DataSource:
    expected = {
        "key": source.key,
        "name": f"{source.provider}: {source.source_kind}",
        "owner": source.provider,
        "purpose": "Open geospatial evidence for the Boise resolution-aware intervention pilot.",
        "base_url": f"https://{source.allowed_host}",
        "license_name": source.licence_name,
        "license_url": source.licence_url,
        "citation": source.citation,
        "refresh_policy": {"mode": "manual_reviewed_capture", "schema_version": 1},
        "retention_days": None,
        "allowed_client_exposure": True,
        "review_state": SourceReviewState.APPROVED,
        "review_due_at": None,
        "reviewed_by": "north-america-intervention-data-workstream",
        "is_active": True,
        "configuration": {
            "metadata_url": source.metadata_url,
            "provider_version": source.provider_version,
            "redistribution_status": source.redistribution_status,
            "licence_obligations": SOURCE_LICENCE_OBLIGATIONS[source.key],
            "spatial_support_kind": source.spatial_support_kind,
            "native_resolution_m": source.native_resolution_m,
            "native_scale_denominator": source.native_scale_denominator,
            "maximum_inference_scale": source.maximum_inference_scale,
        },
    }
    data_source = await session.scalar(select(DataSource).where(DataSource.key == source.key))
    if data_source is None:
        data_source = DataSource(
            **expected,
            reviewed_at=reviewed_at,
        )
        session.add(data_source)
        await session.flush()
    else:
        _assert_contract_fields(data_source, expected, f"data source {source.key}")
        if data_source.reviewed_at is None:
            raise ValueError(f"existing data source {source.key} has no review timestamp")
    return data_source


async def _get_or_create_source_release(
    session: AsyncSession,
    *,
    source: GeospatialCaptureSource,
    receipt: GeospatialCaptureReceipt,
    data_source: DataSource,
    validated_at: datetime,
) -> SourceRelease:
    release = await session.scalar(
        select(SourceRelease).where(
            SourceRelease.data_source_id == data_source.id,
            SourceRelease.source_version == source.provider_version,
            SourceRelease.payload_checksum == receipt.checksum_sha256,
            SourceRelease.transform_version == "raw-capture-v1",
        )
    )
    observed_from, observed_to = _source_observation_window(source.key)
    reference_receipt = receipt.reference_receipt
    release_retrieved_at = (
        max(receipt.retrieved_at, reference_receipt.retrieved_at)
        if reference_receipt is not None
        else receipt.retrieved_at
    )
    reference_contract = (
        {
            "output_file": reference_receipt.output_file,
            "request_url": reference_receipt.request_url,
            "response_url": reference_receipt.response_url,
            "retrieved_at": reference_receipt.retrieved_at.isoformat(),
            "checksum_sha256": reference_receipt.checksum_sha256,
            "size_bytes": reference_receipt.size_bytes,
            "content_type": reference_receipt.content_type,
            "etag": reference_receipt.etag,
            "last_modified": reference_receipt.last_modified,
            "assertions": source.reference_capture.expected_json_values,
        }
        if reference_receipt is not None and source.reference_capture is not None
        else None
    )
    expected = {
        "data_source_id": data_source.id,
        "source_version": source.provider_version,
        "retrieved_at": release_retrieved_at,
        "data_available_at": release_retrieved_at,
        "observed_from": observed_from,
        "observed_to": observed_to,
        "payload_checksum": receipt.checksum_sha256,
        "payload_bytes": receipt.size_bytes,
        "schema_version": "geojson-featurecollection-v1",
        "transform_version": "raw-capture-v1",
        "license_snapshot": (
            f"{source.licence_name} | {source.licence_url} | "
            f"redistribution={source.redistribution_status} | {source.citation}"
        ),
        "query_parameters": {
            "request_url": receipt.request_url,
            "query": {
                key: sorted(values) for key, values in sorted(parse_qs(urlsplit(receipt.request_url).query).items())
            },
        },
        "quality_summary": {
            "feature_count": receipt.feature_count,
            "feature_keys": receipt.feature_keys,
            "geometry_types": receipt.geometry_types,
            "content_type": receipt.content_type,
            "etag": receipt.etag,
            "last_modified": receipt.last_modified,
            "reference_receipt": reference_contract,
            "maximum_inference_scale": source.maximum_inference_scale,
            "confidence_basis": SOURCE_CONFIDENCE_BASIS[source.key],
        },
        "validation_state": ReleaseValidationState.VALID,
        "supersedes_release_id": None,
        "retraction_reason": None,
    }
    if release is None:
        release = SourceRelease(
            **expected,
            validated_at=validated_at,
        )
        session.add(release)
        await session.flush()
    else:
        _assert_contract_fields(release, expected, f"source release {source.key}")
        if release.validated_at is None or release.validated_at < release_retrieved_at:
            raise ValueError(f"existing source release {source.key} has an invalid validation timestamp")
    return release


async def _get_or_create_artifact(  # noqa: PLR0913
    session: AsyncSession,
    *,
    source: GeospatialCaptureSource,
    receipt: GeospatialCaptureReceipt,
    source_release: SourceRelease,
    capture_root: Path,
    raw_bytes: bytes,
) -> Artifact:
    uri = f"local-capture://{capture_root.parent.name}/{capture_root.name}/{receipt.output_file}"
    expected = {
        "source_release_id": source_release.id,
        "kind": "raw_geospatial_source",
        "uri": uri,
        "media_type": receipt.content_type or source.expected_media_type,
        "checksum_sha256": receipt.checksum_sha256,
        "size_bytes": receipt.size_bytes,
        "storage_class": "database_inline",
        "encryption_key_ref": None,
        "metadata_json": {
            "source_key": source.key,
            "request_url": receipt.request_url,
            "response_url": receipt.response_url,
            "retrieved_at": receipt.retrieved_at.isoformat(),
            "etag": receipt.etag,
            "last_modified": receipt.last_modified,
            "plan_source": source.model_dump(mode="json"),
        },
        "content_bytes": raw_bytes,
    }
    artifact = await session.scalar(
        select(Artifact).where(
            Artifact.uri == uri,
            Artifact.checksum_sha256 == receipt.checksum_sha256,
        )
    )
    if artifact is None:
        artifact = Artifact(**expected)
        session.add(artifact)
        await session.flush()
    else:
        _assert_contract_fields(artifact, expected, f"artifact {uri}")
    return artifact


async def _get_or_create_reference_artifact(  # noqa: PLR0913
    session: AsyncSession,
    *,
    source: GeospatialCaptureSource,
    receipt: GeospatialCaptureReceipt,
    source_release: SourceRelease,
    capture_root: Path,
    reference_bytes: bytes | None,
) -> Artifact | None:
    """Persist planned source-version evidence as a separate immutable artifact."""
    reference = source.reference_capture
    reference_receipt = receipt.reference_receipt
    if reference is None:
        if reference_receipt is not None or reference_bytes is not None:
            raise ValueError("unplanned source reference cannot be persisted")
        return None
    if reference_receipt is None or reference_bytes is None:
        raise ValueError("planned source reference bytes are required")
    uri = f"local-capture://{capture_root.parent.name}/{capture_root.name}/{reference_receipt.output_file}"
    expected = {
        "source_release_id": source_release.id,
        "kind": "source_metadata_reference",
        "uri": uri,
        "media_type": (reference_receipt.content_type or reference.expected_media_type),
        "checksum_sha256": reference_receipt.checksum_sha256,
        "size_bytes": reference_receipt.size_bytes,
        "storage_class": "database_inline",
        "encryption_key_ref": None,
        "metadata_json": {
            "source_key": source.key,
            "request_url": reference_receipt.request_url,
            "response_url": reference_receipt.response_url,
            "retrieved_at": reference_receipt.retrieved_at.isoformat(),
            "etag": reference_receipt.etag,
            "last_modified": reference_receipt.last_modified,
            "expected_json_values": reference.expected_json_values,
        },
        "content_bytes": reference_bytes,
    }
    artifact = await session.scalar(
        select(Artifact).where(
            Artifact.uri == uri,
            Artifact.checksum_sha256 == reference_receipt.checksum_sha256,
        )
    )
    if artifact is None:
        artifact = Artifact(**expected)
        session.add(artifact)
        await session.flush()
    else:
        _assert_contract_fields(artifact, expected, f"reference artifact {uri}")
    return artifact


async def _get_or_create_normalized_feature(
    session: AsyncSession,
    *,
    captured: CapturedFeature,
    source_release: SourceRelease,
    artifact: Artifact,
    data_available_at: datetime,
) -> NormalizedSourceFeature:
    existing = await session.scalar(
        select(NormalizedSourceFeature).where(
            NormalizedSourceFeature.source_release_id == source_release.id,
            NormalizedSourceFeature.feature_key == captured.feature_key,
            NormalizedSourceFeature.method_key == NORMALIZATION_METHOD_KEY,
            NormalizedSourceFeature.method_version == NORMALIZATION_METHOD_VERSION,
        )
    )
    geometry_checksum = await _validate_postgis_geometry(session, captured.geometry_json)
    source = captured.source
    numeric_value: float | None = None
    text_value: str | None = None
    value_unit: str | None = None
    if source.key == "census-tigerweb-boise-2025":
        numeric_value = float(captured.attributes["AREALAND"])
        value_unit = "m2"
    elif source.key == "osm-hillside-to-hollow-20260723":
        text_value = str(captured.attributes["name"])
    else:
        text_value = str(captured.attributes["wuiclass2020"])
    observed_from, observed_to = _source_observation_window(source.key)
    expected = {
        "source_release_id": source_release.id,
        "artifact_id": artifact.id,
        "feature_key": captured.feature_key,
        "feature_kind": source.source_kind,
        "geometry_checksum": geometry_checksum,
        "observed_from": observed_from,
        "observed_to": observed_to,
        "data_available_at": data_available_at,
        "numeric_value": numeric_value,
        "text_value": text_value,
        "value_unit": value_unit,
        "attributes_json": captured.attributes,
        "spatial_support_kind": source.spatial_support_kind,
        "native_resolution_m": source.native_resolution_m,
        "native_scale": SOURCE_NATIVE_SCALE[source.key],
        "maximum_inference_scale": source.maximum_inference_scale,
        "confidence": None,
        "confidence_basis": SOURCE_CONFIDENCE_BASIS[source.key],
        "method_key": NORMALIZATION_METHOD_KEY,
        "method_version": NORMALIZATION_METHOD_VERSION,
        "feature_checksum": captured.feature_checksum,
        "is_life_safety_validated": False,
    }
    if existing is not None:
        _assert_contract_fields(existing, expected, f"normalized feature {captured.feature_key}")
        stored_geometry_checksum = await _stored_geometry_checksum(
            session,
            table_name="normalized_source_feature",
            geometry_column="geometry",
            record_id=existing.id,
        )
        if stored_geometry_checksum != geometry_checksum:
            raise ValueError(f"stored geometry drifted for normalized feature {captured.feature_key}")
        return existing

    feature = NormalizedSourceFeature(
        **expected,
        geometry=func.ST_SetSRID(func.ST_GeomFromGeoJSON(captured.geometry_json), 4326),
    )
    session.add(feature)
    await session.flush()
    return feature


async def _get_or_create_subject(  # noqa: PLR0913
    session: AsyncSession,
    *,
    feature: PersistedFeature,
    subject_key: str,
    subject_kind: str,
    display_name: str,
    parent_subject: AnalysisSubject | None,
) -> AnalysisSubject:
    parent_subject_id = parent_subject.id if parent_subject else None
    subject_version = sha256_json(
        {
            "feature_checksum": feature.normalized.feature_checksum,
            "parent_subject_key": parent_subject.subject_key if parent_subject else None,
            "parent_subject_version": parent_subject.subject_version if parent_subject else None,
        }
    )
    subject = await session.scalar(
        select(AnalysisSubject).where(
            AnalysisSubject.subject_key == subject_key,
            AnalysisSubject.subject_version == subject_version,
        )
    )
    expected = {
        "subject_key": subject_key,
        "subject_version": subject_version,
        "subject_kind": subject_kind,
        "parent_subject_id": parent_subject_id,
        "supersedes_subject_id": None,
        "source_release_id": feature.source_release.id,
        "artifact_id": feature.artifact.id,
        "source_feature_id": feature.normalized.id,
        "display_name": display_name,
        "country_code": "US",
        "geometry_checksum": feature.normalized.geometry_checksum,
        "spatial_support_kind": ("administrative_boundary" if subject_kind == "city" else "native_polygon"),
        "native_resolution_m": None,
        "native_scale": SOURCE_NATIVE_SCALE[feature.captured.source.key],
        "maximum_inference_scale": feature.captured.source.maximum_inference_scale,
        "confidence": None,
        "confidence_basis": SOURCE_CONFIDENCE_BASIS[feature.captured.source.key],
        "method_key": NORMALIZATION_METHOD_KEY,
        "method_version": NORMALIZATION_METHOD_VERSION,
        "is_life_safety_validated": False,
    }
    if subject is None:
        subject = AnalysisSubject(
            **expected,
            geometry=func.ST_SetSRID(func.ST_GeomFromGeoJSON(feature.captured.geometry_json), 4326),
        )
        session.add(subject)
        await session.flush()
    else:
        _assert_contract_fields(subject, expected, f"analysis subject {subject_key}")
        stored_geometry_checksum = await _stored_geometry_checksum(
            session,
            table_name="analysis_subject",
            geometry_column="geometry",
            record_id=subject.id,
        )
        if stored_geometry_checksum != feature.normalized.geometry_checksum:
            raise ValueError(f"stored geometry drifted for analysis subject {subject_key}")
    return subject


async def _get_or_create_release_set(
    session: AsyncSession,
    *,
    plan: GeospatialCapturePlan,
    manifest: GeospatialCaptureManifest,
    source_releases: dict[str, SourceRelease],
) -> ReleaseSet:
    as_of_time = max(release.data_available_at for release in source_releases.values())
    manifest_contract = {
        "pilot_key": plan.pilot_key,
        "capture_plan_checksum": manifest.plan_checksum,
        "capture_receipt_set_checksum": manifest.receipt_set_checksum,
        "members": [
            {
                "source_key": key,
                "source_version": source_releases[key].source_version,
                "payload_checksum": source_releases[key].payload_checksum,
                "transform_version": source_releases[key].transform_version,
            }
            for key in sorted(source_releases)
        ],
    }
    manifest_checksum = sha256_json(manifest_contract)
    description = (
        "Open Boise/Hillside to Hollow evidence inputs: facts, derived context, "
        "and gaps only; no recommendation or life-safety prediction."
    )
    release_set = await session.scalar(select(ReleaseSet).where(ReleaseSet.logical_key == PILOT_LOGICAL_KEY))
    expected_member_ids = {release.id for release in source_releases.values()}
    if release_set is None:
        release_set = ReleaseSet(
            logical_key=PILOT_LOGICAL_KEY,
            as_of_time=as_of_time,
            manifest_checksum=manifest_checksum,
            state=ReleaseSetState.DRAFT,
            description=description,
        )
        session.add(release_set)
        await session.flush()
        for source_release_id in sorted(expected_member_ids, key=str):
            session.add(
                ReleaseSetItem(
                    release_set_id=release_set.id,
                    source_release_id=source_release_id,
                    source_role="evidence_input",
                )
            )
        await session.flush()
        validated_at = datetime.now(UTC)
        release_set.state = ReleaseSetState.VALIDATED
        release_set.validated_at = validated_at
        await session.flush()
    else:
        members = {
            (item.source_release_id, item.source_role)
            for item in (
                await session.scalars(select(ReleaseSetItem).where(ReleaseSetItem.release_set_id == release_set.id))
            ).all()
        }
        expected_members = {(source_release_id, "evidence_input") for source_release_id in expected_member_ids}
        expected = {
            "logical_key": PILOT_LOGICAL_KEY,
            "as_of_time": as_of_time,
            "manifest_checksum": manifest_checksum,
            "state": ReleaseSetState.VALIDATED,
            "description": description,
            "published_at": None,
        }
        _assert_contract_fields(release_set, expected, "pilot release set")
        if release_set.validated_at is None or release_set.validated_at < as_of_time or members != expected_members:
            raise ValueError("existing pilot release set differs from the reviewed immutable inputs")
    return release_set


async def _derive_values(
    session: AsyncSession,
    wui_features: list[PersistedFeature],
    *,
    bind_parameters: dict[str, Any],
) -> DerivedValues:
    if not wui_features:
        raise ValueError("the pilot requires at least one complete WUI AOI feature")
    result = (
        await session.execute(
            text(DERIVED_VALUES_SQL),
            bind_parameters,
        )
    ).one()
    postgis_version = await session.scalar(text("SELECT postgis_full_version()"))
    return DerivedValues(
        city_area_m2=float(result.city_area_m2),
        property_area_m2=float(result.property_area_m2),
        property_covered_by_city=bool(result.property_covered_by_city),
        wui_overlap_fraction=float(result.wui_overlap_fraction),
        wui_classes=str(result.wui_classes),
        intersecting_wui_feature_ids=tuple(result.intersecting_wui_feature_ids),
        wui_vintage_minimum_age_days=int(result.wui_vintage_minimum_age_days),
        postgis_version=str(postgis_version),
    )


async def _get_or_create_analysis_run(
    session: AsyncSession,
    *,
    release_set: ReleaseSet,
    manifest: GeospatialCaptureManifest,
    output_contract: dict[str, Any],
    row_count: int,
) -> InterventionAnalysisRun:
    output_checksum = sha256_json(output_contract)
    code_checksum = sha256_json(
        {
            "sql": DERIVED_VALUES_SQL,
            "bind_parameters": output_contract["bind_parameters"],
        }
    )
    analysis_plan_checksum = sha256_json(
        {
            "capture_plan_checksum": manifest.plan_checksum,
            "release_set_manifest_checksum": release_set.manifest_checksum,
            "method_key": ANALYSIS_METHOD_KEY,
            "method_version": ANALYSIS_METHOD_VERSION,
            "analysis_code_checksum": code_checksum,
            "bind_parameters": output_contract["bind_parameters"],
            "wui_vintage_reference_convention": output_contract["wui_vintage_reference_convention"],
            "output_schema": sorted(output_contract["values"]),
            "rounding": output_contract["rounding"],
        }
    )
    expected = {
        "release_set_id": release_set.id,
        "run_key": "boise-hillside-hollow-context-v1",
        "method_key": ANALYSIS_METHOD_KEY,
        "method_version": ANALYSIS_METHOD_VERSION,
        "analysis_plan_checksum": analysis_plan_checksum,
        "analysis_code_checksum": code_checksum,
        "output_checksum": output_checksum,
        "validation_state": "validated",
        "row_count": row_count,
        "is_life_safety_prediction": False,
    }
    run = await session.scalar(
        select(InterventionAnalysisRun).where(
            InterventionAnalysisRun.release_set_id == release_set.id,
            InterventionAnalysisRun.run_key == "boise-hillside-hollow-context-v1",
        )
    )
    if run is None:
        validated_at = datetime.now(UTC)
        run = InterventionAnalysisRun(
            **expected,
            validated_at=validated_at,
            finalized_at=validated_at,
        )
        session.add(run)
        await session.flush()
    else:
        _assert_contract_fields(run, expected, "intervention analysis receipt")
        if run.validated_at is None or run.validated_at < release_set.as_of_time or run.finalized_at < run.validated_at:
            raise ValueError("existing intervention analysis receipt has invalid timestamps")
    return run


async def _write_evidence_set(  # noqa: PLR0913
    session: AsyncSession,
    *,
    release_set: ReleaseSet,
    city_subject: AnalysisSubject,
    property_subject: AnalysisSubject,
    city_feature: PersistedFeature,
    property_feature: PersistedFeature,
    wui_features: list[PersistedFeature],
    derived: DerivedValues,
    analysis_run: InterventionAnalysisRun,
    rounded_values: dict[str, Any],
) -> list[InterventionEvidenceInput]:
    if release_set.validated_at is None:
        raise ValueError("pilot evidence requires a validated release set timestamp")
    gap_available_at = release_set.validated_at
    evidence: list[InterventionEvidenceInput] = []
    evidence.append(
        await _get_or_create_evidence(
            session,
            release_set=release_set,
            subject=city_subject,
            geometry_json=city_feature.captured.geometry_json,
            evidence_kind="observed_fact",
            metric_name="census_reported_land_area_m2",
            numeric_value=float(city_feature.captured.attributes["AREALAND"]),
            value_unit="m2",
            spatial_support_kind="administrative_boundary",
            native_scale=SOURCE_NATIVE_SCALE[city_feature.captured.source.key],
            maximum_inference_scale="city",
            confidence_basis=SOURCE_CONFIDENCE_BASIS[city_feature.captured.source.key],
            method_key=NORMALIZATION_METHOD_KEY,
            method_version=NORMALIZATION_METHOD_VERSION,
            analysis_run=None,
            data_available_at=city_feature.source_release.data_available_at,
            observed_from=_source_observation_window(city_feature.captured.source.key)[0],
            observed_to=_source_observation_window(city_feature.captured.source.key)[1],
            lineages=[(city_feature, "direct_observation")],
        )
    )
    evidence.append(
        await _get_or_create_evidence(
            session,
            release_set=release_set,
            subject=property_subject,
            geometry_json=property_feature.captured.geometry_json,
            evidence_kind="observed_fact",
            metric_name="source_property_classification",
            text_value="OpenStreetMap named nature_reserve boundary; non-cadastral",
            spatial_support_kind="native_polygon",
            native_scale=SOURCE_NATIVE_SCALE[property_feature.captured.source.key],
            maximum_inference_scale="neighborhood",
            confidence_basis=SOURCE_CONFIDENCE_BASIS[property_feature.captured.source.key],
            method_key=NORMALIZATION_METHOD_KEY,
            method_version=NORMALIZATION_METHOD_VERSION,
            analysis_run=None,
            data_available_at=property_feature.source_release.data_available_at,
            lineages=[(property_feature, "direct_observation")],
        )
    )
    intersecting_wui = [item for item in wui_features if item.normalized.id in derived.intersecting_wui_feature_ids]
    derived_specs: list[dict[str, Any]] = [
        {
            "subject": city_subject,
            "geometry_json": city_feature.captured.geometry_json,
            "metric_name": "city_boundary_geometry_area_m2",
            "numeric_value": rounded_values["city_boundary_geometry_area_m2"],
            "value_unit": "m2",
            "support": "administrative_boundary",
            "scale": "city",
            "basis": "PostGIS geodesic area of the pinned Census boundary.",
            "observed_from": _source_observation_window(city_feature.captured.source.key)[0],
            "observed_to": _source_observation_window(city_feature.captured.source.key)[1],
            "lineages": [(city_feature, "derivation_input")],
        },
        {
            "subject": property_subject,
            "geometry_json": property_feature.captured.geometry_json,
            "metric_name": "osm_reserve_geometry_area_m2_context",
            "numeric_value": rounded_values["osm_reserve_geometry_area_m2_context"],
            "value_unit": "m2",
            "support": "native_polygon",
            "scale": "neighborhood",
            "basis": "PostGIS geodesic area of the non-cadastral OSM property boundary.",
            "lineages": [(property_feature, "derivation_input")],
        },
        {
            "subject": property_subject,
            "geometry_json": property_feature.captured.geometry_json,
            "metric_name": "property_covered_by_city_boundary",
            "boolean_value": derived.property_covered_by_city,
            "support": "native_polygon",
            "scale": "neighborhood",
            "basis": "PostGIS coverage test between pinned OSM property and Census city geometries.",
            "lineages": [
                (property_feature, "derivation_input"),
                (city_feature, "derivation_input"),
            ],
        },
        {
            "subject": property_subject,
            "geometry_json": property_feature.captured.geometry_json,
            "metric_name": "usfs_wui_2020_census_block_geometry_overlap_fraction_context",
            "numeric_value": rounded_values["usfs_wui_2020_census_block_geometry_overlap_fraction_context"],
            "value_unit": "fraction",
            "support": "area_aggregate",
            "scale": "neighborhood",
            "basis": "Geodesic overlap with 2020 census-block WUI context; not parcel hazard.",
            "observed_from": _source_observation_window(wui_features[0].captured.source.key)[0],
            "observed_to": _source_observation_window(wui_features[0].captured.source.key)[1],
            "lineages": [(property_feature, "derivation_input")]
            + [(item, "derivation_input") for item in intersecting_wui],
        },
        {
            "subject": property_subject,
            "geometry_json": property_feature.captured.geometry_json,
            "metric_name": "usfs_wui_2020_census_block_class_context",
            "text_value": rounded_values["usfs_wui_2020_census_block_class_context"],
            "support": "area_aggregate",
            "scale": "neighborhood",
            "basis": "Pinned USFS 2020 census-block WUI classes intersecting the property.",
            "observed_from": _source_observation_window(wui_features[0].captured.source.key)[0],
            "observed_to": _source_observation_window(wui_features[0].captured.source.key)[1],
            "lineages": [(property_feature, "derivation_input")]
            + [(item, "derivation_input") for item in intersecting_wui],
        },
        {
            "subject": property_subject,
            "geometry_json": property_feature.captured.geometry_json,
            "metric_name": "usfs_wui_vintage_minimum_age_days_at_capture",
            "numeric_value": rounded_values["usfs_wui_vintage_minimum_age_days_at_capture"],
            "value_unit": "days",
            "support": "area_aggregate",
            "scale": "neighborhood",
            "basis": WUI_VINTAGE_CONFIDENCE_BASIS,
            "lineages": [(item, "derivation_input") for item in wui_features],
        },
    ]
    for item in derived_specs:
        evidence.append(  # noqa: PERF401
            await _get_or_create_evidence(
                session,
                release_set=release_set,
                subject=item["subject"],
                geometry_json=item["geometry_json"],
                evidence_kind="model_derived_feature",
                metric_name=item["metric_name"],
                numeric_value=item.get("numeric_value"),
                text_value=item.get("text_value"),
                boolean_value=item.get("boolean_value"),
                value_unit=item.get("value_unit"),
                spatial_support_kind=item["support"],
                native_scale="Derived from pinned inputs without spatial upscaling",
                maximum_inference_scale=item["scale"],
                confidence_basis=item["basis"],
                method_key=ANALYSIS_METHOD_KEY,
                method_version=ANALYSIS_METHOD_VERSION,
                analysis_run=analysis_run,
                data_available_at=analysis_run.finalized_at,
                observed_from=item.get("observed_from"),
                observed_to=item.get("observed_to"),
                lineages=item["lineages"],
            )
        )
    for metric_name, gap_detail in GAP_INPUTS.items():
        evidence.append(
            await _get_or_create_evidence(
                session,
                release_set=release_set,
                subject=property_subject,
                geometry_json=property_feature.captured.geometry_json,
                evidence_kind="known_gap",
                metric_name=metric_name,
                gap_detail=gap_detail,
                spatial_support_kind="unknown",
                native_scale="No qualifying open site evidence ingested",
                maximum_inference_scale="neighborhood",
                confidence_basis=(
                    "Reviewed gap register records a missing requirement; the property lineage "
                    "supplies scope only and does not prove absence."
                ),
                method_key="open-pilot-gap-register",
                method_version="1",
                analysis_run=None,
                data_available_at=gap_available_at,
                lineages=[(property_feature, "coverage_basis")],
            )
        )
    return evidence


async def _get_or_create_evidence(  # noqa: PLR0913
    session: AsyncSession,
    *,
    release_set: ReleaseSet,
    subject: AnalysisSubject,
    geometry_json: str,
    evidence_kind: str,
    metric_name: str,
    spatial_support_kind: str,
    native_scale: str,
    maximum_inference_scale: str,
    confidence_basis: str,
    method_key: str,
    method_version: str,
    analysis_run: InterventionAnalysisRun | None,
    data_available_at: datetime,
    lineages: list[tuple[PersistedFeature, str]],
    observed_from: datetime | None = None,
    observed_to: datetime | None = None,
    numeric_value: float | None = None,
    text_value: str | None = None,
    boolean_value: bool | None = None,
    value_unit: str | None = None,
    gap_detail: str | None = None,
) -> InterventionEvidenceInput:
    geometry_checksum = await _validate_postgis_geometry(session, geometry_json)
    lineage_contract = [
        {
            "source_key": item.captured.source.key,
            "feature_key": item.captured.feature_key,
            "feature_checksum": item.normalized.feature_checksum,
            "role": role,
        }
        for item, role in lineages
    ]
    evidence_checksum = sha256_json(
        {
            "release_set_manifest_checksum": release_set.manifest_checksum,
            "subject_key": subject.subject_key,
            "subject_version": subject.subject_version,
            "evidence_kind": evidence_kind,
            "metric_name": metric_name,
            "numeric_value": numeric_value,
            "text_value": text_value,
            "boolean_value": boolean_value,
            "value_unit": value_unit,
            "gap_detail": gap_detail,
            "geometry_checksum": geometry_checksum,
            "observed_from": observed_from.isoformat() if observed_from else None,
            "observed_to": observed_to.isoformat() if observed_to else None,
            "data_available_at": data_available_at.isoformat(),
            "spatial_support_kind": spatial_support_kind,
            "native_scale": native_scale,
            "maximum_inference_scale": maximum_inference_scale,
            "confidence_basis": confidence_basis,
            "method_key": method_key,
            "method_version": method_version,
            "analysis_output_checksum": analysis_run.output_checksum if analysis_run else None,
            "lineages": lineage_contract,
        }
    )
    expected = {
        "release_set_id": release_set.id,
        "analysis_subject_id": subject.id,
        "evidence_kind": evidence_kind,
        "metric_name": metric_name,
        "numeric_value": numeric_value,
        "text_value": text_value,
        "boolean_value": boolean_value,
        "value_unit": value_unit,
        "gap_detail": gap_detail,
        "observed_from": observed_from,
        "observed_to": observed_to,
        "data_available_at": data_available_at,
        "spatial_support_kind": spatial_support_kind,
        "native_resolution_m": None,
        "native_scale": native_scale,
        "maximum_inference_scale": maximum_inference_scale,
        "confidence": None,
        "confidence_basis": confidence_basis,
        "method_key": method_key,
        "method_version": method_version,
        "intervention_analysis_run_id": analysis_run.id if analysis_run else None,
        "evidence_checksum": evidence_checksum,
        "is_life_safety_validated": False,
    }
    evidence = await session.scalar(
        select(InterventionEvidenceInput).where(
            InterventionEvidenceInput.release_set_id == release_set.id,
            InterventionEvidenceInput.analysis_subject_id == subject.id,
            InterventionEvidenceInput.evidence_checksum == evidence_checksum,
        )
    )
    if evidence is not None:
        _assert_contract_fields(evidence, expected, f"evidence {metric_name}")
        stored_geometry_checksum = await _stored_geometry_checksum(
            session,
            table_name="intervention_evidence_input",
            geometry_column="evidence_geometry",
            record_id=evidence.id,
        )
        if stored_geometry_checksum != geometry_checksum:
            raise ValueError(f"stored geometry drifted for evidence {metric_name}")
        existing_lineages = (
            await session.scalars(
                select(InterventionEvidenceLineage)
                .where(InterventionEvidenceLineage.evidence_input_id == evidence.id)
                .order_by(InterventionEvidenceLineage.input_order)
            )
        ).all()
        expected_lineages = [
            (
                evidence.id,
                feature.source_release.id,
                feature.normalized.id,
                None,
                None,
                None,
                role,
                input_order,
            )
            for input_order, (feature, role) in enumerate(lineages)
        ]
        actual_lineages = [
            (
                lineage.evidence_input_id,
                lineage.source_release_id,
                lineage.source_feature_id,
                lineage.source_record_table,
                lineage.source_record_key,
                lineage.source_record_checksum,
                lineage.lineage_role,
                lineage.input_order,
            )
            for lineage in existing_lineages
        ]
        if actual_lineages != expected_lineages:
            raise ValueError(f"existing evidence lineage differs for {metric_name}")
        return evidence
    evidence = InterventionEvidenceInput(
        **expected,
        evidence_geometry=func.ST_SetSRID(func.ST_GeomFromGeoJSON(geometry_json), 4326),
    )
    session.add(evidence)
    await session.flush()
    for input_order, (feature, lineage_role) in enumerate(lineages):
        session.add(
            InterventionEvidenceLineage(
                evidence_input_id=evidence.id,
                source_release_id=feature.source_release.id,
                source_feature_id=feature.normalized.id,
                lineage_role=lineage_role,
                input_order=input_order,
            )
        )
    await session.flush()
    return evidence


async def _validate_postgis_geometry(session: AsyncSession, geometry_json: str) -> str:
    result = (
        await session.execute(
            text(
                """
                WITH candidate AS (
                    SELECT ST_SetSRID(ST_GeomFromGeoJSON(:geometry_json), 4326) AS geometry
                )
                SELECT
                    ST_IsValid(geometry) AS is_valid,
                    ST_IsEmpty(geometry) AS is_empty,
                    ST_NDims(geometry) AS dimensions,
                    encode(public.digest(ST_AsEWKB(geometry), 'sha256'), 'hex') AS checksum
                FROM candidate
                """
            ),
            {"geometry_json": geometry_json},
        )
    ).one()
    if not result.is_valid or result.is_empty or result.dimensions != EXPECTED_GEOMETRY_DIMENSIONS:
        raise ValueError("captured geometry failed PostGIS validity, emptiness, or dimension checks")
    return str(result.checksum)


async def _stored_geometry_checksum(
    session: AsyncSession,
    *,
    table_name: str,
    geometry_column: str,
    record_id: uuid.UUID,
) -> str:
    allowed = {
        ("normalized_source_feature", "geometry"),
        ("analysis_subject", "geometry"),
        ("intervention_evidence_input", "evidence_geometry"),
    }
    if (table_name, geometry_column) not in allowed:
        raise ValueError("unsupported governed geometry locator")
    checksum = await session.scalar(
        text(
            f"SELECT encode(public.digest(ST_AsEWKB({geometry_column}), 'sha256'), 'hex') "
            f"FROM agri.{table_name} WHERE id = :record_id"
        ),
        {"record_id": record_id},
    )
    if checksum is None:
        raise ValueError(f"missing governed geometry for {table_name}")
    return str(checksum)


def _source_observation_window(source_key: str) -> tuple[datetime | None, datetime | None]:
    if source_key == "census-tigerweb-boise-2025":
        reference_date = datetime(2025, 1, 1, tzinfo=UTC)
        return reference_date, reference_date
    # The WUI product combines 2019 vegetation with 2020 Census housing inputs.
    # No single observation interval is asserted for that provider classification.
    return None, None


async def _run(plan_path: Path, capture_base: Path) -> PilotIngestionReceipt:
    database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(database_url) as session, session.begin():
        return await ingest_boise_intervention_pilot(
            session,
            plan_path=plan_path,
            capture_base=capture_base,
        )


def main() -> None:
    """Run the local-only pilot writer."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--capture-base", required=True, type=Path)
    args = parser.parse_args()
    receipt = asyncio.run(_run(args.plan, args.capture_base))
    print(receipt.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
