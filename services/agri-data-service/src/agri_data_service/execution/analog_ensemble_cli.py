"""Standalone Click commands for the Analog Ensemble lane and NDVI split-conformal recalibration.

Deliberately not registered by this module: `interface/cli/forecast.py` owns the shared
forecast-family registry and adds:

    from agri_data_service.execution.analog_ensemble_cli import forecast_recalibrate_ndvi, forecast_train_anen
    forecast.add_command(forecast_train_anen, name="train-anen")
    forecast.add_command(forecast_recalibrate_ndvi, name="recalibrate-ndvi")

Both commands are read-only by default and follow the same session/commit-or-rollback shape as
`forecast train-wind` in `agri_data_service.interface.cli` -- see that command's docstring for the shared
receipt-chain contract `agri-service forecast train-anen` also follows.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from typing import Any

import click
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from agri_data_service.config import settings
from agri_data_service.db.engine import forecast_iteration_session
from agri_data_service.execution.analog_ensemble_persist import (
    AnEnTrainingPersistError,
    AnEnTrainingRequest,
    run_analog_ensemble_training,
)
from agri_data_service.execution.conformal_recalibration import RecalibrationSplit, run_recalibration
from agri_data_service.execution.covariate_wind_model import OriginNotEvaluableError
from agri_data_service.method.ml.analog_ensemble import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_K_NEIGHBORS,
    DEFAULT_TEMPORAL_EXCLUSION_DAYS,
    AnEnHyperparameters,
)
from agri_data_service.method.ml.conformal_calibration import DEFAULT_NOMINAL_COVERAGE


def _cli_day(value: str, option_name: str) -> date:
    """Parse one ISO-8601 calendar date CLI option."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise click.BadParameter("must be an ISO-8601 calendar date", param_hint=option_name) from exc


