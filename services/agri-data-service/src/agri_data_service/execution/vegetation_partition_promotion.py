"""Per-day-partition content-SHA scoped promotion for the governed vegetation NDVI plane.

BACKLOG P4, ARMED BUT SHADOW. `execution/vegetation_ndvi_plane.py::register_governed_forward_plane`
has had no caller anywhere in this service (grepped clean 2026-09-18) since it was written: the
Monte Carlo NDVI forecaster it exists to feed has never had a decided checksum scope, which is the
gap `.omc` memory `agri-vegetation-promotion-unarmed` and `execution/AGENTS.md` name. Owner decision
2026-09-18 closes that gap: promotion of one `layer=vegetation/kind=observed/year=/month=/day=`
partition is keyed by THAT PARTITION'S OWN content SHA, matching the availability index's
`generation=<content-sha>` convention (`conductor/code_styleguides/layer-lanes.md` §4a;
`pipeline/parquet/availability_index.py`), not the whole-corpus digest
`execution/vegetation_ndvi_plane._corpus_digest` computes for its own, unrelated Postgres purpose.

This module NEVER widens `register_governed_forward_plane`'s existing per-day-touched-cells
scoping (`execution/vegetation_ndvi_plane.py::register_governed_forward_plane`, its `cell_days`
argument); it only decides, per partition, whether that
call is owed at all. Reuses `foundation.canonical.sha256_digest`/`canonical_json` -- the same digest
routine the availability index binds into every generation key -- rather than a second one.

Evaluation-only artifacts (`kind != "observed"`) are never promotable: `VegetationDayPartitionKey`
refuses construction for anything else, so an evaluation-only day can never reach the register verb
through this path.

Lane wiring (`execution/lane_ids.py::VEGETATION_NDVI_PROMOTION_LANE_ID`,
`execution/lane_specs.py`) registers this verb so it CAN be scheduled, but it is disabled by
default: it is registered in `LANE_SPECS` and absent from the deployed
`PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` allow-list (`execution/AGENTS.md` §Lane activation), which is a
production mutation this backlog change deliberately does not make; the activation command lives
with the allow-list in `execution/AGENTS.md` §Lane activation.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal, cast

from agri_data_service.execution.vegetation_ndvi_plane import (
    RegistrationSummary,
    register_governed_forward_plane,
)
from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.vegetation.forward import settled_through
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: The only kind this verb is ever asked about. Anything else is refused at construction, never
#: filtered after the fact, so a caller cannot accidentally promote what should stay evaluation-only.
PROMOTABLE_KIND: Final = "observed"
_JSON_ENCODING: Final = "utf-8"
_RECEIPT_SCHEMA_VERSION: Final = 1
#: Default backlog width when an operator names no explicit `--day`: the same conservative single-day
#: default `pipeline/direct/vegetation/forward.py::VEGETATION_DEFAULT_MAX_DAYS` uses for its own turn.
DEFAULT_MAX_DAYS: Final = 1

RegisterForwardPlane = Callable[..., Awaitable[RegistrationSummary]]


class EmptyDayPartitionError(ValueError):
    """A day partition that was WRITTEN but holds no cell values.

    Distinct from a day the lane never wrote, which is a governed absence the turn records and moves
    past (`layer-lanes.md` §4). A written-but-empty partition is an anomaly in the writer, so it
    still fails -- loudly, and naming the lane and the day (`engineering-principles.md` §2).
    """

    def __init__(self, *, layer: str, day: date) -> None:
        super().__init__(
            f"{layer} day partition {day.isoformat()} was written but holds no cell values, so it cannot be "
            f"content-addressed; the forward writer, not this promoter, owes the fix"
        )
        self.layer = layer
        self.day = day


class EvaluationArtifactNotPromotableError(ValueError):
    """Raised when a partition kind outside `observed` is offered to the governed-plane promoter."""

    def __init__(self, *, kind: str) -> None:
        super().__init__(
            f"vegetation partition kind {kind!r} is evaluation-only and is never promoted to the governed plane"
        )
        self.kind = kind


@dataclass(frozen=True, slots=True)
class VegetationDayPartitionKey:
    """One `layer=vegetation/kind=<kind>/year=/month=/day=` partition identity.

    Zoom-INDEPENDENT on purpose, matching `foundation.parquet.paths.promotion_receipt_path`: the
    governed plane is promoted once per day, not once per rendered rung.
    """

    day: date
    kind: str = PROMOTABLE_KIND
    layer: Literal["vegetation"] = VEGETATION_PLANE_STREAM

    def __post_init__(self) -> None:
        if self.kind != PROMOTABLE_KIND:
            raise EvaluationArtifactNotPromotableError(kind=self.kind)


@dataclass(frozen=True, slots=True)
class VegetationPromotionReceipt:
    """One recorded promotion decision for a single day partition."""

    partition: VegetationDayPartitionKey
    content_sha256: str
    promoted_at: datetime
    release_set_id: str | None
    source_release_id: str | None

    def to_json_bytes(self) -> bytes:
        """Serialize with the same canonical, deterministic JSON the digest itself uses."""
        return canonical_json(
            {
                "schema_version": _RECEIPT_SCHEMA_VERSION,
                "day": self.partition.day.isoformat(),
                "kind": self.partition.kind,
                "content_sha256": self.content_sha256,
                "promoted_at": self.promoted_at.astimezone(UTC).isoformat(),
                "release_set_id": self.release_set_id,
                "source_release_id": self.source_release_id,
            }
        ).encode(_JSON_ENCODING)

    @classmethod
    def from_json_bytes(cls, payload: bytes) -> VegetationPromotionReceipt:
        """Parse a previously written receipt, failing closed on anything not shaped as expected."""
        decoded = json.loads(payload.decode(_JSON_ENCODING))
        if not isinstance(decoded, dict) or decoded.get("schema_version") != _RECEIPT_SCHEMA_VERSION:
            raise ValueError("vegetation promotion receipt is not a recognised schema_version 1 record")
        return cls(
            partition=VegetationDayPartitionKey(day=date.fromisoformat(str(decoded["day"])), kind=str(decoded["kind"])),
            content_sha256=str(decoded["content_sha256"]),
            promoted_at=datetime.fromisoformat(str(decoded["promoted_at"])),
            release_set_id=None if decoded.get("release_set_id") is None else str(decoded["release_set_id"]),
            source_release_id=None if decoded.get("source_release_id") is None else str(decoded["source_release_id"]),
        )


@dataclass(frozen=True, slots=True)
class VegetationPromotionOutcome:
    """Measured effect of one bounded partition-promotion decision."""

    partition: VegetationDayPartitionKey
    content_sha256: str
    status: Literal["promoted", "unchanged"]
    receipt: VegetationPromotionReceipt
    registration: RegistrationSummary | None


def day_partition_content_sha256(cell_values: Sequence[tuple[str, float]]) -> str:
    """Digest one day-partition's exact cell-value content.

    Reuses `foundation.canonical.sha256_digest`/`canonical_json` -- the identical digest routine
    the availability index binds into every `generation=<content-sha>` key
    (`pipeline/parquet/availability_index.py`) -- rather than a second, driftable one scoped to
    this verb. Content-addressed, so an unchanged partition always re-digests to the same value
    regardless of row order the caller happened to read them in.
    """
    if not cell_values:
        raise ValueError("a day partition requires at least one cell value to be content-addressed")
    deduplicated: dict[str, float] = {}
    for cell_key, value in cell_values:
        if cell_key in deduplicated:
            raise ValueError(f"day partition cell key {cell_key!r} is duplicated")
        deduplicated[cell_key] = value
    ordered = [[cell_key, deduplicated[cell_key]] for cell_key in sorted(deduplicated)]
    return sha256_digest(canonical_json(ordered))


async def promote_vegetation_day_partition(  # noqa: PLR0913 - one argument per idempotency input; bundling loses the per-field docstring above
    session: AsyncSession,
    *,
    day: date,
    kind: str,
    cell_values: Sequence[tuple[str, float]],
    previous_receipt: VegetationPromotionReceipt | None,
    now: datetime | None = None,
    register: RegisterForwardPlane = register_governed_forward_plane,
) -> VegetationPromotionOutcome:
    """Promote exactly one day partition, keyed by its own content SHA, or report it unchanged.

    An unchanged partition (content SHA equal to `previous_receipt.content_sha256`) never calls the
    governed-plane register verb again -- a re-run of an already-promoted, byte-identical partition
    is idempotent by construction, never a second registration attempt against the same day. A
    changed partition re-promotes only itself: `register_governed_forward_plane` is already scoped
    to the touched cell-days it is given, so this verb hands it exactly this day's cells, never the
    whole corpus.

    Raises `EvaluationArtifactNotPromotableError` for any `kind` other than `observed`, before any
    checksum is computed or any register call is attempted.
    """
    partition = VegetationDayPartitionKey(day=day, kind=kind)  # raises for a non-`observed` kind
    content_sha256 = day_partition_content_sha256(cell_values)
    moment = now or datetime.now(UTC)
    if previous_receipt is not None and previous_receipt.content_sha256 == content_sha256:
        return VegetationPromotionOutcome(
            partition=partition,
            content_sha256=content_sha256,
            status="unchanged",
            receipt=previous_receipt,
            registration=None,
        )
    cell_days = tuple((cell_key, day) for cell_key, _value in cell_values)
    registration = await register(session, cutoff_day=day, cell_days=cell_days)
    receipt = VegetationPromotionReceipt(
        partition=partition,
        content_sha256=content_sha256,
        promoted_at=moment,
        release_set_id=str(registration.plane.release_set_id),
        source_release_id=str(registration.plane.source_release_id),
    )
    return VegetationPromotionOutcome(
        partition=partition,
        content_sha256=content_sha256,
        status="promoted",
        receipt=receipt,
        registration=registration,
    )


def load_promotion_receipt(
    store: ObjectStore, *, day: date, kind: PartitionKind = PROMOTABLE_KIND
) -> VegetationPromotionReceipt | None:
    """Return the last recorded promotion receipt for one day partition, or `None` when never promoted."""
    payload = store.read_promotion_receipt(VEGETATION_PLANE_STREAM, kind, day)
    return None if payload is None else VegetationPromotionReceipt.from_json_bytes(payload)


def save_promotion_receipt(store: ObjectStore, receipt: VegetationPromotionReceipt) -> str:
    """Durably record one promotion decision so the next turn can re-run it as a no-op."""
    # `receipt.partition.kind` is `str` -- `VegetationDayPartitionKey.__post_init__` already refuses
    # construction for anything but `PROMOTABLE_KIND`, so this narrows a runtime-guaranteed value.
    return store.write_promotion_receipt(
        receipt.to_json_bytes(),
        layer=VEGETATION_PLANE_STREAM,
        kind=cast("PartitionKind", receipt.partition.kind),
        day=receipt.partition.day,
    )


def read_day_partition_cell_values(store: ObjectStore, day: date) -> tuple[tuple[str, float], ...]:
    """Read one base-rung day partition's `(cell_id, metric_value)` rows straight off Parquet."""
    table = store.read_partition(VEGETATION_PLANE_STREAM, PROMOTABLE_KIND, LANE_BASE_ZOOM_TIER, day)
    cell_ids = table.column("cell_id").to_pylist()
    metric_values = table.column("metric_value").to_pylist()
    return tuple(
        (cell_id, float(metric_value))
        for cell_id, metric_value in zip(cell_ids, metric_values, strict=True)
        if cell_id is not None
    )


