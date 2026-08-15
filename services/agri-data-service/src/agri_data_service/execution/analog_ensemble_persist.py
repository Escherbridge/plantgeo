"""Evaluation-only training/backtest receipts for the Analog Ensemble (AnEn) k-NN forecaster.

Reuses the SAME wind-lane receipt-chain SQL (`sql/execution/insert_*.sql`,
`select_validated_release_set.sql`, `validate_training_run.sql`) `covariate_wind_persist.py`
established -- those statements are generic across every `model_kind = 'ml'` forecaster, not
wind-specific, so both lanes import them, and the four insert/lookup/resolve helpers that drive
them, from the one shared `forecast_receipt_writer.py` rather than each forking its own copy. See
`execution/AGENTS.md` under `covariate_wind_persist.py` for what the chain writes, what it
deliberately does not write (no `forecast_receipt`, no `forecast_value`, no publication --
the forecast run stays `staged` forever), and why that is the structural leakage guarantee
rather than a `WHERE` clause someone could forget.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.execution import forecast_receipt_writer as _receipt_writer
from agri_data_service.execution.analog_ensemble_model import (
    FULL_VECTOR_VARIANT,
    AnEnEvaluationReport,
    AnEnOriginRun,
    AnEnRollingBacktest,
    evaluate_analog_ensemble,
    model_document,
    training_code_checksum,
)
from agri_data_service.execution.covariate_wind_model import (
    SCHEMA_VERSION,
    TARGET_SIGNAL_NAME,
    canonical_digest,
    canonical_json,
    feature_code_checksum,
)
from agri_data_service.jobs.lease import apply_statement_timeout
from agri_data_service.method.ml.analog_ensemble import METHOD_NAME, AnEnHyperparameters

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import TextClause

EVALUATION_LABEL: Final = "evaluation_only"
EVALUATION_DISCLAIMER: Final = (
    "Pipeline/evaluation evidence for a single cell and metric. Not an operational forecast, "
    "not life-safety validated, never joined to a serving or publication surface."
)
AS_OF_MODE: Final = "global"
EVALUATION_CAVEATS: Final[tuple[str, ...]] = (
    "Scores prove the framework runs end to end; they are not an operational or life-safety forecast.",
    "Interval coverage is an empirical residual-band hit rate, not a calibrated confidence bound.",
    "Horizons from one origin are consecutive days of an autocorrelated variable, so the effective "
    "sample size is far below the reported pair count; read origin_count, not evaluated_count.",
    "Analog-space bias correction is wired but not exercised: no parallel NWP-forecast series exists "
    "in this warehouse to derive a per-analog bias term from, so mean_bias_correction is always 0.0.",
    "as_of_mode is 'global': one knowledge cutoff gates the whole history, so a revision published "
    "after an old observation day is still admitted for that old day.",
    "One spatial cell and one metric. Nothing here generalizes to another cell without re-running it.",
    "The persisted forecast run and its backtest metrics come from the full_vector variant only; the "
    "target_lags_only variant is an ablation comparison recorded in validation_metrics, not persisted "
    "as a second receipt.",
)

TRAINING_DEFINITION_NAME: Final = "agri.forecast.analog_ensemble_train"
TRAINING_DEFINITION_VERSION: Final = "2026-08-14"
TRAINING_HANDLER_TOKEN: Final = "execution.analog_ensemble_train"
TRAINING_QUEUE_NAME: Final = "forecast"
TRAINING_MAX_ATTEMPTS: Final = 3
TRAINING_LEASE_SECONDS: Final = 1_800
TRAINING_TIME_BUDGET_SECONDS: Final = 1_500

MODEL_KEY: Final = "agri.analog_ensemble"
MODEL_ALGORITHM: Final = METHOD_NAME
FEATURE_RECIPE_VERSION: Final = SCHEMA_VERSION
ARTIFACT_URI_PREFIX: Final = "agri-eval://analog-ensemble-model"

DIGEST_KEY_LENGTH: Final = 16
IDENTITY_KEY_MAX_LENGTH: Final = 255
MODEL_VERSION_MAX_LENGTH: Final = 100

# The nine governed statements below are the SAME `load_query_sql(...)` results
# `covariate_wind_persist.py` binds; both lanes import them from `forecast_receipt_writer.py`
# (the one call site per file) rather than each loading its own copy. Bound to this module's own
# private names so `persist_training_receipt` below is unchanged from before the extraction.
_INSERT_JOB_DEFINITION = _receipt_writer.INSERT_JOB_DEFINITION
_INSERT_JOB_RUN = _receipt_writer.INSERT_JOB_RUN
_INSERT_MODEL_ARTIFACT = _receipt_writer.INSERT_MODEL_ARTIFACT
_INSERT_JOB_OUTPUT = _receipt_writer.INSERT_JOB_OUTPUT
_INSERT_FEATURE_SNAPSHOT = _receipt_writer.INSERT_FEATURE_SNAPSHOT
_INSERT_FORECAST_MODEL = _receipt_writer.INSERT_FORECAST_MODEL
_INSERT_TRAINING_RUN = _receipt_writer.INSERT_TRAINING_RUN
_INSERT_FORECAST_RUN = _receipt_writer.INSERT_FORECAST_RUN
_INSERT_BACKTEST_METRIC = _receipt_writer.INSERT_BACKTEST_METRIC
_VALIDATE_TRAINING_RUN = _receipt_writer.VALIDATE_TRAINING_RUN

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


class AnEnTrainingPersistError(_receipt_writer.ForecastReceiptPersistError):
    """Raised when a governed prerequisite for an AnEn training receipt is missing or unresolvable."""


def _utc_midnight(day: date) -> datetime:
    """Read a calendar day as the UTC instant it starts at."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class AnEnTrainingRequest:
    """Every knob one Analog Ensemble training invocation is pinned by."""

    cell_id: str
    series_id: str
    history_start: date
    history_end: date
    origin_date: date
    as_of_time: datetime
    hyperparams: AnEnHyperparameters
    origin_count: int = 1
    origin_stride_days: int | None = None
    schema_version: str = SCHEMA_VERSION
    target_signal_name: str = TARGET_SIGNAL_NAME
    quality_policy_key: str | None = None
    requested_by: str = "agri-cli forecast-train-anen"

    def __post_init__(self) -> None:
        """Refuse a request whose windows cannot produce a scoreable origin, before any query runs."""
        if self.history_start >= self.history_end:
            raise ValueError("history_start must precede history_end")
        if self.origin_count < 1:
            raise ValueError("origin_count must be at least one origin")
        if self.origin_stride_days is not None and self.origin_stride_days < 1:
            raise ValueError("origin_stride_days must be at least one day")
        if self.as_of_time.utcoffset() is None:
            raise ValueError("as_of_time must include a timezone")

    @property
    def effective_stride_days(self) -> int:
        """Days between rolling origins; defaults to horizon_days so target spans do not overlap."""
        return self.origin_stride_days if self.origin_stride_days is not None else self.hyperparams.horizon_days

    def parameter_payload(self) -> dict[str, object]:
        """The canonical parameter set the run's parameter checksum and identity keys derive from."""
        return {
            "digest_version": "agri_analog_ensemble_parameters_v1",
            "schema_version": self.schema_version,
            "target_signal_name": self.target_signal_name,
            "cell_id": self.cell_id,
            "series_id": self.series_id,
            "history_start": self.history_start.isoformat(),
            "history_end": self.history_end.isoformat(),
            "as_of_time": self.as_of_time.astimezone(UTC).isoformat(),
            "as_of_mode": AS_OF_MODE,
            "origin_date": self.origin_date.isoformat(),
            "origin_count": self.origin_count,
            "origin_stride_days": self.effective_stride_days,
            "hyperparameters": {
                "k_neighbors": self.hyperparams.k_neighbors,
                "temporal_exclusion_days": self.hyperparams.temporal_exclusion_days,
                "horizon_days": self.hyperparams.horizon_days,
                "feature_weights": list(self.hyperparams.feature_weights),
            },
        }

    @property
    def parameter_checksum(self) -> str:
        """Digest over `parameter_payload`: two runs share it only if every pinned knob agrees."""
        return canonical_digest(self.parameter_payload())

    @property
    def training_key(self) -> str:
        """Stable per-invocation identity, so a re-claimed durable shard resolves rather than duplicates."""
        digest = self.parameter_checksum[:DIGEST_KEY_LENGTH]
        identity = f"analog-ensemble:{self.cell_id}:{self.origin_date.isoformat()}:{digest}"
        return identity[:IDENTITY_KEY_MAX_LENGTH]


