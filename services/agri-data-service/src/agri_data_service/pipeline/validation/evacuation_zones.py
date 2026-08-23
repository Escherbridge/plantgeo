"""Reconcile the evacuation-zones lane's written Parquet snapshot against Oregon OEM's live feed.

Layer L2 pipeline: may import `foundation`, `warehouse`; needs the network (httpx, via
`agri_data_service.ingest.evacuation_zones`), which is exactly why this cannot live in `method`.
May NOT import `method`, `planes`, or `interface`. See `docs/lanes/evacuation-zones.md` section 6
for what is honestly checkable given a current-state-only source, and
`conductor/code_styleguides/layer-lanes.md` section 4 for the "never against local intermediate
state" rule this module exists to satisfy.

Oregon's feed answers only "what does the upstream currently say" -- it has no cohort or release
structure and no record of a past day's state (`docs/lanes/evacuation-zones.md` section 3). A real
live comparison is therefore only possible when the snapshot under test IS today's; every other day
gets internal-consistency checks only, and this module says so rather than skipping silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import polars as pl

from agri_data_service.foundation.parquet.paths import (
    partition_day_statuses,
    stream_prefix,
    validate_partition_kind,
)
from agri_data_service.ingest.evacuation_zones import EVACUATION_ZONES_BOUNDS, fetch_evacuation_zones
from agri_data_service.ingest.http import upstream_client
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.warehouse.schemas.evacuation_zones import (
    EVACUATION_ZONES_PRODUCER,
    EVACUATION_ZONES_SCHEMA,
    EVACUATION_ZONES_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus, PartitionKind
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

# Pinned so scanning a day with zero part files (a real gap, or before this lane's first snapshot)
# returns a correctly-typed zero-row frame rather than `polars.exceptions.ComputeError` on an empty
# glob. Duplicated from `planes/evacuation_zones.py` deliberately: `pipeline` may not import
# `planes` (`test_layer_import_contract.py`), and a lane's layer files stay self-sufficient rather
# than reaching sideways for a shared helper outside its own file (`layer-lanes.md` section 1).
# `pl.from_arrow` is typed as returning `DataFrame | Series`; a `pa.Table` input always yields a
# `DataFrame`, and the assert narrows that for mypy rather than asserting it away with a cast.
_evacuation_zones_empty_frame = pl.from_arrow(EVACUATION_ZONES_SCHEMA.arrow_schema.empty_table())
assert isinstance(_evacuation_zones_empty_frame, pl.DataFrame)
_EVACUATION_ZONES_POLARS_SCHEMA: Final = _evacuation_zones_empty_frame.schema


@dataclass(frozen=True, slots=True)
class EvacuationLevelMismatch:
    """One area whose evacuation level differs between the written snapshot and Oregon's live feed.

    A mismatch can be real drift during an active incident rather than a defect -- two polls
    minutes apart can legitimately disagree (`docs/lanes/evacuation-zones.md` section 6) -- so this
    is reported, never treated by itself as proof the write path is wrong.
    """

    global_id: str
    written_level: int | None
    live_level: int | None


@dataclass(frozen=True, slots=True)
class EvacuationZonesValidationReport:
    """One reconciliation result for `snapshot_day`: checked against the source system when the
    source can honestly answer, and against itself when it cannot.
    """

    lane: str
    snapshot_day: date
    today: date
    partition_status: PartitionDayStatus
    written_row_count: int
    identity_integrity_ok: bool
    grain_integrity_ok: bool
    live_comparison_performed: bool
    live_comparison_skipped_reason: str | None
    zones_missing_from_write: frozenset[str]
    zones_retired_upstream: frozenset[str]
    level_mismatches: tuple[EvacuationLevelMismatch, ...]
    source_response_summary: str
    ok: bool
    failure_reasons: tuple[str, ...]


def _evacuation_zones_scan_pattern(*, root: str, kind: PartitionKind) -> str:
    """Return the glob rooted at `root` for one partition kind's subtree of the frozen layout."""
    normalized_root = root if root.endswith("/") else f"{root}/"
    return f"{normalized_root}{stream_prefix(EVACUATION_ZONES_STREAM, validate_partition_kind(kind))}**/*.parquet"


def _snapshot_day_status(store: ObjectStore, day: date) -> PartitionDayStatus:
    """Classify one day by listing alone -- data, absent, conflict, or a real gap -- never by opening a file."""
    keys = store.list_partition_keys(EVACUATION_ZONES_STREAM, "observed", year=day.year, month=day.month)
    return partition_day_statuses(
        layer=EVACUATION_ZONES_STREAM, kind="observed", first_day=day, last_day=day, keys=keys
    )[day]


