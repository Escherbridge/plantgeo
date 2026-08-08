"""Evaluation-only training/backtest receipts for the covariate wind ridge.

This is the first production writer of `agri.forecast_training_run` and
`agri.forecast_backtest_metric`. What it writes, what it deliberately does not
write, and why the forecast run stays `staged` forever live in `AGENTS.md`
(this directory) under `covariate_wind_model.py`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution.covariate_wind_model import (
    DEFAULT_CALIBRATION_DAYS,
    DEFAULT_HORIZON_COUNT,
    DEFAULT_ORIGIN_COUNT,
    DEFAULT_RIDGE_ALPHA,
    HORIZON_ORIGIN_OFFSET,
    LOWER_QUANTILE,
    SCHEMA_VERSION,
    TARGET_SIGNAL_NAME,
    UPPER_QUANTILE,
    FeatureCoverage,
    RollingOriginBacktest,
    canonical_digest,
    canonical_json,
    feature_code_checksum,
    leading_coefficients,
    load_baseline_evaluation,
    load_covariate_matrix,
    load_target_series,
    model_document,
    origin_split,
    rolling_origin_dates,
    run_out_of_fit_point_scores,
    run_rolling_origin_backtest,
    training_code_checksum,
)
from agri_data_service.jobs.lease import apply_statement_timeout

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import TextClause

    from agri_data_service.execution.covariate_wind_model import OriginBacktest

EVALUATION_LABEL: Final = "evaluation_only"
EVALUATION_DISCLAIMER: Final = (
    "Pipeline/evaluation evidence for a single cell and metric. Not an operational forecast, "
    "not life-safety validated, never joined to a serving or publication surface."
)

# The one honest name for what the feature plane's availability gate actually is: ONE global
# knowledge cutoff for the whole history, not a per-observation-date one. Recorded in every
# receipt so a reader never has to infer it. See execution/AGENTS.md "as_of_mode".
AS_OF_MODE: Final = "global"

# Carried in the receipt itself rather than only in AGENTS.md: a number read out of a JSONB
# column travels without the document that qualified it.
EVALUATION_CAVEATS: Final[tuple[str, ...]] = (
    "Scores prove the framework runs end to end; they are not an operational or life-safety forecast.",
    "Interval coverage is an empirical residual-band hit rate, not a calibrated confidence bound.",
    "Horizons from one origin are consecutive days of an autocorrelated variable, so the effective "
    "sample size is far below the reported pair count; read origin_count, not evaluated_count.",
    "The target is NASA POWER WS2M, a reanalysis product, and five of the forty features are its own "
    "lags, so this is an autoregressive baseline over a modelled target, not a station forecast.",
    "as_of_mode is 'global': one knowledge cutoff gates the whole history, so a revision published "
    "after an old observation day is still admitted for that day.",
    "One spatial cell and one metric. Nothing here generalizes to another cell without re-running it.",
)

TRAINING_DEFINITION_NAME: Final = "agri.forecast.covariate_wind_train"
TRAINING_DEFINITION_VERSION: Final = "2026-08-08"
TRAINING_HANDLER_TOKEN: Final = "execution.covariate_wind_train"
TRAINING_QUEUE_NAME: Final = "forecast"
TRAINING_MAX_ATTEMPTS: Final = 3
TRAINING_LEASE_SECONDS: Final = 1_800
TRAINING_TIME_BUDGET_SECONDS: Final = 1_500

MODEL_KEY: Final = "agri.covariate_wind_ridge"
MODEL_ALGORITHM: Final = "standardized_closed_form_ridge_direct_multi_horizon"
FEATURE_RECIPE_VERSION: Final = SCHEMA_VERSION
ARTIFACT_URI_PREFIX: Final = "agri-eval://covariate-wind-model"

# `forecast_model.model_version` is VARCHAR(100) and the identity keys are VARCHAR(255). An
# over-long value aborts the INSERT rather than truncating, so identities are built from
# bounded parts and a digest slice rather than from free text.
DIGEST_KEY_LENGTH: Final = 16
IDENTITY_KEY_MAX_LENGTH: Final = 255
MODEL_VERSION_MAX_LENGTH: Final = 100

# A bounded, readable coefficient summary for the receipt JSONB; the full set is in the artifact.
REPORTED_COEFFICIENT_COUNT: Final = 10

_SELECT_RELEASE_SET = text(load_query_sql("execution/select_validated_release_set.sql"))
_INSERT_JOB_DEFINITION = text(load_query_sql("execution/insert_training_job_definition.sql"))
_INSERT_JOB_RUN = text(load_query_sql("execution/insert_training_job_run.sql"))
_INSERT_MODEL_ARTIFACT = text(load_query_sql("execution/insert_model_artifact.sql"))
_INSERT_JOB_OUTPUT = text(load_query_sql("execution/insert_job_output.sql"))
_INSERT_FEATURE_SNAPSHOT = text(load_query_sql("execution/insert_forecast_feature_snapshot.sql"))
_INSERT_FORECAST_MODEL = text(load_query_sql("execution/insert_forecast_model.sql"))
_INSERT_TRAINING_RUN = text(load_query_sql("execution/insert_forecast_training_run.sql"))
_INSERT_FORECAST_RUN = text(load_query_sql("execution/insert_forecast_run.sql"))
_INSERT_BACKTEST_METRIC = text(load_query_sql("execution/insert_forecast_backtest_metric.sql"))
_VALIDATE_TRAINING_RUN = text(load_query_sql("execution/validate_training_run.sql"))

_SELECT_QUALITY_POLICY = text(
    "SELECT id FROM agri.forecast_quality_policy WHERE policy_key = :policy_key AND is_active"
)
_VALIDATE_FEATURE_SNAPSHOT = text(
    "SELECT (agri.validate_forecast_feature_snapshot(CAST(:snapshot_id AS uuid))).status AS status"
)
_LOOKUP_JOB_DEFINITION = text("SELECT id FROM agri.job_definition WHERE name = :name AND version = :version")
_LOOKUP_JOB_RUN = text("SELECT id FROM agri.job_run WHERE logical_run_key = :logical_run_key")
_LOOKUP_ARTIFACT = text("SELECT id FROM agri.artifact WHERE uri = :uri AND checksum_sha256 = :checksum_sha256")
_LOOKUP_JOB_OUTPUT = text("SELECT id FROM agri.job_output WHERE job_run_id = :job_run_id AND output_key = :output_key")
_LOOKUP_FEATURE_SNAPSHOT = text("SELECT id FROM agri.forecast_feature_snapshot WHERE snapshot_key = :snapshot_key")
_LOOKUP_TRAINING_RUN = text("SELECT id FROM agri.forecast_training_run WHERE training_key = :training_key")
_LOOKUP_FORECAST_RUN = text("SELECT id FROM agri.forecast_run WHERE run_key = :run_key")
_LOOKUP_FORECAST_MODEL = text(
    "SELECT id FROM agri.forecast_model WHERE model_key = :model_key AND model_version = :model_version"
)


class ForecastTrainingPersistError(RuntimeError):
    """Raised when a governed prerequisite for a training receipt is missing or unresolvable."""


def _utc_midnight(day: date) -> datetime:
    """Read a calendar day as the UTC instant it starts at."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class WindTrainingRequest:
    """Every knob one covariate wind training invocation is pinned by."""

    cell_id: str
    series_id: str
    history_start: date
    history_end: date
    origin_date: date
    as_of_time: datetime
    horizon_count: int = DEFAULT_HORIZON_COUNT
    calibration_days: int = DEFAULT_CALIBRATION_DAYS
    origin_count: int = DEFAULT_ORIGIN_COUNT
    origin_stride_days: int | None = None
    alpha: float = DEFAULT_RIDGE_ALPHA
    schema_version: str = SCHEMA_VERSION
    quality_policy_key: str | None = None
    requested_by: str = "agri-cli forecast-train-wind"

    def __post_init__(self) -> None:
        """Refuse a request whose windows cannot produce a scoreable origin, before any query runs."""
        if self.history_start >= self.history_end:
            raise ValueError("history_start must precede history_end")
        if self.horizon_count < 1:
            raise ValueError("horizon_count must be at least one horizon")
        if self.calibration_days < 1:
            raise ValueError("calibration_days must be at least one day")
        if self.origin_count < 1:
            raise ValueError("origin_count must be at least one origin")
        if self.origin_stride_days is not None and self.origin_stride_days < 1:
            raise ValueError("origin_stride_days must be at least one day")
        if self.as_of_time.utcoffset() is None:
            raise ValueError("as_of_time must include a timezone")

    @property
    def effective_stride_days(self) -> int:
        """Days between rolling origins; defaults to the horizon count so target spans do not overlap.

        Overlapping origins would pool the same calendar days twice and make an already
        autocorrelated sample look larger than it is. Non-overlapping is the honest default.
        """
        return self.origin_stride_days if self.origin_stride_days is not None else self.horizon_count

    def origin_dates(self) -> tuple[date, ...]:
        """The ascending rolling origins this request scores, ending at `origin_date`."""
        return rolling_origin_dates(
            self.origin_date,
            origin_count=self.origin_count,
            origin_stride_days=self.effective_stride_days,
        )

    def parameter_payload(self) -> dict[str, object]:
        """The canonical parameter set the run's parameter checksum and identity keys derive from."""
        return {
            "digest_version": "agri_covariate_wind_parameters_v1",
            "schema_version": self.schema_version,
            "target_signal_name": TARGET_SIGNAL_NAME,
            "cell_id": self.cell_id,
            "series_id": self.series_id,
            "history_start": self.history_start.isoformat(),
            "history_end": self.history_end.isoformat(),
            "as_of_time": self.as_of_time.astimezone(UTC).isoformat(),
            "as_of_mode": AS_OF_MODE,
            "origin_dates": [day.isoformat() for day in self.origin_dates()],
            "origin_stride_days": self.effective_stride_days,
            "horizon_count": self.horizon_count,
            "horizon_origin_offset": HORIZON_ORIGIN_OFFSET,
            "calibration_days": self.calibration_days,
            "ridge_alpha": self.alpha,
            "band_quantiles": [LOWER_QUANTILE, UPPER_QUANTILE],
        }

    @property
    def parameter_checksum(self) -> str:
        """Digest over `parameter_payload`: two runs share it only if every pinned knob agrees."""
        return canonical_digest(self.parameter_payload())

    @property
    def training_key(self) -> str:
        """Stable per-invocation identity, so a re-claimed durable shard resolves rather than duplicates."""
        digest = self.parameter_checksum[:DIGEST_KEY_LENGTH]
        identity = f"covariate-wind-ridge:{self.cell_id}:{self.origin_date.isoformat()}:{digest}"
        return identity[:IDENTITY_KEY_MAX_LENGTH]


