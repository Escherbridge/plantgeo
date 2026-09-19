"""What a `kind=forecast` lane root needs around its rows: the rung ladder, the bootstrap, the index.

Layer L3, spec FR-4 and FR-4a. Holds the three things both daily forecast runners share: the lattice
arithmetic that derives the coarse rungs, the immutable bootstrap receipt and marker the sibling's
`parquet_ops/availability_coverage.py` reads before it will admit a lane at all, and the terminal
rows a day's availability generation is built from. Rationale, the byte-compatibility argument for
the two bootstrap documents, and why a forecast lane's index is issue-scoped live in
`AGENTS-forecast-lanes.md` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import polars as pl

from plantgeo_ml_service.foundation.canonical import canonical_json, sha256_digest
from plantgeo_ml_service.foundation.parquet_markers import PartitionCompletion
from plantgeo_ml_service.foundation.parquet_paths import (
    BASE_PARTITION_ZOOM,
    ZOOM_TIERS,
    availability_lane_root,
)
from plantgeo_ml_service.pipeline.availability_publisher import (
    build_generation,
    publish_generation,
)

# Re-exported rather than re-declared: the derivation vocabulary moved out when this module passed
# the size ceiling, and every existing importer names it here.
from plantgeo_ml_service.pipeline.forecast_lane_documents import (
    BOOTSTRAP_MARKER_SCHEMA_VERSION,
    SYSTEM_BOOTSTRAP_SCHEMA_VERSION,
    availability_provenance_summary,
    bootstrap_lane,
    bootstrap_marker_payload,
    bootstrap_receipt_payload,
    ensure_lane_bootstrap,
    read_lane_bootstrap_receipt,
)
from plantgeo_ml_service.pipeline.forecast_lane_rungs import (
    FORECAST_TIER_DERIVATIONS,
    TIER_PITCH_MICRO_DEGREES,
    ColumnAggregation,
    ColumnMerge,
    ForecastLaneError,
    GridAggregation,
    RowSelect,
    derive_coarse_rung,
    floor_to_tier,
)
from plantgeo_ml_service.pipeline.object_store import JSON_CONTENT_TYPE, completed_parts_from
from plantgeo_ml_service.warehouse.availability import (
    AvailabilityConfig,
    AvailabilityIdentity,
    AvailabilityRow,
    EvidenceReceipt,
    lane_identity_for,
)
from plantgeo_ml_service.warehouse.lanes import forecast_root_kind, settled_through

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from plantgeo_ml_service.pipeline.availability_publisher import PointerStore, PublicationReceipt
    from plantgeo_ml_service.pipeline.object_store import ObjectStore, ParquetWriteReceipt

#: The reason an expected forecast day carries when the run simply produced nothing for it. A hole
#: in a horizon is a governed absence with a name, never a day the index does not mention: silence
#: is indistinguishable from a day nobody attempted (`layer-lanes.md` section 4).
UNFORECAST_DAY_REASON: Final = "no_forecast_rows_produced"


@dataclass(frozen=True, slots=True)
class ForecastDayReceipt:
    """Everything one forecast day carries, rung by rung — or the reason it holds nothing at all."""

    day: date
    parts_by_rung: Mapping[int, tuple[ParquetWriteReceipt, ...]]
    completions_by_rung: Mapping[int, EvidenceReceipt]
    row_counts_by_rung: Mapping[int, int]
    #: Set exactly when the day was NOT written; the day is then indexed as a governed absence.
    absence_reason: str | None = None

    @property
    def is_absent(self) -> bool:
        """Return whether this day is a governed absence rather than a written partition set."""
        return self.absence_reason is not None


def forecast_source_ceiling(layer: str, *, issued_on: date, horizon_days: int) -> date:
    """Return the newest day a forecast lane can hold: its lane's settled frontier plus the horizon.

    Derived from the PROVIDER's frontier (`settled_through`), never from the wall clock: a ceiling
    taken from today would move on its own and make a lane look fresh because the run is recent
    rather than because the source advanced.
    """
    if horizon_days < 1:
        raise ForecastLaneError(f"a forecast horizon is at least one day, got {horizon_days}")
    return forecast_issue_frontier(layer, issued_on=issued_on) + timedelta(days=horizon_days)


def forecast_issue_frontier(layer: str, *, issued_on: date) -> date:
    """Return the day a run's horizons are counted FROM: the lane's provider frontier, never the run date.

    The other end of `forecast_source_ceiling`. Stated as its own function because the day ladder an
    availability generation owes runs from this day to that ceiling, and deriving it by subtracting
    the horizon from the ceiling is the same arithmetic written twice.
    """
    return settled_through(layer, issued_on)


def write_forecast_day(  # noqa: PLR0913 - one keyword per partition identity field is the contract
    store: ObjectStore,
    frame: pl.DataFrame,
    *,
    layer: str,
    day: date,
    run_id: str,
    completed_at: datetime | None = None,
    absence_reason: str = UNFORECAST_DAY_REASON,
) -> ForecastDayReceipt:
    """Write one forecast day at EVERY rung of the ladder, each with its own completion marker.

    The base rung is written from the rows as produced; every coarser rung is derived from those
    same rows by one lattice binning, so no rung is ever indexed without having been written.

    A day with no rows is NOT an error: it returns a governed-absence receipt carrying
    `absence_reason`, and nothing is written. Raising here would have made a partially forecast run
    fail whole instead of publishing the holes it genuinely has.
    """
    if frame.height == 0:
        return ForecastDayReceipt(
            day=day,
            parts_by_rung={},
            completions_by_rung={},
            row_counts_by_rung={},
            absence_reason=absence_reason,
        )
    moment = completed_at if completed_at is not None else datetime.now(tz=UTC)
    parts: dict[int, tuple[ParquetWriteReceipt, ...]] = {}
    completions: dict[int, EvidenceReceipt] = {}
    row_counts: dict[int, int] = {}
    for zoom in ZOOM_TIERS:
        rung_frame = frame if zoom == BASE_PARTITION_ZOOM else derive_coarse_rung(frame, layer=layer, zoom=zoom)
        part = store.write_partition(
            rung_frame.to_arrow(),
            layer=layer,
            kind=forecast_root_kind(layer),
            zoom=zoom,
            day=day,
        )
        completion = store.write_completion_marker(
            PartitionCompletion(
                part_count=1,
                row_count=part.row_count,
                completed_at=moment,
                run_id=run_id,
                parts=completed_parts_from((part,)),
            ),
            layer=layer,
            kind=forecast_root_kind(layer),
            zoom=zoom,
            day=day,
        )
        parts[zoom] = (part,)
        completions[zoom] = EvidenceReceipt(key=completion.relative_path, sha256=completion.sha256)
        row_counts[zoom] = part.row_count
    return ForecastDayReceipt(
        day=day,
        parts_by_rung=parts,
        completions_by_rung=completions,
        row_counts_by_rung=row_counts,
    )


def write_run_receipt(
    store: ObjectStore,
    payload: Mapping[str, object],
    *,
    layer: str,
    forecast_run_id: str,
) -> EvidenceReceipt:
    """Write one run's immutable source receipt under the lane root, and return the binding to it.

    Every availability row of the run names this object as its `source_receipt`: it is what the
    rows came from, stated once rather than re-asserted per rung.
    """
    body = canonical_json(dict(payload)).encode("utf-8")
    digest = sha256_digest(body)
    relative_path = (
        f"{availability_lane_root(layer, forecast_root_kind(layer))}/availability/runs/run={forecast_run_id}.json"
    )
    store.put_immutable(relative_path, body, content_type=JSON_CONTENT_TYPE)
    return EvidenceReceipt(key=relative_path, sha256=digest)


def terminal_rows_for_day(
    written: ForecastDayReceipt,
    *,
    layer: str,
    source_receipt: EvidenceReceipt,
    source_ceiling: date,
    published_at: datetime,
) -> tuple[AvailabilityRow, ...]:
    """Project one day into the terminal availability row EACH rung of the ladder owes.

    Both terminal states are produced here, and always for the WHOLE ladder: a day mixing states
    across its rungs is refused by the publisher, because it would be selectable at one resolution
    and absent at another.
    """
    identity = lane_identity_for(layer, forecast_root_kind(layer), verified_source_inventory_root=source_receipt.sha256)
    return terminal_rows_for_identity(
        written,
        identity=identity,
        source_receipt=source_receipt,
        source_ceiling=source_ceiling,
        published_at=published_at,
    )


def terminal_rows_for_identity(
    written: ForecastDayReceipt,
    *,
    identity: AvailabilityIdentity,
    source_receipt: EvidenceReceipt,
    source_ceiling: date,
    published_at: datetime,
) -> tuple[AvailabilityRow, ...]:
    """Project one day onto an ALREADY-BUILT lane identity, for a lane whose nature is not daily."""
    if written.is_absent:
        return tuple(
            AvailabilityRow(
                lane=identity.lane,
                product=identity.product,
                nature=identity.nature,
                day=written.day,
                rung=zoom,
                terminal_state="governed_absence",
                row_count=0,
                source_receipt=source_receipt,
                terminal_receipt=source_receipt,
                data_receipts=(),
                completion_receipt=None,
                absence_reason=written.absence_reason,
                source_ceiling=source_ceiling,
                published_at=published_at,
            )
            for zoom in identity.required_rungs
        )
    return tuple(
        AvailabilityRow(
            lane=identity.lane,
            product=identity.product,
            nature=identity.nature,
            day=written.day,
            rung=zoom,
            terminal_state="published",
            row_count=written.row_counts_by_rung[zoom],
            source_receipt=source_receipt,
            terminal_receipt=written.completions_by_rung[zoom],
            data_receipts=tuple(
                sorted(
                    (
                        EvidenceReceipt(key=part.relative_path, sha256=part.sha256)
                        for part in written.parts_by_rung[zoom]
                    ),
                    key=lambda receipt: receipt.key,
                )
            ),
            completion_receipt=written.completions_by_rung[zoom],
            absence_reason=None,
            source_ceiling=source_ceiling,
            published_at=published_at,
        )
        for zoom in identity.required_rungs
    )


def expected_forecast_days(*, issued_on: date, source_ceiling: date) -> tuple[date, ...]:
    """Return every day a run issued on `issued_on` OWES the index, first horizon through ceiling.

    The ladder is over DAYS as well as rungs: a run that forecast three of its fourteen horizons
    owes eleven governed absences, not eleven silences (FR-4a, `layer-lanes.md` section 4).
    """
    if source_ceiling < issued_on:
        raise ForecastLaneError(
            f"a source ceiling of {source_ceiling.isoformat()} precedes the issue frontier {issued_on.isoformat()}; "
            "a forecast lane's ceiling is its frontier plus the horizon, never behind it"
        )
    span = (source_ceiling - issued_on).days
    return tuple(issued_on + timedelta(days=step) for step in range(1, span + 1))


@dataclass(frozen=True, slots=True)
class LanePublication:
    """What one lane's write-then-publish step did, from the first rung to the pointer."""

    layer: str
    written_days: tuple[date, ...]
    absent_days: tuple[date, ...]
    row_count: int
    source_ceiling: date
    bootstrap_receipt: EvidenceReceipt
    publication: PublicationReceipt


