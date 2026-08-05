"""Governed NDVI observation plane plus Monte Carlo iteration writer; see execution/AGENTS.md."""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.execution.vegetation_ndvi_forecast import (
    GAP_POLICY,
    METHOD_NAME,
    NDVI_LOWER_BOUND,
    NDVI_UPPER_BOUND,
    HorizonQuantiles,
    InsufficientNdviHistoryError,
    ObservedDay,
    SeasonalHistory,
    SimulationRequest,
    build_seasonal_history,
    canonical_parameter_text,
    climatology_baseline,
    history_checksum,
    parameter_checksum,
    persistence_baseline,
    simulate_horizon_quantiles,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SOURCE_LAYER_NAME: Final = "vegetation"
DATA_SOURCE_KEY: Final = "sentinel2-ndvi-l2a"
GRID_NAME: Final = "sentinel2-ndvi-0p25deg"
TRANSFORM_VERSION: Final = "sentinel2-ndvi-daily-cell-mean-v1"
SOURCE_VERSION: Final = "sentinel2-l2a-earth-search-v1"
SCHEMA_VERSION: Final = "geo-features-vegetation-properties-v1"
LICENSE_NAME: Final = "Copernicus Sentinel Data Legal Notice"
LICENSE_URL: Final = "https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice"
CITATION: Final = (
    "Contains modified Copernicus Sentinel-2 L2A data (ESA); NDVI aggregated to "
    "0.25-degree cell-day means by the PlantGeo agri-data-service."
)
METRIC_NAME: Final = "ndvi"
METRIC_UNIT: Final = "ndvi_index"
ENTITY_TYPE: Final = "grid_cell"
SERIES_KEY_PREFIX: Final = "ndvi-daily"
DAY_BUCKET_RULE: Final = "iso_date_prefix"
MIN_CANDIDATE_OBSERVED_DAYS: Final = 24
CELL_BATCH_SIZE: Final = 200
STATEMENT_TIMEOUT: Final = "120s"
DETERMINISM_GUCS: Final = (
    "SET LOCAL TimeZone = 'UTC'",
    "SET LOCAL DateStyle = 'ISO, MDY'",
    "SET LOCAL IntervalStyle = 'postgres'",
    "SET LOCAL extra_float_digits = 1",
    f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'",
)


@dataclass(frozen=True, slots=True)
class GovernedPlane:
    """Identifiers and checksums of the governed NDVI release every simulation is pinned to."""

    data_source_id: uuid.UUID
    source_release_id: uuid.UUID
    release_set_id: uuid.UUID
    release_manifest_checksum: str
    payload_checksum: str
    corpus_cell_count: int
    corpus_cell_day_count: int
    corpus_row_count: int
    first_observed_day: date
    last_observed_day: date


@dataclass(frozen=True, slots=True)
class RegistrationSummary:
    """Measured effect of one governed-plane registration pass."""

    plane: GovernedPlane
    requested_cell_count: int
    spatial_cell_count: int
    series_count: int
    observation_count: int


@dataclass(frozen=True, slots=True)
class SeriesIdentity:
    """One registered NDVI cell series and its geometry link."""

    series_id: uuid.UUID
    series_key: str
    entity_key: str
    spatial_cell_id: uuid.UUID
    contract_checksum: str
    contract_snapshot: str


@dataclass(frozen=True, slots=True)
class IterationOutcome:
    """One written iteration, or the reason a cell was refused without fabrication."""

    series_key: str
    iteration_id: uuid.UUID | None
    iteration_key: str
    value_count: int
    training_day_count: int
    skipped_reason_code: str | None


@dataclass(frozen=True, slots=True)
class OutcomeRow:
    """One reconciled forecast-versus-actual pair from the shipped iteration outcome view."""

    series_id: uuid.UUID
    cutoff_day: date
    horizon_step: int
    valid_day: date
    low_value: float
    median_value: float
    high_value: float
    actual_value: float
    interval_covered: bool


@dataclass(frozen=True, slots=True)
class ErrorMetrics:
    """Holdout error of one predictor over a shared evaluation set."""

    label: str
    point_count: int
    mean_absolute_error: float
    root_mean_squared_error: float
    bias: float


@dataclass(frozen=True, slots=True)
class HoldoutEvaluation:
    """Time-honest holdout evidence for the method against its trivial baselines."""

    cutoff_days: tuple[date, ...]
    iteration_count: int
    reconciled_actual_count: int
    interval_coverage_fraction: float
    method_metrics: ErrorMetrics
    persistence_metrics: ErrorMetrics
    climatology_metrics: ErrorMetrics
    metrics_by_horizon_bucket: tuple[tuple[str, ErrorMetrics], ...]


class IterationEvidenceConflictError(ValueError):
    """Raised when a recorded iteration key already carries different immutable evidence."""

    def __init__(self, iteration_key: str) -> None:
        super().__init__(f"forecast iteration {iteration_key} already exists with different immutable evidence")
        self.iteration_key = iteration_key


async def pin_determinism(session: AsyncSession) -> None:
    """Pin UTC, rendering and the transaction-local statement timeout before any checksummed read."""
    for statement in DETERMINISM_GUCS:
        await session.execute(text(statement))


def prefixed_cell_key(entity_key: str) -> str:
    """Return the grid-qualified spatial-cell key for one vegetation cell."""
    return f"{GRID_NAME}:{entity_key}"


def release_set_logical_key(cutoff_day: date) -> str:
    """Return the release-set logical key for one publisher-day cutoff."""
    return f"{DATA_SOURCE_KEY}-{GRID_NAME}-through-{cutoff_day.isoformat()}"


def _batched(values: tuple[str, ...], size: int) -> tuple[tuple[str, ...], ...]:
    return tuple(values[start : start + size] for start in range(0, len(values), size))


def _midnight(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC)


async def select_candidate_cell_keys(session: AsyncSession, *, cutoff_day: date, cell_limit: int) -> tuple[str, ...]:
    """Return a deterministic, spatially spread sample of vegetation cell keys with usable depth."""
    result = await session.execute(
        text(
            """
            WITH observed AS (
                SELECT
                    feature.properties->>'cellKey' AS entity_key,
                    substring(feature.properties->>'observedAt', 1, 10)::date AS observed_day
                FROM geo.features AS feature
                INNER JOIN geo.layers AS layer ON layer.id = feature.layer_id
                WHERE layer.name = :layer_name
                  AND substring(feature.properties->>'observedAt', 1, 10)::date <= :cutoff_day
                GROUP BY 1, 2
            )
            SELECT observed.entity_key
            FROM observed
            GROUP BY observed.entity_key
            HAVING count(*) >= :min_observed_days
            ORDER BY md5(observed.entity_key)
            LIMIT :cell_limit
            """
        ),
        {
            "layer_name": SOURCE_LAYER_NAME,
            "cutoff_day": cutoff_day,
            "min_observed_days": MIN_CANDIDATE_OBSERVED_DAYS,
            "cell_limit": cell_limit,
        },
    )
    return tuple(str(row[0]) for row in result.all())


async def _register_data_source(session: AsyncSession, *, reviewed_at: datetime) -> uuid.UUID:
    await session.execute(
        text(
            """
            INSERT INTO agri.data_source (
                key, name, owner, purpose, base_url, license_name, license_url, citation,
                refresh_policy, allowed_client_exposure, review_state, reviewed_at, reviewed_by,
                is_active, configuration
            )
            VALUES (
                :key, :name, :owner, :purpose, :base_url, :license_name, :license_url, :citation,
                CAST(:refresh_policy AS jsonb), true, 'approved', :reviewed_at, :reviewed_by,
                true, CAST(:configuration AS jsonb)
            )
            ON CONFLICT (key) DO NOTHING
            """
        ),
        {
            "key": DATA_SOURCE_KEY,
            "name": "Sentinel-2 L2A NDVI (Copernicus)",
            "owner": "European Space Agency / Copernicus",
            "purpose": (
                "Free and open surface-reflectance NDVI for the Pacific Northwest 0.25-degree "
                "vegetation lattice, used as the governed observation plane for NDVI forecasting."
            ),
            "base_url": "https://earth-search.aws.element84.com/v1",
            "license_name": LICENSE_NAME,
            "license_url": LICENSE_URL,
            "citation": CITATION,
            "refresh_policy": json.dumps({"cadence": "sentinel2_revisit", "nominalRevisitDays": 5}),
            "reviewed_at": reviewed_at,
            "reviewed_by": "agri-data-service/vegetation_ndvi_plane",
            "configuration": json.dumps(
                {
                    "sourceLayer": SOURCE_LAYER_NAME,
                    "gridName": GRID_NAME,
                    "dayBucketRule": DAY_BUCKET_RULE,
                    "maxSceneCloudCoverPercent": 20,
                }
            ),
        },
    )
    result = await session.execute(
        text("SELECT id FROM agri.data_source WHERE key = :key"),
        {"key": DATA_SOURCE_KEY},
    )
    return uuid.UUID(str(result.scalar_one()))


@dataclass(frozen=True, slots=True)
class _CorpusDigest:
    payload_checksum: str
    cell_count: int
    cell_day_count: int
    row_count: int
    first_observed_day: date
    last_observed_day: date


async def _corpus_digest(session: AsyncSession, *, cutoff_day: date) -> _CorpusDigest:
    result = await session.execute(
        text(
            """
            WITH corpus AS (
                SELECT
                    feature.properties->>'cellKey' AS entity_key,
                    substring(feature.properties->>'observedAt', 1, 10)::date AS observed_day,
                    avg((feature.properties->>'ndvi')::double precision) AS metric_value,
                    count(*) AS source_row_count
                FROM geo.features AS feature
                INNER JOIN geo.layers AS layer ON layer.id = feature.layer_id
                WHERE layer.name = :layer_name
                  AND substring(feature.properties->>'observedAt', 1, 10)::date <= :cutoff_day
                GROUP BY 1, 2
            )
            SELECT
                encode(
                    digest(
                        string_agg(
                            concat_ws('|', corpus.entity_key, corpus.observed_day::text, corpus.metric_value::text),
                            '|'
                            ORDER BY corpus.entity_key, corpus.observed_day
                        ),
                        'sha256'
                    ),
                    'hex'
                ) AS payload_checksum,
                count(*)::integer AS cell_day_count,
                count(DISTINCT corpus.entity_key)::integer AS cell_count,
                coalesce(sum(corpus.source_row_count), 0)::integer AS row_count,
                min(corpus.observed_day) AS first_observed_day,
                max(corpus.observed_day) AS last_observed_day
            FROM corpus
            """
        ),
        {"layer_name": SOURCE_LAYER_NAME, "cutoff_day": cutoff_day},
    )
    row = result.mappings().one()
    if row["payload_checksum"] is None:
        raise ValueError(f"no vegetation observations exist at or before {cutoff_day.isoformat()}")
    return _CorpusDigest(
        payload_checksum=str(row["payload_checksum"]),
        cell_count=int(row["cell_count"]),
        cell_day_count=int(row["cell_day_count"]),
        row_count=int(row["row_count"]),
        first_observed_day=row["first_observed_day"],
        last_observed_day=row["last_observed_day"],
    )


async def _register_source_release(
    session: AsyncSession,
    *,
    data_source_id: uuid.UUID,
    corpus: _CorpusDigest,
    cutoff_day: date,
    recorded_at: datetime,
) -> uuid.UUID:
    await session.execute(
        text(
            """
            INSERT INTO agri.source_release (
                data_source_id, source_version, retrieved_at, data_available_at,
                observed_from, observed_to, payload_checksum, schema_version, license_snapshot,
                query_parameters, quality_summary, validation_state, validated_at, transform_version
            )
            VALUES (
                :data_source_id, :source_version, :retrieved_at, :data_available_at,
                :observed_from, :observed_to, :payload_checksum, :schema_version, :license_snapshot,
                CAST(:query_parameters AS jsonb), CAST(:quality_summary AS jsonb),
                'valid', :validated_at, :transform_version
            )
            ON CONFLICT (data_source_id, source_version, payload_checksum, transform_version) DO NOTHING
            """
        ),
        {
            "data_source_id": data_source_id,
            "source_version": SOURCE_VERSION,
            "retrieved_at": recorded_at,
            "data_available_at": recorded_at,
            "observed_from": _midnight(corpus.first_observed_day),
            "observed_to": _midnight(corpus.last_observed_day + timedelta(days=1)),
            "payload_checksum": corpus.payload_checksum,
            "schema_version": SCHEMA_VERSION,
            "license_snapshot": LICENSE_NAME,
            "query_parameters": json.dumps(
                {
                    "sourceLayer": SOURCE_LAYER_NAME,
                    "gridName": GRID_NAME,
                    "dayBucketRule": DAY_BUCKET_RULE,
                    "publisherDayCutoff": cutoff_day.isoformat(),
                }
            ),
            "quality_summary": json.dumps(
                {
                    "corpusCellCount": corpus.cell_count,
                    "corpusCellDayCount": corpus.cell_day_count,
                    "corpusSourceRowCount": corpus.row_count,
                    "firstObservedDay": corpus.first_observed_day.isoformat(),
                    "lastObservedDay": corpus.last_observed_day.isoformat(),
                    "materialisationIsIncremental": True,
                }
            ),
            "validated_at": recorded_at,
            "transform_version": TRANSFORM_VERSION,
        },
    )
    result = await session.execute(
        text(
            """
            SELECT id
            FROM agri.source_release
            WHERE data_source_id = :data_source_id
              AND source_version = :source_version
              AND payload_checksum = :payload_checksum
              AND transform_version = :transform_version
            """
        ),
        {
            "data_source_id": data_source_id,
            "source_version": SOURCE_VERSION,
            "payload_checksum": corpus.payload_checksum,
            "transform_version": TRANSFORM_VERSION,
        },
    )
    return uuid.UUID(str(result.scalar_one()))


async def _register_release_set(
    session: AsyncSession,
    *,
    source_release_id: uuid.UUID,
    payload_checksum: str,
    cutoff_day: date,
    recorded_at: datetime,
) -> tuple[uuid.UUID, str]:
    logical_key = release_set_logical_key(cutoff_day)
    manifest_result = await session.execute(
        text(
            """
            SELECT encode(
                digest(
                    concat_ws(
                        '|',
                        CAST(:prefix AS text),
                        CAST(:logical_key AS text),
                        CAST(:payload_checksum AS text)
                    ),
                    'sha256'
                ),
                'hex'
            )
            """
        ),
        {
            "prefix": "sentinel2_ndvi_release_manifest_v1",
            "logical_key": logical_key,
            "payload_checksum": payload_checksum,
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO agri.release_set (
                logical_key, as_of_time, manifest_checksum, state, description, created_at
            )
            VALUES (:logical_key, :as_of_time, :manifest_checksum, 'draft', :description, :created_at)
            ON CONFLICT (logical_key) DO NOTHING
            """
        ),
        {
            "logical_key": logical_key,
            "as_of_time": recorded_at,
            "manifest_checksum": str(manifest_result.scalar_one()),
            "description": (
                "Governed Sentinel-2 NDVI cell-day observation release for the Pacific Northwest "
                "0.25-degree lattice, pinned by publisher-named day."
            ),
            "created_at": recorded_at,
        },
    )
    release_set_result = await session.execute(
        text("SELECT id, state, manifest_checksum FROM agri.release_set WHERE logical_key = :logical_key"),
        {"logical_key": logical_key},
    )
    release_set_row = release_set_result.mappings().one()
    release_set_id = uuid.UUID(str(release_set_row["id"]))
    await session.execute(
        text(
            """
            INSERT INTO agri.release_set_item (release_set_id, source_release_id, source_role, added_at)
            VALUES (:release_set_id, :source_release_id, 'primary_observation', :added_at)
            ON CONFLICT DO NOTHING
            """
        ),
        {"release_set_id": release_set_id, "source_release_id": source_release_id, "added_at": recorded_at},
    )
    if str(release_set_row["state"]) == "draft":
        await session.execute(
            text("UPDATE agri.release_set SET state = 'validated', validated_at = :validated_at WHERE id = :id"),
            {"id": release_set_id, "validated_at": recorded_at},
        )
    return release_set_id, str(release_set_row["manifest_checksum"])


async def _register_spatial_cells(session: AsyncSession, *, cell_keys: tuple[str, ...]) -> int:
    inserted = 0
    for batch in _batched(cell_keys, CELL_BATCH_SIZE):
        result = await session.execute(
            text(
                """
                WITH selected AS (
                    SELECT
                        feature.properties->>'cellKey' AS entity_key,
                        ST_Force2D(ST_Envelope(ST_Collect(feature.geom))) AS geometry,
                        min((feature.properties->>'resolutionMetres')::integer) AS resolution_m
                    FROM geo.features AS feature
                    INNER JOIN geo.layers AS layer ON layer.id = feature.layer_id
                    WHERE layer.name = :layer_name
                      AND feature.properties->>'cellKey' = ANY(CAST(:cell_keys AS text[]))
                    GROUP BY 1
                )
                INSERT INTO agri.spatial_cell (
                    cell_key, grid_name, resolution_m, geometry, centroid, coverage_fraction
                )
                SELECT
                    :grid_name || ':' || selected.entity_key,
                    :grid_name,
                    selected.resolution_m,
                    selected.geometry,
                    ST_Centroid(selected.geometry),
                    1.0
                FROM selected
                ON CONFLICT DO NOTHING
                RETURNING id
                """
            ),
            {"layer_name": SOURCE_LAYER_NAME, "cell_keys": list(batch), "grid_name": GRID_NAME},
        )
        inserted += len(result.all())
    return inserted


async def _register_series(session: AsyncSession, *, data_source_id: uuid.UUID, cell_keys: tuple[str, ...]) -> int:
    inserted = 0
    for batch in _batched(cell_keys, CELL_BATCH_SIZE):
        result = await session.execute(
            text(
                """
                INSERT INTO agri.forecast_series (
                    series_key, source_variant_key, input_adapter, data_source_id,
                    source_transform_version, entity_type, entity_key, metric_name, metric_unit,
                    spatial_cell_id, representation_kind, spatial_support_kind,
                    source_spatial_resolution_m, output_spatial_resolution_m,
                    source_temporal_support, output_temporal_support, aggregation_method, metadata_json
                )
                SELECT
                    :series_key_prefix || ':' || cell.cell_key,
                    :source_variant_key,
                    'forecast_observation',
                    :data_source_id,
                    :transform_version,
                    :entity_type,
                    right(cell.cell_key, length(cell.cell_key) - length(:grid_name) - 1),
                    :metric_name,
                    :metric_unit,
                    cell.id,
                    'aggregate',
                    'native_grid_cell',
                    cell.resolution_m,
                    cell.resolution_m,
                    interval '1 day',
                    interval '1 day',
                    'daily_mean',
                    CAST(:metadata_json AS jsonb)
                FROM agri.spatial_cell AS cell
                WHERE cell.grid_name = :grid_name
                  AND cell.cell_key = ANY(CAST(:prefixed_cell_keys AS text[]))
                ON CONFLICT DO NOTHING
                RETURNING id
                """
            ),
            {
                "series_key_prefix": SERIES_KEY_PREFIX,
                "source_variant_key": TRANSFORM_VERSION,
                "data_source_id": data_source_id,
                "transform_version": TRANSFORM_VERSION,
                "entity_type": ENTITY_TYPE,
                "metric_name": METRIC_NAME,
                "metric_unit": METRIC_UNIT,
                "grid_name": GRID_NAME,
                "prefixed_cell_keys": [prefixed_cell_key(key) for key in batch],
                "metadata_json": json.dumps(
                    {
                        "sourceLayer": SOURCE_LAYER_NAME,
                        "gridName": GRID_NAME,
                        "dayBucketRule": DAY_BUCKET_RULE,
                        "physicalRange": [NDVI_LOWER_BOUND, NDVI_UPPER_BOUND],
                    }
                ),
            },
        )
        inserted += len(result.all())
    return inserted


async def _load_observations(
    session: AsyncSession,
    *,
    source_release_id: uuid.UUID,
    cell_keys: tuple[str, ...],
    cutoff_day: date,
) -> int:
    inserted = 0
    for batch in _batched(cell_keys, CELL_BATCH_SIZE):
        result = await session.execute(
            text(
                """
                WITH daily AS (
                    SELECT
                        feature.properties->>'cellKey' AS entity_key,
                        substring(feature.properties->>'observedAt', 1, 10)::date AS observed_day,
                        avg((feature.properties->>'ndvi')::double precision) AS metric_value,
                        count(*)::integer AS source_row_count,
                        max(feature.created_at) AS data_available_at,
                        sum((feature.properties->>'sampleCount')::integer)::integer AS pixel_sample_count,
                        max((feature.properties->>'cloudCover')::double precision) AS max_cloud_cover,
                        array_agg(DISTINCT feature.properties->>'sceneId') AS scene_ids
                    FROM geo.features AS feature
                    INNER JOIN geo.layers AS layer ON layer.id = feature.layer_id
                    WHERE layer.name = :layer_name
                      AND feature.properties->>'cellKey' = ANY(CAST(:cell_keys AS text[]))
                      AND substring(feature.properties->>'observedAt', 1, 10)::date <= :cutoff_day
                    GROUP BY 1, 2
                )
                INSERT INTO agri.forecast_observation (
                    series_id, source_release_id, observed_at, valid_from, valid_to,
                    data_available_at, metric_value, quality_flag, source_event_key,
                    observation_checksum, metadata_json
                )
                SELECT
                    series.id,
                    :source_release_id,
                    daily.observed_day::timestamptz,
                    daily.observed_day::timestamptz,
                    (daily.observed_day + 1)::timestamptz,
                    daily.data_available_at,
                    daily.metric_value,
                    'accepted',
                    concat_ws(':', daily.entity_key, daily.observed_day::text),
                    encode(
                        digest(
                            concat_ws(
                                '|',
                                'sentinel2_ndvi_daily_cell_mean_v1',
                                daily.entity_key,
                                daily.observed_day::text,
                                daily.metric_value::text,
                                daily.source_row_count::text,
                                array_to_string(daily.scene_ids, ',')
                            ),
                            'sha256'
                        ),
                        'hex'
                    ),
                    jsonb_build_object(
                        'dayBucketRule', CAST(:day_bucket_rule AS text),
                        'sourceRowCount', daily.source_row_count,
                        'pixelSampleCount', daily.pixel_sample_count,
                        'maxSceneCloudCoverPercent', daily.max_cloud_cover,
                        'sceneIds', to_jsonb(daily.scene_ids)
                    )
                FROM daily
                INNER JOIN agri.spatial_cell AS cell
                    ON cell.cell_key = CAST(:grid_name AS text) || ':' || daily.entity_key
                INNER JOIN agri.forecast_series AS series
                    ON series.spatial_cell_id = cell.id
                   AND series.metric_name = CAST(:metric_name AS varchar)
                   AND series.source_transform_version = CAST(:transform_version AS varchar)
                ON CONFLICT DO NOTHING
                RETURNING id
                """
            ),
            {
                "layer_name": SOURCE_LAYER_NAME,
                "cell_keys": list(batch),
                "cutoff_day": cutoff_day,
                "source_release_id": source_release_id,
                "day_bucket_rule": DAY_BUCKET_RULE,
                "grid_name": GRID_NAME,
                "metric_name": METRIC_NAME,
                "transform_version": TRANSFORM_VERSION,
            },
        )
        inserted += len(result.all())
    return inserted


async def register_governed_plane(
    session: AsyncSession,
    *,
    cutoff_day: date,
    cell_keys: tuple[str, ...],
) -> RegistrationSummary:
    """Register the governed Sentinel-2 NDVI observation plane for a bounded cell selection."""
    if not cell_keys:
        raise ValueError("registration requires at least one vegetation cell key")
    await pin_determinism(session)
    recorded_at = datetime.now(tz=UTC)
    data_source_id = await _register_data_source(session, reviewed_at=recorded_at)
    corpus = await _corpus_digest(session, cutoff_day=cutoff_day)
    source_release_id = await _register_source_release(
        session,
        data_source_id=data_source_id,
        corpus=corpus,
        cutoff_day=cutoff_day,
        recorded_at=recorded_at,
    )
    release_set_id, manifest_checksum = await _register_release_set(
        session,
        source_release_id=source_release_id,
        payload_checksum=corpus.payload_checksum,
        cutoff_day=cutoff_day,
        recorded_at=recorded_at,
    )
    spatial_cell_count = await _register_spatial_cells(session, cell_keys=cell_keys)
    series_count = await _register_series(session, data_source_id=data_source_id, cell_keys=cell_keys)
    observation_count = await _load_observations(
        session,
        source_release_id=source_release_id,
        cell_keys=cell_keys,
        cutoff_day=cutoff_day,
    )
    return RegistrationSummary(
        plane=GovernedPlane(
            data_source_id=data_source_id,
            source_release_id=source_release_id,
            release_set_id=release_set_id,
            release_manifest_checksum=manifest_checksum,
            payload_checksum=corpus.payload_checksum,
            corpus_cell_count=corpus.cell_count,
            corpus_cell_day_count=corpus.cell_day_count,
            corpus_row_count=corpus.row_count,
            first_observed_day=corpus.first_observed_day,
            last_observed_day=corpus.last_observed_day,
        ),
        requested_cell_count=len(cell_keys),
        spatial_cell_count=spatial_cell_count,
        series_count=series_count,
        observation_count=observation_count,
    )


async def load_governed_plane(session: AsyncSession, *, cutoff_day: date) -> GovernedPlane:
    """Return the already registered governed plane for one publisher-day cutoff."""
    logical_key = release_set_logical_key(cutoff_day)
    result = await session.execute(
        text(
            """
            SELECT
                source.id AS data_source_id,
                release.id AS source_release_id,
                release.payload_checksum,
                release.observed_from,
                release.observed_to,
                release.quality_summary,
                release_set.id AS release_set_id,
                release_set.manifest_checksum,
                release_set.state
            FROM agri.release_set AS release_set
            INNER JOIN agri.release_set_item AS member ON member.release_set_id = release_set.id
            INNER JOIN agri.source_release AS release ON release.id = member.source_release_id
            INNER JOIN agri.data_source AS source ON source.id = release.data_source_id
            WHERE release_set.logical_key = :logical_key
              AND source.key = :data_source_key
            """
        ),
        {"logical_key": logical_key, "data_source_key": DATA_SOURCE_KEY},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ValueError(f"no governed NDVI release set exists for publisher-day cutoff {cutoff_day.isoformat()}")
    if str(row["state"]) not in {"validated", "published"}:
        raise ValueError(f"governed NDVI release set {logical_key} is {row['state']!s}, not validated")
    observed_from: datetime = row["observed_from"]
    observed_to: datetime = row["observed_to"]
    quality_summary = row["quality_summary"]
    summary = quality_summary if isinstance(quality_summary, dict) else json.loads(str(quality_summary))
    return GovernedPlane(
        data_source_id=uuid.UUID(str(row["data_source_id"])),
        source_release_id=uuid.UUID(str(row["source_release_id"])),
        release_set_id=uuid.UUID(str(row["release_set_id"])),
        release_manifest_checksum=str(row["manifest_checksum"]),
        payload_checksum=str(row["payload_checksum"]),
        corpus_cell_count=int(summary.get("corpusCellCount", 0)),
        corpus_cell_day_count=int(summary.get("corpusCellDayCount", 0)),
        corpus_row_count=int(summary.get("corpusSourceRowCount", 0)),
        first_observed_day=observed_from.date(),
        last_observed_day=(observed_to - timedelta(days=1)).date(),
    )


async def load_series_identities(
    session: AsyncSession,
    *,
    cell_keys: tuple[str, ...] | None,
) -> tuple[SeriesIdentity, ...]:
    """Return registered NDVI series with their pinned contract snapshots and geometry links."""
    result = await session.execute(
        text(
            """
            SELECT
                contract.series_id,
                contract.series_key,
                contract.entity_key,
                contract.spatial_cell_id,
                contract.contract_checksum,
                contract.contract_snapshot::text AS contract_snapshot,
                contract.data_source_review_state,
                contract.license_name,
                contract.license_url,
                contract.citation
            FROM agri.v_forecast_timeseries_contract AS contract
            WHERE contract.data_source_key = :data_source_key
              AND contract.metric_name = :metric_name
              AND (
                    CAST(:cell_keys AS text[]) IS NULL
                    OR contract.entity_key = ANY(CAST(:cell_keys AS text[]))
              )
            ORDER BY contract.series_key
            """
        ),
        {
            "data_source_key": DATA_SOURCE_KEY,
            "metric_name": METRIC_NAME,
            "cell_keys": None if cell_keys is None else list(cell_keys),
        },
    )
    identities: list[SeriesIdentity] = []
    for row in result.mappings().all():
        series_key = str(row["series_key"])
        if str(row["data_source_review_state"]) != "approved":
            raise ValueError(f"NDVI series {series_key} is not behind an approved data source")
        if not str(row["license_name"]).strip() or not str(row["license_url"]).strip():
            raise ValueError(f"NDVI series {series_key} has no license snapshot")
        if not str(row["citation"]).strip():
            raise ValueError(f"NDVI series {series_key} has no citation")
        identities.append(
            SeriesIdentity(
                series_id=uuid.UUID(str(row["series_id"])),
                series_key=series_key,
                entity_key=str(row["entity_key"]),
                spatial_cell_id=uuid.UUID(str(row["spatial_cell_id"])),
                contract_checksum=str(row["contract_checksum"]),
                contract_snapshot=str(row["contract_snapshot"]),
            )
        )
    return tuple(identities)


async def load_governed_history(
    session: AsyncSession,
    *,
    release_set_id: uuid.UUID,
    as_of_time: datetime,
    cutoff_day: date,
) -> dict[uuid.UUID, tuple[ObservedDay, ...]]:
    """Return every registered series' leakage-free history in one governed pass."""
    result = await session.execute(
        text(
            """
            SELECT
                contract.series_id,
                contract.observed_at,
                contract.metric_value,
                contract.observation_checksum,
                contract.source_release_license_snapshot,
                contract.license_name
            FROM agri.forecast_timeseries_contract(:release_set_id, :as_of_time) AS contract
            WHERE contract.metric_name = :metric_name
              AND contract.observed_at < :cutoff_exclusive
            ORDER BY contract.series_id, contract.observed_at
            """
        ),
        {
            "release_set_id": release_set_id,
            "as_of_time": as_of_time,
            "metric_name": METRIC_NAME,
            "cutoff_exclusive": _midnight(cutoff_day + timedelta(days=1)),
        },
    )
    history: dict[uuid.UUID, list[ObservedDay]] = {}
    for row in result.mappings().all():
        if str(row["source_release_license_snapshot"]) != str(row["license_name"]):
            raise ValueError("governed NDVI observation carries a license snapshot the contract does not approve")
        observed_at: datetime = row["observed_at"]
        series_id = uuid.UUID(str(row["series_id"]))
        history.setdefault(series_id, []).append(
            ObservedDay(
                observed_day=observed_at.astimezone(UTC).date(),
                metric_value=float(row["metric_value"]),
                observation_checksum=str(row["observation_checksum"]),
            )
        )
    return {series_id: tuple(rows) for series_id, rows in history.items()}


async def load_license_snapshots(
    session: AsyncSession,
    *,
    release_set_id: uuid.UUID,
    as_of_time: datetime,
    cutoff_day: date,
) -> dict[uuid.UUID, str]:
    """Return each series' governed license snapshots in one pass, mirroring the shipped procedure."""
    result = await session.execute(
        text(
            """
            SELECT
                governed.series_id,
                jsonb_agg(
                    jsonb_build_object(
                        'source_release_id', governed.source_release_id,
                        'license_snapshot', governed.source_release_license_snapshot
                    )
                    ORDER BY governed.source_release_id
                )::text AS snapshots
            FROM (
                SELECT DISTINCT
                    contract.series_id,
                    contract.source_release_id,
                    contract.source_release_license_snapshot
                FROM agri.forecast_timeseries_contract(:release_set_id, :as_of_time) AS contract
                WHERE contract.metric_name = :metric_name
                  AND contract.observed_at < :cutoff_exclusive
            ) AS governed
            GROUP BY governed.series_id
            """
        ),
        {
            "release_set_id": release_set_id,
            "as_of_time": as_of_time,
            "metric_name": METRIC_NAME,
            "cutoff_exclusive": _midnight(cutoff_day + timedelta(days=1)),
        },
    )
    return {uuid.UUID(str(row["series_id"])): str(row["snapshots"]) for row in result.mappings().all()}


async def _existing_iteration_id(
    session: AsyncSession,
    *,
    iteration_key: str,
    parameter_checksum: str,
    governed_history_checksum: str,
) -> uuid.UUID:
    """Return an already finalized iteration only when its immutable evidence still matches."""
    result = await session.execute(
        text(
            """
            SELECT iteration.id, iteration.status, iteration.parameter_checksum, iteration.history_checksum
            FROM agri.forecast_iteration AS iteration
            WHERE iteration.iteration_key = :iteration_key
            """
        ),
        {"iteration_key": iteration_key},
    )
    row = result.mappings().one()
    if (
        str(row["status"]) != "finalized"
        or str(row["parameter_checksum"]) != parameter_checksum
        or str(row["history_checksum"]) != governed_history_checksum
    ):
        raise IterationEvidenceConflictError(iteration_key)
    return uuid.UUID(str(row["id"]))


async def _write_iteration(  # noqa: PLR0913
    session: AsyncSession,
    *,
    identity: SeriesIdentity,
    plane: GovernedPlane,
    purpose: str,
    as_of_time: datetime,
    history: SeasonalHistory,
    request: SimulationRequest,
    quantiles: tuple[HorizonQuantiles, ...],
    governed_history_checksum: str,
    checksum: str,
    license_snapshots: str,
    iteration_key: str,
) -> uuid.UUID:
    cutoff_time = _midnight(history.cutoff_day)
    availability_mode = (
        "as_of_pinned_release" if as_of_time <= cutoff_time + timedelta(days=1) else "retrospective_pinned_release"
    )
    iteration_id = uuid.uuid4()
    insert_result = await session.execute(
        text(
            """
            INSERT INTO agri.forecast_iteration (
                id, iteration_key, series_id, release_set_id, purpose, availability_mode, method,
                as_of_time, cutoff_time, history_start, horizon_days, simulation_count, simulation_seed,
                gap_policy, lower_bound, upper_bound, input_release_checksum, input_license_snapshots,
                contract_snapshot, contract_checksum, history_checksum, parameter_checksum,
                training_day_count, increment_count, expected_value_count
            )
            VALUES (
                :iteration_id, :iteration_key, :series_id, :release_set_id, :purpose, :availability_mode, :method,
                :as_of_time, :cutoff_time, :history_start, :horizon_days, :simulation_count, :simulation_seed,
                :gap_policy, :lower_bound, :upper_bound, :input_release_checksum,
                CAST(:input_license_snapshots AS jsonb), CAST(:contract_snapshot AS jsonb), :contract_checksum,
                :history_checksum, :parameter_checksum, :training_day_count, :increment_count, :expected_value_count
            )
            ON CONFLICT (iteration_key) DO NOTHING
            RETURNING id
            """
        ),
        {
            "iteration_id": iteration_id,
            "iteration_key": iteration_key,
            "series_id": identity.series_id,
            "release_set_id": plane.release_set_id,
            "purpose": purpose,
            "availability_mode": availability_mode,
            "method": METHOD_NAME,
            "as_of_time": as_of_time,
            "cutoff_time": cutoff_time,
            "history_start": _midnight(history.history_start_day),
            "horizon_days": request.horizon_days,
            "simulation_count": request.simulation_count,
            "simulation_seed": request.seed,
            "gap_policy": GAP_POLICY,
            "lower_bound": NDVI_LOWER_BOUND,
            "upper_bound": NDVI_UPPER_BOUND,
            "input_release_checksum": plane.release_manifest_checksum,
            "input_license_snapshots": license_snapshots,
            "contract_snapshot": identity.contract_snapshot,
            "contract_checksum": identity.contract_checksum,
            "history_checksum": governed_history_checksum,
            "parameter_checksum": checksum,
            "training_day_count": history.training_day_count,
            "increment_count": min(row.innovation_pool_size for row in quantiles),
            "expected_value_count": request.horizon_days,
        },
    )
    inserted_id = insert_result.scalar_one_or_none()
    if inserted_id is None:
        return await _existing_iteration_id(
            session,
            iteration_key=iteration_key,
            parameter_checksum=checksum,
            governed_history_checksum=governed_history_checksum,
        )
    await session.execute(
        text(
            """
            INSERT INTO agri.forecast_iteration_value (
                iteration_id, valid_time, horizon_step, low_value, median_value, high_value,
                increment_count, parameter_checksum, value_checksum
            )
            SELECT
                :iteration_id,
                simulated.valid_time,
                simulated.horizon_step,
                simulated.low_value,
                simulated.median_value,
                simulated.high_value,
                simulated.increment_count,
                :parameter_checksum,
                agri.forecast_iteration_value_checksum(
                    simulated.valid_time,
                    simulated.horizon_step,
                    simulated.low_value,
                    simulated.median_value,
                    simulated.high_value,
                    simulated.increment_count,
                    CAST(:parameter_checksum AS varchar)
                )
            FROM unnest(
                CAST(:valid_times AS timestamptz[]),
                CAST(:horizon_steps AS integer[]),
                CAST(:low_values AS double precision[]),
                CAST(:median_values AS double precision[]),
                CAST(:high_values AS double precision[]),
                CAST(:increment_counts AS integer[])
            ) AS simulated(valid_time, horizon_step, low_value, median_value, high_value, increment_count)
            """
        ),
        {
            "iteration_id": iteration_id,
            "parameter_checksum": checksum,
            "valid_times": [_midnight(row.valid_day) for row in quantiles],
            "horizon_steps": [row.horizon_step for row in quantiles],
            "low_values": [row.low_value for row in quantiles],
            "median_values": [row.median_value for row in quantiles],
            "high_values": [row.high_value for row in quantiles],
            "increment_counts": [row.innovation_pool_size for row in quantiles],
        },
    )
    await session.execute(
        text(
            """
            UPDATE agri.forecast_iteration
            SET receipt_checksum = agri.forecast_iteration_receipt_checksum(:iteration_id),
                recorded_at = clock_timestamp(),
                status = 'finalized'
            WHERE id = :iteration_id
            """
        ),
        {"iteration_id": iteration_id},
    )
    return iteration_id


def iteration_key_for(*, series_key: str, cutoff_day: date, request: SimulationRequest) -> str:
    """Return the deterministic idempotency key of one simulated iteration."""
    return (
        f"{METHOD_NAME}:{series_key}:{cutoff_day.isoformat()}"
        f":h{request.horizon_days}:n{request.simulation_count}:s{request.seed}"
    )


async def simulate_cells(  # noqa: PLR0913
    session: AsyncSession,
    *,
    plane: GovernedPlane,
    identities: tuple[SeriesIdentity, ...],
    governed_history: dict[uuid.UUID, tuple[ObservedDay, ...]],
    license_snapshots: dict[uuid.UUID, str],
    purpose: str,
    as_of_time: datetime,
    cutoff_day: date,
    request: SimulationRequest,
) -> tuple[tuple[IterationOutcome, ...], dict[uuid.UUID, SeasonalHistory]]:
    """Write one Monte Carlo iteration per eligible cell and report every refusal by reason."""
    outcomes: list[IterationOutcome] = []
    histories: dict[uuid.UUID, SeasonalHistory] = {}
    as_of_text = as_of_time.astimezone(UTC).isoformat()
    for identity in identities:
        observations = governed_history.get(identity.series_id, ())
        iteration_key = iteration_key_for(series_key=identity.series_key, cutoff_day=cutoff_day, request=request)
        try:
            history = build_seasonal_history(observations, cutoff_day)
            governed_history_checksum = history_checksum(observations, cutoff_day)
            checksum = parameter_checksum(
                canonical_parameter_text(
                    series_key=identity.series_key,
                    release_set_id=str(plane.release_set_id),
                    input_release_checksum=plane.release_manifest_checksum,
                    contract_checksum=identity.contract_checksum,
                    governed_history_checksum=governed_history_checksum,
                    as_of_text=as_of_text,
                    history=history,
                    request=request,
                )
            )
            quantiles = simulate_horizon_quantiles(history=history, request=request, checksum=checksum)
        except InsufficientNdviHistoryError as refusal:
            outcomes.append(
                IterationOutcome(
                    series_key=identity.series_key,
                    iteration_id=None,
                    iteration_key=iteration_key,
                    value_count=0,
                    training_day_count=len(observations),
                    skipped_reason_code=refusal.reason_code,
                )
            )
            continue
        series_license_snapshots = license_snapshots.get(identity.series_id)
        if series_license_snapshots is None:
            raise ValueError(f"governed NDVI series {identity.series_key} has no license snapshots")
        try:
            iteration_id = await _write_iteration(
                session,
                identity=identity,
                plane=plane,
                purpose=purpose,
                as_of_time=as_of_time,
                history=history,
                request=request,
                quantiles=quantiles,
                governed_history_checksum=governed_history_checksum,
                checksum=checksum,
                license_snapshots=series_license_snapshots,
                iteration_key=iteration_key,
            )
        except IterationEvidenceConflictError:
            # ON CONFLICT DO NOTHING never aborts the transaction, so the batch keeps going.
            outcomes.append(
                IterationOutcome(
                    series_key=identity.series_key,
                    iteration_id=None,
                    iteration_key=iteration_key,
                    value_count=0,
                    training_day_count=history.training_day_count,
                    skipped_reason_code="iteration_key_recorded_with_different_evidence",
                )
            )
            continue
        histories[identity.series_id] = history
        outcomes.append(
            IterationOutcome(
                series_key=identity.series_key,
                iteration_id=iteration_id,
                iteration_key=iteration_key,
                value_count=len(quantiles),
                training_day_count=history.training_day_count,
                skipped_reason_code=None,
            )
        )
    return tuple(outcomes), histories


async def reconcile_actuals(
    session: AsyncSession,
    *,
    iteration_ids: tuple[uuid.UUID, ...],
    release_set_id: uuid.UUID,
    as_of_time: datetime,
) -> int:
    """Append governed actuals to finalized iterations through the shipped reconciliation procedure."""
    inserted = 0
    for iteration_id in iteration_ids:
        result = await session.execute(
            text(
                """
                CALL agri.reconcile_forecast_iteration_actuals(
                    CAST(NULL AS integer),
                    CAST(:iteration_id AS uuid),
                    CAST(:release_set_id AS uuid),
                    CAST(:as_of_time AS timestamptz)
                )
                """
            ),
            {"iteration_id": iteration_id, "release_set_id": release_set_id, "as_of_time": as_of_time},
        )
        inserted += int(result.scalar_one() or 0)
    return inserted


async def load_outcome_rows(session: AsyncSession, *, iteration_ids: tuple[uuid.UUID, ...]) -> tuple[OutcomeRow, ...]:
    """Return reconciled forecast-versus-actual rows from the shipped iteration outcome view."""
    result = await session.execute(
        text(
            """
            SELECT
                outcome.series_id,
                outcome.cutoff_time,
                outcome.horizon_step,
                outcome.valid_time,
                outcome.low_value,
                outcome.median_value,
                outcome.high_value,
                outcome.actual_value,
                outcome.interval_covered
            FROM agri.v_forecast_iteration_outcome AS outcome
            WHERE outcome.iteration_id = ANY(CAST(:iteration_ids AS uuid[]))
              AND outcome.actual_value IS NOT NULL
            ORDER BY outcome.series_id, outcome.cutoff_time, outcome.valid_time
            """
        ),
        {"iteration_ids": [str(value) for value in iteration_ids]},
    )
    rows: list[OutcomeRow] = []
    for row in result.mappings().all():
        cutoff_time: datetime = row["cutoff_time"]
        valid_time: datetime = row["valid_time"]
        rows.append(
            OutcomeRow(
                series_id=uuid.UUID(str(row["series_id"])),
                cutoff_day=cutoff_time.astimezone(UTC).date(),
                horizon_step=int(row["horizon_step"]),
                valid_day=valid_time.astimezone(UTC).date(),
                low_value=float(row["low_value"]),
                median_value=float(row["median_value"]),
                high_value=float(row["high_value"]),
                actual_value=float(row["actual_value"]),
                interval_covered=bool(row["interval_covered"]),
            )
        )
    return tuple(rows)


def _error_metrics(label: str, predictions: list[float], actuals: list[float]) -> ErrorMetrics:
    paired = [
        (prediction, actual)
        for prediction, actual in zip(predictions, actuals, strict=True)
        if math.isfinite(prediction) and math.isfinite(actual)
    ]
    if not paired:
        return ErrorMetrics(
            label=label,
            point_count=0,
            mean_absolute_error=math.nan,
            root_mean_squared_error=math.nan,
            bias=math.nan,
        )
    errors = [prediction - actual for prediction, actual in paired]
    squared = sum(error * error for error in errors) / len(errors)
    return ErrorMetrics(
        label=label,
        point_count=len(errors),
        mean_absolute_error=sum(abs(error) for error in errors) / len(errors),
        root_mean_squared_error=math.sqrt(squared),
        bias=sum(errors) / len(errors),
    )


def summarize_holdout(
    *,
    cutoff_days: tuple[date, ...],
    iteration_count: int,
    outcome_rows: tuple[OutcomeRow, ...],
    histories_by_cutoff: dict[date, dict[uuid.UUID, SeasonalHistory]],
    horizon_buckets: tuple[tuple[str, int, int], ...],
) -> HoldoutEvaluation:
    """Summarize method and baseline error over the identical reconciled evaluation set."""
    method_predictions: list[float] = []
    persistence_predictions: list[float] = []
    climatology_predictions: list[float] = []
    actual_values: list[float] = []
    covered = 0
    bucket_rows: dict[str, tuple[list[float], list[float]]] = {name: ([], []) for name, _, _ in horizon_buckets}
    for row in outcome_rows:
        history = histories_by_cutoff.get(row.cutoff_day, {}).get(row.series_id)
        if history is None:
            continue
        method_predictions.append(row.median_value)
        persistence_predictions.append(persistence_baseline(history))
        climatology_predictions.append(climatology_baseline(history, row.valid_day))
        actual_values.append(row.actual_value)
        covered += int(row.interval_covered)
        for name, lower, upper in horizon_buckets:
            if lower <= row.horizon_step <= upper:
                bucket_rows[name][0].append(row.median_value)
                bucket_rows[name][1].append(row.actual_value)
    point_count = len(actual_values)
    return HoldoutEvaluation(
        cutoff_days=cutoff_days,
        iteration_count=iteration_count,
        reconciled_actual_count=point_count,
        interval_coverage_fraction=(covered / point_count if point_count else math.nan),
        method_metrics=_error_metrics(METHOD_NAME, method_predictions, actual_values),
        persistence_metrics=_error_metrics("persistence_last_observed", persistence_predictions, actual_values),
        climatology_metrics=_error_metrics("seasonal_naive_climatology", climatology_predictions, actual_values),
        metrics_by_horizon_bucket=tuple(
            (name, _error_metrics(name, bucket_rows[name][0], bucket_rows[name][1])) for name, _, _ in horizon_buckets
        ),
    )