@dataclass(frozen=True, slots=True)
class TrainingReceipt:
    """The rows one persisted invocation wrote, so a caller can point at them without re-querying."""

    training_key: str
    training_run_id: uuid.UUID
    training_run_status: str
    forecast_run_id: uuid.UUID
    feature_snapshot_id: uuid.UUID
    feature_snapshot_status: str
    model_id: uuid.UUID
    model_artifact_id: uuid.UUID
    job_run_id: uuid.UUID
    release_set_id: uuid.UUID
    release_set_key: str
    quality_policy_id: uuid.UUID
    model_checksum: str
    validation_checksum: str
    feature_checksum: str
    input_release_checksum: str
    parameter_checksum: str
    training_code_checksum: str
    backtest_metric_count: int

    def to_summary(self) -> dict[str, object]:
        """Render the receipt for the verb's JSON line."""
        return {
            "training_key": self.training_key,
            "training_run_id": str(self.training_run_id),
            "training_run_status": self.training_run_status,
            "forecast_run_id": str(self.forecast_run_id),
            # Stated rather than read back: this lane never calls agri.validate_forecast_run, so
            # the row it wrote is still in the state its insert trigger required.
            "forecast_run_status": "staged",
            "feature_snapshot_id": str(self.feature_snapshot_id),
            "feature_snapshot_status": self.feature_snapshot_status,
            "model_id": str(self.model_id),
            "model_artifact_id": str(self.model_artifact_id),
            "job_run_id": str(self.job_run_id),
            "release_set_id": str(self.release_set_id),
            "release_set_key": self.release_set_key,
            "quality_policy_id": str(self.quality_policy_id),
            "model_checksum": self.model_checksum,
            "validation_checksum": self.validation_checksum,
            "feature_checksum": self.feature_checksum,
            "input_release_checksum": self.input_release_checksum,
            "parameter_checksum": self.parameter_checksum,
            "training_code_checksum": self.training_code_checksum,
            "backtest_metric_count": self.backtest_metric_count,
        }


