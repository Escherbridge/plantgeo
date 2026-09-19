"""One daily turn: fire-risk, then every Monte Carlo lane, then the AnEn signal lane, then weather.

Layer L3. Each lane is isolated, so one lane's refusal never stops the next, and the turn writes ONE
receipt naming what every lane did. Why a turn where every lane refused still exits 0 lives in
`AGENTS-forecast-lanes.md` in this directory.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

from plantgeo_ml_service.foundation.canonical import canonical_json, sha256_digest
from plantgeo_ml_service.foundation.parquet_paths import availability_bootstrap_marker_key, availability_lane_root
from plantgeo_ml_service.method.ml.fire_risk_model import FireRiskModelError, artifact_from_mapping
from plantgeo_ml_service.pipeline.fire_risk_daily import (
    ARTIFACT_PREFIX,
    DEFAULT_RANDOM_SEED,
    forecast_run_id,
    run_fire_risk_daily,
)
from plantgeo_ml_service.pipeline.fire_risk_features import DEFAULT_HORIZON_DAYS, binding_frontier
from plantgeo_ml_service.pipeline.forecast_lane_bootstrap import FORECAST_KIND
from plantgeo_ml_service.pipeline.monte_carlo_daily import dispatchable_lanes, run_monte_carlo_lane
from plantgeo_ml_service.pipeline.object_store import JSON_CONTENT_TYPE, ObjectStoreError, scratch_rooted_store
from plantgeo_ml_service.pipeline.weather_forecast_daily import read_forecast_cells, run_weather_forecast_daily
from plantgeo_ml_service.warehouse.availability import AvailabilityConfig, EvidenceReceipt, lane_identity_for
from plantgeo_ml_service.warehouse.streams import FIRE_RISK_STREAM, SIGNAL_STREAM
from plantgeo_ml_service.warehouse.weather_forecast import WEATHER_FORECAST_STREAM

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from plantgeo_ml_service.method.ml.fire_risk_model import FireRiskArtifact
    from plantgeo_ml_service.pipeline.availability_publisher import PointerStore
    from plantgeo_ml_service.pipeline.monte_carlo_adapters import MonteCarloOptions
    from plantgeo_ml_service.pipeline.object_store import ObjectStore
    from plantgeo_ml_service.pipeline.observed_reader import ObservedReader
    from plantgeo_ml_service.pipeline.weather_forecast_daily import ForecastCell

#: Where a turn's receipt lands, relative to the service's `ml_prefix`.
PREDICT_RECEIPT_PREFIX: Final = "receipts/predict-daily"

PREDICT_RECEIPT_SCHEMA_VERSION: Final = 1

#: The lane slug a turn reports the fire-risk step under, which is the stream it writes.
FIRE_RISK_LANE: Final = FIRE_RISK_STREAM

#: Reported when a step was not attempted at all, as opposed to attempted and refused. "Skipped"
#: is reserved for a state the DEPLOYMENT is correct in: no artifact has been trained yet is a
#: true, temporary fact about a lane that is otherwise wired.
NO_FIRE_RISK_ARTIFACT: Final = "no_fire_risk_artifact_published"

#: NOT a skip. A turn that cannot say which cells to fetch is misconfigured, and reporting that as
#: "skipped" puts a lane that will never run behind a word that reads like a decision. The receipt
#: names the setting that is missing, because "act on this" is the whole difference.
FORECAST_CELLS_UNCONFIGURED: Final = "forecast_cells_unconfigured"

LaneStatus = Literal["written", "refused", "skipped"]


class PredictDailyInfrastructureError(RuntimeError):
    """The turn could not reach the bucket at all. The ONE failure that exits non-zero."""


@dataclass(frozen=True, slots=True)
class LaneOutcome:
    """What one lane's step did, in the vocabulary the turn receipt reports."""

    lane: str
    forecaster: str
    status: LaneStatus
    detail: str
    written_days: tuple[date, ...]
    absent_days: tuple[date, ...]
    row_count: int

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection."""
        return {
            "absent_days": [day.isoformat() for day in self.absent_days],
            "detail": self.detail,
            "forecaster": self.forecaster,
            "lane": self.lane,
            "row_count": self.row_count,
            "status": self.status,
            "written_days": [day.isoformat() for day in self.written_days],
        }


@dataclass(frozen=True, slots=True)
class PredictDailyReceipt:
    """One turn: which lanes ran, what each produced, and where the receipt itself landed."""

    issued_on: date
    prefix: str
    scratch_run: bool
    outcomes: tuple[LaneOutcome, ...]
    sha256: str
    relative_path: str

    @property
    def every_lane_refused(self) -> bool:
        """Return whether no lane wrote anything. A bounded turn, not a failure (owner 2026-09-04)."""
        return all(outcome.status != "written" for outcome in self.outcomes)

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON document, without its own digest and without a wall clock."""
        return {
            "issued_on": self.issued_on.isoformat(),
            "lanes": [outcome.to_wire() for outcome in self.outcomes],
            "prefix": self.prefix,
            "schema_version": PREDICT_RECEIPT_SCHEMA_VERSION,
            "scratch_run": self.scratch_run,
        }

    def to_canonical_json(self) -> str:
        """Return the receipt document plus its own digest, which is what is stored."""
        document = self.to_wire()
        document["sha256"] = self.sha256
        return canonical_json(document)


