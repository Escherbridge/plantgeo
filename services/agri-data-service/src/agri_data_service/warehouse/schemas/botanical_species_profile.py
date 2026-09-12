"""Native Arrow evidence schemas for the nonspatial botanical reference product."""

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema

_STRING = pa.string()
_UTC = pa.timestamp("us", tz="UTC")


def _field(name: str, kind: pa.DataType = _STRING, *, nullable: bool = False) -> pa.Field:
    return pa.field(name, kind, nullable=nullable)


def _list(kind: pa.DataType) -> pa.DataType:
    return pa.list_(pa.field("item", kind, nullable=False))


IDENTITY_TYPE: Final = pa.struct(
    [
        _field("authority"),
        _field("authority_version"),
        _field("taxon_id"),
    ]
)
NAME_TYPE: Final = pa.struct(
    [
        _field("name"),
        _field("source_name_id"),
        _field("accepted_taxon_id"),
        _field("status"),
        _field("source_id"),
        _field("source_version", nullable=True),
        _field("source_record_json", nullable=True),
    ]
)
VALUE_TYPE: Final = pa.struct(
    [
        _field("text", nullable=True),
        _field("number", pa.float64(), nullable=True),
        _field("boolean", pa.bool_(), nullable=True),
        _field("lower", pa.float64(), nullable=True),
        _field("upper", pa.float64(), nullable=True),
        _field("unit", nullable=True),
    ]
)
CONTEXT_TYPE: Final = pa.struct(
    [
        _field(name, nullable=True)
        for name in (
            "tissue_or_component",
            "live_dead_state",
            "moisture_basis",
            "season",
            "life_stage",
            "geography",
            "environment",
            "conditions",
        )
    ]
)
SOURCE_TYPE: Final = pa.struct(
    [
        *(
            _field(name)
            for name in (
                "source_id",
                "version",
                "release_url",
                "download_url",
                "licence_id",
                "licence_url",
                "content_sha256",
                "terms_sha256",
            )
        ),
        _field("retrieved_at", _UTC),
        _field("reviewer"),
        _field("reviewed_at", _UTC),
        _field("review_basis"),
        _field("machine_readable_verified", pa.bool_()),
        _field("reuse_allowed", pa.bool_()),
        _field("access"),
        _field("admitted_traits", _list(_STRING)),
        _field("admitted_taxon_ids", _list(_STRING)),
    ]
)
TAXA_SCHEMA: Final = pa.schema(
    [
        _field("identity", IDENTITY_TYPE),
        *(_field(name) for name in ("accepted_name", "rank", "source_id", "source_record_id", "source_name")),
        _field("source_record_json", nullable=True),
        _field("synonyms", _list(NAME_TYPE)),
        _field("cultivars", _list(NAME_TYPE)),
        _field("authoring_row_id", nullable=True),
        _field("authoring_provenance"),
    ]
)
ASSERTIONS_SCHEMA: Final = pa.schema(
    [
        _field("assertion_id"),
        _field("identity", IDENTITY_TYPE),
        *(
            _field(name)
            for name in (
                "trait",
                "source_id",
                "source_version",
                "source_url",
                "source_record_id",
                "licence_id",
                "evidence_locator",
                "evidence_kind",
            )
        ),
        _field("raw_value", VALUE_TYPE, nullable=True),
        _field("normalized_value", VALUE_TYPE, nullable=True),
        _field("qualifier", nullable=True),
        _field("method", nullable=True),
        _field("context", CONTEXT_TYPE),
        _field("retrieved_at", _UTC),
        _field("valid_from", _UTC, nullable=True),
        _field("valid_to", _UTC, nullable=True),
        _field("missingness", nullable=True),
        _field("review_state"),
        _field("reviewer", nullable=True),
        _field("review_reason"),
        _field("corrects_assertion_id", nullable=True),
        _field("withdraws_assertion_id", nullable=True),
        _field("related_taxon", IDENTITY_TYPE, nullable=True),
        _field("authoring_row_id", nullable=True),
        _field("authoring_provenance"),
    ]
)
DECISIONS_SCHEMA: Final = pa.schema(
    [
        _field("decision_id"),
        _field("identity", IDENTITY_TYPE),
        _field("trait"),
        _field("state"),
        _field("assertion_ids", _list(_STRING)),
        _field("selected_assertion_ids", _list(_STRING)),
        _field("excluded_assertion_ids", _list(_STRING)),
        _field("value", VALUE_TYPE, nullable=True),
        _field("reason"),
    ]
)
PROFILE_FIELD_TYPE: Final = pa.struct(
    [
        _field("trait"),
        _field("state"),
        _field("value", VALUE_TYPE, nullable=True),
        _field("assertion_ids", _list(_STRING)),
        _field("decision_id"),
        _field("reason"),
    ]
)
PROFILE_SECTION_TYPE: Final = pa.struct(
    [
        _field("name"),
        _field("fields", _list(PROFILE_FIELD_TYPE)),
    ]
)
PROFILES_SCHEMA: Final = pa.schema(
    [
        _field("identity", IDENTITY_TYPE),
        _field("accepted_name"),
        _field("sections", _list(PROFILE_SECTION_TYPE)),
    ]
)
ARTIFACT_RECEIPT_TYPE: Final = pa.struct(
    [
        _field("name"),
        _field("key"),
        _field("sha256"),
        _field("byte_count", pa.int64()),
        _field("row_count", pa.int64()),
    ]
)
MANIFEST_SCHEMA: Final = pa.schema(
    [
        _field("release_id"),
        _field("schema_version"),
        _field("source_releases", _list(SOURCE_TYPE)),
        _field("normalization_recipe"),
        _field("reconciliation_policy"),
        _field("reviewer"),
        _field("review_decision_id"),
        _field("reviewed_at", _UTC),
        _field("authoring_census_state"),
        _field("authoring_census_note"),
        _field("artifacts", _list(ARTIFACT_RECEIPT_TYPE)),
        _field("input_sha256"),
    ]
)
BOTANICAL_SPECIES_PROFILE_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name="botanical-species-profile",
        arrow_schema=PROFILES_SCHEMA,
        sort_columns=("identity",),
    )
)
