"""The per-lane reshaping adapters: observed history in, forecast-schema rows out, one lane each.

Layer L3, spec FR-4. Split out of `monte_carlo_daily.py` when that module passed the size ceiling.
The dispatch, the publication and the run identity stay there; this module is only the three
translations between a lane's observed columns and its forecaster's own vocabulary, which is the
part that grows every time a lane is added. Rationale lives in `AGENTS-forecast-lanes.md`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from plantgeo_ml_service.foundation.canonical import canonical_json, sha256_digest
from plantgeo_ml_service.foundation.parquet_paths import BASE_PARTITION_ZOOM, ZoomTier
from plantgeo_ml_service.pipeline.analog_ensemble_daily import ForecastRefusal
from plantgeo_ml_service.pipeline.observed_reader import MAX_WINDOW_DAYS
from plantgeo_ml_service.warehouse.lanes import lane_contract
from plantgeo_ml_service.warehouse.streams import (
    FIRE_DETECTIONS_STREAM,
    SIGNAL_STREAM,
    VEGETATION_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date
    from types import ModuleType

    import polars as pl

    from plantgeo_ml_service.method.monte_carlo.vegetation_ndvi_forecast import ProvenancedForecastRow

MONTE_CARLO_HORIZON_DAYS: Final = 30

#: Draws per cell-series. Above `fire_detections.MIN_SIMULATION_COUNT`, which is the strictest floor
#: any of the five modules enforces, so one ensemble size satisfies every lane.
DEFAULT_SIMULATION_COUNT: Final = 500


class MonteCarloDispatchError(RuntimeError):
    """Raised when a lane cannot be dispatched to a forecaster this service knows how to execute."""


@dataclass(frozen=True, slots=True)
class MonteCarloOptions:
    """The knobs one dispatch run may vary, so no public function here grows a sixth parameter."""

    horizon_days: int = MONTE_CARLO_HORIZON_DAYS
    simulation_count: int = DEFAULT_SIMULATION_COUNT
    history_days: int = MAX_WINDOW_DAYS
    zoom: ZoomTier = BASE_PARTITION_ZOOM
    created_at: datetime | None = None

    def resolved_moment(self) -> datetime:
        """Return the instant every receipt of this run is stamped with."""
        return self.created_at if self.created_at is not None else datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class LaneSimulationInputs:
    """Everything one lane's simulation and publication share, assembled once per lane."""

    layer: str
    stem: str
    module: ModuleType
    cutoff: date
    issued_on: date
    random_seed: int
    options: MonteCarloOptions

    @property
    def forecast_run_id(self) -> str:
        """Return the run identity every row of this lane-run carries."""
        return sha256_digest(
            canonical_json(
                {
                    "cutoff": self.cutoff.isoformat(),
                    "ensemble_size": self.options.simulation_count,
                    "horizon_days": self.options.horizon_days,
                    "issued_on": self.issued_on.isoformat(),
                    "lane": self.layer,
                    "method_module": self.stem,
                    "random_seed": self.random_seed,
                }
            )
        )


#: Forecast rows rest on zero observations and zero releases; the observed counting columns carry
#: that honestly rather than borrowing the history's counts.
FORECAST_OBSERVATION_COUNT: Final = 0
FORECAST_RELEASE_COUNT: Final = 0


def simulate_lane(
    frame: pl.DataFrame, *, inputs: LaneSimulationInputs
) -> tuple[tuple[dict[str, object], ...], tuple[ForecastRefusal, ...]]:
    """Run the lane's own forecaster over every group of its observed history."""
    if inputs.layer == SIGNAL_STREAM:
        return _simulate_signal(frame, inputs=inputs)
    if inputs.layer == FIRE_DETECTIONS_STREAM:
        return _simulate_fire_detections(frame, inputs=inputs)
    if inputs.layer == VEGETATION_STREAM:
        return _simulate_vegetation(frame, inputs=inputs)
    raise MonteCarloDispatchError(f"lane {inputs.layer!r} has no reshaping adapter in this service")


