"""Both sides of every phase-2A parity fixture, wired to the one fixed case set.

An adapter is the seam that lets one case set judge two module layouts: this service keeps the whole
object-key grammar in `foundation/parquet_paths.py`, while agri-data-service spreads the same
knowledge over `foundation/parquet/paths.py`, `foundation/parquet/zoom.py`,
`pipeline/parquet/objectstore.py` and `pipeline/parquet/availability_*.py`. Where a signature also
differs, the shim lives HERE and nowhere in the shipped modules, so a rename on either side fails a
test instead of quietly widening a public function. See `tests/AGENTS.md`.

The sibling adapters reach two private names (`_generation_receipt_sha256`, `_metadata`). That is
deliberate: those two functions ARE the generation's content address, so comparing anything more
public would prove less.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from parity_parquet_cases import (
    AvailabilityAdapter,
    LanesAdapter,
    MarkersAdapter,
    PathsAdapter,
    StreamsAdapter,
    sibling_module,
)

if TYPE_CHECKING:
    from datetime import date

    from plantgeo_ml_service.foundation.parquet_paths import PartitionKind


def _ml_bootstrap_marker_key(lane_root: str) -> str:
    """Return this service's bootstrap marker key; it lives in the L0 path module, not beside the writer."""
    from plantgeo_ml_service.foundation.parquet_paths import availability_bootstrap_marker_key  # noqa: PLC0415

    return availability_bootstrap_marker_key(lane_root)


def _lane_identity(lane_root: str) -> tuple[str, str]:
    """Split a `layer=<slug>/kind=<kind>` root back into its two segments."""
    layer_segment, kind_segment = lane_root.split("/")
    return layer_segment.removeprefix("layer="), kind_segment.removeprefix("kind=")


# --- This service ----------------------------------------------------------------------------------


def ml_paths_adapter() -> PathsAdapter:
    """Return this service's path grammar, all of which lives in one L0 module."""
    from plantgeo_ml_service.foundation import parquet_paths as paths  # noqa: PLC0415

    return PathsAdapter(
        validate_layer_slug=paths.validate_layer_slug,
        serving_zoom_tier=paths.serving_zoom_tier,
        partition_path=paths.partition_path,
        absence_marker_path=paths.absence_marker_path,
        completion_marker_path=paths.completion_marker_path,
        derived_empty_completion_marker_path=paths.derived_empty_completion_marker_path,
        promotion_receipt_path=paths.promotion_receipt_path,
        # `kind` is validated inside `availability_lane_root` itself; the cast just widens the fixed
        # case-set's `str` to the narrower `PartitionKind` the real function requires.
        availability_lane_root=lambda layer, kind: paths.availability_lane_root(layer, cast("PartitionKind", kind)),
        day_prefix=paths.day_prefix,
        try_parse_partition_path=paths.try_parse_partition_path,
        try_parse_absence_marker_path=paths.try_parse_absence_marker_path,
        try_parse_completion_marker_path=paths.try_parse_completion_marker_path,
        availability_retry_path=paths.availability_retry_path,
        availability_retry_quarantine_path=paths.availability_retry_quarantine_path,
        availability_retry_prefix=lambda layer, kind: paths.availability_retry_prefix(
            layer, cast("PartitionKind", kind)
        ),
        try_parse_availability_retry_path=paths.try_parse_availability_retry_path,
        try_parse_availability_retry_quarantine_path=paths.try_parse_availability_retry_quarantine_path,
    )


def ml_markers_adapter() -> MarkersAdapter:
    """Return this service's completion and absence payload classes."""
    from plantgeo_ml_service.foundation import parquet_markers as markers  # noqa: PLC0415

    return MarkersAdapter(
        partition_completion=markers.PartitionCompletion,
        completed_part=markers.CompletedPart,
        governed_absence=markers.GovernedAbsence,
    )