def run_predict_daily(  # noqa: PLR0913 - one keyword per run boundary is the contract
    store: ObjectStore,
    *,
    issued_on: date,
    dry_run_prefix: str | None = None,
    artifact_store: ObjectStore | None = None,
    reader: ObservedReader,
    pointers: PointerStore,
    lanes: Sequence[str] | None = None,
    forecast_cells: Sequence[ForecastCell] = (),
    forecast_cells_key: str | None = None,
    monte_carlo_options: MonteCarloOptions | None = None,
    random_seed: int = DEFAULT_RANDOM_SEED,
    completed_at: datetime | None = None,
    ml_prefix: str = "ml/",
) -> PredictDailyReceipt:
    """Run every selected lane for one issue day, in order, and write ONE receipt for the turn.

    `artifact_store` is where trained artifacts are READ from, which is the published bucket even on
    a dry run: a scratch prefix holds no artifacts, and re-training one to prove a write is not what
    a dry run is for. Every lane is isolated, so one lane's refusal never stops the next.
    """
    moment = completed_at if completed_at is not None else datetime.now(tz=UTC)
    artifacts = artifact_store if artifact_store is not None else store
    target = scratch_rooted_store(store, dry_run_prefix)
    selected = _selected_lanes(lanes)
    outcomes: list[LaneOutcome] = []
    if FIRE_RISK_LANE in selected:
        outcomes.append(
            _fire_risk_outcome(
                store,
                artifacts,
                reader=reader,
                pointers=pointers,
                issued_on=issued_on,
                dry_run_prefix=dry_run_prefix,
                random_seed=random_seed,
                moment=moment,
                ml_prefix=ml_prefix,
            )
        )
    outcomes.extend(
        _monte_carlo_outcome(
            target,
            pointers,
            reader,
            layer=layer,
            issued_on=issued_on,
            random_seed=random_seed,
            options=monte_carlo_options,
        )
        for layer in _monte_carlo_order(selected)
    )
    if WEATHER_FORECAST_STREAM in selected:
        outcomes.append(
            _weather_outcome(
                store,
                pointers,
                issued_on=issued_on,
                cells=forecast_cells,
                cells_key=forecast_cells_key,
                dry_run_prefix=dry_run_prefix,
                moment=moment,
            )
        )
    return _written_receipt(target, outcomes, issued_on=issued_on, dry_run_prefix=dry_run_prefix, ml_prefix=ml_prefix)


def every_lane() -> tuple[str, ...]:
    """Return every lane a turn runs, in the order it runs them."""
    return (FIRE_RISK_LANE, *_monte_carlo_order(None), WEATHER_FORECAST_STREAM)


def fire_risk_availability(*, issued_on: date, run_identity: str) -> AvailabilityConfig:
    """Return the fire-risk lane's availability identity for one run.

    `verified_source_inventory_root` is the run identity digest, because a model-written lane's
    "source inventory" IS the run that produced it: there is no upstream inventory to verify, and a
    fabricated digest would bind the generation to nothing.
    """
    lane_root = availability_lane_root(FIRE_RISK_STREAM, FORECAST_KIND)
    return AvailabilityConfig(
        identity=lane_identity_for(FIRE_RISK_STREAM, FORECAST_KIND, verified_source_inventory_root=run_identity),
        source_ceiling=binding_frontier(issued_on) + timedelta(days=max(DEFAULT_HORIZON_DAYS)),
        # A PLACEHOLDER by construction: `run_fire_risk_daily` replaces it with the marker actually
        # on the lane, because two bootstraps would be two histories.
        bootstrap_receipt=EvidenceReceipt(key=availability_bootstrap_marker_key(lane_root), sha256=run_identity),
    )


def newest_fire_risk_artifact_key(store: ObjectStore, *, ml_prefix: str = "ml/") -> str | None:
    """Return the newest fire-risk artifact key the bucket holds, or `None` when it holds none."""
    prefix = f"{ml_prefix}{ARTIFACT_PREFIX}/"
    keys = sorted(key for key in store.list_relative_paths(prefix) if key.endswith(".json"))
    return keys[-1] if keys else None


