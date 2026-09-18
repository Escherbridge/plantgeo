"""Bounded reads over manifest-closed immutable snapshot product prefixes.

A PRODUCT MAY HAVE A FORWARD EDGE THE MANIFEST CANNOT SEE. Four climate products and the three
soil-wetness lanes were frozen at `CLIMATE_DIRECT_WRITER_START_DAY`, and the five ERA5-Land soil
products at `SOIL_DIRECT_WRITER_START_DAY` -- two different upstreams, two different release
schedules, two different edges. Every day from a product's own edge on is written by that upstream's
direct writer into the ORDINARY lane layout -- `layer=<stream>/kind=observed/zoom=NN/year=/month=/day=/`
-- under a completion marker rather than under this module's receipt chain. `forward_first_day` is the
one boundary between the two: below it a day is proven by the closed manifest and served from
`part_receipts`; at or above it a day is proven exactly as every other lane's day is, by
`day_status_sets`, and served through the ordinary `DuckDbRowReader`. Without the split the products
would go on reporting a frozen last day while the bucket grew past it, which is the failure that
showed the browser a 27-day tail on five products.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.paths import day_prefix
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.serving import WINDOW_ROW_BUDGET, day_status_sets, read_absence_evidence
from agri_data_service.parquet_ops.snapshot_coverage import (
    SnapshotCoverageCache,
    build_snapshot_coverage,
)
from agri_data_service.parquet_ops.snapshot_evidence import (
    clear_snapshot_evidence_cache,
    load_snapshot_evidence,
)
from agri_data_service.parquet_ops.snapshot_product_catalog import (
    _MONTHLY_PART,
    FORWARD_PARTITION_KIND,
    FROZEN_SNAPSHOT_PRODUCTS,
    MAX_SNAPSHOT_READ_PARTS,
    PRODUCT_BY_LAYER,
    SIGNAL_PRODUCT_COLUMNS,
    SNAPSHOT_COVERAGE_PRODUCT_WORKERS,
    SNAPSHOT_ID,
    SNAPSHOT_PRODUCTS,
    SNAPSHOT_ROW_BUDGET,
    SOIL_TEMPERATURE_COLUMNS,
    SOIL_WETNESS_COLUMNS,
    SnapshotProduct,
    _daily_parts,
    _duckdb_type_for_arrow,
    _month_has_day,
    _parts_for_month,
    _snapshot_declared_list_columns,
    _snapshot_product_arrow_schema,
    product_for_layer,
    serves_from_snapshot,
    snapshot_product_columns,
)
from agri_data_service.parquet_ops.snapshot_receipts import _verify_bound_parts
from agri_data_service.parquet_ops.snapshot_store import (
    ForwardAvailability,
    ForwardAvailabilityPort,
    ForwardAvailabilityWithheld,
    ObjectStoreSnapshotStore,
    SnapshotCoverageCensus,
    SnapshotCoverageWithholding,
    SnapshotEvidence,
    SnapshotStore,
    _lineage_digest,
)
from agri_data_service.parquet_ops.warehouse_reader import DuckDbRowReader, RowRead, part_keys_for_day
from agri_data_service.parquet_ops.wire import (
    DayNotWritten,
    DeclaredListCell,
    GovernedAbsenceDay,
    LaneNeverWritten,
    PublishedDay,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.parquet_ops.duckdb_session import ServingSession
    from agri_data_service.parquet_ops.request_params import ReadScope
    from agri_data_service.parquet_ops.wire import (
        DayEnvelope,
        ServedRow,
    )


def resolve_snapshot_product(
    store: SnapshotStore,
    session: ServingSession,
    *,
    scope: ReadScope,
    day: date,
) -> DayEnvelope:
    """Serve one exact observed day from a closed daily-series snapshot product."""
    evidence = load_snapshot_scope_evidence(store, scope)
    return resolve_snapshot_evidence_day(store, session, evidence=evidence, scope=scope, day=day)


def load_snapshot_scope_evidence(store: SnapshotStore, scope: ReadScope) -> SnapshotEvidence:
    """Resolve one observed scope's immutable evidence without admitting a DuckDB session."""
    _require_observed_scope(scope)
    return load_snapshot_evidence(store, product_for_layer(scope.layer))


def resolve_snapshot_evidence_day(
    store: SnapshotStore,
    session: ServingSession,
    *,
    evidence: SnapshotEvidence,
    scope: ReadScope,
    day: date,
) -> DayEnvelope:
    """Serve an exact day after immutable evidence was resolved before session admission."""
    _require_matching_observed_scope(evidence, scope)
    return _resolve_snapshot_evidence(store, session, evidence=evidence, scope=scope, day=day)