def write_and_publish_forecast_rows(  # noqa: PLR0913 - one keyword per publication boundary is the contract
    store: ObjectStore,
    pointers: PointerStore,
    frame: pl.DataFrame,
    *,
    layer: str,
    run_id: str,
    issued_on: date,
    source_receipt: EvidenceReceipt,
    source_ceiling: date,
    moment: datetime,
    absence_reasons: Mapping[date, str] | None = None,
) -> LanePublication:
    """Write every valid day at every rung, bootstrap the lane root if new, then advance the pointer.

    The order is the whole FR-4a rule in one function: partitions, then completion markers, then the
    immutable generation, then the pointer. Nothing is selectable until the last step lands.

    The DAY ladder is materialized as well as the rung ladder: every day from `issued_on + 1` to
    `source_ceiling` is either published or indexed as a governed absence, so a run that forecast
    part of its horizon publishes its holes rather than leaving them out of the index entirely.
    `absence_reasons` names why a particular day is absent; anything unnamed is `UNFORECAST_DAY_REASON`.
    """
    reasons = dict(absence_reasons or {})
    produced = {value for value in frame.get_column("observed_day").to_list() if isinstance(value, date)}
    ladder = sorted(set(expected_forecast_days(issued_on=issued_on, source_ceiling=source_ceiling)) | produced)
    rows: list[AvailabilityRow] = []
    written_days: list[date] = []
    absent_days: list[date] = []
    for day in ladder:
        written = write_forecast_day(
            store,
            frame.filter(pl.col("observed_day") == day),
            layer=layer,
            day=day,
            run_id=run_id,
            completed_at=moment,
            absence_reason=reasons.get(day, UNFORECAST_DAY_REASON),
        )
        rows.extend(
            terminal_rows_for_day(
                written,
                layer=layer,
                source_receipt=source_receipt,
                source_ceiling=source_ceiling,
                published_at=moment,
            )
        )
        (absent_days if written.is_absent else written_days).append(day)
    bootstrap = read_bootstrap_receipt(store, layer=layer)
    if bootstrap is None:
        bootstrap = bootstrap_forecast_lane(
            store,
            rows,
            layer=layer,
            source_receipt=source_receipt,
            source_ceiling=source_ceiling,
            created_at=moment,
        )
    publication = publish_forecast_day(
        store,
        pointers,
        rows,
        layer=layer,
        config=availability_config_for(
            layer=layer,
            source_receipt=source_receipt,
            source_ceiling=source_ceiling,
            bootstrap_receipt=bootstrap,
        ),
        created_at=moment,
    )
    return LanePublication(
        layer=layer,
        written_days=tuple(written_days),
        absent_days=tuple(absent_days),
        row_count=frame.height,
        source_ceiling=source_ceiling,
        bootstrap_receipt=bootstrap,
        publication=publication,
    )