@dataclass(frozen=True, slots=True)
class AnEnTrainingReceipt:
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
class AnEnTrainingReport:
    """One invocation's evidence: what it read, what it scored, and what (if anything) it wrote."""

    request: AnEnTrainingRequest
    evaluation: AnEnEvaluationReport
    receipt: AnEnTrainingReceipt | None = None
    persisted: bool = False

    @property
    def persisted_backtest(self) -> AnEnRollingBacktest:
        """The variant this lane persists a receipt for -- the full covariate vector, not the ablation."""
        return self.evaluation.full_vector_backtest

    def validation_metrics(self) -> dict[str, object]:
        """The receipt's JSONB body: scores, the ablation comparison, and the honest caveats."""
        return {
            "label": EVALUATION_LABEL,
            "publication_authorized": False,
            "disclaimer": EVALUATION_DISCLAIMER,
            "as_of_mode": AS_OF_MODE,
            "as_of_time": self.request.as_of_time.astimezone(UTC).isoformat(),
            "schema_version": self.request.schema_version,
            "cell_id": self.request.cell_id,
            "series_id": self.request.series_id,
            "target_signal_name": self.request.target_signal_name,
            "parameters": self.request.parameter_payload(),
            "covariate_manifest": dict(self.evaluation.manifest),
            "declared_gaps": self.evaluation.declared_gaps,
            "feature_coverage": self.evaluation.coverage.to_summary(),
            "persisted_variant": FULL_VECTOR_VARIANT,
            "full_vector": self.evaluation.full_vector_backtest.to_summary(),
            "target_lags_only_ablation": self.evaluation.target_lags_only_backtest.to_summary(),
            "daily_increment_bootstrap_v1_baseline": [dict(row) for row in self.evaluation.baseline_iterations],
            "caveats": list(EVALUATION_CAVEATS),
        }

    def to_summary(self) -> dict[str, object]:
        """The verb's one JSON line: the evaluation, the ablation, and the receipt when persisted."""
        return {
            "label": EVALUATION_LABEL,
            "disclaimer": EVALUATION_DISCLAIMER,
            "persisted": self.persisted,
            **self.evaluation.to_summary(),
            "receipt": None if self.receipt is None else self.receipt.to_summary(),
        }


