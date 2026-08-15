"""Database-free proofs for the AnEn training receipt chain: row shapes, order, digests.

Reuses `tests/test_covariate_wind_persist.py`'s `RecordingSession` fake seam unmodified: it
answers a statement purely from its SQL text fragment (`"INSERT INTO agri.forecast_model"` and
so on), and `analog_ensemble_persist.py` loads the SAME `sql/execution/*.sql` files the wind
lane does, so the fake needs no AnEn-specific scripting. What this file does NOT prove is that
the SQL parses or that the governance function accepts the lineage; that needs the
disposable-PostgreSQL contract harness in `test_analog_ensemble_persist_postgresql.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from agri_data_service.execution.analog_ensemble_model import (
    FULL_VECTOR_VARIANT,
    TARGET_LAGS_ONLY_VARIANT,
    AnEnEvaluationReport,
    model_document,
    run_anen_rolling_backtest,
)
from agri_data_service.execution.analog_ensemble_persist import (
    AS_OF_MODE,
    EVALUATION_LABEL,
    AnEnTrainingPersistError,
    AnEnTrainingReceipt,
    AnEnTrainingReport,
    AnEnTrainingRequest,
    persist_training_receipt,
    run_analog_ensemble_training,
)
from agri_data_service.execution.covariate_wind_model import (
    CovariateMatrix,
    OriginNotEvaluableError,
    canonical_digest,
    canonical_json,
)
from agri_data_service.method.ml.analog_ensemble import METHOD_NAME, AnEnHyperparameters
from tests.test_covariate_wind_persist import RecordingSession

CELL_ID = "11111111-1111-4111-8111-111111111111"
SERIES_ID = "22222222-2222-4222-8222-222222222222"
QUALITY_POLICY_KEY = "reviewed-anen-eval-policy"
AS_OF_TIME = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
SHA256_HEX_LENGTH = 64
DAY_COUNT = 60
FIRST_DAY = date(2026, 1, 1)
ORIGIN_INDEX = 55
HORIZON_DAYS = 3
K_NEIGHBORS = 3
FEATURE_NAMES = ("wind_speed_lag_1", "wind_speed_roll_mean_7")


def anen_matrix() -> CovariateMatrix:
    dates = tuple(FIRST_DAY + timedelta(days=offset) for offset in range(DAY_COUNT))
    values = np.column_stack([np.arange(DAY_COUNT, dtype=float), np.arange(DAY_COUNT, dtype=float) * 0.5])
    complete = np.ones(DAY_COUNT, dtype=bool)
    return CovariateMatrix(
        dates, FEATURE_NAMES, values, complete, {"manifest_checksum": "b" * 64, "day_count": DAY_COUNT}
    )


def anen_targets(matrix: CovariateMatrix) -> dict[date, float]:
    return {day: 5.0 + 0.1 * index for index, day in enumerate(matrix.dates)}


def build_report() -> AnEnTrainingReport:
    """A real report over a synthetic matrix, so the persisted shapes are the real shapes."""
    matrix = anen_matrix()
    targets = anen_targets(matrix)
    hyperparams = AnEnHyperparameters(k_neighbors=K_NEIGHBORS, temporal_exclusion_days=2, horizon_days=HORIZON_DAYS)
    request = AnEnTrainingRequest(
        cell_id=CELL_ID,
        series_id=SERIES_ID,
        history_start=matrix.dates[0],
        history_end=matrix.dates[-1],
        origin_date=matrix.dates[ORIGIN_INDEX],
        as_of_time=AS_OF_TIME,
        hyperparams=hyperparams,
        quality_policy_key=QUALITY_POLICY_KEY,
    )
    origins = (matrix.dates[ORIGIN_INDEX],)
    full_mask = (True, True)
    lag_mask = (True, True)  # both fixture columns are the target's own lag shapes
    full_backtest = run_anen_rolling_backtest(
        matrix,
        targets,
        origin_dates=origins,
        feature_mask=full_mask,
        variant_label=FULL_VECTOR_VARIANT,
        hyperparams=hyperparams,
    )
    lag_backtest = run_anen_rolling_backtest(
        matrix,
        targets,
        origin_dates=origins,
        feature_mask=lag_mask,
        variant_label=TARGET_LAGS_ONLY_VARIANT,
        hyperparams=hyperparams,
    )
    evaluation = AnEnEvaluationReport(
        cell_id=CELL_ID,
        series_id=SERIES_ID,
        schema_version=request.schema_version,
        target_signal_name=request.target_signal_name,
        hyperparams=hyperparams,
        coverage=matrix.coverage(targets),
        manifest=matrix.reported_manifest(),
        declared_gaps=None,
        feature_names=matrix.feature_names,
        full_vector_backtest=full_backtest,
        target_lags_only_backtest=lag_backtest,
        baseline_iterations=(),
    )
    return AnEnTrainingReport(request=request, evaluation=evaluation)


async def persist(session: RecordingSession, report: AnEnTrainingReport) -> AnEnTrainingReceipt:
    # RecordingSession is a deliberate narrow stand-in for AsyncSession (see its docstring).
    return await persist_training_receipt(
        session,  # type: ignore[arg-type]
        report,
        started_at=AS_OF_TIME,
        completed_at=AS_OF_TIME,
    )


async def test_persist_writes_the_receipt_chain_in_lineage_order() -> None:
    session = RecordingSession()

    receipt = await persist(session, build_report())

    positions = session.order_of(
        "FROM agri.release_set",
        "INSERT INTO agri.job_definition",
        "INSERT INTO agri.job_run",
        "INSERT INTO agri.artifact",
        "INSERT INTO agri.forecast_model",
        "INSERT INTO agri.job_output",
        "INSERT INTO agri.forecast_feature_snapshot",
        "validate_forecast_feature_snapshot",
        "INSERT INTO agri.forecast_training_run",
        "validate_forecast_training_run",
        "INSERT INTO agri.forecast_run",
        "INSERT INTO agri.forecast_backtest_metric",
    )
    assert positions == sorted(positions)
    assert receipt.training_run_status == "validated"
    assert receipt.feature_snapshot_status == "validated"
    assert not any("validate_forecast_run(" in sql for sql, _ in session.statements)
    assert session.commits == 0


async def test_persist_binds_the_covariate_manifest_checksum_as_the_feature_lineage() -> None:
    session = RecordingSession()

    receipt = await persist(session, build_report())

    snapshot = session.parameters_for("INSERT INTO agri.forecast_feature_snapshot")
    training = session.parameters_for("INSERT INTO agri.forecast_training_run")
    assert snapshot["feature_checksum"] == "b" * SHA256_HEX_LENGTH
    assert training["feature_checksum"] == snapshot["feature_checksum"]
    assert receipt.feature_checksum == "b" * SHA256_HEX_LENGTH


async def test_persist_writes_the_algorithm_name_as_analog_ensemble_v1() -> None:
    session = RecordingSession()

    await persist(session, build_report())

    model_row = session.parameters_for("INSERT INTO agri.forecast_model")
    assert model_row["algorithm"] == METHOD_NAME == "analog_ensemble_v1"


async def test_persist_records_the_target_lags_only_ablation_alongside_the_persisted_full_vector_run() -> None:
    report = build_report()
    session = RecordingSession()

    await persist(session, report)

    metrics = json.loads(str(session.parameters_for("validate_forecast_training_run")["validation_metrics"]))
    assert metrics["persisted_variant"] == FULL_VECTOR_VARIANT
    assert metrics["target_lags_only_ablation"]["variant_label"] == TARGET_LAGS_ONLY_VARIANT
    quality_summary = json.loads(str(session.parameters_for("INSERT INTO agri.forecast_run")["quality_summary"]))
    assert "target_lags_only_ablation_aggregate" in quality_summary


async def test_the_model_checksum_is_the_digest_of_the_stored_model_document() -> None:
    report = build_report()
    session = RecordingSession()

    receipt = await persist(session, report)

    document = model_document(
        report.persisted_backtest,
        cell_id=report.request.cell_id,
        feature_checksum="b" * SHA256_HEX_LENGTH,
        feature_names=report.evaluation.feature_names,
        feature_mask=tuple(True for _ in report.evaluation.feature_names),
    )
    assert receipt.model_checksum == canonical_digest(document)
    assert session.parameters_for("INSERT INTO agri.artifact")["model_document"] == canonical_json(document)


async def test_persist_writes_one_backtest_metric_per_origin_keyed_by_that_origins_cutoff() -> None:
    report = build_report()
    session = RecordingSession()

    receipt = await persist(session, report)

    metrics = session.every_parameters_for("INSERT INTO agri.forecast_backtest_metric")
    origin_count = len(report.persisted_backtest.origins)
    assert len(metrics) == origin_count
    assert receipt.backtest_metric_count == origin_count
    cutoffs = [row["cutoff_time"] for row in metrics]
    assert cutoffs == [
        datetime(origin.origin_date.year, origin.origin_date.month, origin.origin_date.day, tzinfo=UTC)
        for origin in report.persisted_backtest.origins
    ]
    for row in metrics:
        assert 0.0 <= float(str(row["coverage_fraction"])) <= 1.0
        assert len(str(row["metrics_checksum"])) == SHA256_HEX_LENGTH


async def test_the_validation_metrics_record_the_global_as_of_mode_and_the_bias_correction_caveat() -> None:
    report = build_report()
    session = RecordingSession()

    await persist(session, report)

    metrics = json.loads(str(session.parameters_for("validate_forecast_training_run")["validation_metrics"]))
    assert metrics["as_of_mode"] == AS_OF_MODE == "global"
    assert metrics["label"] == EVALUATION_LABEL
    assert metrics["publication_authorized"] is False
    assert any("bias correction is wired but not exercised" in caveat for caveat in metrics["caveats"])


async def test_persist_refuses_when_no_validated_release_set_predates_the_as_of_instant() -> None:
    session = RecordingSession(missing=frozenset({"release_set"}))

    with pytest.raises(AnEnTrainingPersistError, match="no validated release set"):
        await persist(session, build_report())

    assert not any("INSERT INTO agri.forecast_training_run" in sql for sql, _ in session.statements)


async def test_persist_refuses_to_invent_a_quality_policy_when_the_named_one_is_absent() -> None:
    session = RecordingSession(missing=frozenset({"quality_policy"}))

    with pytest.raises(AnEnTrainingPersistError, match="will not invent one"):
        await persist(session, build_report())


async def test_persist_refuses_a_report_with_no_manifest_checksum_to_bind_to() -> None:
    report = build_report()
    unbound = AnEnTrainingReport(
        request=report.request,
        evaluation=AnEnEvaluationReport(
            cell_id=report.evaluation.cell_id,
            series_id=report.evaluation.series_id,
            schema_version=report.evaluation.schema_version,
            target_signal_name=report.evaluation.target_signal_name,
            hyperparams=report.evaluation.hyperparams,
            coverage=report.evaluation.coverage,
            manifest={},
            declared_gaps=None,
            feature_names=report.evaluation.feature_names,
            full_vector_backtest=report.evaluation.full_vector_backtest,
            target_lags_only_backtest=report.evaluation.target_lags_only_backtest,
            baseline_iterations=(),
        ),
    )

    with pytest.raises(AnEnTrainingPersistError, match="no manifest checksum"):
        await persist(RecordingSession(), unbound)


async def test_a_read_only_run_issues_no_write_statement_at_all() -> None:
    """`--persist` off is the default, and the default must not touch a single governed table.

    The fake session answers every covariate-schema query with no rows (it has no script for
    `agri.covariate_feature_schema`), so the read path fails fast on an empty feature vector --
    `ValueError` here, `OriginNotEvaluableError` on a real server once the schema resolves and the
    backtest itself finds no scoreable origin. Either way, the point under test is unchanged: no
    write statement may be reached.
    """
    session = RecordingSession()
    request = build_report().request

    with pytest.raises((OriginNotEvaluableError, ValueError)):
        await run_analog_ensemble_training(session, request, persist=False)  # type: ignore[arg-type]

    assert session.statements, "the read path must at least have queried something"
    assert not any("INSERT INTO" in sql for sql, _ in session.statements)


def test_the_training_key_is_deterministic_for_identical_pinned_inputs() -> None:
    first = build_report().request
    second = build_report().request

    assert first.training_key == second.training_key
    assert first.parameter_checksum == second.parameter_checksum

    widened = AnEnTrainingRequest(
        cell_id=first.cell_id,
        series_id=first.series_id,
        history_start=first.history_start,
        history_end=first.history_end,
        origin_date=first.origin_date,
        as_of_time=first.as_of_time,
        hyperparams=AnEnHyperparameters(
            k_neighbors=first.hyperparams.k_neighbors + 1,
            temporal_exclusion_days=first.hyperparams.temporal_exclusion_days,
            horizon_days=first.hyperparams.horizon_days,
        ),
        quality_policy_key=first.quality_policy_key,
    )
    assert widened.training_key != first.training_key


def test_the_request_refuses_a_naive_as_of_instant() -> None:
    with pytest.raises(ValueError, match="as_of_time must include a timezone"):
        AnEnTrainingRequest(
            cell_id=CELL_ID,
            series_id=SERIES_ID,
            history_start=FIRST_DAY,
            history_end=FIRST_DAY + timedelta(days=DAY_COUNT),
            origin_date=FIRST_DAY + timedelta(days=DAY_COUNT),
            as_of_time=datetime(2026, 8, 1, 12, 0),  # noqa: DTZ001 - a naive instant is the thing under test
            hyperparams=AnEnHyperparameters(),
        )
