"""Dispatch each registry lane to its own Monte Carlo forecaster and publish what it produces.

Layer L3, spec FR-4 and FR-4a. One explicit `importlib` map from a lane's declared
`forecast_module` stem to the module that implements it: never a string built at call time, so the
set of modules this service can execute is readable in one place and cannot be widened by data.
Each forecaster's own insufficient-history refusal is carried out as a governed refusal receipt and
never as a fabricated row. Rationale lives in `AGENTS-forecast-lanes.md` in this directory.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]  # pyarrow ships no stubs

from plantgeo_ml_service.pipeline.analog_ensemble_daily import (
    AnalogEnsembleOptions,
    ForecastRefusal,
    run_analog_ensemble_daily,
)
from plantgeo_ml_service.pipeline.forecast_lane_bootstrap import (
    ForecastLaneError,
    forecast_source_ceiling,
    write_and_publish_forecast_rows,
    write_run_receipt,
)

# Re-exported rather than re-declared: the per-lane reshaping moved out when this module passed the
# size ceiling, and every existing importer names the options and the dispatch error here.
from plantgeo_ml_service.pipeline.monte_carlo_adapters import (
    DEFAULT_SIMULATION_COUNT,
    MONTE_CARLO_HORIZON_DAYS,
    LaneSimulationInputs,
    MonteCarloDispatchError,
    MonteCarloOptions,
    simulate_lane,
)
from plantgeo_ml_service.pipeline.observed_reader import MAX_WINDOW_DAYS
from plantgeo_ml_service.warehouse.lanes import LANE_CONTRACTS, lane_contract
from plantgeo_ml_service.warehouse.streams import (
    FIRE_DETECTIONS_STREAM,
    SIGNAL_STREAM,
    VEGETATION_STREAM,
    stream_schema,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date
    from types import ModuleType

    from plantgeo_ml_service.pipeline.availability_publisher import PointerStore, PublicationReceipt
    from plantgeo_ml_service.pipeline.object_store import ObjectStore, ReadOnlyObjectStore
    from plantgeo_ml_service.pipeline.observed_reader import ObservedReader

#: THE dispatch map, spelled out. A lane's `forecast_module` stem is looked up here and nowhere
#: else; an unmapped stem is a refusal, never a module name assembled from the stem at runtime.
MONTE_CARLO_MODULES: Final[Mapping[str, str]] = {
    "fire_detections": "plantgeo_ml_service.method.monte_carlo.fire_detections",
    "sensors": "plantgeo_ml_service.method.monte_carlo.sensors",
    "signal": "plantgeo_ml_service.method.monte_carlo.signal",
    "vegetation_ndvi_forecast": "plantgeo_ml_service.method.monte_carlo.vegetation_ndvi_forecast",
    "water_gauges": "plantgeo_ml_service.method.monte_carlo.water_gauges",
}

#: Which lane each dispatchable stem writes. `sensors` and `water_gauges` are deliberately absent:
#: neither has a lane contract or a pinned observed schema in this service yet, so a run names them
#: in a refusal receipt rather than inventing a stream to write them to.
STEM_LAYERS: Final[Mapping[str, str]] = {
    "fire_detections": FIRE_DETECTIONS_STREAM,
    "signal": SIGNAL_STREAM,
    "vegetation_ndvi_forecast": VEGETATION_STREAM,
}

#: Where FR-7 keeps this service's Analog Ensemble artifacts. Its presence is what decides whether
#: the `signal` lane is forecast by AnEn or by the Monte Carlo forecaster.
ANALOG_ENSEMBLE_ARTIFACT_PREFIX: Final = "ml/artifacts/analog-ensemble/"

ANALOG_ENSEMBLE_FORECASTER: Final = "analog_ensemble"
MONTE_CARLO_FORECASTER: Final = "monte_carlo"


@dataclass(frozen=True, slots=True)
class LaneForecastReceipt:
    """What one lane's dispatch produced: which forecaster ran, what landed, and what was refused."""

    layer: str
    forecast_module: str
    forecaster: str
    issued_on: date
    forecast_run_id: str
    random_seed: int
    ensemble_size: int
    written_days: tuple[date, ...]
    absent_days: tuple[date, ...]
    row_count: int
    refusals: tuple[ForecastRefusal, ...]
    publication: PublicationReceipt | None

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection an operator reads the turn's outcome from."""
        return {
            "absent_days": [day.isoformat() for day in self.absent_days],
            "ensemble_size": self.ensemble_size,
            "forecast_module": self.forecast_module,
            "forecast_run_id": self.forecast_run_id,
            "forecaster": self.forecaster,
            "issued_on": self.issued_on.isoformat(),
            "layer": self.layer,
            "random_seed": self.random_seed,
            "refusals": [refusal.to_wire() for refusal in self.refusals],
            "row_count": self.row_count,
            "written_days": [day.isoformat() for day in self.written_days],
        }


