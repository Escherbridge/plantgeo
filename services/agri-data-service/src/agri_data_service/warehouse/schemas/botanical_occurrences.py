"""Parquet schemas for the governed botanical occurrence lane: seven streams, one release set.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
`horizon: none` -- a preserved specimen is evidence that a documented collection event happened,
so only `kind=observed` is ever written and there is no forecast sibling.

SEVEN STREAMS IN ONE MODULE, which is a deliberate deviation from the one-slug-per-module autoload
convention in this package's `AGENTS.md`: the seven are not seven lanes, they are the seven grains of
ONE release set, and `get_stream_schema("botanical-raw-occurrence")` would autoload
`botanical_raw_occurrence.py`. Nothing in this lane resolves a schema by autoload -- publisher and
reader both import the constants below by name -- but a future caller that does must import this
module first. See `pipeline/direct/botanical_occurrences/AGENTS.md`, "Schema registration".

EVERY STREAM IS SPARSE. A row exists because a documented record, association or evaluated cell
exists. Nothing here is zero-filled across cells x taxa x days: an absent row is the absence of
evidence, never evidence of absence.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema

BOTANICAL_SOURCE_RELEASE_STREAM: Final = "botanical-source-release"
BOTANICAL_RAW_OCCURRENCE_STREAM: Final = "botanical-raw-occurrence"
BOTANICAL_IDENTIFICATION_STREAM: Final = "botanical-identification"
BOTANICAL_NORMALIZED_OCCURRENCE_STREAM: Final = "botanical-normalized-occurrence"
BOTANICAL_SPATIAL_ASSOCIATION_STREAM: Final = "botanical-spatial-association"
BOTANICAL_SUPPORT_EVALUATION_STREAM: Final = "botanical-support-evaluation"
BOTANICAL_CELL_TAXON_SUMMARY_STREAM: Final = "botanical-cell-taxon-summary"

#: Outcome words a release receipt may carry. `quarantined` is not a failure to retry blindly: the
#: archive was read and refused, and the reasons list says by which control.
RELEASE_OUTCOMES: Final[tuple[str, ...]] = ("complete", "partial", "quarantined")

#: How exactly a normalized event date is known. `interval` is a real source-supplied range; `year`
#: and `month` are partial dates widened to their own span, never narrowed to an invented day.
EVENT_PRECISIONS: Final[tuple[str, ...]] = ("day", "month", "year", "interval", "unknown")

#: What the published coordinate is permitted to mean. `generalized` and `withheld` can never be
#: promoted to `exact`, and `nonspatial` records are served as counts, never as points.
SPATIAL_CLASSES: Final[tuple[str, ...]] = ("exact", "generalized", "withheld", "nonspatial")

#: Whether the record's uncertainty footprint lies inside ONE support cell or merely touches it.
MEMBERSHIPS: Final[tuple[str, ...]] = ("confirmed", "possible")

#: What a support cell's evidence state is. `evaluated_zero` says the cell was inside admitted
#: coverage and held no admitted record -- it does NOT say the taxon is absent from the ground.
SUPPORT_EVALUATIONS: Final[tuple[str, ...]] = (
    "evaluated_zero",
    "documented",
    "outside_coverage",
    "withheld_or_generalized_only",
    "not_evaluated",
)

#: How a resolved name was bound to a concept. Only `resolved` claims an authority agreed.
RESOLUTION_STATES: Final[tuple[str, ...]] = ("resolved", "ambiguous", "unmatched")


# --- Release receipt ---------------------------------------------------------------------------

BOTANICAL_SOURCE_RELEASE_GRAIN: Final[tuple[str, ...]] = ("collection_key", "release_key")

BOTANICAL_SOURCE_RELEASE_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=BOTANICAL_SOURCE_RELEASE_STREAM,
        arrow_schema=pa.schema(
            [
                # PlantGeo's proposed namespace key, e.g. `pnw:UBC:vascular`; not a publisher GUID.
                pa.field("collection_key", pa.string(), nullable=False),
                # sha256 of (collection_key, source_version, archive_sha256); see foundation/release_identity.py.
                pa.field("release_key", pa.string(), nullable=False),
                # Who served the bytes, which is not always who curated them.
                pa.field("distributor", pa.string(), nullable=False),
                # The publisher's own version string (`16.43`), or empty when the publisher states none.
                pa.field("source_version", pa.string(), nullable=False),
                # EML packageId, the only publisher-minted identity in this row.
                pa.field("package_id", pa.string(), nullable=True),
                pa.field("doi", pa.string(), nullable=True),
                # The immutable identity of the bytes. Nothing else in this row proves the archive.
                pa.field("archive_sha256", pa.string(), nullable=False),
                pa.field("archive_bytes", pa.int64(), nullable=False),
                pa.field("eml_sha256", pa.string(), nullable=True),
                pa.field("meta_sha256", pa.string(), nullable=True),
                # Rights as the archive itself declares them; a provider web page is not this field.
                pa.field("rights_uri", pa.string(), nullable=True),
                pa.field("attribution_text", pa.string(), nullable=True),
                # Which records the publisher's coordinate policy applies to, verbatim from the EML.
                pa.field("coordinate_policy_scope", pa.string(), nullable=True),
                pa.field("retrieved_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("publisher_pub_date", pa.string(), nullable=True),
                pa.field("core_row_count", pa.int64(), nullable=False),
                # JSON object member_name -> row count; a map would not survive a schema-free reader.
                pa.field("extension_row_counts", pa.string(), nullable=False),
                # The parser recipe that read this archive; re-reading under a new one is a new release row.
                pa.field("parser_version", pa.string(), nullable=False),
                pa.field("outcome", pa.string(), nullable=False),
                # Why an outcome is `partial` or `quarantined`; empty list for `complete`.
                pa.field("reasons", pa.list_(pa.string()), nullable=False),
            ]
        ),
        sort_columns=BOTANICAL_SOURCE_RELEASE_GRAIN,
    )
)


# --- Verbatim core rows ------------------------------------------------------------------------

BOTANICAL_RAW_OCCURRENCE_GRAIN: Final[tuple[str, ...]] = ("release_key", "member_name", "row_number")

#: Every Darwin Core term this lane promotes to its own column. The full source row also survives
#: verbatim in `verbatim` -- these columns are a read convenience, never the record of what the
#: publisher said.
PROMOTED_OCCURRENCE_TERMS: Final[tuple[str, ...]] = (
    "occurrenceID",
    "catalogNumber",
    "basisOfRecord",
    "recordedBy",
    "recordNumber",
    "eventDate",
    "verbatimEventDate",
    "year",
    "month",
    "day",
    "scientificName",
    "family",
    "genus",
    "taxonRank",
    "identifiedBy",
    "dateIdentified",
    "decimalLatitude",
    "decimalLongitude",
    "geodeticDatum",
    "coordinateUncertaintyInMeters",
    "coordinatePrecision",
    "country",
    "stateProvince",
    "county",
    "locality",
    "informationWithheld",
    "dataGeneralizations",
    "establishmentMeans",
    "occurrenceStatus",
    "modified",
)


def _promoted_term_fields() -> list[pa.Field]:
    """One nullable string column per promoted term: the publisher's own text, never coerced."""
    return [pa.field(term, pa.string(), nullable=True) for term in PROMOTED_OCCURRENCE_TERMS]