def ml_streams_adapter() -> StreamsAdapter:
    """Return this service's pinned stream contracts and the bytes its writer would upload."""
    from plantgeo_ml_service.pipeline import object_store  # noqa: PLC0415
    from plantgeo_ml_service.warehouse import streams  # noqa: PLC0415

    return StreamsAdapter(
        observed_schema=lambda name: streams.stream_schema(name, "observed"),
        forecast_schema=lambda name: streams.stream_schema(name, "forecast"),
        base_non_null_columns=lambda name: streams.observed_stream_schema(name).base_non_null_columns,
        serialize=lambda table, stream: object_store.serialize_parquet(
            object_store.conform_to_stream_schema(table, stream), stream.compression
        ),
        conform=object_store.conform_to_stream_schema,
    )


def ml_lanes_adapter() -> LanesAdapter:
    """Return this service's copied lane contracts."""
    from plantgeo_ml_service.warehouse import lanes  # noqa: PLC0415

    return LanesAdapter(contract=lanes.lane_contract)


def ml_availability_adapter() -> AvailabilityAdapter:
    """Return this service's availability documents, with its two renamed signatures shimmed."""
    from plantgeo_ml_service.warehouse import availability  # noqa: PLC0415

    def metadata(  # noqa: PLR0913 - one keyword per generation-metadata field is the contract
        *,
        config: Any,
        generation_receipt_sha256: str,
        row_count: int,
        earliest_terminal_day: date,
        latest_terminal_day: date,
        prior_generation_key: str | None,
        prior_generation_sha256: str | None,
        created_at: Any,
    ) -> dict[bytes, bytes]:
        return availability.generation_metadata(
            config,
            receipt_sha256=generation_receipt_sha256,
            row_span=(row_count, earliest_terminal_day, latest_terminal_day),
            prior_generation=(prior_generation_key, prior_generation_sha256),
            created_at=created_at,
        )

    def generation_key(lane_root: str, generation_sha256: str) -> str:
        layer, kind = _lane_identity(lane_root)
        return availability.generation_key_for(layer, cast("PartitionKind", kind), generation_sha256)

    def pointer_key(lane_root: str) -> str:
        from plantgeo_ml_service.foundation.parquet_paths import availability_pointer_path  # noqa: PLC0415

        layer, kind = _lane_identity(lane_root)
        return availability_pointer_path(layer, cast("PartitionKind", kind))

    return AvailabilityAdapter(
        index_schema=availability.AVAILABILITY_INDEX_SCHEMA,
        required_rungs=availability.AVAILABILITY_REQUIRED_RUNGS,
        schema_version=availability.AVAILABILITY_SCHEMA_VERSION,
        evidence_receipt=availability.EvidenceReceipt,
        identity=availability.AvailabilityIdentity,
        config=availability.AvailabilityConfig,
        row=availability.AvailabilityRow,
        pointer=availability.AvailabilityPointer,
        pointer_key=pointer_key,
        generation_key=generation_key,
        bootstrap_marker_key=_ml_bootstrap_marker_key,
        receipt_sha256=lambda *, config, rows, prior_generation_key, prior_generation_sha256, created_at: (
            availability.generation_receipt_sha256(
                config,
                rows,
                prior_generation_key=prior_generation_key,
                prior_generation_sha256=prior_generation_sha256,
                created_at=created_at,
            )
        ),
        metadata_keys=availability.AVAILABILITY_METADATA_KEYS,
        metadata=metadata,
    )


# --- agri-data-service -------------------------------------------------------------------------------