def load_forecast_module(stem: str) -> ModuleType:
    """Import the `method/monte_carlo` module one lane's declared stem names, or refuse by name."""
    module_name = MONTE_CARLO_MODULES.get(stem)
    if module_name is None:
        raise MonteCarloDispatchError(
            f"forecast module stem {stem!r} is not in this service's dispatch map; it knows "
            f"{tuple(sorted(MONTE_CARLO_MODULES))}"
        )
    return importlib.import_module(module_name)


def dispatchable_lanes() -> tuple[str, ...]:
    """Return every lane that declares a forecaster this service can execute today, sorted."""
    return tuple(
        sorted(
            slug
            for slug, contract in LANE_CONTRACTS.items()
            if contract.forecast_module is not None and contract.forecast_module in STEM_LAYERS
        )
    )


def run_monte_carlo_daily(  # noqa: PLR0913 - one keyword per run boundary is the contract
    store: ObjectStore,
    pointers: PointerStore,
    reader: ObservedReader,
    *,
    issued_on: date,
    random_seed: int,
    options: MonteCarloOptions | None = None,
) -> tuple[LaneForecastReceipt, ...]:
    """Forecast every dispatchable lane for one issue day, in lane order, and publish each one."""
    settings = options if options is not None else MonteCarloOptions()
    return tuple(
        run_monte_carlo_lane(
            store,
            pointers,
            reader,
            layer=layer,
            issued_on=issued_on,
            random_seed=random_seed,
            options=settings,
        )
        for layer in dispatchable_lanes()
    )


def run_monte_carlo_lane(  # noqa: PLR0913 - one keyword per run boundary is the contract
    store: ObjectStore,
    pointers: PointerStore,
    reader: ObservedReader,
    *,
    layer: str,
    issued_on: date,
    random_seed: int,
    options: MonteCarloOptions | None = None,
) -> LaneForecastReceipt:
    """Forecast one lane: read its settled history, simulate, write every rung, publish availability."""
    settings = options if options is not None else MonteCarloOptions()
    contract = lane_contract(layer)
    stem = contract.forecast_module
    if stem is None:
        raise MonteCarloDispatchError(f"lane {layer!r} declares no forecaster; a horizon of none ships no module")
    if STEM_LAYERS.get(stem) != layer:
        raise MonteCarloDispatchError(
            f"lane {layer!r} declares stem {stem!r}, which this service does not bind to a writable stream; "
            f"it writes {tuple(sorted(STEM_LAYERS))}"
        )
    module = load_forecast_module(stem)
    if layer == SIGNAL_STREAM and analog_ensemble_artifact_sha256(store) is not None:
        return _run_signal_through_analog_ensemble(
            store,
            pointers,
            reader,
            issued_on=issued_on,
            random_seed=random_seed,
            options=settings,
        )
    cutoff = contract.settled_through(issued_on)
    frame = _read_settled_history(
        reader,
        layer=layer,
        cutoff=cutoff,
        issued_on=issued_on,
        options=settings,
    )
    request = LaneSimulationInputs(
        layer=layer,
        stem=stem,
        module=module,
        cutoff=cutoff,
        issued_on=issued_on,
        random_seed=random_seed,
        options=settings,
    )
    rows, refusals = simulate_lane(frame, inputs=request)
    return _publish_lane_rows(store, pointers, rows, inputs=request, refusals=refusals)


def analog_ensemble_artifact_sha256(store: ReadOnlyObjectStore) -> str | None:
    """Return the newest Analog Ensemble artifact digest the bucket holds, or `None` when it holds none."""
    digests = sorted(
        key.removeprefix(ANALOG_ENSEMBLE_ARTIFACT_PREFIX).removesuffix(".json")
        for key in store.list_relative_paths(ANALOG_ENSEMBLE_ARTIFACT_PREFIX)
        if key.endswith(".json")
    )
    return digests[-1] if digests else None


def _run_signal_through_analog_ensemble(  # noqa: PLR0913 - one keyword per run boundary is the contract
    store: ObjectStore,
    pointers: PointerStore,
    reader: ObservedReader,
    *,
    issued_on: date,
    random_seed: int,
    options: MonteCarloOptions,
) -> LaneForecastReceipt:
    """Run the signal lane through AnEn, which outranks the Monte Carlo forecaster when an artifact exists."""
    artifact_sha256 = analog_ensemble_artifact_sha256(store)
    receipt = run_analog_ensemble_daily(
        store,
        pointers,
        reader,
        issued_on=issued_on,
        random_seed=random_seed,
        options=AnalogEnsembleOptions(artifact_sha256=artifact_sha256, created_at=options.created_at),
    )
    return LaneForecastReceipt(
        layer=SIGNAL_STREAM,
        forecast_module="signal",
        forecaster=ANALOG_ENSEMBLE_FORECASTER,
        issued_on=issued_on,
        forecast_run_id=receipt.forecast_run_id,
        random_seed=receipt.random_seed,
        ensemble_size=0,
        written_days=receipt.written_days,
        absent_days=receipt.absent_days,
        row_count=receipt.row_count,
        refusals=receipt.refusals,
        publication=receipt.publication,
    )