def _cli_timestamp(value: str, option_name: str) -> datetime:
    """Parse one timezone-aware ISO-8601 CLI timestamp and normalize it to UTC."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise click.BadParameter("must be an ISO-8601 timestamp", param_hint=option_name) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise click.BadParameter("must include a UTC offset", param_hint=option_name)
    return parsed.astimezone(UTC)


def _cli_day_as_utc_midnight(value: str, option_name: str) -> datetime:
    """Parse an ISO-8601 calendar date CLI option as its UTC midnight instant."""
    day = _cli_day(value, option_name)
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@click.command("train-anen")
@click.option("--cell-id", required=True, help="Spatial cell whose covariate vectors and target series train the fit.")
@click.option("--series-id", required=True, help="Forecast series the backtest metrics are filed under.")
@click.option("--history-start", required=True, help="First covariate day to read, as YYYY-MM-DD.")
@click.option("--history-end", required=True, help="Last covariate day to read, as YYYY-MM-DD.")
@click.option("--origin-date", required=True, help="Newest rolling origin, as YYYY-MM-DD.")
@click.option(
    "--origins",
    "origin_count",
    type=click.IntRange(1, 60),
    default=1,
    show_default=True,
    help="Rolling origins to score, walking back from --origin-date.",
)
@click.option(
    "--origin-stride-days",
    type=click.IntRange(1, 366),
    default=None,
    help="Days between rolling origins; defaults to --horizon-days so target spans do not overlap.",
)
@click.option("--k-neighbors", type=click.IntRange(1, 500), default=DEFAULT_K_NEIGHBORS, show_default=True)
@click.option(
    "--temporal-exclusion-days",
    type=click.IntRange(0, 3660),
    default=DEFAULT_TEMPORAL_EXCLUSION_DAYS,
    show_default=True,
    help="Passed through to the pure AnEn analog search; the execution layer's own leakage "
    "boundary (an analog's whole successor path must precede the origin) is separate and stricter.",
)
@click.option("--horizon-days", type=click.IntRange(1, 366), default=DEFAULT_HORIZON_DAYS, show_default=True)
@click.option(
    "--as-of-time",
    default=None,
    help="Availability gate fed to p_as_of_time; defaults to now(). Pin it to reproduce a manifest checksum.",
)
@click.option(
    "--quality-policy-key",
    default=None,
    help="Existing agri.forecast_quality_policy the backtest run references. Required with --persist.",
)
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help="Write the training/backtest receipt chain. OFF by default; without it nothing is written.",
)
@click.option("--json", "as_json", is_flag=True, default=True, show_default=True, help="Emit one JSON line.")
def forecast_train_anen(  # noqa: PLR0913 - one parameter per click option is the contract
    cell_id: str,
    series_id: str,
    history_start: str,
    history_end: str,
    origin_date: str,
    origin_count: int,
    origin_stride_days: int | None,
    k_neighbors: int,
    temporal_exclusion_days: int,
    horizon_days: int,
    as_of_time: str | None,
    quality_policy_key: str | None,
    persist: bool,
    as_json: bool,
) -> None:
    """Fit and score the evaluation-only Analog Ensemble (k-NN) forecaster over rolling origins.

    READ-ONLY BY DEFAULT. Without `--persist` this reads the pinned covariate vectors and the
    availability-gated target, scores both the full-covariate-vector and the target-lags-only
    ablation variant at every origin, and prints one JSON line -- nothing is written.

    With `--persist` it additionally writes ONE governed training receipt chain for the
    full-vector variant only (a job run, a model artifact, a forecast model, a validated feature
    snapshot, a validated training run, a STAGED forecast run, and one backtest metric row per
    origin), through `agri.validate_forecast_feature_snapshot` and
    `agri.validate_forecast_training_run`. It writes no forecast receipt, no forecast value and
    no publication, so nothing it records can reach a serving surface.

    EVALUATION ONLY. The metrics prove the framework runs end to end. They are not an
    operational forecast and are not life-safety validated.
    """
    if persist and not quality_policy_key:
        raise click.BadParameter(
            "--persist requires --quality-policy-key: a forecast run must reference a reviewed "
            "quality policy, and this lane will not invent one",
            param_hint="--quality-policy-key",
        )
    try:
        request = AnEnTrainingRequest(
            cell_id=cell_id,
            series_id=series_id,
            history_start=_cli_day(history_start, "history-start"),
            history_end=_cli_day(history_end, "history-end"),
            origin_date=_cli_day(origin_date, "origin-date"),
            as_of_time=(_cli_timestamp(as_of_time, "as-of-time") if as_of_time is not None else datetime.now(tz=UTC)),
            hyperparams=AnEnHyperparameters(
                k_neighbors=k_neighbors,
                temporal_exclusion_days=temporal_exclusion_days,
                horizon_days=horizon_days,
            ),
            origin_count=origin_count,
            origin_stride_days=origin_stride_days,
            quality_policy_key=quality_policy_key,
        )
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc
    try:
        summary = asyncio.run(_forecast_train_anen(request, persist=persist))
    except (SQLAlchemyError, OriginNotEvaluableError, AnEnTrainingPersistError, ValueError) as exc:
        reason = (
            f"analog ensemble training failed ({exc.__class__.__name__})"
            if isinstance(exc, SQLAlchemyError)
            else str(exc)
        )
        raise click.ClickException(reason) from exc
    click.echo(json.dumps(summary, sort_keys=True) if as_json else json.dumps(summary, indent=2, sort_keys=True))


async def _forecast_train_anen(request: AnEnTrainingRequest, *, persist: bool) -> dict[str, Any]:
    """Open the evaluation-writer session, run the fit, and commit only when persistence was asked for."""
    database_url = settings.require_forecast_iteration_database_url()
    async with forecast_iteration_session(database_url) as session:
        report = await run_analog_ensemble_training(session, request, persist=persist)
        if persist:
            await session.commit()
        else:
            # An explicit rollback rather than trusting the session's close: a read-only run must
            # leave nothing behind even if a future edit starts writing on a path it does not today.
            await session.rollback()
    return report.to_summary()


@click.command("recalibrate-ndvi")
@click.option(
    "--method",
    default="ndvi_seasonal_anomaly_bootstrap_v1",
    show_default=True,
    help="forecast_iteration.method to recalibrate.",
)
@click.option(
    "--series-id",
    default=None,
    help="Scope to one forecast series; default reads every series governed under --method.",
)
@click.option(
    "--calibration-cutoff-before",
    required=True,
    help="Exclusive upper bound (YYYY-MM-DD) of the calibration fold's origins.",
)
@click.option(
    "--held-out-cutoff-at-or-after",
    required=True,
    help="Inclusive lower bound (YYYY-MM-DD) of the held-out fold's origins.",
)
@click.option("--nominal-coverage", type=float, default=DEFAULT_NOMINAL_COVERAGE, show_default=True)
@click.option("--as-of-time", default=None, help="Availability gate; defaults to now().")
@click.option("--json", "as_json", is_flag=True, default=True, show_default=True, help="Emit one JSON line.")
def forecast_recalibrate_ndvi(  # noqa: PLR0913 - one parameter per click option is the contract
    method: str,
    series_id: str | None,
    calibration_cutoff_before: str,
    held_out_cutoff_at_or_after: str,
    nominal_coverage: float,
    as_of_time: str | None,
    as_json: bool,
) -> None:
    """Split-conformal recalibration decision record over real recorded iteration residuals.

    READ-ONLY, always: this command issues `SET TRANSACTION READ ONLY` and never commits. The
    calibration fold and the held-out fold are disjoint origin (cutoff_time) ranges the caller
    names explicitly -- see `RecalibrationSplit` for why a row whose origin falls between the two
    bounds belongs to neither fold rather than defaulting into one.
    """
    resolved_as_of = _cli_timestamp(as_of_time, "as-of-time") if as_of_time is not None else datetime.now(tz=UTC)
    try:
        split = RecalibrationSplit(
            calibration_cutoff_before=_cli_day_as_utc_midnight(calibration_cutoff_before, "calibration-cutoff-before"),
            held_out_cutoff_at_or_after=_cli_day_as_utc_midnight(
                held_out_cutoff_at_or_after, "held-out-cutoff-at-or-after"
            ),
        )
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc
    try:
        summary = asyncio.run(
            _forecast_recalibrate_ndvi(
                method=method,
                series_id=series_id,
                split=split,
                as_of_time=resolved_as_of,
                nominal_coverage=nominal_coverage,
            )
        )
    except (SQLAlchemyError, ValueError) as exc:
        reason = (
            f"conformal recalibration failed ({exc.__class__.__name__})"
            if isinstance(exc, SQLAlchemyError)
            else str(exc)
        )
        raise click.ClickException(reason) from exc
    click.echo(json.dumps(summary, sort_keys=True) if as_json else json.dumps(summary, indent=2, sort_keys=True))


async def _forecast_recalibrate_ndvi(
    *,
    method: str,
    series_id: str | None,
    split: RecalibrationSplit,
    as_of_time: datetime,
    nominal_coverage: float,
) -> dict[str, Any]:
    """Open a hard read-only session, run the recalibration, and always roll back."""
    database_url = settings.require_forecast_iteration_database_url()
    async with forecast_iteration_session(database_url) as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        report = await run_recalibration(
            session,
            method=method,
            split=split,
            as_of_time=as_of_time,
            series_id=series_id,
            nominal_coverage=nominal_coverage,
        )
        await session.rollback()
    return report.to_summary()