def read_written_snapshot(*, root: str, day: date, storage_options: dict[str, str] | None = None) -> pa.Table | None:
    """Read every row the lane wrote for exactly one snapshot day, across however many parts it spans.

    Returns `None` when the day genuinely has no rows -- the caller decides whether that is a
    failure (a listed `data` partition that somehow reads empty) using `_snapshot_day_status`
    first; this function only reports what the bytes contain.
    """
    frame = (
        pl.scan_parquet(
            _evacuation_zones_scan_pattern(root=root, kind="observed"),
            hive_partitioning=False,
            schema=_EVACUATION_ZONES_POLARS_SCHEMA,
            storage_options=storage_options,
        )
        .filter(pl.col("snapshot_day") == day)
        .select(list(EVACUATION_ZONES_SCHEMA.column_names))
        .sort(list(EVACUATION_ZONES_SCHEMA.sort_columns))
        .collect()
    )
    table = conform_to_stream_schema(frame.to_arrow(), EVACUATION_ZONES_SCHEMA)
    return None if table.num_rows == 0 else table


def _as_optional_level(value: object) -> int | None:
    """Narrow an untrusted live-feed value to Oregon's 1-3 evacuation level or `None`."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _internal_consistency(written: pa.Table) -> tuple[bool, bool, tuple[str, ...]]:
    """Check identity and grain integrity independent of the source system.

    This is the only kind of check a past snapshot can ever receive
    (`docs/lanes/evacuation-zones.md` section 3): Oregon's feed cannot be re-queried for a day that
    has already passed, so a stale day's honesty rests entirely on these structural invariants.
    """
    violations: list[str] = []
    global_ids = written.column("global_id").to_pylist()
    natural_keys = written.column("natural_key").to_pylist()
    producers = written.column("producer").to_pylist()

    blank_identities = sum(1 for value in global_ids if not isinstance(value, str) or not value.strip())
    identity_ok = blank_identities == 0
    if blank_identities:
        violations.append(f"{blank_identities} row(s) carry a blank or missing global_id")

    wrong_producer = sum(1 for value in producers if value != EVACUATION_ZONES_PRODUCER)
    if wrong_producer:
        identity_ok = False
        violations.append(f"{wrong_producer} row(s) carry a producer other than {EVACUATION_ZONES_PRODUCER!r}")

    duplicate_count = len(natural_keys) - len(set(natural_keys))
    grain_ok = duplicate_count == 0
    if not grain_ok:
        violations.append(f"{duplicate_count} duplicate natural_key value(s) within one snapshot_day")

    return identity_ok, grain_ok, tuple(violations)


def _live_reconciliation(
    written: pa.Table, live_zones: Sequence[Mapping[str, object]]
) -> tuple[frozenset[str], frozenset[str], tuple[EvacuationLevelMismatch, ...]]:
    """Diff identities and evacuation levels between what was written and what Oregon reports now."""
    written_ids = [str(value) for value in written.column("global_id").to_pylist()]
    written_levels_raw = written.column("evacuation_level").to_pylist()
    written_levels: dict[str, int | None] = dict(zip(written_ids, written_levels_raw, strict=True))
    written_id_set = frozenset(written_ids)

    live_ids = frozenset(
        str(zone["globalId"])
        for zone in live_zones
        if isinstance(zone.get("globalId"), str) and str(zone["globalId"]).strip()
    )
    live_levels: dict[str, int | None] = {
        str(zone["globalId"]): _as_optional_level(zone.get("evacuationLevel"))
        for zone in live_zones
        if isinstance(zone.get("globalId"), str)
    }

    missing_from_write = live_ids - written_id_set
    retired_upstream = written_id_set - live_ids
    mismatches = tuple(
        EvacuationLevelMismatch(
            global_id=zone_id, written_level=written_levels.get(zone_id), live_level=live_levels.get(zone_id)
        )
        for zone_id in sorted(written_id_set & live_ids)
        if written_levels.get(zone_id) != live_levels.get(zone_id)
    )
    return missing_from_write, retired_upstream, mismatches


async def fetch_live_zones_from_oregon(bbox: str) -> tuple[list[dict[str, object]], bool]:
    """Production wiring for `fetch_live`: open one bounded client and page Oregon's feed once."""
    async with upstream_client(EVACUATION_ZONES_BOUNDS) as client:
        return await fetch_evacuation_zones(client, bbox)