def bootstrap_forecast_lane(  # noqa: PLR0913 - one keyword per bootstrap boundary is the contract
    store: ObjectStore,
    rows: Sequence[AvailabilityRow],
    *,
    layer: str,
    source_receipt: EvidenceReceipt,
    source_ceiling: date,
    created_at: datetime,
) -> EvidenceReceipt:
    """Bootstrap one `kind=forecast` lane root, with the daily-series identity `lane_identity_for` builds."""
    return bootstrap_lane(
        store,
        rows,
        identity=lane_identity_for(
            layer, forecast_root_kind(layer), verified_source_inventory_root=source_receipt.sha256
        ),
        source_receipt=source_receipt,
        source_ceiling=source_ceiling,
        created_at=created_at,
    )


def read_bootstrap_receipt(store: ObjectStore, *, layer: str) -> EvidenceReceipt | None:
    """Return the `kind=forecast` bootstrap receipt of one layer, or `None` when it has none."""
    return read_lane_bootstrap_receipt(store, lane_root=availability_lane_root(layer, forecast_root_kind(layer)))


def publish_forecast_day(  # noqa: PLR0913 - one keyword per publication boundary is the contract
    store: ObjectStore,
    pointers: PointerStore,
    rows: Sequence[AvailabilityRow],
    *,
    layer: str,
    config: AvailabilityConfig,
    created_at: datetime,
) -> PublicationReceipt:
    """Build this issue's generation from its terminal rows and advance the lane pointer onto it."""
    generation = build_generation(config, rows, created_at=created_at)
    return publish_generation(store, pointers, generation, layer=layer, kind=forecast_root_kind(layer))


