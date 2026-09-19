"""The daily fire-risk run: build features, score them, write every rung, then publish availability.

Layer L3. The FR-5 publication gate, why a coarse rung is a rung-SELECT rather than an average, and
why the prediction receipt carries no wall clock live in `AGENTS-fire-risk.md` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import polars as pl

from plantgeo_ml_service.foundation.canonical import canonical_json, sha256_digest
from plantgeo_ml_service.method.ml.fire_risk_model import FireRiskArtifact, predict
from plantgeo_ml_service.pipeline.availability_publisher import build_generation, publish_generation
from plantgeo_ml_service.pipeline.fire_risk_features import (
    DEFAULT_HORIZON_DAYS,
    FEATURE_SET_VERSION,
    FIRE_RISK_FEATURE_NAMES,
    FireRiskFeatureFrame,
    binding_frontier,
    build_fire_risk_features,
)

# Re-exported rather than re-declared: the FR-5 gate moved out when this module passed the size
# ceiling, and every existing importer names its errors and its sentinel here.
from plantgeo_ml_service.pipeline.fire_risk_gate import (
    NO_ARTIFACT_SENTINEL,
    SCRATCH_PREFIX_ROOT,
    FireRiskDailyError,
    FireRiskPublicationGateError,
    artifact_sha,
    target_store,
    verified_cleared_strata,
)
from plantgeo_ml_service.pipeline.forecast_lane_bootstrap import (
    FORECAST_KIND,
    ensure_lane_bootstrap,
    expected_forecast_days,
    terminal_rows_for_identity,
    write_forecast_day,
    write_run_receipt,
)
from plantgeo_ml_service.pipeline.object_store import JSON_CONTENT_TYPE
from plantgeo_ml_service.warehouse.streams import FIRE_RISK_STREAM

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime

    from plantgeo_ml_service.pipeline.availability_publisher import PointerStore, PublicationReceipt
    from plantgeo_ml_service.pipeline.forecast_lane_bootstrap import ForecastDayReceipt
    from plantgeo_ml_service.pipeline.object_store import ObjectStore
    from plantgeo_ml_service.pipeline.observed_reader import ObservedReader
    from plantgeo_ml_service.warehouse.availability import AvailabilityConfig, AvailabilityRow, EvidenceReceipt

#: One reported quantile. The schema carries `quantile` as a number, so the median is 0.5 and not the
#: string "p50"; a reader that wants the label renders it from the number.
MEDIAN_QUANTILE: Final = 0.5

#: The model is a closed-form logistic evaluation, so nothing here draws. The seed is still recorded
#: on every row, because a lane whose provenance columns are conditional is a lane a reader must
#: branch on, and the day this model gains an ensemble the column must already be there.
DEFAULT_RANDOM_SEED: Final = 0
ENSEMBLE_SIZE: Final = 1

#: Where a prediction receipt lands, relative to the service's `ml_prefix`.
PREDICTION_RECEIPT_PREFIX: Final = "receipts/fire-risk"

#: Where an artifact is stored, relative to `ml_prefix`.
ARTIFACT_PREFIX: Final = "artifacts/fire-risk"

PREDICTION_RECEIPT_SCHEMA_VERSION: Final = 2

#: Column order written to a `fire-risk` partition. `write_partition` re-selects and sorts through
#: the registry schema anyway; this is what the frame is built with.
_PARTITION_COLUMNS: Final[tuple[str, ...]] = (
    "cell_longitude",
    "cell_latitude",
    "valid_day",
    "probability",
    "risk_score",
    "stratum",
    "refused_reason",
    "model_artifact_sha256",
    "forecast_run_id",
    "random_seed",
    "ensemble_size",
    "horizon_days",
    "issued_on",
    "quantile",
)


@dataclass(frozen=True, slots=True)
class PartitionRecord:
    """One written rung of one valid day, as the writer measured it."""

    day: date
    zoom: int
    key: str
    relative_path: str
    row_count: int
    sha256: str

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection."""
        return {
            "day": self.day.isoformat(),
            "key": self.key,
            "relative_path": self.relative_path,
            "row_count": self.row_count,
            "sha256": self.sha256,
            "zoom": self.zoom,
        }