def _simulate_signal(
    frame: pl.DataFrame, *, inputs: LaneSimulationInputs
) -> tuple[tuple[dict[str, object], ...], tuple[ForecastRefusal, ...]]:
    """Forecast every (cell, signal) series independently, as the signal lane's own contract requires."""
    module = inputs.module
    rows: list[dict[str, object]] = []
    refusals: list[ForecastRefusal] = []
    for (cell_id, signal_name_object), series in _grouped(frame, ("cell_id", "signal_name")):
        signal_name = str(signal_name_object)
        ordered = series.sort("observed_day")
        try:
            spec = module.series_spec_for(signal_name)
        except LookupError as error:
            refusals.append(_refusal(inputs.layer, cell_id, signal_name, "series_undeclared", str(error)))
            continue
        observations = tuple(
            module.ObservedSignalDay(
                observed_day=row["observed_day"],
                value=float(row["normalized_value"]),
                observation_checksum=_row_fingerprint(row, columns=("observed_day", "normalized_value")),
            )
            for row in ordered.to_dicts()
        )
        try:
            run = module.simulate_signal_forecast(
                spec=spec,
                observations=observations,
                issued_on=inputs.cutoff,
                request=module.SimulationRequest(
                    horizon_days=inputs.options.horizon_days,
                    simulation_count=inputs.options.simulation_count,
                    seed=inputs.random_seed,
                ),
                series_key=f"{cell_id}|{signal_name}",
            )
        except ValueError as error:
            refusals.append(_refusal(inputs.layer, cell_id, signal_name, "insufficient_history", str(error)))
            continue
        latest = ordered.tail(1).to_dicts()[0]
        rows.extend(
            {
                "support_key": latest["support_key"],
                "signal_name": signal_name,
                "normalized_unit": latest["normalized_unit"],
                "cell_id": cell_id,
                "observed_day": quantile_row.valid_day,
                "normalized_value": quantile_row.value,
                "observation_count": FORECAST_OBSERVATION_COUNT,
                "newest_observed_at": ordered.get_column("newest_observed_at").max(),
                "coverage_fraction": None,
                "allowed_client_exposure": latest["allowed_client_exposure"],
                "cell_longitude": latest["cell_longitude"],
                "cell_latitude": latest["cell_latitude"],
                "forecast_run_id": run.forecast_run_id,
                "random_seed": run.random_seed,
                "ensemble_size": run.ensemble_size,
                "horizon_days": quantile_row.horizon_step,
                "issued_on": run.issued_on,
                "quantile": quantile_row.quantile,
            }
            for quantile_row in run.rows
        )
    return tuple(rows), tuple(refusals)


def _simulate_fire_detections(
    frame: pl.DataFrame, *, inputs: LaneSimulationInputs
) -> tuple[tuple[dict[str, object], ...], tuple[ForecastRefusal, ...]]:
    """Forecast every detection cell's hurdle-then-bootstrap ensemble from its own positive days."""
    module = inputs.module
    floor = lane_contract(inputs.layer).history_floor
    rows: list[dict[str, object]] = []
    refusals: list[ForecastRefusal] = []
    for (longitude_object, latitude_object), cell_rows in _grouped(frame, ("cell_longitude", "cell_latitude")):
        longitude, latitude = float(longitude_object), float(latitude_object)  # type: ignore[arg-type]  # float64 columns
        ordered = cell_rows.sort("observed_day")
        cell_name = f"{longitude}|{latitude}"
        observations = tuple(
            module.ObservedCellDay(
                observed_day=row["observed_day"],
                detection_count=int(row["detection_count"]),
                frp_sum=None if row["frp_sum"] is None else float(row["frp_sum"]),
                frp_observation_count=int(row["frp_observation_count"]),
                high_confidence_detection_count=int(row["high_confidence_detection_count"]),
                observation_checksum=_row_fingerprint(row, columns=("observed_day", "detection_count", "frp_sum")),
            )
            for row in ordered.to_dicts()
        )
        try:
            history = module.build_cell_ignition_history(
                observations,
                history_start_day=floor,
                cutoff_day=inputs.cutoff,
            )
            request = module.SimulationRequest(
                forecast_run_id=inputs.forecast_run_id,
                horizon_days=inputs.options.horizon_days,
                simulation_count=inputs.options.simulation_count,
                seed=inputs.random_seed,
            )
            quantiles = module.simulate_cell_day_quantiles(
                cell_longitude=float(longitude),
                cell_latitude=float(latitude),
                history=history,
                request=request,
                checksum=module.history_checksum(
                    observations,
                    history_start_day=floor,
                    cutoff_day=inputs.cutoff,
                ),
            )
        except ValueError as error:
            refusals.append(_refusal(inputs.layer, cell_name, inputs.layer, "insufficient_history", str(error)))
            continue
        newest = ordered.get_column("newest_observed_at").max()
        rows.extend(
            {
                "cell_longitude": quantile_row.cell_longitude,
                "cell_latitude": quantile_row.cell_latitude,
                "observed_day": quantile_row.observed_day,
                "detection_count": quantile_row.detection_count,
                "frp_sum": quantile_row.frp_sum,
                "frp_observation_count": quantile_row.frp_observation_count,
                "high_confidence_detection_count": quantile_row.high_confidence_detection_count,
                "newest_observed_at": newest,
                "forecast_run_id": quantile_row.forecast_run_id,
                "random_seed": quantile_row.random_seed,
                "ensemble_size": quantile_row.ensemble_size,
                "horizon_days": quantile_row.horizon_days,
                "issued_on": quantile_row.issued_on,
                "quantile": quantile_row.quantile,
            }
            for quantile_row in quantiles
        )
    return tuple(rows), tuple(refusals)