def _selected_lanes(lanes: Sequence[str] | None) -> frozenset[str]:
    """Return the lanes this turn runs, refusing a slug no lane answers to."""
    known = every_lane()
    if lanes is None:
        return frozenset(known)
    unknown = sorted(set(lanes) - set(known))
    if unknown:
        raise ValueError(f"no lane named {unknown[0]!r}; this turn runs {known}")
    return frozenset(lanes)


def _monte_carlo_order(selected: frozenset[str] | None) -> tuple[str, ...]:
    """Return the dispatchable lanes with `signal` LAST, so the AnEn step follows the Monte Carlo ones.

    `signal` is the lane that routes to the Analog Ensemble when an artifact exists, so running it
    last is what makes the turn's order "Monte Carlo, then AnEn" rather than an alphabetical
    accident that happens to read that way today.
    """
    dispatchable = [layer for layer in dispatchable_lanes() if layer != SIGNAL_STREAM]
    ordered = (*sorted(dispatchable), SIGNAL_STREAM)
    if selected is None:
        return ordered
    return tuple(layer for layer in ordered if layer in selected)


def _fire_risk_outcome(  # noqa: PLR0913 - one keyword per run boundary is the contract
    store: ObjectStore,
    artifacts: ObjectStore,
    *,
    reader: ObservedReader,
    pointers: PointerStore,
    issued_on: date,
    dry_run_prefix: str | None,
    random_seed: int,
    moment: datetime,
    ml_prefix: str,
) -> LaneOutcome:
    """Run the fire-risk lane, recording a typed refusal when no artifact has been published.

    A bucket fault while READING this lane's artifact is this lane's refusal, not the turn's: the
    lanes after it reach the same bucket through their own calls and are perfectly able to say so
    themselves, and raising here made one lane's bad read end every other lane's turn.
    """
    try:
        artifact = _loaded_artifact(artifacts, ml_prefix=ml_prefix)
    except Exception as error:  # including ObjectStoreError: one lane's fault stays in one lane
        return _refused(FIRE_RISK_LANE, "fire_risk", error)
    if artifact is None:
        return LaneOutcome(
            lane=FIRE_RISK_LANE,
            forecaster="fire_risk",
            status="skipped",
            detail=NO_FIRE_RISK_ARTIFACT,
            written_days=(),
            absent_days=(),
            row_count=0,
        )
    identity = forecast_run_id(
        artifact_sha256=artifact.sha256,
        issued_on=binding_frontier(issued_on),
        run_date=issued_on,
        random_seed=random_seed,
    )
    try:
        receipt = run_fire_risk_daily(
            reader,
            store,
            artifact,
            run_date=issued_on,
            dry_run_prefix=dry_run_prefix,
            random_seed=random_seed,
            completed_at=moment,
            availability=fire_risk_availability(issued_on=issued_on, run_identity=identity),
            pointers=pointers,
            ml_prefix=ml_prefix,
        )
    except Exception as error:
        return _refused(FIRE_RISK_LANE, "fire_risk", error)
    return LaneOutcome(
        lane=FIRE_RISK_LANE,
        forecaster="fire_risk",
        status="written" if receipt.partitions else "refused",
        detail=receipt.availability_outcome or "no_availability_publication",
        written_days=tuple(sorted({partition.day for partition in receipt.partitions})),
        absent_days=receipt.absent_days,
        row_count=receipt.scored_row_count,
    )


def _monte_carlo_outcome(  # noqa: PLR0913 - one keyword per run boundary is the contract
    store: ObjectStore,
    pointers: PointerStore,
    reader: ObservedReader,
    *,
    layer: str,
    issued_on: date,
    random_seed: int,
    options: MonteCarloOptions | None,
) -> LaneOutcome:
    """Forecast one dispatchable lane, recording its own refusal rather than raising it."""
    try:
        receipt = run_monte_carlo_lane(
            store, pointers, reader, layer=layer, issued_on=issued_on, random_seed=random_seed, options=options
        )
    except Exception as error:
        return _refused(layer, "monte_carlo", error)
    detail = "not_published" if receipt.publication is None else receipt.publication.outcome
    return LaneOutcome(
        lane=layer,
        forecaster=receipt.forecaster,
        status="written" if receipt.written_days else "refused",
        detail=detail if receipt.written_days else _first_refusal_reason(receipt.refusals),
        written_days=receipt.written_days,
        absent_days=receipt.absent_days,
        row_count=receipt.row_count,
    )