def _read_settled_history(
    reader: ObservedReader,
    *,
    layer: str,
    cutoff: date,
    issued_on: date,
    options: MonteCarloOptions,
) -> pl.DataFrame:
    """Read one lane's observed history up to its settled frontier, in reader-sized chunks.

    The reader bounds a single call at `MAX_WINDOW_DAYS`; a lane whose history floor is deeper than
    that is walked in chunks rather than in one read, which is what its own budget note asks for.
    """
    contract = lane_contract(layer)
    first_day = max(contract.history_floor, cutoff - timedelta(days=options.history_days - 1))
    frames: list[pl.DataFrame] = []
    window_start = first_day
    while window_start <= cutoff:
        window_end = min(cutoff, window_start + timedelta(days=MAX_WINDOW_DAYS - 1))
        frames.append(reader.read_lane_window(layer, options.zoom, window_start, window_end, as_of=issued_on))
        window_start = window_end + timedelta(days=1)
    if not frames:
        empty = stream_schema(layer, "observed").arrow_schema.empty_table()
        return pl.from_arrow(empty)  # type: ignore[return-value]  # a Table always yields a DataFrame
    return pl.concat(frames, how="vertical")


def _publish_lane_rows(
    store: ObjectStore,
    pointers: PointerStore,
    rows: Sequence[Mapping[str, object]],
    *,
    inputs: LaneSimulationInputs,
    refusals: tuple[ForecastRefusal, ...],
) -> LaneForecastReceipt:
    """Write one lane's simulated rows at every rung and publish them, or return the refusal-only receipt."""
    moment = inputs.options.resolved_moment()
    source_receipt = write_run_receipt(
        store,
        {
            "cutoff": inputs.cutoff.isoformat(),
            "ensemble_size": inputs.options.simulation_count,
            "forecast_module": inputs.stem,
            "forecast_run_id": inputs.forecast_run_id,
            "forecaster": MONTE_CARLO_FORECASTER,
            "horizon_days": inputs.options.horizon_days,
            "issued_on": inputs.issued_on.isoformat(),
            "lane": inputs.layer,
            "random_seed": inputs.random_seed,
            "refusals": [refusal.to_wire() for refusal in refusals],
            "row_count": len(rows),
        },
        layer=inputs.layer,
        forecast_run_id=inputs.forecast_run_id,
    )
    # A lane that simulated nothing still publishes: every day of its horizon becomes a governed
    # absence, so "refused" and "never ran" are not the same silence in the index (FR-4a).
    schema = stream_schema(inputs.layer, "forecast")
    frame = pl.from_arrow(pa.Table.from_pylist([dict(row) for row in rows], schema=schema.arrow_schema))
    published = write_and_publish_forecast_rows(
        store,
        pointers,
        frame,  # type: ignore[arg-type]  # a Table always yields a DataFrame
        layer=inputs.layer,
        run_id=inputs.forecast_run_id,
        # `cutoff` IS the lane's settled frontier for this issue day, which is what the horizons
        # were counted from, so it is what the day ladder starts one day after.
        issued_on=inputs.cutoff,
        source_receipt=source_receipt,
        source_ceiling=forecast_source_ceiling(
            inputs.layer,
            issued_on=inputs.issued_on,
            horizon_days=inputs.options.horizon_days,
        ),
        moment=moment,
    )
    return _lane_receipt(
        inputs,
        refusals=refusals,
        written_days=published.written_days,
        absent_days=published.absent_days,
        row_count=published.row_count,
        publication=published.publication,
    )


def _lane_receipt(  # noqa: PLR0913 - one keyword per outcome the receipt reports
    inputs: LaneSimulationInputs,
    *,
    refusals: tuple[ForecastRefusal, ...],
    written_days: tuple[date, ...],
    absent_days: tuple[date, ...],
    row_count: int,
    publication: PublicationReceipt | None,
) -> LaneForecastReceipt:
    """Build the receipt both the published and the refusal-only path return; neither returns None."""
    return LaneForecastReceipt(
        layer=inputs.layer,
        forecast_module=inputs.stem,
        forecaster=MONTE_CARLO_FORECASTER,
        issued_on=inputs.issued_on,
        forecast_run_id=inputs.forecast_run_id,
        random_seed=inputs.random_seed,
        ensemble_size=inputs.options.simulation_count,
        written_days=written_days,
        absent_days=absent_days,
        row_count=row_count,
        refusals=refusals,
        publication=publication,
    )


__all__ = [
    "ANALOG_ENSEMBLE_ARTIFACT_PREFIX",
    "DEFAULT_SIMULATION_COUNT",
    "MONTE_CARLO_HORIZON_DAYS",
    "MONTE_CARLO_MODULES",
    "STEM_LAYERS",
    "ForecastLaneError",
    "LaneForecastReceipt",
    "MonteCarloDispatchError",
    "MonteCarloOptions",
    "analog_ensemble_artifact_sha256",
    "dispatchable_lanes",
    "load_forecast_module",
    "run_monte_carlo_daily",
    "run_monte_carlo_lane",
]
