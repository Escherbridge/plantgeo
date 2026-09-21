"""Normalize admitted BLM geometries and documented office routes into the serving schemas."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.pipeline.direct.land_context.products import (
    CENSUS_STATES,
    FIELD_OFFICES,
    SURFACE_IDAHO,
    SURFACE_ORWA,
)
from agri_data_service.pipeline.direct.land_context.source import canonical_bytes, digest, refuse
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.warehouse.parquet.tiers import derivation_session
from agri_data_service.warehouse.schemas.land_context import BOUNDARIES_SCHEMA, CONTACTS_SCHEMA, OFFICES_SCHEMA

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.pipeline.direct.land_context.source import LandContextSnapshot, ProductCapture

MAX_PART_BYTES = 8 * 1024 * 1024
MAX_SINGLE_GEOMETRY_BYTES = 64 * 1024 * 1024
MAX_PART_ROWS = 1000


def _boundary(capture: ProductCapture, feature: dict[str, Any], snapshot: LandContextSnapshot) -> dict[str, Any]:
    props = feature["properties"]
    office = capture.product == FIELD_OFFICES
    native_key = str(props["ADM_UNIT_CD"] if office else props[capture.product.key_field]).strip()
    return {
        "source_namespace": capture.product.slug,
        "native_feature_key": native_key,
        "native_feature_version": digest(canonical_bytes(feature)),
        "source_native_feature_key": native_key,
        "family": "blm_office_jurisdiction" if office else "blm_surface_management",
        "interest_type": "administrative_jurisdiction" if office else "surface_management",
        "state": "",
        "county": None,
        "geom_wkb": None,
        "label": str(props["ADMU_NAME"]) if office else "BLM managed surface",
        "aggregation_basis": "Source polygon; physical state assigned by representative point in Census state geometry",
        "release_publisher": "U.S. Bureau of Land Management",
        "release_canonical_endpoint": capture.product.endpoint,
        "release_source_version": snapshot.content_sha256,
        "release_captured_at": snapshot.captured_at.isoformat(),
        "release_source_watermark_at": None,
        "release_admission_verdict": "admitted",
        "source_manifest_sha256": snapshot.manifest_sha256,
        "source_feature_count": 1,
        "release_day": snapshot.captured_at.date(),
        "geojson": json.dumps(feature["geometry"], separators=(",", ":"), allow_nan=False),
    }


def _normalize_geometry(snapshot: LandContextSnapshot) -> list[dict[str, Any]]:
    states = next(capture for capture in snapshot.products if capture.product == CENSUS_STATES)
    state_rows = [
        {"state": feature["properties"]["STUSAB"], "geojson": json.dumps(feature["geometry"])}
        for feature in states.features
    ]
    raw = [
        _boundary(capture, feature, snapshot)
        for capture in snapshot.products
        if capture.product != CENSUS_STATES
        for feature in capture.features
    ]
    with derivation_session() as connection:
        connection.register("raw_states", pa.Table.from_pylist(state_rows))
        connection.execute(
            "CREATE TEMP TABLE states AS SELECT state, ST_MakeValid(ST_GeomFromGeoJSON(geojson)) AS g FROM raw_states"
        )
        connection.register("raw_features", pa.Table.from_pylist(raw))
        connection.execute(
            "CREATE TEMP TABLE shapes AS SELECT * EXCLUDE (geojson), "
            "ST_CollectionExtract(ST_MakeValid(ST_GeomFromGeoJSON(geojson)), 3) AS g FROM raw_features"
        )
        invalid = connection.execute("SELECT count(*) FROM shapes WHERE g IS NULL OR ST_IsEmpty(g)").fetchone()
        if invalid is None or invalid[0]:
            raise refuse("A source geometry has no valid polygonal area; refusing the complete capture")
        connection.execute(
            "UPDATE shapes SET g = ST_CollectionExtract(ST_Intersection(g, "
            "(SELECT ST_Union_Agg(g) FROM states)), 3) WHERE family = 'blm_office_jurisdiction'"
        )
        connection.execute("DELETE FROM shapes WHERE ST_IsEmpty(g)")
        connection.execute(
            "CREATE TEMP TABLE office_groups AS "
            "SELECT * EXCLUDE (g, native_feature_version, source_feature_count), "
            "sha256(string_agg(native_feature_version, ',' ORDER BY native_feature_version)) "
            "AS native_feature_version, "
            "sum(source_feature_count) AS source_feature_count, ST_Union_Agg(g) AS g "
            "FROM shapes WHERE family = 'blm_office_jurisdiction' GROUP BY ALL"
        )
        connection.execute("DELETE FROM shapes WHERE family = 'blm_office_jurisdiction'")
        connection.execute("INSERT INTO shapes BY NAME SELECT * FROM office_groups")
        connection.execute(
            "UPDATE shapes SET g = ST_CollectionExtract(ST_Intersection(g, "
            "(SELECT g FROM states WHERE state = 'ID')), 3) WHERE source_namespace = ?",
            [SURFACE_IDAHO.slug],
        )
        connection.execute(
            "UPDATE shapes SET g = ST_CollectionExtract(ST_Intersection(g, "
            "(SELECT ST_Union_Agg(g) FROM states WHERE state IN ('OR', 'WA'))), 3) "
            "WHERE source_namespace = ?",
            [SURFACE_ORWA.slug],
        )
        connection.execute("DELETE FROM shapes WHERE ST_IsEmpty(g)")
        geometry_rows: list[dict[str, Any]] = (
            connection.execute(
                "SELECT shapes.* EXCLUDE (g, state), states.state, ST_AsWKB(shapes.g) AS geometry_wkb "
                "FROM shapes JOIN states ON ST_Covers(states.g, ST_PointOnSurface(shapes.g))"
            )
            .fetch_arrow_table()
            .to_pylist()
        )
        expected = connection.execute("SELECT count(*) FROM shapes").fetchone()
        keys = {(row["source_namespace"], row["native_feature_key"]) for row in geometry_rows}
        if expected is None or len(geometry_rows) != expected[0] or len(keys) != len(geometry_rows):
            raise refuse("A BLM feature does not have one physical PNW state; state assignment requires review")
    for row in geometry_rows:
        if row["family"] == "blm_surface_management":
            row["native_feature_key"] = f"serving:{row['state']}:{row['native_feature_key']}"
            if row["source_namespace"] == SURFACE_ORWA.slug:
                row["aggregation_basis"] = (
                    "Source polygon clipped to Census Oregon/Washington union; "
                    "physical state assigned by representative point"
                )
            if row["source_namespace"] == SURFACE_IDAHO.slug:
                row["aggregation_basis"] = (
                    "National limited-scale polygon generalized at 0.0001 degrees and clipped to Census Idaho boundary"
                )
    return geometry_rows


def _source_instant(value: object, *, captured_at: datetime) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    try:
        instant = datetime.fromtimestamp(value / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return instant.isoformat() if datetime(1970, 1, 1, tzinfo=UTC) <= instant <= captured_at else None


def _contact(feature: dict[str, Any], snapshot: LandContextSnapshot) -> dict[str, Any]:
    props = feature["properties"]
    office = str(props["ADM_UNIT_CD"])
    url = str(props.get("ADMU_ST_URL") or "")
    parsed = urlparse(url)
    public_url = url if parsed.scheme == "https" and parsed.hostname in {"blm.gov", "www.blm.gov"} else None
    return {
        "subject_id": f"{FIELD_OFFICES.slug}:{office}",
        "object_id": office,
        "relationship_kind": "publisher_office_jurisdiction",
        "applicable_geography": str(props["ADMU_NAME"]),
        "documented_topic": None,
        "assignment_method": "publisher_admin_unit_code",
        "review_status": "reviewed",
        "effective_from": _source_instant(props.get("EFF_DT"), captured_at=snapshot.captured_at),
        "effective_to": None,
        "source_evidence_url": FIELD_OFFICES.endpoint,
        "organization_id": "us-doi-blm",
        "office_id": office,
        "official_public_name": str(props["ADMU_NAME"]),
        "office_type": "field_office",
        "parent_organization_id": props.get("PARENT_CD"),
        "route_type": "official_state_website",
        "route_meaning": "contact_process_inquiry",
        "documented_help": (
            "Publisher-listed state website for this field-office geography; "
            "program responsibility and forwarding are not established."
        ),
        "official_inquiry_url": public_url,
        "public_business_phone": None,
        "public_business_email": None,
        "published_professional_name": None,
        "route_status": "unverified",
        "verified_at": None,
        "forwarding_documented": False,
        "source_manifest_sha256": snapshot.manifest_sha256,
        "release_day": snapshot.captured_at.date(),
    }


def snapshot_tables(snapshot: LandContextSnapshot, *, release_day: date) -> dict[str, pa.Table]:
    """Publish surface and jurisdiction separately; never turn a jurisdiction into ownership."""
    rows = _normalize_geometry(snapshot)
    boundaries = [row for row in rows if row["family"] == "blm_surface_management"]
    offices = [row for row in rows if row["family"] == "blm_office_jurisdiction"]
    office_ids = {row["source_native_feature_key"] for row in offices}
    contacts_by_office: dict[str, dict[str, Any]] = {}
    for capture in snapshot.products:
        if capture.product != FIELD_OFFICES:
            continue
        for feature in capture.features:
            office_id = str(feature["properties"]["ADM_UNIT_CD"])
            if office_id not in office_ids:
                continue
            contact = _contact(feature, snapshot)
            previous = contacts_by_office.get(office_id)
            if previous is None:
                contacts_by_office[office_id] = contact
                continue
            for column, value in contact.items():
                if previous[column] != value:
                    if column not in {"effective_from", "official_inquiry_url", "parent_organization_id"}:
                        raise refuse(f"BLM office {office_id} has conflicting {column} across source pieces")
                    previous[column] = None
    contacts = list(contacts_by_office.values())
    tables: dict[str, pa.Table] = {}
    for schema, selected in ((BOUNDARIES_SCHEMA, boundaries), (OFFICES_SCHEMA, offices), (CONTACTS_SCHEMA, contacts)):
        if not selected:
            raise refuse(f"{schema.name} unexpectedly has no admitted PNW rows")
        for row in selected:
            row["release_day"] = release_day
        tables[schema.name] = conform_to_stream_schema(
            pa.Table.from_pylist(selected, schema=schema.arrow_schema), schema
        )
    return tables


def split_parts(table: pa.Table) -> tuple[pa.Table, ...]:
    """Bound geometry parts by payload bytes and ordinary reference rows by count."""
    parts: list[pa.Table] = []
    first = 0
    size = 0
    geometry = (
        table.column("geometry_wkb").to_pylist() if "geometry_wkb" in table.column_names else [b""] * table.num_rows
    )
    for index, value in enumerate(geometry):
        length = len(value)
        if length > MAX_SINGLE_GEOMETRY_BYTES:
            raise refuse("A BLM geometry exceeds the per-feature byte budget")
        if index > first and (size + length > MAX_PART_BYTES or index - first >= MAX_PART_ROWS):
            parts.append(table.slice(first, index - first))
            first, size = index, 0
        size += length
    if table.num_rows > first:
        parts.append(table.slice(first))
    return tuple(parts)