@dataclass(frozen=True, slots=True)
class FireRiskDailyReceipt:
    """What one run produced: the partitions, the absences, the gate decision and the refusal census."""

    #: The BINDING FRONTIER horizons were counted from, not the calendar day the run happened.
    issued_on: date
    #: The calendar day the run happened on, recorded separately from the day it was issued FROM.
    run_date: date
    forecast_run_id: str
    artifact_sha256: str
    prefix: str
    scratch_run: bool
    cleared_strata: tuple[str, ...]
    withheld_strata: tuple[str, ...]
    partitions: tuple[PartitionRecord, ...]
    absent_days: tuple[date, ...]
    refusal_counts: dict[str, int]
    scored_row_count: int
    features: dict[str, object]
    availability_outcome: str | None
    relative_path: str

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON document, without its own digest and without a wall clock."""
        return {
            "absent_days": [day.isoformat() for day in self.absent_days],
            "artifact_sha256": self.artifact_sha256,
            "availability_outcome": self.availability_outcome,
            "cleared_strata": list(self.cleared_strata),
            "features": self.features,
            "forecast_run_id": self.forecast_run_id,
            "issued_on": self.issued_on.isoformat(),
            "partitions": [partition.to_wire() for partition in self.partitions],
            "prefix": self.prefix,
            "refusal_counts": dict(sorted(self.refusal_counts.items())),
            "run_date": self.run_date.isoformat(),
            "schema_version": PREDICTION_RECEIPT_SCHEMA_VERSION,
            "scored_row_count": self.scored_row_count,
            "scratch_run": self.scratch_run,
            "withheld_strata": list(self.withheld_strata),
        }

    def to_canonical_json(self) -> str:
        """Return the receipt document plus its own digest, which is what is stored."""
        document = self.to_wire()
        document["sha256"] = sha256_digest(canonical_json(document))
        return canonical_json(document)


@dataclass(frozen=True, slots=True)
class _RunIdentity:
    """The four facts that make one run's rows distinguishable from every other run's."""

    artifact_sha256: str
    issued_on: date
    run_date: date
    random_seed: int


def run_fire_risk_daily(  # noqa: PLR0913 - one keyword per run identity field is the contract
    reader: ObservedReader,
    store: ObjectStore,
    artifact: FireRiskArtifact | None,
    *,
    run_date: date,
    horizons: Sequence[int] = DEFAULT_HORIZON_DAYS,
    dry_run_prefix: str | None = None,
    random_seed: int = DEFAULT_RANDOM_SEED,
    completed_at: datetime,
    availability: AvailabilityConfig | None = None,
    pointers: PointerStore | None = None,
    ml_prefix: str = "ml/",
) -> FireRiskDailyReceipt:
    """Score one run date, write every rung of every horizon day, then publish the lane's availability.

    Horizons are counted from `binding_frontier(run_date)`, not from `run_date`: see that function.
    """
    target = target_store(store, dry_run_prefix)
    scratch_run = dry_run_prefix is not None
    if artifact is None and not scratch_run:
        raise FireRiskPublicationGateError(
            "a run with no artifact can only refuse every row, and a lane of refusals on a real prefix is "
            f"indistinguishable from a lane that measured no risk; FR-5 permits it only under {SCRATCH_PREFIX_ROOT!r}"
        )
    # A scratch run withholds NOTHING and clears nothing: the FR-5 gate is about what reaches a real
    # prefix, and a proving run that could not score would prove nothing. It also does not claim any
    # cleared stratum, because it never verified the receipt that would have cleared one.
    cleared = () if (artifact is None or scratch_run) else verified_cleared_strata(store, artifact)
    withheld = (
        frozenset() if (artifact is None or scratch_run) else frozenset(artifact.scoreable_strata) - frozenset(cleared)
    )
    features = build_fire_risk_features(reader, run_date=run_date, horizons=horizons)
    identity = _RunIdentity(
        artifact_sha256=artifact_sha(artifact),
        issued_on=features.issued_on,
        run_date=run_date,
        random_seed=random_seed,
    )
    run_id = forecast_run_id(
        artifact_sha256=identity.artifact_sha256,
        issued_on=identity.issued_on,
        run_date=run_date,
        random_seed=random_seed,
    )
    scored = _scored_frame(features, artifact=artifact, withheld=withheld, run_id=run_id, random_seed=random_seed)
    written = _write_every_day(
        target,
        scored,
        identity=identity,
        run_id=run_id,
        horizons=horizons,
        completed_at=completed_at,
        availability=availability,
    )
    publication = _publish(target, pointers, availability=availability, written=written, created_at=completed_at)
    receipt = FireRiskDailyReceipt(
        issued_on=identity.issued_on,
        run_date=run_date,
        forecast_run_id=run_id,
        artifact_sha256=identity.artifact_sha256,
        prefix=target.prefix,
        scratch_run=scratch_run,
        cleared_strata=tuple(cleared),
        withheld_strata=tuple(sorted(withheld)),
        partitions=written.partitions,
        absent_days=written.absent_days,
        refusal_counts=_refusal_counts(scored),
        scored_row_count=int(scored.get_column("probability").is_not_null().sum()),
        features=features.to_wire(),
        availability_outcome=None if publication is None else publication.outcome,
        relative_path=prediction_receipt_path(run_date, run_id, ml_prefix=ml_prefix),
    )
    target.put_immutable(
        receipt.relative_path, receipt.to_canonical_json().encode("utf-8"), content_type=JSON_CONTENT_TYPE
    )
    return receipt


def forecast_run_id(*, artifact_sha256: str, issued_on: date, run_date: date, random_seed: int) -> str:
    """Return the deterministic run identity every row of this run carries.

    All four facts, because any two runs that agree on all of them produced the same rows: the
    artifact (or its declared absence), the frontier the horizons were counted from, the calendar
    day that frontier was resolved on, and the seed.
    """
    return sha256_digest(
        canonical_json(
            {
                "artifact_sha256": artifact_sha256,
                "issued_on": issued_on.isoformat(),
                "random_seed": random_seed,
                "run_date": run_date.isoformat(),
            }
        )
    )


def prediction_receipt_path(run_date: date, run_id: str, *, ml_prefix: str = "ml/") -> str:
    """Return the relative object key of one run's prediction receipt, filed under the day it ran."""
    return f"{ml_prefix}{PREDICTION_RECEIPT_PREFIX}/{run_date.isoformat()}/{run_id}.json"