@dataclass(frozen=True, slots=True)
class WindTrainingReport:
    """One invocation's evidence: what it read, what it scored, and what (if anything) it wrote."""

    request: WindTrainingRequest
    coverage: FeatureCoverage
    backtest: RollingOriginBacktest
    manifest: Mapping[str, object]
    declared_gaps: object
    target_day_count: int
    feature_names: tuple[str, ...]
    out_of_fit: Mapping[str, object]
    baseline_iterations: Sequence[Mapping[str, object]]
    leading_coefficients: Sequence[Mapping[str, object]]
    receipt: TrainingReceipt | None = None
    persisted: bool = False

    def validation_metrics(self) -> dict[str, object]:
        """The receipt's JSONB body: scores, sample sizes, coverage accounting and the honest caveats.

        Deterministic given a pinned as-of instant, because
        `agri.validate_forecast_training_run` raises if a re-validation supplies metrics that
        differ from the ones already recorded.
        """
        return {
            "label": EVALUATION_LABEL,
            "publication_authorized": False,
            "disclaimer": EVALUATION_DISCLAIMER,
            "as_of_mode": AS_OF_MODE,
            "as_of_time": self.request.as_of_time.astimezone(UTC).isoformat(),
            "schema_version": self.request.schema_version,
            "cell_id": self.request.cell_id,
            "series_id": self.request.series_id,
            "target_signal_name": TARGET_SIGNAL_NAME,
            "parameters": self.request.parameter_payload(),
            "covariate_manifest": dict(self.manifest),
            "declared_gaps": self.declared_gaps,
            "feature_coverage": self.coverage.to_summary(),
            "target_day_count": self.target_day_count,
            "rolling_origin_backtest": self.backtest.to_summary(),
            "out_of_fit_point_only": dict(self.out_of_fit),
            "leading_standardized_coefficients": [dict(entry) for entry in self.leading_coefficients],
            "caveats": list(EVALUATION_CAVEATS),
        }

    def to_summary(self) -> dict[str, object]:
        """The verb's one JSON line: the metrics, the accounting, and the receipt when persisted."""
        return {
            "label": EVALUATION_LABEL,
            "disclaimer": EVALUATION_DISCLAIMER,
            "persisted": self.persisted,
            "as_of_mode": AS_OF_MODE,
            "as_of_time": self.request.as_of_time.astimezone(UTC).isoformat(),
            "schema_version": self.request.schema_version,
            "cell_id": self.request.cell_id,
            "series_id": self.request.series_id,
            "covariate_manifest": dict(self.manifest),
            "declared_gaps": self.declared_gaps,
            "feature_coverage": self.coverage.to_summary(),
            "target_day_count": self.target_day_count,
            "model": self.backtest.to_summary(),
            "model_out_of_fit_point_only": dict(self.out_of_fit),
            "leading_standardized_coefficients": [dict(entry) for entry in self.leading_coefficients],
            "baseline_iterations": [dict(row) for row in self.baseline_iterations],
            "caveats": list(EVALUATION_CAVEATS),
            "receipt": None if self.receipt is None else self.receipt.to_summary(),
        }