def _simulate_vegetation(
    frame: pl.DataFrame, *, inputs: LaneSimulationInputs
) -> tuple[tuple[dict[str, object], ...], tuple[ForecastRefusal, ...]]:
    """Forecast every NDVI cell's seasonal anomaly bootstrap from its own governed history."""
    module = inputs.module
    rows: list[dict[str, object]] = []
    refusals: list[ForecastRefusal] = []
    for (cell_id,), cell_rows in _grouped(frame, ("cell_id",)):
        ordered = cell_rows.sort("observed_day")
        observations = tuple(
            module.ObservedDay(
                observed_day=row["observed_day"],
                metric_value=float(row["metric_value"]),
                observation_checksum=str(row["observation_checksum"]),
            )
            for row in ordered.to_dicts()
        )
        request = module.SimulationRequest(
            horizon_days=inputs.options.horizon_days,
            simulation_count=inputs.options.simulation_count,
            seed=inputs.random_seed,
        )
        try:
            history = module.build_seasonal_history(observations, inputs.cutoff)
            checksum = module.history_checksum(observations, inputs.cutoff)
            quantiles = module.simulate_horizon_quantiles(history=history, request=request, checksum=checksum)
        except ValueError as error:
            refusals.append(_refusal(inputs.layer, str(cell_id), inputs.layer, "insufficient_history", str(error)))
            continue
        provenanced = module.provenanced_forecast_rows(
            history=history,
            quantiles=quantiles,
            request=request,
            forecast_run_id=inputs.forecast_run_id,
        )
        latest = ordered.tail(1).to_dicts()[0]
        rows.extend(
            {
                "cell_id": cell_id,
                "grid_name": latest["grid_name"],
                "metric_name": latest["metric_name"],
                "metric_unit": latest["metric_unit"],
                "observed_day": forecast_row.valid_day,
                "metric_value": forecast_row.metric_value,
                # This lane requires the column non-null at the base rung, and a forecast row may
                # never inherit an observation's checksum: it carries its OWN fingerprint instead.
                "observation_checksum": _forecast_row_fingerprint(forecast_row, cell_id=str(cell_id)),
                "data_available_at": ordered.get_column("data_available_at").max(),
                "release_count": FORECAST_RELEASE_COUNT,
                "allowed_client_exposure": bool(latest["allowed_client_exposure"]),
                "cell_longitude": latest["cell_longitude"],
                "cell_latitude": latest["cell_latitude"],
                "forecast_run_id": forecast_row.forecast_run_id,
                "random_seed": forecast_row.random_seed,
                "ensemble_size": forecast_row.ensemble_size,
                "horizon_days": forecast_row.horizon_days,
                "issued_on": forecast_row.issued_on,
                "quantile": forecast_row.quantile,
            }
            for forecast_row in provenanced
        )
    return tuple(rows), tuple(refusals)


def _grouped(frame: pl.DataFrame, columns: tuple[str, ...]) -> tuple[tuple[tuple[object, ...], pl.DataFrame], ...]:
    """Partition one lane's history by its series key, in key order, so a run is reproducible."""
    if frame.height == 0:
        return ()
    partitions = frame.partition_by(list(columns), as_dict=True)
    return tuple(sorted(partitions.items(), key=lambda entry: tuple(str(value) for value in entry[0])))


def _refusal(layer: str, cell_id: object, series_name: str, reason: str, detail: str) -> ForecastRefusal:
    """Build one governed refusal receipt entry; nothing fabricates a row in its place."""
    return ForecastRefusal(
        layer=layer,
        cell_id=str(cell_id),
        series_name=series_name,
        reason=reason,
        detail=detail,
    )


def _row_fingerprint(row: Mapping[str, object], *, columns: tuple[str, ...]) -> str:
    """Return one observed row's own governed fingerprint, for a lane whose export carries none.

    A digest OF the row, never a checksum borrowed from elsewhere: it exists so a forecaster that
    requires a per-row fingerprint gets a reproducible one, and it is not published anywhere.
    """
    rendered = "|".join(str(row.get(column)) for column in columns)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _forecast_row_fingerprint(forecast_row: ProvenancedForecastRow, *, cell_id: str) -> str:
    """Return a forecast row's OWN fingerprint, so it never carries an observation's checksum."""
    return sha256_digest(
        canonical_json(
            {
                "cell_id": cell_id,
                "forecast_run_id": forecast_row.forecast_run_id,
                "horizon_days": forecast_row.horizon_days,
                "quantile": forecast_row.quantile,
                "valid_day": forecast_row.valid_day.isoformat(),
            }
        )
    )


__all__ = [
    "DEFAULT_SIMULATION_COUNT",
    "FORECAST_OBSERVATION_COUNT",
    "FORECAST_RELEASE_COUNT",
    "MONTE_CARLO_HORIZON_DAYS",
    "LaneSimulationInputs",
    "MonteCarloDispatchError",
    "MonteCarloOptions",
    "simulate_lane",
]