def artifact_path(artifact_sha256: str, *, ml_prefix: str = "ml/") -> str:
    """Return the relative object key one artifact digest is stored under."""
    return f"{ml_prefix}{ARTIFACT_PREFIX}/{artifact_sha256}.json"


# --- Scoring and partition assembly ------------------------------------------------------------------


def _scored_frame(
    features: FireRiskFeatureFrame,
    *,
    artifact: FireRiskArtifact | None,
    withheld: frozenset[str],
    run_id: str,
    random_seed: int,
) -> pl.DataFrame:
    """Return the partition-shaped frame: one scored or refused row per cell and valid day."""
    frame = features.frame
    if frame.height == 0:
        return _empty_partition_frame()
    batch = predict(
        artifact,
        frame.select(FIRE_RISK_FEATURE_NAMES).to_numpy().astype(float),
        strata=frame.get_column("stratum").to_list(),
        feature_names=FIRE_RISK_FEATURE_NAMES,
        feature_set_version=FEATURE_SET_VERSION,
        withheld_strata=withheld,
    )
    return (
        frame.select("cell_longitude", "cell_latitude", "valid_day", "horizon_days", "issued_on")
        .with_columns(
            pl.Series("probability", list(batch.probability), dtype=pl.Float64),
            pl.Series("risk_score", list(batch.risk_score), dtype=pl.Float64),
            pl.Series("stratum", list(batch.stratum), dtype=pl.String),
            pl.Series("refused_reason", list(batch.refused_reason), dtype=pl.String),
            pl.lit(batch.artifact_sha256, dtype=pl.String).alias("model_artifact_sha256"),
            pl.lit(run_id, dtype=pl.String).alias("forecast_run_id"),
            pl.lit(random_seed, dtype=pl.Int64).alias("random_seed"),
            pl.lit(ENSEMBLE_SIZE, dtype=pl.Int32).alias("ensemble_size"),
            pl.lit(MEDIAN_QUANTILE, dtype=pl.Float64).alias("quantile"),
        )
        .select(_PARTITION_COLUMNS)
    )


