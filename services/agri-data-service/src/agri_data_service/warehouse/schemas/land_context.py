"""The BLM surface, office jurisdiction, and documented routing products."""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema
from agri_data_service.warehouse.parquet.tiers import (
    ColumnAggregation,
    GeometrySimplification,
    HierarchicalDissolve,
    TierDerivation,
    TierPassthrough,
    register_tier_derivation,
)

BOUNDARIES_STREAM: Final = "land-context-boundaries"
OFFICES_STREAM: Final = "land-context-offices"
CONTACTS_STREAM: Final = "land-context-contacts"
LAND_CONTEXT_STREAMS: Final = (BOUNDARIES_STREAM, OFFICES_STREAM, CONTACTS_STREAM)

_BOUNDARY_FIELDS = [
    pa.field(name, pa.string(), nullable=nullable)
    for name, nullable in (
        ("source_namespace", False),
        ("native_feature_key", False),
        ("native_feature_version", True),
        ("source_native_feature_key", True),
        ("family", False),
        ("interest_type", False),
        ("state", False),
        ("county", True),
        ("geom_wkb", True),
        ("label", False),
        ("aggregation_basis", True),
        ("release_publisher", False),
        ("release_canonical_endpoint", False),
        ("release_source_version", False),
        ("release_captured_at", True),
        ("release_source_watermark_at", True),
        ("release_admission_verdict", False),
        ("source_manifest_sha256", False),
    )
] + [
    pa.field("source_feature_count", pa.int64(), nullable=False),
    pa.field("release_day", pa.date32(), nullable=False),
    pa.field("geometry_wkb", pa.binary(), nullable=False),
]

BOUNDARIES_SCHEMA = register_stream_schema(
    ParquetStreamSchema(
        name=BOUNDARIES_STREAM,
        arrow_schema=pa.schema(_BOUNDARY_FIELDS),
        sort_columns=("native_feature_key",),
    )
)
OFFICES_SCHEMA = register_stream_schema(
    ParquetStreamSchema(
        name=OFFICES_STREAM,
        arrow_schema=pa.schema(_BOUNDARY_FIELDS),
        sort_columns=("native_feature_key",),
    )
)

_NULL_ON_AGGREGATE = {"source_native_feature_key", "native_feature_version", "aggregation_basis"}
register_tier_derivation(
    TierDerivation(
        stream=BOUNDARIES_STREAM,
        strategy=GeometrySimplification(
            geometry_column="geometry_wkb",
            dissolve=HierarchicalDissolve(code_column="native_feature_key", code_length_by_tier={9: 10, 5: 10, 0: 10}),
            aggregations=tuple(
                ColumnAggregation(
                    column=column,
                    how=(
                        "sum"
                        if column == "source_feature_count"
                        else "null"
                        if column in _NULL_ON_AGGREGATE
                        else "first"
                    ),
                )
                for column in BOUNDARIES_SCHEMA.column_names
                if column not in {"native_feature_key", "geometry_wkb"}
            ),
        ),
    )
)
register_tier_derivation(
    TierDerivation(
        stream=OFFICES_STREAM,
        strategy=GeometrySimplification(geometry_column="geometry_wkb"),
    )
)

_CONTACT_FIELDS = [
    pa.field(name, pa.string(), nullable=nullable)
    for name, nullable in (
        ("subject_id", False),
        ("object_id", False),
        ("relationship_kind", False),
        ("applicable_geography", True),
        ("documented_topic", True),
        ("assignment_method", False),
        ("review_status", False),
        ("effective_from", True),
        ("effective_to", True),
        ("source_evidence_url", True),
        ("organization_id", False),
        ("office_id", False),
        ("official_public_name", False),
        ("office_type", False),
        ("parent_organization_id", True),
        ("route_type", False),
        ("route_meaning", False),
        ("documented_help", True),
        ("official_inquiry_url", True),
        ("public_business_phone", True),
        ("public_business_email", True),
        ("published_professional_name", True),
        ("route_status", False),
        ("verified_at", True),
        ("source_manifest_sha256", False),
    )
] + [
    pa.field("forwarding_documented", pa.bool_(), nullable=False),
    pa.field("release_day", pa.date32(), nullable=False),
]
CONTACTS_SCHEMA = register_stream_schema(
    ParquetStreamSchema(
        name=CONTACTS_STREAM,
        arrow_schema=pa.schema(_CONTACT_FIELDS),
        sort_columns=("subject_id", "office_id"),
    )
)
register_tier_derivation(TierDerivation(stream=CONTACTS_STREAM, strategy=TierPassthrough()))