async def _scalar_uuid(
    session: AsyncSession,
    statement: TextClause,
    parameters: Mapping[str, object],
) -> uuid.UUID | None:
    """Run a statement expected to yield at most one id, returning it as a UUID."""
    value = (await session.execute(statement, dict(parameters))).scalar_one_or_none()
    return None if value is None else uuid.UUID(str(value))


async def _insert_or_resolve(  # noqa: PLR0913 - one parameter per statement/parameter pair, plus the label
    session: AsyncSession,
    *,
    insert_statement: TextClause,
    insert_parameters: Mapping[str, object],
    lookup_statement: TextClause,
    lookup_parameters: Mapping[str, object],
    description: str,
) -> uuid.UUID:
    """Insert a row keyed by a derived identity, or resolve the one a previous attempt already wrote.

    Every insert in this lane is `ON CONFLICT ... DO NOTHING RETURNING id`, which yields no row
    on the conflict path. That is what makes a re-claimed durable shard idempotent instead of a
    unique-violation dead letter.
    """
    inserted = await _scalar_uuid(session, insert_statement, insert_parameters)
    if inserted is not None:
        return inserted
    existing = await _scalar_uuid(session, lookup_statement, lookup_parameters)
    if existing is None:
        raise ForecastTrainingPersistError(f"{description} was neither inserted nor found")
    return existing