async def validate_evacuation_zones_snapshot(  # noqa: PLR0913
    store: ObjectStore,
    *,
    root: str,
    snapshot_day: date,
    today: date,
    bbox: str | None,
    storage_options: dict[str, str] | None = None,
    fetch_live: Callable[[], Awaitable[tuple[list[dict[str, object]], bool]]] | None = None,
) -> EvacuationZonesValidationReport:
    """Reconcile one day's written evacuation-zones snapshot against Oregon OEM's live feed.

    Zero written rows on a governed-absence day is a normal quiet season, not a failure
    (`layer-lanes.md` section 4). A `missing` or `conflict` partition status IS a failure: the
    first is an ungoverned gap, the second an admin-only anomaly this validator refuses to resolve
    on its own. `fetch_live` defaults to `None` (no live comparison attempted); wire it to
    `fetch_live_zones_from_oregon` for the real reconciliation.
    """
    status = _snapshot_day_status(store, snapshot_day)
    failures: list[str] = []
    written: pa.Table | None = None
    written_row_count = 0
    identity_ok = True
    grain_ok = True

    if status == "missing":
        failures.append(
            f"evacuation-zones snapshot {snapshot_day} has neither a data partition nor a "
            "governed-absence marker (partition status: missing)"
        )
    elif status == "conflict":
        failures.append(
            f"evacuation-zones snapshot {snapshot_day} carries both a data partition and a "
            "governed-absence marker; resolving that is a manual admin action, not something "
            "this validator may decide (partition status: conflict)"
        )
    elif status == "data":
        written = read_written_snapshot(root=root, day=snapshot_day, storage_options=storage_options)
        if written is None:
            failures.append(
                f"evacuation-zones snapshot {snapshot_day} lists a data partition but no rows "
                "could be read back from it"
            )
        else:
            written_row_count = written.num_rows
            identity_ok, grain_ok, violations = _internal_consistency(written)
            failures.extend(f"evacuation-zones snapshot {snapshot_day}: {violation}" for violation in violations)
    # status == "absent": zero active zones is a normal quiet-season state (layer-lanes.md section 4).

    live_comparison_performed = False
    live_comparison_skipped_reason: str | None = None
    zones_missing_from_write: frozenset[str] = frozenset()
    zones_retired_upstream: frozenset[str] = frozenset()
    level_mismatches: tuple[EvacuationLevelMismatch, ...] = ()
    source_response_summary = "not queried"

    if snapshot_day != today:
        live_comparison_skipped_reason = (
            "Oregon OEM publishes a current-state-only feed; a past snapshot cannot be re-fetched "
            "from it for comparison (docs/lanes/evacuation-zones.md section 3). Only internal "
            "consistency was checked."
        )
        source_response_summary = "not queried: snapshot day is not today, and the source holds no history"
    elif bbox is None:
        live_comparison_skipped_reason = "INGEST_BBOX is not configured; the live re-query was skipped"
        source_response_summary = "not queried: no bbox configured"
    elif fetch_live is None:
        live_comparison_skipped_reason = "no live-feed fetcher was wired into this validation run"
        source_response_summary = "not queried: no fetcher wired"
    else:
        try:
            live_zones, exceeded_transfer_limit = await fetch_live()
        except Exception as exc:  # reported, never resolved: an honest gap beats a filled one
            live_comparison_skipped_reason = f"Oregon OEM live re-query failed: {exc}"
            source_response_summary = f"query failed: {exc}"
        else:
            live_comparison_performed = True
            source_response_summary = (
                f"queried live; {len(live_zones)} zone(s) returned, exceeded_transfer_limit={exceeded_transfer_limit}"
            )
            if written is not None:
                zones_missing_from_write, zones_retired_upstream, level_mismatches = _live_reconciliation(
                    written, live_zones
                )

    return EvacuationZonesValidationReport(
        lane=EVACUATION_ZONES_STREAM,
        snapshot_day=snapshot_day,
        today=today,
        partition_status=status,
        written_row_count=written_row_count,
        identity_integrity_ok=identity_ok,
        grain_integrity_ok=grain_ok,
        live_comparison_performed=live_comparison_performed,
        live_comparison_skipped_reason=live_comparison_skipped_reason,
        zones_missing_from_write=zones_missing_from_write,
        zones_retired_upstream=zones_retired_upstream,
        level_mismatches=level_mismatches,
        source_response_summary=source_response_summary,
        ok=not failures,
        failure_reasons=tuple(failures),
    )