# The four helpers below used to be defined here byte-identically to `covariate_wind_persist.py`
# -- both lanes drive the SAME receipt-chain sequence. They now live once in
# `forecast_receipt_writer.py`; these are thin wrappers that bind this lane's own
# `AnEnTrainingPersistError` as the failure type, so every call site below is unchanged.
_scalar_uuid = _receipt_writer.scalar_uuid


async def _insert_or_resolve(  # noqa: PLR0913 - one parameter per statement/parameter pair, plus the label
    session: AsyncSession,
    *,
    insert_statement: TextClause,
    insert_parameters: Mapping[str, object],
    lookup_statement: TextClause,
    lookup_parameters: Mapping[str, object],
    description: str,
) -> uuid.UUID:
    """Insert a row keyed by a derived identity, or resolve the one a previous attempt already wrote."""
    return await _receipt_writer.insert_or_resolve(
        session,
        insert_statement=insert_statement,
        insert_parameters=insert_parameters,
        lookup_statement=lookup_statement,
        lookup_parameters=lookup_parameters,
        description=description,
        error_type=AnEnTrainingPersistError,
    )


async def _resolve_release_set(session: AsyncSession, *, as_of_time: datetime) -> _receipt_writer.ReleaseBinding:
    """Bind the newest validated release set that already existed at the run's as-of instant."""
    return await _receipt_writer.resolve_release_set(
        session, as_of_time=as_of_time, error_type=AnEnTrainingPersistError
    )


async def _resolve_quality_policy(session: AsyncSession, *, policy_key: str) -> uuid.UUID:
    """Resolve the reviewed quality policy a forecast run must reference; never mint one."""
    return await _receipt_writer.resolve_quality_policy(
        session, policy_key=policy_key, error_type=AnEnTrainingPersistError
    )


def _origin_metric_payload(origin: AnEnOriginRun, *, parameter_checksum: str) -> dict[str, object]:
    """The canonical body one backtest metric row's checksum is derived from."""
    return {
        "digest_version": "agri_analog_ensemble_backtest_metric_v1",
        "parameter_checksum": parameter_checksum,
        "cutoff_time": _utc_midnight(origin.origin_date).isoformat(),
        "training_point_count": origin.analog_pool_size,
        "backtest_point_count": origin.scores.evaluated_count,
        "mean_absolute_error": origin.scores.mean_absolute_error,
        "root_mean_squared_error": origin.scores.root_mean_squared_error,
        "naive_root_mean_squared_error": origin.naive_root_mean_squared_error,
        "skill_score": origin.skill_score,
        "bias": origin.scores.bias,
        "mean_absolute_percentage_error": origin.scores.mean_absolute_percentage_error,
        "interval_coverage": origin.scores.interval_coverage,
    }