@dataclass(frozen=True, slots=True)
class _ReleaseBinding:
    """The governed release set a receipt pins its inputs to."""

    release_set_id: uuid.UUID
    logical_key: str
    manifest_checksum: str


async def _resolve_release_set(session: AsyncSession, *, as_of_time: datetime) -> _ReleaseBinding:
    """Bind the newest validated release set that already existed at the run's as-of instant."""
    row = (await session.execute(_SELECT_RELEASE_SET, {"as_of_time": as_of_time})).mappings().first()
    if row is None:
        raise ForecastTrainingPersistError(
            "no validated release set exists at or before the as-of instant; a training receipt "
            "cannot pin its inputs, and this lane will not mint a release set of its own"
        )
    return _ReleaseBinding(
        release_set_id=uuid.UUID(str(row["id"])),
        logical_key=str(row["logical_key"]),
        manifest_checksum=str(row["manifest_checksum"]),
    )


async def _resolve_quality_policy(session: AsyncSession, *, policy_key: str) -> uuid.UUID:
    """Resolve the reviewed quality policy a forecast run must reference; never mint one."""
    policy_id = await _scalar_uuid(session, _SELECT_QUALITY_POLICY, {"policy_key": policy_key})
    if policy_id is None:
        raise ForecastTrainingPersistError(
            f"no active agri.forecast_quality_policy with policy_key {policy_key!r}. A quality policy "
            "encodes the thresholds a forecast must clear, so this lane will not invent one; seed it "
            "through the reviewed path first"
        )
    return policy_id


def _origin_metric_payload(origin: OriginBacktest, *, parameter_checksum: str) -> dict[str, object]:
    """The canonical body one backtest metric row's checksum is derived from."""
    return {
        "digest_version": "agri_covariate_wind_backtest_metric_v1",
        "parameter_checksum": parameter_checksum,
        "cutoff_time": _utc_midnight(origin.origin_date).isoformat(),
        "training_point_count": origin.training_point_count,
        "backtest_point_count": origin.scores.evaluated_count,
        "mean_absolute_error": origin.scores.mean_absolute_error,
        "root_mean_squared_error": origin.scores.root_mean_squared_error,
        "naive_root_mean_squared_error": origin.naive_root_mean_squared_error,
        "skill_score": origin.skill_score,
        "bias": origin.scores.bias,
        "mean_absolute_percentage_error": origin.scores.mean_absolute_percentage_error,
        "interval_coverage": origin.scores.interval_coverage,
    }


async def evaluate_covariate_wind(session: AsyncSession, request: WindTrainingRequest) -> WindTrainingReport:
    """Read the governed inputs, run the rolling-origin backtest, and report without writing anything."""
    matrix = await load_covariate_matrix(
        session,
        cell_id=request.cell_id,
        window_start=request.history_start,
        window_end=request.history_end,
        as_of_time=request.as_of_time,
        schema_version=request.schema_version,
    )
    targets = await load_target_series(session, cell_id=request.cell_id, as_of_time=request.as_of_time)
    baseline = await load_baseline_evaluation(session, series_id=request.series_id, as_of_time=request.as_of_time)

    backtest = run_rolling_origin_backtest(
        matrix,
        targets,
        origin_dates=request.origin_dates(),
        calibration_days=request.calibration_days,
        horizon_count=request.horizon_count,
        alpha=request.alpha,
    )
    newest_fit_last, newest_calibration_last = origin_split(
        backtest.newest_origin, calibration_days=request.calibration_days
    )
    return WindTrainingReport(
        request=request,
        coverage=matrix.coverage(targets),
        backtest=backtest,
        manifest=matrix.reported_manifest(),
        declared_gaps=matrix.manifest.get("declared_gaps"),
        target_day_count=len(targets),
        feature_names=matrix.feature_names,
        out_of_fit=run_out_of_fit_point_scores(
            matrix,
            targets,
            fit_target_last=newest_fit_last,
            evaluation_target_last=newest_calibration_last,
            horizon_count=request.horizon_count,
            alpha=request.alpha,
        ),
        baseline_iterations=baseline,
        leading_coefficients=leading_coefficients(
            backtest, feature_names=matrix.feature_names, limit=REPORTED_COEFFICIENT_COUNT
        ),
    )