BOTANICAL_RAW_OCCURRENCE_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=BOTANICAL_RAW_OCCURRENCE_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("collection_key", pa.string(), nullable=False),
                pa.field("release_key", pa.string(), nullable=False),
                # The publisher's native record key: occurrenceID, else catalogNumber, else the locator.
                pa.field("source_record_key", pa.string(), nullable=False),
                # Which archive member the row came out of, and its 1-based data row within it.
                pa.field("member_name", pa.string(), nullable=False),
                pa.field("row_number", pa.int64(), nullable=False),
                # sha256 over the row's verbatim field values; how a reused key with changed content shows.
                pa.field("row_sha256", pa.string(), nullable=False),
                # JSON object of EVERY core column, including ones this schema does not promote.
                pa.field("verbatim", pa.string(), nullable=False),
                *_promoted_term_fields(),
            ]
        ),
        sort_columns=BOTANICAL_RAW_OCCURRENCE_GRAIN,
    )
)


# --- Identification extension ------------------------------------------------------------------

BOTANICAL_IDENTIFICATION_GRAIN: Final[tuple[str, ...]] = ("release_key", "member_name", "row_number")

BOTANICAL_IDENTIFICATION_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=BOTANICAL_IDENTIFICATION_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("collection_key", pa.string(), nullable=False),
                pa.field("release_key", pa.string(), nullable=False),
                # The extension's `coreid`: which occurrence this determination annotates.
                pa.field("core_id", pa.string(), nullable=False),
                pa.field("member_name", pa.string(), nullable=False),
                pa.field("row_number", pa.int64(), nullable=False),
                pa.field("row_sha256", pa.string(), nullable=False),
                # Every determination is preserved: a later one does not delete an earlier one.
                pa.field("verbatim", pa.string(), nullable=False),
                pa.field("identifiedBy", pa.string(), nullable=True),
                pa.field("dateIdentified", pa.string(), nullable=True),
                pa.field("scientificName", pa.string(), nullable=True),
                pa.field("taxonRank", pa.string(), nullable=True),
                pa.field("identificationRemarks", pa.string(), nullable=True),
            ]
        ),
        sort_columns=BOTANICAL_IDENTIFICATION_GRAIN,
    )
)