def _weather_outcome(  # noqa: PLR0913 - one keyword per run boundary is the contract
    store: ObjectStore,
    pointers: PointerStore,
    *,
    issued_on: date,
    cells: Sequence[ForecastCell],
    cells_key: str | None,
    dry_run_prefix: str | None,
    moment: datetime,
) -> LaneOutcome:
    """Admit one provider run, or REFUSE loudly when this deployment names no cell inventory."""
    try:
        resolved = _resolved_cells(store, cells=cells, cells_key=cells_key)
    except Exception as error:  # a broken inventory is this lane's refusal, under its own code
        return _refused(WEATHER_FORECAST_STREAM, "provider_run", error)
    if not resolved:
        return LaneOutcome(
            lane=WEATHER_FORECAST_STREAM,
            forecaster="provider_run",
            status="refused",
            detail=FORECAST_CELLS_UNCONFIGURED,
            written_days=(),
            absent_days=(),
            row_count=0,
        )
    try:
        receipt = run_weather_forecast_daily(
            store, issued_on, resolved, dry_run_prefix=dry_run_prefix, pointers=pointers, now=moment
        )
    except Exception as error:
        return _refused(WEATHER_FORECAST_STREAM, "provider_run", error)
    return LaneOutcome(
        lane=WEATHER_FORECAST_STREAM,
        forecaster="provider_run",
        status="written" if receipt.base_row_count else "refused",
        detail="not_published" if receipt.publication is None else receipt.publication.outcome,
        written_days=(receipt.issue_date,) if receipt.base_row_count else (),
        absent_days=() if receipt.base_row_count else (receipt.issue_date,),
        row_count=receipt.base_row_count,
    )


def _resolved_cells(
    store: ObjectStore, *, cells: Sequence[ForecastCell], cells_key: str | None
) -> tuple[ForecastCell, ...]:
    """Return the cells this turn fetches: the ones passed in, else the configured inventory."""
    if cells:
        return tuple(cells)
    if cells_key is None:
        return ()
    return read_forecast_cells(store.read_only(), key=cells_key)


def _loaded_artifact(store: ObjectStore, *, ml_prefix: str) -> FireRiskArtifact | None:
    """Return the newest published fire-risk artifact, or `None` when none has been published."""
    key = newest_fire_risk_artifact_key(store, ml_prefix=ml_prefix)
    if key is None:
        return None
    payload = store.read_object(key)
    if payload is None:
        raise FireRiskModelError(f"the listed artifact at {key!r} is no longer readable")
    return artifact_from_mapping(json.loads(payload.decode("utf-8")))


def _refused(lane: str, forecaster: str, error: Exception) -> LaneOutcome:
    """Record one lane's failure as this turn's refusal, under a stable CODE for the fault.

    The message is dropped rather than embedded: a receipt is compared against other receipts, and
    a detail carrying a key, a row count or a wrapped provider string is a field no two turns agree
    on even when they failed the same way. The full exception is logged where a turn is debugged.
    """
    return LaneOutcome(
        lane=lane,
        forecaster=forecaster,
        status="refused",
        detail=error_code_for(error),
        written_days=(),
        absent_days=(),
        row_count=0,
    )


def error_code_for(error: Exception) -> str:
    """Return the snake_case code one exception type is reported as on a receipt."""
    name = type(error).__name__.removesuffix("Error")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower() + "_error"


def _first_refusal_reason(refusals: Sequence[object]) -> str:
    """Return the first refusal reason a lane reported, or a named default when it reported none."""
    for refusal in refusals:
        reason = getattr(refusal, "reason", None)
        if isinstance(reason, str):
            return reason
    return "no_rows_produced"


def _written_receipt(
    store: ObjectStore,
    outcomes: Sequence[LaneOutcome],
    *,
    issued_on: date,
    dry_run_prefix: str | None,
    ml_prefix: str,
) -> PredictDailyReceipt:
    """Digest the turn, file it under the day it ran, and return what was stored."""
    draft = PredictDailyReceipt(
        issued_on=issued_on,
        prefix=store.prefix,
        scratch_run=dry_run_prefix is not None,
        outcomes=tuple(outcomes),
        sha256="",
        relative_path="",
    )
    digest = sha256_digest(canonical_json(draft.to_wire()))
    receipt = PredictDailyReceipt(
        issued_on=issued_on,
        prefix=store.prefix,
        scratch_run=dry_run_prefix is not None,
        outcomes=tuple(outcomes),
        sha256=digest,
        relative_path=f"{ml_prefix}{PREDICT_RECEIPT_PREFIX}/{issued_on.isoformat()}/{digest}.json",
    )
    try:
        store.put_immutable(
            receipt.relative_path, receipt.to_canonical_json().encode("utf-8"), content_type=JSON_CONTENT_TYPE
        )
    except ObjectStoreError as error:
        raise PredictDailyInfrastructureError(f"the turn receipt could not be written: {error}") from error
    return receipt


__all__ = [
    "FORECAST_CELLS_UNCONFIGURED",
    "NO_FIRE_RISK_ARTIFACT",
    "PREDICT_RECEIPT_PREFIX",
    "LaneOutcome",
    "PredictDailyInfrastructureError",
    "PredictDailyReceipt",
    "error_code_for",
    "every_lane",
    "fire_risk_availability",
    "newest_fire_risk_artifact_key",
    "run_predict_daily",
]