def _require_matching_observed_scope(evidence: SnapshotEvidence, scope: ReadScope) -> None:
    """Keep pre-admitted immutable evidence bound to the exact requested product."""
    _require_observed_scope(scope)
    if scope.layer != evidence.product.layer:
        raise faults.snapshot_unpublished(
            layer=scope.layer,
            snapshot_id=evidence.product.snapshot_id,
            detail="pre-admitted snapshot evidence belongs to a different allowlisted layer",
        )


def _require_observed_scope(scope: ReadScope) -> None:
    """Reject unsupported snapshot streams before any immutable evidence read."""
    if scope.kind != "observed":
        raise faults.snapshot_unpublished(
            layer=scope.layer,
            snapshot_id=SNAPSHOT_ID,
            detail="immutable product snapshots publish observed rows only",
        )


def resolve_snapshot_window(
    store: SnapshotStore,
    session: ServingSession,
    *,
    scope: ReadScope,
    first_day: date,
    last_day: date,
) -> tuple[DayEnvelope, ...]:
    """Resolve a bounded closed range while binding the immutable manifest only once."""
    evidence = load_snapshot_scope_evidence(store, scope)
    return resolve_snapshot_evidence_window(
        store,
        session,
        evidence=evidence,
        scope=scope,
        first_day=first_day,
        last_day=last_day,
    )


def resolve_snapshot_evidence_window(  # noqa: PLR0913 - evidence and closed-range bounds are distinct
    store: SnapshotStore,
    session: ServingSession,
    *,
    evidence: SnapshotEvidence,
    scope: ReadScope,
    first_day: date,
    last_day: date,
) -> tuple[DayEnvelope, ...]:
    """Resolve a range after immutable evidence was resolved before session admission."""
    _require_matching_observed_scope(evidence, scope)
    verified_parts: set[str] = set()
    envelopes: list[DayEnvelope] = []
    remaining_rows = WINDOW_ROW_BUDGET
    truncated_from: date | None = None
    for offset in range((last_day - first_day).days + 1):
        day = first_day + timedelta(days=offset)
        budget_was_exhausted = remaining_rows == 0
        envelope = _resolve_snapshot_evidence(
            store,
            session,
            evidence=evidence,
            scope=scope,
            day=day,
            verified_parts=verified_parts,
            row_budget=min(SNAPSHOT_ROW_BUDGET, remaining_rows),
        )
        if isinstance(envelope, PublishedDay):
            remaining_rows -= len(envelope.rows)
            if envelope.truncated and truncated_from is None:
                truncated_from = day
            if budget_was_exhausted and truncated_from is None:
                truncated_from = day
            if truncated_from is not None and day >= truncated_from and not envelope.truncated:
                envelope = PublishedDay(
                    requested_day=envelope.requested_day,
                    served_day=envelope.served_day,
                    rows=envelope.rows,
                    truncated=True,
                )
        envelopes.append(envelope)
    return tuple(envelopes)


def _resolve_snapshot_evidence(  # noqa: PLR0913 - exact evidence, request, and shared-window budget are distinct
    store: SnapshotStore,
    session: ServingSession,
    *,
    evidence: SnapshotEvidence,
    scope: ReadScope,
    day: date,
    verified_parts: set[str] | None = None,
    row_budget: int = SNAPSHOT_ROW_BUDGET,
) -> DayEnvelope:
    # Read off the EVIDENCE's own product rather than through `serves_from_snapshot`: a window may
    # straddle the boundary, so this branch has to hold for a request the route sent down the
    # snapshot path, and it must not depend on the layer being reachable in `PRODUCT_BY_LAYER`.
    forward_first_day = evidence.product.forward_first_day
    if forward_first_day is not None and day >= forward_first_day:
        return _resolve_forward_lane_day(
            store,
            session,
            product=evidence.product,
            scope=scope,
            day=day,
            row_budget=row_budget,
        )
    tier_parts = evidence.parts_by_tier[int(scope.tier)]
    if not tier_parts:
        return LaneNeverWritten(requested_day=day)
    if evidence.product.layout == "daily":
        selected_parts = _daily_parts(tier_parts, day=day, product=evidence.product)
        _verify_bound_parts(store, evidence, selected_parts, verified=verified_parts)
    else:
        selected_parts = _parts_for_month(tier_parts, day.replace(day=1))
        _verify_bound_parts(store, evidence, selected_parts, verified=verified_parts)
        if selected_parts and not _month_has_day(session, selected_parts, day=day):
            selected_parts = ()
    if not selected_parts:
        return DayNotWritten(requested_day=day)
    if len(selected_parts) > MAX_SNAPSHOT_READ_PARTS:
        raise faults.snapshot_unpublished(
            layer=evidence.product.layer,
            snapshot_id=evidence.product.snapshot_id,
            detail=f"exact-day read exceeds the {MAX_SNAPSHOT_READ_PARTS}-part serving limit",
        )
    _verify_exact_schemas(session, evidence, selected_parts)
    rows, truncated = _read_observed_day(
        session,
        keys=selected_parts,
        observed_day=day,
        scope=scope,
        row_budget=row_budget,
    )
    return PublishedDay(
        requested_day=day,
        served_day=day,
        rows=rows,
        truncated=truncated,
    )