# --- Normalized occurrence ---------------------------------------------------------------------

BOTANICAL_NORMALIZED_OCCURRENCE_GRAIN: Final[tuple[str, ...]] = ("occurrence_id",)

BOTANICAL_NORMALIZED_OCCURRENCE_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=BOTANICAL_NORMALIZED_OCCURRENCE_STREAM,
        arrow_schema=pa.schema(
            [
                # Deterministic: sha256 of (collection_key, source_record_key, row_sha256).
                pa.field("occurrence_id", pa.string(), nullable=False),
                pa.field("collection_key", pa.string(), nullable=False),
                pa.field("release_key", pa.string(), nullable=False),
                pa.field("source_record_key", pa.string(), nullable=False),
                # The concept key shared with the nonspatial species-profile lookup.
                pa.field("taxon_concept_id", pa.string(), nullable=False),
                pa.field("taxonomy_recipe_version", pa.string(), nullable=False),
                pa.field("resolution_state", pa.string(), nullable=False),
                pa.field("scientific_name", pa.string(), nullable=True),
                pa.field("family", pa.string(), nullable=True),
                # The collecting event, as an interval. A year value spans its whole year.
                pa.field("event_start", pa.date32(), nullable=True),
                pa.field("event_end", pa.date32(), nullable=True),
                pa.field("event_precision", pa.string(), nullable=False),
                pa.field("longitude", pa.float64(), nullable=True),
                pa.field("latitude", pa.float64(), nullable=True),
                pa.field("coordinate_uncertainty_m", pa.float64(), nullable=True),
                pa.field("spatial_class", pa.string(), nullable=False),
                pa.field("qc_policy_version", pa.string(), nullable=False),
                # Why the record is classified as it is, and every duplicate it collides with.
                pa.field("qc_reasons", pa.list_(pa.string()), nullable=False),
                # Whether the point falls inside the lane's declared admitted envelope.
                pa.field("within_envelope", pa.bool_(), nullable=False),
                # WGS 84 point WKB, null for withheld and nonspatial records. No SRID is stored.
                pa.field("geom", pa.binary(), nullable=True),
                pa.field("catalog_number", pa.string(), nullable=True),
                pa.field("recorded_by", pa.string(), nullable=True),
                pa.field("basis_of_record", pa.string(), nullable=True),
                pa.field("rights_uri", pa.string(), nullable=True),
                pa.field("attribution_text", pa.string(), nullable=True),
            ]
        ),
        sort_columns=BOTANICAL_NORMALIZED_OCCURRENCE_GRAIN,
    )
)


# --- Spatial association -----------------------------------------------------------------------

BOTANICAL_SPATIAL_ASSOCIATION_GRAIN: Final[tuple[str, ...]] = ("support_id", "cell_id", "occurrence_id")

BOTANICAL_SPATIAL_ASSOCIATION_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=BOTANICAL_SPATIAL_ASSOCIATION_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("occurrence_id", pa.string(), nullable=False),
                pa.field("support_id", pa.string(), nullable=False),
                pa.field("cell_id", pa.string(), nullable=False),
                pa.field("membership", pa.string(), nullable=False),
                # What a distance to this association MEANS: to the point, or to the cell centroid.
                pa.field("distance_semantics", pa.string(), nullable=False),
            ]
        ),
        sort_columns=BOTANICAL_SPATIAL_ASSOCIATION_GRAIN,
    )
)


# --- Support evaluation ------------------------------------------------------------------------

