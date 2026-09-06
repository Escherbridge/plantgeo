"""Conform one release day's fetched MTBS records to `BURN_SEVERITY_SCHEMA`, WKB-repaired through DuckDB spatial."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.pipeline.direct.burn_severity.support import (
    burn_severity_geometry_session,
    repair_burn_severity_geometries_to_wkb,
)
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from agri_data_service.ingest.mtbs import MtbsBurnSeverityRecord

#: Namespaces a direct row's `feature_id` as never a genuine `geo.features.id`. The schema documents
#: `feature_id` as `features.id::text` for every Postgres-sourced row
#: (`sql/pipeline/burn_severity_day_export.sql:75`), and a direct write has no `geo.features` row to
#: cite. A `geo.features.id` is a bare UUID and never contains a colon, so this can never collide
#: with a real one -- the same `direct:` discipline `pipeline/direct/AGENTS.md`'s weather-observations
#: section names ("a `direct:` token, not a fabricated UUID").
DIRECT_FEATURE_ID_PREFIX: Final = "direct"


def direct_feature_id(fire_id: str) -> str:
    """Build the deterministic, `direct:`-namespaced id a direct row carries in place of a real one."""
    return f"{DIRECT_FEATURE_ID_PREFIX}:{fire_id}"


def burn_severity_release_day_table(
    records: Sequence[MtbsBurnSeverityRecord],
    *,
    observed_day: date,
) -> pa.Table:
    """Build the base-rung Arrow table for one release day, repairing every fire's polygon through DuckDB spatial.

    `observed_day` is the caller's own release day, never re-derived from `records`: a release day's
    records are already the union of every ignition-year cohort whose `data_available_at` resolves
    to that day (`products.py::release_days_by_ignition_year`, `source.py::fetch_burn_severity_release_day`),
    so re-deriving it here would only restate what the caller already proved and risk silently
    disagreeing with it if `records` were ever empty.

    `allowed_client_exposure` is a hardcoded `False` literal, never read off a column, matching
    `sql/pipeline/burn_severity_day_export.sql`'s own header: this is ingest-module POLICY
    (`ingest/mtbs.py` `MTBS_PURPOSE`, "nothing MTBS-derived may reach a public CDN without a fresh
    licensing review"), not a governed database row -- a direct fetch carries exactly the same
    licensing restriction as the Postgres-sourced rows the export carries it forward for.
    """
    with burn_severity_geometry_session() as session:
        repaired = repair_burn_severity_geometries_to_wkb(
            session, [(record.producer_local_id, record.geometry) for record in records]
        )
    rows = [
        {
            "feature_id": direct_feature_id(record.producer_local_id),
            "fire_id": record.producer_local_id,
            # `record.natural_key` is already `f"{producer}:{producer_local_id}"`
            # (`ingest/identity.py::FeatureIdentity.natural_key`), i.e. `mtbs:<fire_id>` -- carried
            # through rather than rebuilt, so this row can never disagree with the identity builder
            # that minted it.
            "natural_key": record.natural_key,
            "release_identifier": record.release_identifier,
            "mapping_revision": record.mapping_revision,
            "fire_year": record.ignition_year,
            "ignition_date": record.ignition_date,
            "observed_day": observed_day,
            "data_available_at": record.data_available_at,
            "fire_name": record.fire_name,
            "fire_type": record.fire_type,
            "assessment_type": record.assessment_type,
            "acres": record.acres,
            "severity_class": record.severity_class,
            "dnbr_offset": record.severity_thresholds.dnbr_offset,
            "dnbr_standard_deviation": record.severity_thresholds.dnbr_standard_deviation,
            "nodata_threshold": record.severity_thresholds.nodata_threshold,
            "greenness_threshold": record.severity_thresholds.greenness_threshold,
            "low_threshold": record.severity_thresholds.low_threshold,
            "moderate_threshold": record.severity_thresholds.moderate_threshold,
            "high_threshold": record.severity_thresholds.high_threshold,
            "allowed_client_exposure": False,
            "geom": repaired[record.producer_local_id],
        }
        for record in records
    ]
    return pa.Table.from_pylist(rows, schema=BURN_SEVERITY_SCHEMA.arrow_schema)


__all__ = ["DIRECT_FEATURE_ID_PREFIX", "burn_severity_release_day_table", "direct_feature_id"]