def _resolve_forward_lane_day(  # noqa: PLR0913 - the live-lane read needs its own store, session and budget
    store: SnapshotStore,
    session: ServingSession,
    *,
    product: SnapshotProduct,
    scope: ReadScope,
    day: date,
    row_budget: int,
) -> DayEnvelope:
    """Serve one day the LIVE writer owns, through the ordinary layout and never through receipts.

    A day at or after `forward_first_day` was written into `layer=<slug>/kind=observed/zoom=NN/...`
    by a direct writer and is bound by a COMPLETION MARKER, not by the frozen manifest, so there is
    no receipt chain to verify it against. The four-state classification below is exactly
    `serving.resolve_day`'s, applied through the snapshot store's two primitives, and the rows come
    back through the same `DuckDbRowReader` every other lane's day is served by -- so a forward day
    and a lane day of the same shape can never disagree about columns, viewport support or budgets.

    ONE DAY PREFIX IS LISTED, never the tier: the day is named, so the listing that proves it is the
    cheapest one that can, and this path stays affordable on the per-request budget.

    THE ROWS COME BACK IN THE CLOSED HALF'S ORDER. `_read_observed_day` sorts the frozen half by
    `cell_longitude, cell_latitude`; `DuckDbRowReader` sorts every lane's day by source key, which
    for a window straddling `forward_first_day` puts two differently-ordered halves in one answer.
    They are re-sorted here rather than in the reader, because that reader serves twelve other lanes
    whose grain is not a cell. THE TRUNCATION BOUNDARY IS NOT RE-SORTED and cannot be: the reader's
    `LIMIT` selects by ITS order, so a truncated forward day returns a source-key-ordered SUBSET
    presented in lon/lat order. That is why `truncated` rides on the envelope -- a partial day is
    declared partial, and a client may not read its last row as the day's last row.
    """
    keys = tuple(store.iter_keys(day_prefix(product.layer, FORWARD_PARTITION_KIND, scope.tier, day)))
    statuses = day_status_sets(keys, layer=product.layer, kind=FORWARD_PARTITION_KIND, tier=scope.tier)
    if day in statuses.conflict:
        raise faults.day_conflict(layer=product.layer, day=day.isoformat())
    if day in statuses.incomplete:
        raise faults.day_incomplete(layer=product.layer, day=day.isoformat())
    if day in statuses.absent:
        return GovernedAbsenceDay(
            requested_day=day,
            served_day=day,
            absence=read_absence_evidence(store, scope=scope, day=day),
        )
    if day not in statuses.data:
        # `DayNotWritten`, never `LaneNeverWritten`: the closed snapshot below this boundary already
        # proves the lane has published, so the lane-level state cannot honestly be "never written".
        return DayNotWritten(requested_day=day)
    result = DuckDbRowReader(session=session).read_rows(
        RowRead(
            scope=scope,
            keys=part_keys_for_day(keys, layer=product.layer, kind=FORWARD_PARTITION_KIND, tier=scope.tier, day=day),
            row_budget=row_budget,
        )
    )
    return PublishedDay(
        requested_day=day,
        served_day=day,
        rows=_in_closed_half_order(tuple(row for _, row in result.rows)),
        truncated=result.budget_exhausted or result.unpositioned_rows > 0,
    )


#: The grain the closed half is sorted by, and therefore the one the forward half must match.
_SNAPSHOT_ROW_ORDER: Final = ("cell_longitude", "cell_latitude")


def _in_closed_half_order(rows: tuple[ServedRow, ...]) -> tuple[ServedRow, ...]:
    """Sort forward rows by the same key the closed half's SQL orders on, when they carry it.

    A row missing either column is left where it was rather than sorted under a fabricated key --
    every snapshot product declares both, so this is a guard rather than a supported second shape.
    """
    if not rows or any(column not in row for row in rows for column in _SNAPSHOT_ROW_ORDER):
        return rows
    return tuple(sorted(rows, key=lambda row: tuple(_sortable(row[column]) for column in _SNAPSHOT_ROW_ORDER)))