BOTANICAL_SUPPORT_EVALUATION_GRAIN: Final[tuple[str, ...]] = ("release_set_id", "support_id", "cell_id")

BOTANICAL_SUPPORT_EVALUATION_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=BOTANICAL_SUPPORT_EVALUATION_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("release_set_id", pa.string(), nullable=False),
                pa.field("support_id", pa.string(), nullable=False),
                pa.field("cell_id", pa.string(), nullable=False),
                pa.field("evaluation", pa.string(), nullable=False),
                pa.field("record_count", pa.int64(), nullable=False),
                # Distinct concepts among CONFIRMED records; recomputed per support, never summed.
                pa.field("documented_taxa", pa.int64(), nullable=False),
                # Distinct (recordedBy, event interval) pairs: how many collection events, not specimens.
                pa.field("event_estimate", pa.int64(), nullable=False),
                pa.field("collection_count", pa.int64(), nullable=False),
                pa.field("excluded_by_qc", pa.int64(), nullable=False),
                pa.field("possible_only_records", pa.int64(), nullable=False),
                pa.field("geom", pa.binary(), nullable=False),
            ]
        ),
        sort_columns=BOTANICAL_SUPPORT_EVALUATION_GRAIN,
    )
)


# --- Sparse cell/taxon summary -----------------------------------------------------------------

BOTANICAL_CELL_TAXON_SUMMARY_GRAIN: Final[tuple[str, ...]] = (
    "release_set_id",
    "support_id",
    "cell_id",
    "taxon_concept_id",
)

BOTANICAL_CELL_TAXON_SUMMARY_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=BOTANICAL_CELL_TAXON_SUMMARY_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("release_set_id", pa.string(), nullable=False),
                pa.field("support_id", pa.string(), nullable=False),
                pa.field("cell_id", pa.string(), nullable=False),
                pa.field("taxon_concept_id", pa.string(), nullable=False),
                pa.field("record_count", pa.int64(), nullable=False),
                pa.field("event_estimate", pa.int64(), nullable=False),
                pa.field("collection_count", pa.int64(), nullable=False),
                pa.field("earliest_event", pa.date32(), nullable=True),
                pa.field("latest_event", pa.date32(), nullable=True),
            ]
        ),
        sort_columns=BOTANICAL_CELL_TAXON_SUMMARY_GRAIN,
    )
)


BOTANICAL_STREAMS: Final[tuple[ParquetStreamSchema, ...]] = (
    BOTANICAL_SOURCE_RELEASE_SCHEMA,
    BOTANICAL_RAW_OCCURRENCE_SCHEMA,
    BOTANICAL_IDENTIFICATION_SCHEMA,
    BOTANICAL_NORMALIZED_OCCURRENCE_SCHEMA,
    BOTANICAL_SPATIAL_ASSOCIATION_SCHEMA,
    BOTANICAL_SUPPORT_EVALUATION_SCHEMA,
    BOTANICAL_CELL_TAXON_SUMMARY_SCHEMA,
)

__all__ = [
    "BOTANICAL_CELL_TAXON_SUMMARY_SCHEMA",
    "BOTANICAL_CELL_TAXON_SUMMARY_STREAM",
    "BOTANICAL_IDENTIFICATION_SCHEMA",
    "BOTANICAL_IDENTIFICATION_STREAM",
    "BOTANICAL_NORMALIZED_OCCURRENCE_SCHEMA",
    "BOTANICAL_NORMALIZED_OCCURRENCE_STREAM",
    "BOTANICAL_RAW_OCCURRENCE_SCHEMA",
    "BOTANICAL_RAW_OCCURRENCE_STREAM",
    "BOTANICAL_SOURCE_RELEASE_SCHEMA",
    "BOTANICAL_SOURCE_RELEASE_STREAM",
    "BOTANICAL_SPATIAL_ASSOCIATION_SCHEMA",
    "BOTANICAL_SPATIAL_ASSOCIATION_STREAM",
    "BOTANICAL_STREAMS",
    "BOTANICAL_SUPPORT_EVALUATION_SCHEMA",
    "BOTANICAL_SUPPORT_EVALUATION_STREAM",
    "EVENT_PRECISIONS",
    "MEMBERSHIPS",
    "PROMOTED_OCCURRENCE_TERMS",
    "RELEASE_OUTCOMES",
    "RESOLUTION_STATES",
    "SPATIAL_CLASSES",
    "SUPPORT_EVALUATIONS",
]