def availability_config_for(
    *,
    layer: str,
    source_receipt: EvidenceReceipt,
    source_ceiling: date,
    bootstrap_receipt: EvidenceReceipt,
) -> AvailabilityConfig:
    """Bind one lane's identity, its current ceiling and its immutable bootstrap into one config."""
    return AvailabilityConfig(
        identity=lane_identity_for(
            layer, forecast_root_kind(layer), verified_source_inventory_root=source_receipt.sha256
        ),
        source_ceiling=source_ceiling,
        bootstrap_receipt=bootstrap_receipt,
    )


__all__ = [
    "BOOTSTRAP_MARKER_SCHEMA_VERSION",
    "FORECAST_TIER_DERIVATIONS",
    "SYSTEM_BOOTSTRAP_SCHEMA_VERSION",
    "TIER_PITCH_MICRO_DEGREES",
    "UNFORECAST_DAY_REASON",
    "ColumnAggregation",
    "ColumnMerge",
    "ForecastDayReceipt",
    "ForecastLaneError",
    "GridAggregation",
    "LanePublication",
    "RowSelect",
    "availability_config_for",
    "availability_provenance_summary",
    "bootstrap_forecast_lane",
    "bootstrap_lane",
    "bootstrap_marker_payload",
    "bootstrap_receipt_payload",
    "derive_coarse_rung",
    "ensure_lane_bootstrap",
    "expected_forecast_days",
    "floor_to_tier",
    "forecast_issue_frontier",
    "forecast_source_ceiling",
    "publish_forecast_day",
    "read_bootstrap_receipt",
    "read_lane_bootstrap_receipt",
    "terminal_rows_for_day",
    "terminal_rows_for_identity",
    "write_and_publish_forecast_rows",
    "write_forecast_day",
    "write_run_receipt",
]