def sibling_paths_adapter() -> PathsAdapter:
    """Return the sibling's path grammar, gathered from the three modules it is spread across."""
    paths = sibling_module("agri_data_service.foundation.parquet.paths")
    zoom = sibling_module("agri_data_service.foundation.parquet.zoom")
    objectstore = sibling_module("agri_data_service.pipeline.parquet.objectstore")
    return PathsAdapter(
        validate_layer_slug=paths.validate_layer_slug,
        serving_zoom_tier=zoom.serving_zoom_tier,
        partition_path=paths.partition_path,
        absence_marker_path=paths.absence_marker_path,
        completion_marker_path=paths.completion_marker_path,
        derived_empty_completion_marker_path=paths.derived_empty_completion_marker_path,
        promotion_receipt_path=paths.promotion_receipt_path,
        availability_lane_root=objectstore.availability_lane_root,
        day_prefix=paths.day_prefix,
        try_parse_partition_path=paths.try_parse_partition_path,
        try_parse_absence_marker_path=paths.try_parse_absence_marker_path,
        try_parse_completion_marker_path=paths.try_parse_completion_marker_path,
        availability_retry_path=objectstore.availability_retry_path,
        availability_retry_quarantine_path=objectstore.availability_retry_quarantine_path,
        availability_retry_prefix=objectstore.availability_retry_prefix,
        try_parse_availability_retry_path=objectstore.try_parse_availability_retry_path,
        try_parse_availability_retry_quarantine_path=objectstore.try_parse_availability_retry_quarantine_path,
    )


def sibling_markers_adapter() -> MarkersAdapter:
    """Return the sibling's completion and absence payload classes."""
    completion = sibling_module("agri_data_service.foundation.parquet.completion")
    absence = sibling_module("agri_data_service.foundation.parquet.absence")
    return MarkersAdapter(
        partition_completion=completion.PartitionCompletion,
        completed_part=completion.CompletedPart,
        governed_absence=absence.GovernedAbsence,
    )


def sibling_streams_adapter() -> StreamsAdapter:
    """Return the sibling's registered stream contracts, autoloading each lane's schema module."""
    schema = sibling_module("agri_data_service.warehouse.parquet.schema")
    tiers = sibling_module("agri_data_service.warehouse.parquet.tiers")
    for lane in ("fire_detections", "vegetation", "drought", "burn_severity", "weather_observations"):
        sibling_module(f"agri_data_service.warehouse.schemas.{lane}")
    objectstore = sibling_module("agri_data_service.pipeline.parquet.objectstore")
    return StreamsAdapter(
        observed_schema=lambda name: schema.get_stream_schema(name, "observed"),
        forecast_schema=lambda name: schema.get_stream_schema(name, "forecast"),
        base_non_null_columns=tiers.base_non_null_columns,
        # `_serialize_parquet` is private and reached deliberately: the bytes it writes ARE the
        # contract, and anything more public would compare something other than the uploaded file.
        serialize=lambda table, stream: objectstore._serialize_parquet(
            objectstore.conform_to_stream_schema(table, stream), stream.compression
        ),
        conform=objectstore.conform_to_stream_schema,
    )


def sibling_lanes_adapter() -> LanesAdapter:
    """Return the sibling's lane registry entries."""
    registry = sibling_module("agri_data_service.pipeline.parquet.lane_registry")
    return LanesAdapter(contract=lambda slug: registry.LANE_REGISTRY[slug])


def sibling_availability_adapter() -> AvailabilityAdapter:
    """Return the sibling's availability documents and the two private functions that address them."""
    documents = sibling_module("agri_data_service.pipeline.parquet.availability_documents")
    schema = sibling_module("agri_data_service.warehouse.schemas.availability_index")
    return AvailabilityAdapter(
        index_schema=schema.AVAILABILITY_INDEX_SCHEMA,
        required_rungs=schema.AVAILABILITY_REQUIRED_RUNGS,
        schema_version=schema.AVAILABILITY_SCHEMA_VERSION,
        evidence_receipt=documents.EvidenceReceipt,
        identity=documents.AvailabilityIdentity,
        config=documents.AvailabilityConfig,
        row=documents.AvailabilityRow,
        pointer=documents.AvailabilityPointer,
        pointer_key=documents.availability_pointer_key,
        generation_key=documents.availability_generation_key,
        bootstrap_marker_key=documents.availability_bootstrap_marker_key,
        receipt_sha256=documents._generation_receipt_sha256,
        metadata_keys=schema.AVAILABILITY_METADATA_KEYS,
        # `availability_index._metadata` is deliberately NOT reached: it sits behind a module that
        # imports SQLAlchemy, and a zero-Postgres service does not take an ORM into its lockfile to
        # read one pure function. The key set above is what crosses; see `tests/AGENTS.md`.
        metadata=None,
    )
