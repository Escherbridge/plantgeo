"""The `fire-risk` point read: one cell, one valid day, the median-quantile row and its provenance.

Layer L4. Why a refused cell is answered rather than hidden, and why the newest issue day wins,
live in `AGENTS.md` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

from plantgeo_ml_service.foundation.parquet_paths import BASE_PARTITION_ZOOM
from plantgeo_ml_service.pipeline.fire_risk_gate import NO_ARTIFACT_SENTINEL
from plantgeo_ml_service.planes import refusals
from plantgeo_ml_service.planes.availability_reads import read_lane_availability
from plantgeo_ml_service.planes.partition_reads import (
    day_part_keys,
    nearest_cell_position,
    rows_at_cell_position,
)
from plantgeo_ml_service.planes.wire import (
    ARTIFACT_ABSENT_NO_ARTIFACT,
    ClaimProvenance,
    render_row,
)
from plantgeo_ml_service.warehouse.lanes import forecast_root_kind
from plantgeo_ml_service.warehouse.streams import FIRE_RISK_STREAM, POINT_QUANTILE

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from plantgeo_ml_service.foundation.parquet_paths import PartitionKind
    from plantgeo_ml_service.pipeline.duckdb_session import DuckDbSession
    from plantgeo_ml_service.pipeline.object_store import ReadOnlyObjectStore

#: Asked of the lane contract, never spelled: the root a lane's future lives under is a property
#: of the lane (`layer-lanes.md` section 2), and a literal here is the bug production hit on
#: `96831d8b` -- a release-series lane judged against a root nothing writes.
FORECAST_KIND: Final[PartitionKind] = forecast_root_kind(FIRE_RISK_STREAM)


@dataclass(frozen=True, slots=True)
class FireRiskPoint:
    """One cell's fire-risk answer for one valid day: the score, or the reason there is none."""

    cell_longitude: float
    cell_latitude: float
    valid_day: date
    probability: float | None
    risk_score: float | None
    stratum: str
    refused_reason: str | None
    model_artifact_sha256: str
    issued_on: date
    forecast_run_id: str
    random_seed: int
    ensemble_size: int
    horizon_days: int
    truncated: bool

    def to_wire(self) -> dict[str, object]:
        """Render the point payload; the claim block is added by the route."""
        return render_row(
            {
                "layer": FIRE_RISK_STREAM,
                "cell_longitude": self.cell_longitude,
                "cell_latitude": self.cell_latitude,
                "valid_day": self.valid_day,
                "probability": self.probability,
                "risk_score": self.risk_score,
                "stratum": self.stratum,
                "refused_reason": self.refused_reason,
                "forecast_run_id": self.forecast_run_id,
                "random_seed": self.random_seed,
                "ensemble_size": self.ensemble_size,
                "horizon_days": self.horizon_days,
                "quantile": POINT_QUANTILE,
                "truncated": self.truncated,
            }
        )

    def claim(self) -> ClaimProvenance:
        """Return the claim block: the artifact this row names, or the declared absence of one."""
        if self.model_artifact_sha256 == NO_ARTIFACT_SENTINEL:
            return ClaimProvenance(
                artifact_sha256=None,
                artifact_absent_reason=ARTIFACT_ABSENT_NO_ARTIFACT,
                issued_on=self.issued_on,
            )
        return ClaimProvenance(
            artifact_sha256=self.model_artifact_sha256,
            artifact_absent_reason=None,
            issued_on=self.issued_on,
        )


def read_fire_risk_point(
    store: ReadOnlyObjectStore,
    session: DuckDbSession,
    *,
    longitude: float,
    latitude: float,
    day: date,
) -> FireRiskPoint:
    """Return the nearest base-rung fire-risk cell's answer for one valid day, or refuse by name.

    The availability POINTER settles whether the day is selectable, before any listing is touched.
    Writing a partition does not publish it (FR-4a), so a day answered from the objects under its
    prefix would serve a run the lane never advanced its pointer onto.
    """
    availability = read_lane_availability(store, layer=FIRE_RISK_STREAM, kind=FORECAST_KIND)
    if not availability.covers(day):
        raise refusals.availability_day_not_covered(
            layer=FIRE_RISK_STREAM,
            kind=FORECAST_KIND,
            day=day.isoformat(),
            detail=f"{availability.earliest_terminal_day.isoformat()}..{availability.latest_terminal_day.isoformat()}",
        )
    keys = day_part_keys(store, layer=FIRE_RISK_STREAM, kind=FORECAST_KIND, zoom=BASE_PARTITION_ZOOM, day=day)
    position = nearest_cell_position(session, keys, longitude=longitude, latitude=latitude)
    if position is None:
        raise refusals.cell_not_covered(
            layer=FIRE_RISK_STREAM,
            day=day.isoformat(),
            longitude=longitude,
            latitude=latitude,
        )
    answer = rows_at_cell_position(session, keys, position=position)
    row = _point_row(answer.rows)
    if row is None:
        raise refusals.cell_series_absent(
            layer=FIRE_RISK_STREAM,
            cell_id=f"{position.longitude},{position.latitude}",
            origin=day.isoformat(),
        )
    return FireRiskPoint(
        cell_longitude=position.longitude,
        cell_latitude=position.latitude,
        valid_day=_as_day(row["valid_day"]),
        probability=_as_optional_float(row["probability"]),
        risk_score=_as_optional_float(row["risk_score"]),
        stratum=str(row["stratum"]),
        refused_reason=None if row["refused_reason"] is None else str(row["refused_reason"]),
        model_artifact_sha256=str(row["model_artifact_sha256"]),
        issued_on=_as_day(row["issued_on"]),
        forecast_run_id=str(row["forecast_run_id"]),
        random_seed=int(row["random_seed"]),  # type: ignore[call-overload]  # the schema pins this int64 non-null
        ensemble_size=int(row["ensemble_size"]),  # type: ignore[call-overload]  # the schema pins this int32 non-null
        horizon_days=int(row["horizon_days"]),  # type: ignore[call-overload]  # the schema pins this int16 non-null
        truncated=answer.truncated,
    )


def _point_row(rows: Sequence[Mapping[str, object]]) -> Mapping[str, object] | None:
    """Return the POINT row of the NEWEST issue day, or `None` when the cell carries none.

    A valid day can hold rows from several issue days at several horizons: the newest issue is the
    one a reader means by "today's forecast", and the shortest horizon of that issue is its sharpest.
    `POINT_QUANTILE` is what a deterministic product writes; a row wearing an ensemble fraction on
    this lane was not written by this model and is not answered with.
    """
    point = [row for row in rows if row.get("quantile") == POINT_QUANTILE]
    if not point:
        return None
    return max(point, key=lambda row: (_as_day(row["issued_on"]), -int(row["horizon_days"])))  # type: ignore[call-overload]


def _as_day(value: object) -> date:
    """Narrow one date32 cell, refusing a shape the pinned schema says cannot occur."""
    if not isinstance(value, date):
        raise refusals.serving_fault(operation="fire_risk", fault=f"{type(value).__name__} in a date column")
    return value


def _as_optional_float(value: object) -> float | None:
    """Narrow one nullable float cell without turning an absent score into a number."""
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise refusals.serving_fault(operation="fire_risk", fault=f"{type(value).__name__} in a float column")
    return float(value)


__all__ = ["FireRiskPoint", "read_fire_risk_point"]