def _sortable(value: object) -> tuple[int, float]:
    """Order a coordinate cell, putting a null last rather than raising on a mixed comparison."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (0, float(value))
    return (1, 0.0)


def _verify_exact_schemas(
    session: ServingSession,
    evidence: SnapshotEvidence,
    keys: Sequence[str],
) -> None:
    expected_schema = _snapshot_product_arrow_schema(evidence.product)
    expected = {field.name: _duckdb_type_for_arrow(field.type) for field in expected_schema}
    for key in keys:
        uri = session.object_uri(key)
        cursor = session.connection.execute(
            "SELECT * FROM read_parquet(?, hive_partitioning=false, union_by_name=false) LIMIT 0",
            [[uri]],
        )
        actual = {str(column[0]): str(column[1]) for column in cursor.description or ()}
        type_mismatches = sorted(
            (name, expected[name], actual[name])
            for name in expected.keys() & actual.keys()
            if actual[name] != expected[name]
        )
        if actual.keys() != expected.keys() or type_mismatches:
            missing = sorted(expected.keys() - actual.keys())
            extra = sorted(actual.keys() - expected.keys())
            raise faults.snapshot_schema_mismatch(
                layer=evidence.product.layer,
                key=key,
                detail=f"missing={missing[:5]}, extra={extra[:5]}, type_mismatches={type_mismatches[:5]}",
            )


def _read_observed_day(
    session: ServingSession,
    *,
    keys: Sequence[str],
    observed_day: date,
    scope: ReadScope,
    row_budget: int,
) -> tuple[tuple[ServedRow, ...], bool]:
    uris = [session.object_uri(key) for key in keys]
    predicates = ["observed_day = ?"]
    parameters: list[object] = [uris, observed_day]
    if scope.bbox is not None:
        predicates.extend(("cell_longitude BETWEEN ? AND ?", "cell_latitude BETWEEN ? AND ?"))
        parameters.extend(
            (
                scope.bbox.west,
                scope.bbox.east,
                scope.bbox.south,
                scope.bbox.north,
            )
        )
    statement = (
        "SELECT * FROM read_parquet(?, hive_partitioning=false, union_by_name=false) WHERE "
        + " AND ".join(predicates)
        + " ORDER BY cell_longitude, cell_latitude LIMIT ?"
    )
    parameters.append(row_budget + 1)
    cursor = session.connection.execute(statement, parameters)
    columns = tuple(description[0] for description in cursor.description or ())
    values = cursor.fetchall()
    truncated = len(values) > row_budget
    declared_list_columns = _snapshot_declared_list_columns(product_for_layer(scope.layer))
    rows: list[ServedRow] = []
    for values_row in values[:row_budget]:
        row = dict(zip(columns, values_row, strict=True))
        for column in declared_list_columns:
            value = row.get(column)
            if isinstance(value, (list, tuple)):
                row[column] = DeclaredListCell(tuple(value))
        rows.append(row)
    return (tuple(rows), truncated)


#: The underscored entries are module-internal helpers the contract tests exercise one refusal at a
#: time; they are listed so the split into sibling modules stays invisible to every caller.
__all__ = [
    "FORWARD_PARTITION_KIND",
    "FROZEN_SNAPSHOT_PRODUCTS",
    "MAX_SNAPSHOT_READ_PARTS",
    "PRODUCT_BY_LAYER",
    "SIGNAL_PRODUCT_COLUMNS",
    "SNAPSHOT_COVERAGE_PRODUCT_WORKERS",
    "SNAPSHOT_ID",
    "SNAPSHOT_PRODUCTS",
    "SOIL_TEMPERATURE_COLUMNS",
    "SOIL_WETNESS_COLUMNS",
    "_MONTHLY_PART",
    "ForwardAvailability",
    "ForwardAvailabilityPort",
    "ForwardAvailabilityWithheld",
    "ObjectStoreSnapshotStore",
    "SnapshotCoverageCache",
    "SnapshotCoverageCensus",
    "SnapshotCoverageWithholding",
    "SnapshotEvidence",
    "SnapshotProduct",
    "SnapshotStore",
    "_duckdb_type_for_arrow",
    "_lineage_digest",
    "build_snapshot_coverage",
    "clear_snapshot_evidence_cache",
    "load_snapshot_evidence",
    "load_snapshot_scope_evidence",
    "product_for_layer",
    "resolve_snapshot_evidence_day",
    "resolve_snapshot_evidence_window",
    "resolve_snapshot_product",
    "resolve_snapshot_window",
    "serves_from_snapshot",
    "snapshot_product_columns",
]