@dataclass(frozen=True, slots=True)
class _WrittenDays:
    """What the day ladder produced: the partitions, the holes, and the rows that index both."""

    partitions: tuple[PartitionRecord, ...]
    absent_days: tuple[date, ...]
    rows: tuple[AvailabilityRow, ...]
    source_receipt: EvidenceReceipt | None


def _write_every_day(  # noqa: PLR0913 - a ladder names its run, its clock and its availability
    store: ObjectStore,
    scored: pl.DataFrame,
    *,
    identity: _RunIdentity,
    run_id: str,
    horizons: Sequence[int],
    completed_at: datetime,
    availability: AvailabilityConfig | None,
) -> _WrittenDays:
    """Write EVERY day of the horizon ladder, publishing a governed absence for the ones with no rows.

    The full ladder, not just the days the model happened to score: a horizon the run produced
    nothing for is a hole with a name, and a hole the index does not mention is indistinguishable
    from a day nobody attempted (FR-4a).
    """
    ceiling = _source_ceiling(identity, horizons=horizons, availability=availability)
    produced = {value for value in scored.get_column("valid_day").to_list() if value is not None}
    ladder = sorted(set(expected_forecast_days(issued_on=identity.issued_on, source_ceiling=ceiling)) | produced)
    _refuse_days_past_ceiling(produced, ceiling=ceiling)
    source_receipt = _written_run_receipt(store, identity=identity, run_id=run_id, availability=availability)
    partitions: list[PartitionRecord] = []
    absent_days: list[date] = []
    rows: list[AvailabilityRow] = []
    for day in ladder:
        written = write_forecast_day(
            store,
            scored.filter(pl.col("valid_day") == day),
            layer=FIRE_RISK_STREAM,
            day=day,
            run_id=run_id,
            completed_at=completed_at,
        )
        if written.is_absent:
            absent_days.append(day)
        else:
            partitions.extend(_partition_records(written))
        if availability is not None and source_receipt is not None:
            rows.extend(
                terminal_rows_for_identity(
                    written,
                    identity=availability.identity,
                    source_receipt=source_receipt,
                    source_ceiling=ceiling,
                    published_at=completed_at,
                )
            )
    return _WrittenDays(
        partitions=tuple(partitions),
        absent_days=tuple(absent_days),
        rows=tuple(rows),
        source_receipt=source_receipt,
    )


def _source_ceiling(
    identity: _RunIdentity, *, horizons: Sequence[int], availability: AvailabilityConfig | None
) -> date:
    """Return the newest day this run may index: the frontier plus its longest horizon."""
    horizon_ceiling = identity.issued_on + timedelta(days=max(horizons) if horizons else 1)
    if availability is None:
        return horizon_ceiling
    if availability.source_ceiling < horizon_ceiling:
        raise FireRiskDailyError(
            f"horizon {max(horizons)} reaches {horizon_ceiling.isoformat()}, past the generation's source ceiling "
            f"{availability.source_ceiling.isoformat()}; raise the ceiling before publishing the horizon"
        )
    return availability.source_ceiling


def _refuse_days_past_ceiling(produced: set[date], *, ceiling: date) -> None:
    """Refuse a scored day the generation could not index, rather than dropping it silently."""
    beyond = sorted(day for day in produced if day > ceiling)
    if beyond:
        raise FireRiskDailyError(
            f"valid day {beyond[0].isoformat()} is past the generation's source ceiling {ceiling.isoformat()}"
        )