async def persist_training_receipt(
    session: AsyncSession,
    report: WindTrainingReport,
    *,
    started_at: datetime,
    completed_at: datetime,
) -> TrainingReceipt:
    """Write the whole training/backtest receipt chain for one invocation, in one transaction.

    Order is load-bearing: every row a validator inspects must already exist, and already be in
    the state that validator demands, before the validator is called.
    """
    request = report.request
    if request.quality_policy_key is None:
        raise ForecastTrainingPersistError("persisting a training receipt requires a quality policy key")
    if report.coverage.usable_day_count <= 0:
        raise ForecastTrainingPersistError(
            "no candidate day was both feature-complete and target-bearing, so there is no training "
            "set to record; read feature_coverage.blocking_features for which feature excluded them"
        )

    release = await _resolve_release_set(session, as_of_time=request.as_of_time)
    quality_policy_id = await _resolve_quality_policy(session, policy_key=request.quality_policy_key)

    feature_checksum = str(report.manifest.get("manifest_checksum") or "")
    if not feature_checksum:
        raise ForecastTrainingPersistError(
            "agri.covariate_vector_manifest returned no manifest checksum; the feature lineage "
            "cannot be bound and the receipt would claim provenance it does not have"
        )

    document = model_document(report.backtest, feature_names=report.feature_names, alpha=request.alpha)
    model_text = canonical_json(document)
    model_checksum = canonical_digest(document)
    validation_metrics = report.validation_metrics()
    validation_checksum = canonical_digest(validation_metrics)
    code_checksum = training_code_checksum()
    training_key = request.training_key
    digest_key = request.parameter_checksum[:DIGEST_KEY_LENGTH]

    definition_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_JOB_DEFINITION,
        insert_parameters={
            "name": TRAINING_DEFINITION_NAME,
            "version": TRAINING_DEFINITION_VERSION,
            "handler": TRAINING_HANDLER_TOKEN,
            "queue_name": TRAINING_QUEUE_NAME,
            "max_attempts": TRAINING_MAX_ATTEMPTS,
            "lease_seconds": TRAINING_LEASE_SECONDS,
            "time_budget_seconds": TRAINING_TIME_BUDGET_SECONDS,
            "parameters": canonical_json(
                {
                    "lane": "covariate_wind_train",
                    "label": EVALUATION_LABEL,
                    "publication_authorized": False,
                    "schema_version": request.schema_version,
                    "target_signal_name": TARGET_SIGNAL_NAME,
                }
            ),
        },
        lookup_statement=_LOOKUP_JOB_DEFINITION,
        lookup_parameters={"name": TRAINING_DEFINITION_NAME, "version": TRAINING_DEFINITION_VERSION},
        description="covariate wind training job definition",
    )

    logical_run_key = f"covariate-wind-training:{training_key}"[:IDENTITY_KEY_MAX_LENGTH]
    job_run_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_JOB_RUN,
        insert_parameters={
            "job_definition_id": definition_id,
            "release_set_id": release.release_set_id,
            "logical_run_key": logical_run_key,
            "scheduled_for": started_at,
            "started_at": started_at,
            "completed_at": completed_at,
            "requested_by": request.requested_by,
        },
        lookup_statement=_LOOKUP_JOB_RUN,
        lookup_parameters={"logical_run_key": logical_run_key},
        description="covariate wind training job run",
    )

    artifact_uri = f"{ARTIFACT_URI_PREFIX}/{request.cell_id}/{model_checksum}"
    artifact_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_MODEL_ARTIFACT,
        insert_parameters={
            "uri": artifact_uri,
            "checksum_sha256": model_checksum,
            "metadata_json": canonical_json(
                {
                    "label": EVALUATION_LABEL,
                    "schema_version": request.schema_version,
                    "parameter_checksum": request.parameter_checksum,
                    "training_code_checksum": code_checksum,
                }
            ),
            "model_document": model_text,
        },
        lookup_statement=_LOOKUP_ARTIFACT,
        lookup_parameters={"uri": artifact_uri, "checksum_sha256": model_checksum},
        description="covariate wind model artifact",
    )

    model_version = f"{request.schema_version}+{model_checksum[:DIGEST_KEY_LENGTH]}"[:MODEL_VERSION_MAX_LENGTH]
    model_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_FORECAST_MODEL,
        insert_parameters={
            "model_key": MODEL_KEY,
            "model_version": model_version,
            "algorithm": MODEL_ALGORITHM,
            "model_code_checksum": code_checksum,
            "artifact_id": artifact_id,
            "metadata_json": canonical_json(
                {
                    "label": EVALUATION_LABEL,
                    "publication_authorized": False,
                    "ridge_alpha": request.alpha,
                    "horizon_count": request.horizon_count,
                    "horizon_origin_offset": HORIZON_ORIGIN_OFFSET,
                    "calibration_days": request.calibration_days,
                    "band_quantiles": [LOWER_QUANTILE, UPPER_QUANTILE],
                    "schema_version": request.schema_version,
                    "target_signal_name": TARGET_SIGNAL_NAME,
                    "as_of_mode": AS_OF_MODE,
                }
            ),
        },
        lookup_statement=_LOOKUP_FORECAST_MODEL,
        lookup_parameters={"model_key": MODEL_KEY, "model_version": model_version},
        description="covariate wind forecast model",
    )

    model_output_key = f"covariate-wind-model:{digest_key}:{model_checksum[:DIGEST_KEY_LENGTH]}"
    model_output_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_JOB_OUTPUT,
        insert_parameters={
            "job_run_id": job_run_id,
            "artifact_id": artifact_id,
            "output_key": model_output_key,
            "kind": "model_training",
            "checksum_sha256": model_checksum,
            "row_count": 1,
            "metadata_json": canonical_json({"validation_checksum": validation_checksum}),
            "validated_at": completed_at,
        },
        lookup_statement=_LOOKUP_JOB_OUTPUT,
        lookup_parameters={"job_run_id": job_run_id, "output_key": model_output_key},
        description="covariate wind model training output",
    )

    snapshot_key = f"covariate-wind-features:{training_key}"[:IDENTITY_KEY_MAX_LENGTH]
    snapshot_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_FEATURE_SNAPSHOT,
        insert_parameters={
            "snapshot_key": snapshot_key,
            "job_run_id": job_run_id,
            "release_set_id": release.release_set_id,
            "input_release_checksum": release.manifest_checksum,
            "feature_recipe_version": FEATURE_RECIPE_VERSION,
            "feature_code_checksum": feature_code_checksum(report.feature_names, schema_version=request.schema_version),
            "feature_checksum": feature_checksum,
            "training_window_start": _utc_midnight(request.history_start),
            "training_window_end": _utc_midnight(request.history_end),
            "row_count": report.coverage.usable_day_count,
        },
        lookup_statement=_LOOKUP_FEATURE_SNAPSHOT,
        lookup_parameters={"snapshot_key": snapshot_key},
        description="covariate wind feature snapshot",
    )
    snapshot_status = str(
        (await session.execute(_VALIDATE_FEATURE_SNAPSHOT, {"snapshot_id": snapshot_id})).scalar_one()
    )

    training_run_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_TRAINING_RUN,
        insert_parameters={
            "training_key": training_key,
            "model_id": model_id,
            "job_run_id": job_run_id,
            "job_output_id": model_output_id,
            "feature_snapshot_id": snapshot_id,
            "input_release_checksum": release.manifest_checksum,
            "feature_checksum": feature_checksum,
            "training_code_checksum": code_checksum,
            "started_at": started_at,
            "completed_at": completed_at,
        },
        lookup_statement=_LOOKUP_TRAINING_RUN,
        lookup_parameters={"training_key": training_key},
        description="covariate wind training run",
    )
    training_status = str(
        (
            await session.execute(
                _VALIDATE_TRAINING_RUN,
                {
                    "training_run_id": training_run_id,
                    "model_checksum": model_checksum,
                    "validation_checksum": validation_checksum,
                    "validation_metrics": canonical_json(validation_metrics),
                },
            )
        ).scalar_one()
    )

    backtest_output_key = f"covariate-wind-backtest:{digest_key}"
    backtest_output_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_JOB_OUTPUT,
        insert_parameters={
            "job_run_id": job_run_id,
            "artifact_id": None,
            "output_key": backtest_output_key,
            "kind": "forecast_backtest",
            "checksum_sha256": validation_checksum,
            "row_count": len(report.backtest.origins),
            "metadata_json": canonical_json(
                {"label": EVALUATION_LABEL, "parameter_checksum": request.parameter_checksum}
            ),
            "validated_at": completed_at,
        },
        lookup_statement=_LOOKUP_JOB_OUTPUT,
        lookup_parameters={"job_run_id": job_run_id, "output_key": backtest_output_key},
        description="covariate wind backtest output",
    )

    run_key = f"covariate-wind-backtest-run:{training_key}"[:IDENTITY_KEY_MAX_LENGTH]
    forecast_run_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_FORECAST_RUN,
        insert_parameters={
            "run_key": run_key,
            "job_run_id": job_run_id,
            "feature_snapshot_id": snapshot_id,
            "model_id": model_id,
            "training_run_id": training_run_id,
            "quality_policy_id": quality_policy_id,
            "issue_time": _utc_midnight(report.backtest.newest_origin),
            "valid_from": _utc_midnight(report.backtest.earliest_origin),
            "valid_to": _utc_midnight(report.backtest.newest_origin + timedelta(days=request.horizon_count)),
            "horizon_steps": request.horizon_count,
            "input_release_checksum": release.manifest_checksum,
            "feature_checksum": feature_checksum,
            "model_checksum": model_checksum,
            "parameter_checksum": request.parameter_checksum,
            "quality_summary": canonical_json(
                {
                    "label": EVALUATION_LABEL,
                    "publication_authorized": False,
                    "as_of_mode": AS_OF_MODE,
                    "aggregate": report.backtest.aggregate.to_summary(),
                    "per_horizon": report.backtest.per_horizon(),
                    "origin_count": len(report.backtest.origins),
                    "skipped_origins": [skip.to_summary() for skip in report.backtest.skipped],
                    "feature_coverage": report.coverage.to_summary(),
                }
            ),
        },
        lookup_statement=_LOOKUP_FORECAST_RUN,
        lookup_parameters={"run_key": run_key},
        description="covariate wind backtest forecast run",
    )

    for origin in report.backtest.origins:
        await session.execute(
            _INSERT_BACKTEST_METRIC,
            {
                "forecast_run_id": forecast_run_id,
                "job_output_id": backtest_output_id,
                "series_id": request.series_id,
                "cutoff_time": _utc_midnight(origin.origin_date),
                "training_point_count": origin.training_point_count,
                "backtest_point_count": origin.scores.evaluated_count,
                "mae": origin.scores.mean_absolute_error,
                "rmse": origin.scores.root_mean_squared_error,
                "naive_rmse": origin.naive_root_mean_squared_error,
                "skill_score": origin.skill_score,
                "bias": origin.scores.bias,
                "mape": origin.scores.mean_absolute_percentage_error,
                # A run whose band could not be scored reports zero coverage rather than NULL:
                # the column is NOT NULL, and zero is the true hit rate over an empty band.
                "coverage_fraction": origin.scores.interval_coverage or 0.0,
                "metrics_checksum": canonical_digest(
                    _origin_metric_payload(origin, parameter_checksum=request.parameter_checksum)
                ),
            },
        )

    return TrainingReceipt(
        training_key=training_key,
        training_run_id=training_run_id,
        training_run_status=training_status,
        forecast_run_id=forecast_run_id,
        feature_snapshot_id=snapshot_id,
        feature_snapshot_status=snapshot_status,
        model_id=model_id,
        model_artifact_id=artifact_id,
        job_run_id=job_run_id,
        release_set_id=release.release_set_id,
        release_set_key=release.logical_key,
        quality_policy_id=quality_policy_id,
        model_checksum=model_checksum,
        validation_checksum=validation_checksum,
        feature_checksum=feature_checksum,
        input_release_checksum=release.manifest_checksum,
        parameter_checksum=request.parameter_checksum,
        training_code_checksum=code_checksum,
        backtest_metric_count=len(report.backtest.origins),
    )


async def run_covariate_wind_training(
    session: AsyncSession,
    request: WindTrainingRequest,
    *,
    persist: bool = False,
) -> WindTrainingReport:
    """Evaluate, and when asked, write the receipt chain. The caller owns the commit and the rollback.

    Without `persist` this reads and scores only: no row anywhere changes, which is the
    module's long-standing default behaviour.
    """
    await apply_statement_timeout(session)
    started_at = datetime.now(tz=UTC)
    report = await evaluate_covariate_wind(session, request)
    if not persist:
        return report
    receipt = await persist_training_receipt(
        session,
        report,
        started_at=started_at,
        completed_at=datetime.now(tz=UTC),
    )
    return replace(report, receipt=receipt, persisted=True)