async def run_analog_ensemble_training(
    session: AsyncSession, request: AnEnTrainingRequest, *, persist: bool = False
) -> AnEnTrainingReport:
    """Evaluate, and when asked, write the receipt chain. The caller owns the commit and the rollback."""
    await apply_statement_timeout(session)
    started_at = datetime.now(tz=UTC)
    evaluation = await evaluate_analog_ensemble(
        session,
        cell_id=request.cell_id,
        series_id=request.series_id,
        history_start=request.history_start,
        history_end=request.history_end,
        origin_date=request.origin_date,
        as_of_time=request.as_of_time,
        origin_count=request.origin_count,
        origin_stride_days=request.origin_stride_days,
        hyperparams=request.hyperparams,
        schema_version=request.schema_version,
        target_signal_name=request.target_signal_name,
    )
    report = AnEnTrainingReport(request=request, evaluation=evaluation)
    if not persist:
        return report
    receipt = await persist_training_receipt(session, report, started_at=started_at, completed_at=datetime.now(tz=UTC))
    return replace(report, receipt=receipt, persisted=True)


async def persist_training_receipt(
    session: AsyncSession, report: AnEnTrainingReport, *, started_at: datetime, completed_at: datetime
) -> AnEnTrainingReceipt:
    """Write the whole training/backtest receipt chain for one invocation, in one transaction.

    Order is load-bearing: every row a validator inspects must already exist, and already be in
    the state that validator demands, before the validator is called. Mirrors
    `covariate_wind_persist.persist_training_receipt` exactly; see that function for the
    lineage-order rationale.
    """
    request = report.request
    if request.quality_policy_key is None:
        raise AnEnTrainingPersistError("persisting a training receipt requires a quality policy key")
    if report.evaluation.coverage.usable_day_count <= 0:
        raise AnEnTrainingPersistError(
            "no candidate day was both feature-complete and target-bearing, so there is no training "
            "set to record; read feature_coverage.blocking_features for which feature excluded them"
        )

    release = await _resolve_release_set(session, as_of_time=request.as_of_time)
    quality_policy_id = await _resolve_quality_policy(session, policy_key=request.quality_policy_key)

    feature_checksum = str(report.evaluation.manifest.get("manifest_checksum") or "")
    if not feature_checksum:
        raise AnEnTrainingPersistError(
            "agri.covariate_vector_manifest returned no manifest checksum; the feature lineage "
            "cannot be bound and the receipt would claim provenance it does not have"
        )

    backtest = report.persisted_backtest
    full_feature_mask = tuple(True for _ in report.evaluation.feature_names)
    document = model_document(
        backtest,
        cell_id=request.cell_id,
        feature_checksum=feature_checksum,
        feature_names=report.evaluation.feature_names,
        feature_mask=full_feature_mask,
    )
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
                    "lane": "analog_ensemble_train",
                    "label": EVALUATION_LABEL,
                    "publication_authorized": False,
                    "schema_version": request.schema_version,
                    "target_signal_name": request.target_signal_name,
                }
            ),
        },
        lookup_statement=_LOOKUP_JOB_DEFINITION,
        lookup_parameters={"name": TRAINING_DEFINITION_NAME, "version": TRAINING_DEFINITION_VERSION},
        description="analog ensemble training job definition",
    )

    logical_run_key = f"analog-ensemble-training:{training_key}"[:IDENTITY_KEY_MAX_LENGTH]
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
        description="analog ensemble training job run",
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
        description="analog ensemble model artifact",
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
                    "k_neighbors": request.hyperparams.k_neighbors,
                    "temporal_exclusion_days": request.hyperparams.temporal_exclusion_days,
                    "horizon_days": request.hyperparams.horizon_days,
                    "schema_version": request.schema_version,
                    "target_signal_name": request.target_signal_name,
                    "as_of_mode": AS_OF_MODE,
                }
            ),
        },
        lookup_statement=_LOOKUP_FORECAST_MODEL,
        lookup_parameters={"model_key": MODEL_KEY, "model_version": model_version},
        description="analog ensemble forecast model",
    )

    model_output_key = f"analog-ensemble-model:{digest_key}:{model_checksum[:DIGEST_KEY_LENGTH]}"
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
        description="analog ensemble model training output",
    )

    snapshot_key = f"analog-ensemble-features:{training_key}"[:IDENTITY_KEY_MAX_LENGTH]
    snapshot_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_FEATURE_SNAPSHOT,
        insert_parameters={
            "snapshot_key": snapshot_key,
            "job_run_id": job_run_id,
            "release_set_id": release.release_set_id,
            "input_release_checksum": release.manifest_checksum,
            "feature_recipe_version": FEATURE_RECIPE_VERSION,
            "feature_code_checksum": feature_code_checksum(
                report.evaluation.feature_names, schema_version=request.schema_version
            ),
            "feature_checksum": feature_checksum,
            "training_window_start": _utc_midnight(request.history_start),
            "training_window_end": _utc_midnight(request.history_end),
            "row_count": report.evaluation.coverage.usable_day_count,
        },
        lookup_statement=_LOOKUP_FEATURE_SNAPSHOT,
        lookup_parameters={"snapshot_key": snapshot_key},
        description="analog ensemble feature snapshot",
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
        description="analog ensemble training run",
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

    backtest_output_key = f"analog-ensemble-backtest:{digest_key}"
    backtest_output_id = await _insert_or_resolve(
        session,
        insert_statement=_INSERT_JOB_OUTPUT,
        insert_parameters={
            "job_run_id": job_run_id,
            "artifact_id": None,
            "output_key": backtest_output_key,
            "kind": "forecast_backtest",
            "checksum_sha256": validation_checksum,
            "row_count": len(backtest.origins),
            "metadata_json": canonical_json(
                {"label": EVALUATION_LABEL, "parameter_checksum": request.parameter_checksum}
            ),
            "validated_at": completed_at,
        },
        lookup_statement=_LOOKUP_JOB_OUTPUT,
        lookup_parameters={"job_run_id": job_run_id, "output_key": backtest_output_key},
        description="analog ensemble backtest output",
    )

    run_key = f"analog-ensemble-backtest-run:{training_key}"[:IDENTITY_KEY_MAX_LENGTH]
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
            "issue_time": _utc_midnight(backtest.newest_origin),
            "valid_from": _utc_midnight(backtest.earliest_origin),
            "valid_to": _utc_midnight(backtest.newest_origin + timedelta(days=request.hyperparams.horizon_days)),
            "horizon_steps": request.hyperparams.horizon_days,
            "input_release_checksum": release.manifest_checksum,
            "feature_checksum": feature_checksum,
            "model_checksum": model_checksum,
            "parameter_checksum": request.parameter_checksum,
            "quality_summary": canonical_json(
                {
                    "label": EVALUATION_LABEL,
                    "publication_authorized": False,
                    "as_of_mode": AS_OF_MODE,
                    "persisted_variant": FULL_VECTOR_VARIANT,
                    "aggregate": backtest.aggregate.to_summary(),
                    "origin_count": len(backtest.origins),
                    "skipped_origins": [skip.to_summary() for skip in backtest.skipped],
                    "feature_coverage": report.evaluation.coverage.to_summary(),
                    "target_lags_only_ablation_aggregate": (
                        report.evaluation.target_lags_only_backtest.aggregate.to_summary()
                    ),
                }
            ),
        },
        lookup_statement=_LOOKUP_FORECAST_RUN,
        lookup_parameters={"run_key": run_key},
        description="analog ensemble backtest forecast run",
    )

    for origin in backtest.origins:
        await session.execute(
            _INSERT_BACKTEST_METRIC,
            {
                "forecast_run_id": forecast_run_id,
                "job_output_id": backtest_output_id,
                "series_id": request.series_id,
                "cutoff_time": _utc_midnight(origin.origin_date),
                "training_point_count": origin.analog_pool_size,
                "backtest_point_count": origin.scores.evaluated_count,
                "mae": origin.scores.mean_absolute_error,
                "rmse": origin.scores.root_mean_squared_error,
                "naive_rmse": origin.naive_root_mean_squared_error,
                "skill_score": origin.skill_score,
                "bias": origin.scores.bias,
                "mape": origin.scores.mean_absolute_percentage_error,
                "coverage_fraction": origin.scores.interval_coverage or 0.0,
                "metrics_checksum": canonical_digest(
                    _origin_metric_payload(origin, parameter_checksum=request.parameter_checksum)
                ),
            },
        )

    return AnEnTrainingReceipt(
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
        backtest_metric_count=len(backtest.origins),
    )