def _written_run_receipt(
    store: ObjectStore,
    *,
    identity: _RunIdentity,
    run_id: str,
    availability: AvailabilityConfig | None,
) -> EvidenceReceipt | None:
    """Write the immutable run document every availability row of this run cites as its source.

    The SOURCE of a fire-risk day is this run, not the artifact: an availability row citing the
    artifact key had to invent a digest for a run that had no artifact, and an empty digest is not
    a receipt. The artifact is named INSIDE this document, and on every data row.
    """
    if availability is None:
        return None
    return write_run_receipt(
        store,
        {
            "artifact_key": artifact_path(identity.artifact_sha256),
            "artifact_sha256": identity.artifact_sha256,
            "forecast_run_id": run_id,
            "issued_on": identity.issued_on.isoformat(),
            "lane": FIRE_RISK_STREAM,
            "random_seed": identity.random_seed,
            "run_date": identity.run_date.isoformat(),
        },
        layer=FIRE_RISK_STREAM,
        forecast_run_id=run_id,
    )


def _partition_records(written: ForecastDayReceipt) -> tuple[PartitionRecord, ...]:
    """Project one written day's rung receipts into the records the prediction receipt carries."""
    return tuple(
        PartitionRecord(
            day=written.day,
            zoom=zoom,
            key=part.key,
            relative_path=part.relative_path,
            row_count=part.row_count,
            sha256=part.sha256,
        )
        for zoom in sorted(written.parts_by_rung)
        for part in written.parts_by_rung[zoom]
    )


def _empty_partition_frame() -> pl.DataFrame:
    """Return a typed, zero-row partition frame so a run with no cells has the same columns."""
    return pl.DataFrame(
        schema={
            "cell_longitude": pl.Float64,
            "cell_latitude": pl.Float64,
            "valid_day": pl.Date,
            "probability": pl.Float64,
            "risk_score": pl.Float64,
            "stratum": pl.String,
            "refused_reason": pl.String,
            "model_artifact_sha256": pl.String,
            "forecast_run_id": pl.String,
            "random_seed": pl.Int64,
            "ensemble_size": pl.Int32,
            "horizon_days": pl.Int64,
            "issued_on": pl.Date,
            "quantile": pl.Float64,
        }
    )


def _refusal_counts(scored: pl.DataFrame) -> dict[str, int]:
    """Return how many rows each refusal reason accounts for, so a silent whole-plane refusal is loud."""
    if scored.height == 0:
        return {}
    census = scored.group_by("refused_reason").agg(pl.len().alias("row_count"))
    return {
        str(row["refused_reason"]): int(row["row_count"])
        for row in census.iter_rows(named=True)
        if row["refused_reason"] is not None
    }


# --- Availability --------------------------------------------------------------------------------


def _publish(
    store: ObjectStore,
    pointers: PointerStore | None,
    *,
    availability: AvailabilityConfig | None,
    written: _WrittenDays,
    created_at: datetime,
) -> PublicationReceipt | None:
    """Bootstrap the lane root if it is new, then publish this run's generation and advance the pointer."""
    if availability is None or pointers is None or not written.rows or written.source_receipt is None:
        return None
    bootstrap = ensure_lane_bootstrap(
        store,
        written.rows,
        identity=availability.identity,
        source_receipt=written.source_receipt,
        source_ceiling=availability.source_ceiling,
        created_at=created_at,
    )
    # The marker ON THE LANE wins over whatever the caller declared: it is the immutable first
    # generation every later pointer binds, and two bootstraps are two histories.
    config = replace(availability, bootstrap_receipt=bootstrap)
    generation = build_generation(config, written.rows, created_at=created_at)
    return publish_generation(store, pointers, generation, layer=FIRE_RISK_STREAM, kind=FORECAST_KIND)


__all__ = [
    "ARTIFACT_PREFIX",
    "DEFAULT_RANDOM_SEED",
    "ENSEMBLE_SIZE",
    "MEDIAN_QUANTILE",
    "NO_ARTIFACT_SENTINEL",
    "PREDICTION_RECEIPT_PREFIX",
    "SCRATCH_PREFIX_ROOT",
    "FireRiskDailyError",
    "FireRiskDailyReceipt",
    "FireRiskPublicationGateError",
    "PartitionRecord",
    "artifact_path",
    "binding_frontier",
    "forecast_run_id",
    "prediction_receipt_path",
    "run_fire_risk_daily",
]