def emit(payload: dict[str, object]) -> None:
    """Write one stable JSON progress record to stderr, leaving stdout for the terminal report."""
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


def parser() -> argparse.ArgumentParser:
    """Build the bounded governed-plane promotion operator.

    Without `--day`, promotes the newest `--max-days` settled vegetation days (the same
    `settled_through` boundary `pipeline/direct/vegetation/forward.py` publishes against), so this
    lane is schedulable exactly like its sibling direct writers. Every day it touches is still
    idempotent against its own promotion receipt, so a wider `--max-days` never re-registers an
    unchanged partition.
    """
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--day", action="append", dest="days", default=None, help="one ISO date; repeatable")
    built.add_argument("--max-days", type=int, default=DEFAULT_MAX_DAYS)
    built.add_argument("--run-id", default=None)
    return built


def default_promotion_days(*, today: date, max_days: int) -> tuple[date, ...]:
    """Return the newest `max_days` settled vegetation days, oldest first, with no explicit `--day`."""
    if max_days < 1:
        raise ValueError("--max-days must be at least one")
    ceiling = settled_through(today=today)
    return tuple(sorted(ceiling - timedelta(days=offset) for offset in range(max_days)))


async def run_vegetation_promotion(
    session: AsyncSession,
    store: ObjectStore,
    *,
    days: Sequence[date],
    now: datetime | None = None,
) -> dict[str, object]:
    """Promote every named day, newest last, each idempotent against its own last receipt.

    A day the forward writer never published is a GOVERNED ABSENCE, not a failure: it is recorded
    with its lane, its day and a named reason, and the remaining days still promote
    (`layer-lanes.md` §4, "Report an honest gap rather than a filled one"). Before 2026-09-18 the
    missing partition surfaced as a bare store error that failed the whole scheduled turn, including
    the days that would have promoted (STYLE-REVIEW-W2 S5). A partition that exists and is empty
    still raises, because that is the writer misbehaving rather than the source having nothing.
    """
    # Imported here, like `main()`'s own store import: the object-store module carries the heavy
    # client dependencies this module otherwise only needs at CLI time.
    from agri_data_service.pipeline.parquet.objectstore import ParquetWriteError  # noqa: PLC0415

    results: list[dict[str, object]] = []
    for day in days:
        try:
            cell_values = read_day_partition_cell_values(store, day)
        except ParquetWriteError as absence:
            results.append(
                {
                    "day": day.isoformat(),
                    "layer": VEGETATION_PLANE_STREAM,
                    "status": "absent",
                    "reason": "no_day_partition_written",
                    "detail": str(absence),
                }
            )
            emit({"event": "vegetation_promotion_day", **results[-1]})
            continue
        if not cell_values:
            raise EmptyDayPartitionError(layer=VEGETATION_PLANE_STREAM, day=day)
        previous_receipt = load_promotion_receipt(store, day=day)
        outcome = await promote_vegetation_day_partition(
            session,
            day=day,
            kind=PROMOTABLE_KIND,
            cell_values=cell_values,
            previous_receipt=previous_receipt,
            now=now,
        )
        save_promotion_receipt(store, outcome.receipt)
        results.append(
            {
                "day": day.isoformat(),
                "layer": VEGETATION_PLANE_STREAM,
                "status": outcome.status,
                "content_sha256": outcome.content_sha256,
                "cell_count": len(cell_values),
            }
        )
        emit({"event": "vegetation_promotion_day", **results[-1]})
    return {
        "status": "completed",
        "days": results,
        # Surfaced at the top level so a scheduled turn's report states its absences without the
        # reader walking every day entry.
        "absent_days": [str(entry["day"]) for entry in results if entry["status"] == "absent"],
    }


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded promotion turn over explicitly named days and emit one terminal report."""
    from agri_data_service.config import settings  # noqa: PLC0415 - CLI-only
    from agri_data_service.db.engine import local_source_loader_session  # noqa: PLC0415 - CLI-only
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore  # noqa: PLC0415 - CLI-only

    arguments = parser().parse_args(argv)
    days = (
        tuple(sorted(date.fromisoformat(value) for value in arguments.days))
        if arguments.days
        else default_promotion_days(today=datetime.now(UTC).date(), max_days=arguments.max_days)
    )
    store = ObjectStore.from_settings()
    loader_database_url = settings.require_local_source_loader_database_url()
    try:
        async with local_source_loader_session(loader_database_url) as session:
            report = await run_vegetation_promotion(session, store, days=days)
    except Exception as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


__all__ = [
    "DEFAULT_MAX_DAYS",
    "PROMOTABLE_KIND",
    "EmptyDayPartitionError",
    "EvaluationArtifactNotPromotableError",
    "VegetationDayPartitionKey",
    "VegetationPromotionOutcome",
    "VegetationPromotionReceipt",
    "day_partition_content_sha256",
    "default_promotion_days",
    "load_promotion_receipt",
    "main",
    "promote_vegetation_day_partition",
    "read_day_partition_cell_values",
    "run_vegetation_promotion",
    "save_promotion_receipt",
]


if __name__ == "__main__":  # pragma: no cover - process entry point
    import asyncio

    raise SystemExit(asyncio.run(main()))
