"""The weather-forecast daily lane: fetch one provider run, write every rung, publish availability.

Layer L3, spec FR-12. One invocation admits ONE model run: the provider's issue date is the
partition day, the future-ness of a row lives in its `valid_time` column, and the whole thing is
written under `kind=observed` because an admitted run is source data, not a projection this repo
generated. Rationale lives in `sources/AGENTS.md`; the coarse-rung arithmetic is cited inline.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]  # pyarrow ships no stubs

from plantgeo_ml_service.foundation.canonical import canonical_json, sha256_digest
from plantgeo_ml_service.foundation.parquet_markers import PartitionCompletion
from plantgeo_ml_service.foundation.parquet_paths import (
    BASE_PARTITION_ZOOM,
    ZOOM_TIERS,
    ZoomTier,
    availability_lane_root,
)
from plantgeo_ml_service.pipeline.availability_publisher import build_generation, publish_generation
from plantgeo_ml_service.pipeline.forecast_lane_bootstrap import (
    ForecastDayReceipt,
    ensure_lane_bootstrap,
    terminal_rows_for_identity,
)
from plantgeo_ml_service.pipeline.object_store import (
    JSON_CONTENT_TYPE,
    ScratchPrefixError,
    completed_parts_from,
    scratch_rooted_store,
    sha256_of,
)
from plantgeo_ml_service.pipeline.sources.open_meteo import (
    MAX_FORECAST_RUN_DAYS,
    OPEN_METEO_WEATHER_FORECAST_SOURCE,
)
from plantgeo_ml_service.pipeline.sources.protocol import SourceRequestError

# Re-exported rather than re-declared: the row reshaping moved out when this module passed the
# size ceiling, and the two constants are part of this lane's published vocabulary.
from plantgeo_ml_service.pipeline.weather_forecast_rows import (
    MERGED_SUPPORT,
    WeatherForecastRowError,
    base_rows,
    rows_for_rung,
)
from plantgeo_ml_service.warehouse.availability import (
    AVAILABILITY_REQUIRED_RUNGS,
    AvailabilityConfig,
    AvailabilityIdentity,
    AvailabilityRow,
    EvidenceReceipt,
)
from plantgeo_ml_service.warehouse.weather_forecast import (
    UPSTREAM_VARIABLES,
    WEATHER_FORECAST_KIND,
    WEATHER_FORECAST_NATURE,
    WEATHER_FORECAST_SCHEMA,
    WEATHER_FORECAST_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from plantgeo_ml_service.pipeline.availability_publisher import PointerStore, PublicationReceipt
    from plantgeo_ml_service.pipeline.object_store import (
        CompletionWriteReceipt,
        ObjectStore,
        ParquetWriteReceipt,
    )
    from plantgeo_ml_service.pipeline.sources.protocol import (
        SourceReceipt,
        WeatherForecastRunPayload,
        WeatherForecastSource,
    )

#: The run this lane admits each day. One run per invocation, and 00Z is the initialization whose
#: forward horizon covers the whole issue day.
MODEL_INIT_HOUR: Final = 0

#: How many lattice cells one daily run may carry: ten provider requests at the source's own
#: 200-location ceiling. A bound, not a target; a caller with more cells narrows its region rather
#: than silently fetching for an hour.
MAX_FORECAST_CELLS: Final = 2_000

#: The reason the issue day carries when the provider answered with no hourly readings at all. A
#: day with no rows is indexed as a governed absence, never omitted (FR-4a).
NO_READINGS_REASON: Final = "provider_returned_no_readings"


class WeatherForecastRunError(RuntimeError):
    """Raised when a daily run cannot be admitted, written, or published as asked."""


@dataclass(frozen=True, slots=True)
class ForecastCell:
    """One lattice cell a run is fetched for: the identity published rows carry, and where it is."""

    cell_id: str
    longitude: float
    latitude: float


@dataclass(frozen=True, slots=True)
class BatchAnswer:
    """One bounded request and what came back: the cells asked for, aligned to the run's locations."""

    cells: tuple[ForecastCell, ...]
    run: WeatherForecastRunPayload


@dataclass(frozen=True, slots=True)
class RungWrite:
    """What one rung of one day landed as: its part receipts and the completion marker over them."""

    zoom: ZoomTier
    row_count: int
    parts: tuple[ParquetWriteReceipt, ...]
    completion: CompletionWriteReceipt


@dataclass(frozen=True, slots=True)
class WeatherForecastDailyReceipt:
    """Everything one daily run did: what it fetched, what it wrote, and whether it became selectable."""

    run_id: str
    issue_date: date
    prefix: str
    source_receipts: tuple[SourceReceipt, ...]
    rungs: tuple[RungWrite, ...]
    publication: PublicationReceipt

    @property
    def base_row_count(self) -> int:
        """Return how many rows the base rung published; zero when the day is a governed absence."""
        return next((rung.row_count for rung in self.rungs if rung.zoom == BASE_PARTITION_ZOOM), 0)


def run_weather_forecast_daily(  # noqa: PLR0913 - one keyword per run-shaping decision, all defaulted
    store: ObjectStore,
    issue_date: date,
    cells: Sequence[ForecastCell],
    *,
    dry_run_prefix: str | None = None,
    source: WeatherForecastSource | None = None,
    pointers: PointerStore | None = None,
    forecast_days: int = MAX_FORECAST_RUN_DAYS,
    now: datetime | None = None,
) -> WeatherForecastDailyReceipt:
    """Admit one provider run for `issue_date`: write every rung, mark them complete, publish them.

    `dry_run_prefix` re-roots every write inside the bucket, so a proving run cannot reach the
    published lane. `pointers` is how a written day becomes SELECTABLE; a run given none is refused
    before it writes anything, because a partition nobody can point at is not a publication.
    """
    if pointers is None:
        raise WeatherForecastRunError(
            "a weather-forecast run needs a pointer store: writing a partition does not publish it, and a day "
            "with no availability pointer is never selectable"
        )
    binding = source if source is not None else OPEN_METEO_WEATHER_FORECAST_SOURCE
    inventory = _validated_cells(cells, binding)
    moment = now if now is not None else datetime.now(tz=UTC)
    model_init_time = datetime.combine(issue_date, time(hour=MODEL_INIT_HOUR), tzinfo=UTC)
    answers = _fetch_every_batch(binding, inventory, model_init_time=model_init_time, forecast_days=forecast_days)
    run_id = answers[0].run.run_id
    target = _target_store(store, dry_run_prefix)
    try:
        rows = base_rows(answers, model_init_time=model_init_time, published_at=moment)
    except WeatherForecastRowError as error:
        raise WeatherForecastRunError(str(error)) from error
    # A run that answered with no readings publishes the issue day as a governed absence rather
    # than writing nothing: an unmentioned day is indistinguishable from one nobody attempted.
    rungs = (
        ()
        if not rows
        else tuple(
            _write_rung(target, rows_for_rung(rows, rung), zoom=rung, day=issue_date, run_id=run_id, now=moment)
            for rung in ZOOM_TIERS
        )
    )
    receipts = tuple(answer.run.receipt for answer in answers)
    publication = _publish(
        target,
        pointers,
        rungs,
        inventory=inventory,
        receipts=receipts,
        issue_date=issue_date,
        published_at=moment,
    )
    return WeatherForecastDailyReceipt(
        run_id=run_id,
        issue_date=issue_date,
        prefix=target.prefix,
        source_receipts=receipts,
        rungs=rungs,
        publication=publication,
    )


def _validated_cells(cells: Sequence[ForecastCell], source: WeatherForecastSource) -> tuple[ForecastCell, ...]:
    """Refuse a cell list that is empty, over budget, duplicated, or outside the source's coverage."""
    if not cells or len(cells) > MAX_FORECAST_CELLS:
        raise WeatherForecastRunError(
            f"a daily weather-forecast run carries between one and {MAX_FORECAST_CELLS} cells, got {len(cells)}"
        )
    identifiers = [cell.cell_id for cell in cells]
    if len(set(identifiers)) != len(identifiers):
        raise WeatherForecastRunError("two cells in one run share a cell_id; the lane's grain would collide")
    outside = [cell.cell_id for cell in cells if not source.coverage.covers(cell.longitude, cell.latitude)]
    if outside:
        raise WeatherForecastRunError(
            f"cells {outside[:3]} lie outside source {source.source_slug!r}'s coverage claim; a cell the source "
            "cannot answer for is a caller error, not a governed absence this lane invents"
        )
    # Sorted by identity so the batch split, the request URLs and the published bytes are a function
    # of the cell SET rather than of the order a caller happened to assemble it in.
    return tuple(sorted(cells, key=lambda cell: cell.cell_id))


def _fetch_every_batch(
    source: WeatherForecastSource,
    cells: Sequence[ForecastCell],
    *,
    model_init_time: datetime,
    forecast_days: int,
) -> tuple[BatchAnswer, ...]:
    """Fetch the cell list in bounded batches, one request at a time, and refuse a partial answer."""
    size = source.max_locations_per_request
    if size < 1:
        raise SourceRequestError(f"source {source.source_slug!r} declares no room for a single location")
    batches = [tuple(cells[start : start + size]) for start in range(0, len(cells), size)]

    async def fetch_all() -> tuple[BatchAnswer, ...]:
        """Await the batches in order; concurrency here would only multiply the provider's rate limit."""
        answered: list[BatchAnswer] = []
        for batch in batches:
            run = await source.fetch_run(
                model_init_time=model_init_time,
                coordinates=tuple((cell.latitude, cell.longitude) for cell in batch),
                variables=UPSTREAM_VARIABLES,
                forecast_days=forecast_days,
            )
            answered.append(BatchAnswer(cells=batch, run=run))
        return tuple(answered)

    answers = asyncio.run(fetch_all())
    for answer in answers:
        if len(answer.run.locations) != len(answer.cells):
            raise WeatherForecastRunError(
                f"the source answered for {len(answer.run.locations)} of {len(answer.cells)} cells in one batch; "
                "a partial run is refused rather than published as if the missing cells had no weather"
            )
    if len({answer.run.run_id for answer in answers}) != 1:
        raise WeatherForecastRunError("the batches of one day answered under different run identities")
    return answers


def _target_store(store: ObjectStore, dry_run_prefix: str | None) -> ObjectStore:
    """Return the store this run writes through, refusing a dry-run prefix that is not scratch.

    The SAME check fire-risk makes, through the same helper: an unvalidated `dry_run_prefix` meant
    a proving run could re-root itself onto the published lane and look like a real publication.
    """
    try:
        return scratch_rooted_store(store, dry_run_prefix)
    except ScratchPrefixError as error:
        raise WeatherForecastRunError(str(error)) from error


def _write_rung(  # noqa: PLR0913 - one keyword per partition identity field is the contract
    store: ObjectStore,
    rows: Sequence[dict[str, object]],
    *,
    zoom: ZoomTier,
    day: date,
    run_id: str,
    now: datetime,
) -> RungWrite:
    """Write one rung's single part file and the completion marker that ASSERTS the day finished."""
    if not rows:
        raise WeatherForecastRunError(f"rung {zoom} derived no rows from a non-empty base rung")
    table = pa.Table.from_pylist(list(rows), schema=WEATHER_FORECAST_SCHEMA.arrow_schema)
    part = store.write_partition(
        table,
        layer=WEATHER_FORECAST_STREAM,
        kind=WEATHER_FORECAST_KIND,
        zoom=zoom,
        day=day,
    )
    completion = store.write_completion_marker(
        PartitionCompletion(
            part_count=1,
            row_count=part.row_count,
            completed_at=now,
            run_id=run_id,
            parts=completed_parts_from((part,)),
        ),
        layer=WEATHER_FORECAST_STREAM,
        kind=WEATHER_FORECAST_KIND,
        zoom=zoom,
        day=day,
    )
    return RungWrite(zoom=zoom, row_count=part.row_count, parts=(part,), completion=completion)


def _publish(  # noqa: PLR0913 - one keyword per fact the generation cites
    store: ObjectStore,
    pointers: PointerStore,
    rungs: Sequence[RungWrite],
    *,
    inventory: Sequence[ForecastCell],
    receipts: Sequence[SourceReceipt],
    issue_date: date,
    published_at: datetime,
) -> PublicationReceipt:
    """Write the day's evidence objects, build the generation over every rung, advance the pointer.

    The ORDER is FR-4a in one function, and the bootstrap goes through the shared path: the rows are
    what the bootstrap receipt digests, so they are built first, then the receipt, then the marker
    naming it, then the generation, then the pointer.
    """
    lane_root = availability_lane_root(WEATHER_FORECAST_STREAM, WEATHER_FORECAST_KIND)
    source_receipt = _written_evidence(store, _source_evidence(receipts, issue_date), lane_root, "source", issue_date)
    identity = AvailabilityIdentity(
        lane_root=lane_root,
        lane=WEATHER_FORECAST_STREAM,
        product=WEATHER_FORECAST_KIND,
        nature=WEATHER_FORECAST_NATURE,
        required_rungs=AVAILABILITY_REQUIRED_RUNGS,
        verified_source_inventory_root=_inventory_root(inventory),
    )
    rows = (
        _absence_rows(identity, source_receipt=source_receipt, issue_date=issue_date, published_at=published_at)
        if not rungs
        else _published_rows(
            store,
            rungs,
            identity=identity,
            source_receipt=source_receipt,
            issue_date=issue_date,
            published_at=published_at,
        )
    )
    config = AvailabilityConfig(
        identity=identity,
        source_ceiling=issue_date,
        bootstrap_receipt=ensure_lane_bootstrap(
            store,
            rows,
            identity=identity,
            source_receipt=source_receipt,
            source_ceiling=issue_date,
            created_at=published_at,
        ),
    )
    generation = build_generation(config, rows, created_at=published_at)
    return publish_generation(store, pointers, generation, layer=WEATHER_FORECAST_STREAM, kind=WEATHER_FORECAST_KIND)


def _published_rows(  # noqa: PLR0913 - one keyword per fact a terminal row binds
    store: ObjectStore,
    rungs: Sequence[RungWrite],
    *,
    identity: AvailabilityIdentity,
    source_receipt: EvidenceReceipt,
    issue_date: date,
    published_at: datetime,
) -> tuple[AvailabilityRow, ...]:
    """Return one published terminal row per written rung, each binding its own evidence object."""
    return tuple(
        AvailabilityRow(
            lane=identity.lane,
            product=identity.product,
            nature=identity.nature,
            day=issue_date,
            rung=rung.zoom,
            terminal_state="published",
            row_count=rung.row_count,
            source_receipt=source_receipt,
            terminal_receipt=_written_evidence(
                store,
                _terminal_evidence(rung, issue_date),
                identity.lane_root,
                f"terminal-z{rung.zoom:02d}",
                issue_date,
            ),
            data_receipts=tuple(
                sorted(
                    (EvidenceReceipt(key=part.relative_path, sha256=part.sha256) for part in rung.parts),
                    key=lambda receipt: receipt.key,
                )
            ),
            completion_receipt=EvidenceReceipt(key=rung.completion.relative_path, sha256=rung.completion.sha256),
            absence_reason=None,
            source_ceiling=issue_date,
            published_at=published_at,
        )
        for rung in rungs
    )


def _absence_rows(
    identity: AvailabilityIdentity,
    *,
    source_receipt: EvidenceReceipt,
    issue_date: date,
    published_at: datetime,
) -> tuple[AvailabilityRow, ...]:
    """Return the WHOLE ladder as governed absences, through the shared forecast-lane projection."""
    return terminal_rows_for_identity(
        ForecastDayReceipt(
            day=issue_date,
            parts_by_rung={},
            completions_by_rung={},
            row_counts_by_rung={},
            absence_reason=NO_READINGS_REASON,
        ),
        identity=identity,
        source_receipt=source_receipt,
        source_ceiling=issue_date,
        published_at=published_at,
    )


def _source_evidence(receipts: Sequence[SourceReceipt], issue_date: date) -> bytes:
    """Serialize the day's fetches: the credential-free URLs and the digests of the bytes parsed."""
    document = {
        "issue_date": issue_date.isoformat(),
        "lane": WEATHER_FORECAST_STREAM,
        "requests": [
            {
                "location_count": receipt.location_count,
                "request_url": receipt.request_url,
                "response_bytes": receipt.response_bytes,
                "response_sha256": receipt.response_sha256,
                "source_slug": receipt.source_slug,
            }
            for receipt in receipts
        ],
    }
    return canonical_json(document).encode("utf-8")


def _terminal_evidence(rung: RungWrite, issue_date: date) -> bytes:
    """Serialize what one rung terminally holds: its parts, their digests, and its completion marker."""
    document = {
        "completion": {"key": rung.completion.relative_path, "sha256": rung.completion.sha256},
        "issue_date": issue_date.isoformat(),
        "parts": [{"key": part.relative_path, "sha256": part.sha256} for part in rung.parts],
        "row_count": rung.row_count,
        "rung": rung.zoom,
    }
    return canonical_json(document).encode("utf-8")


def _written_evidence(store: ObjectStore, payload: bytes, lane_root: str, name: str, day: date) -> EvidenceReceipt:
    """Write one evidence document under a key naming its OWN digest, and return the receipt to it.

    Content-addressed for the same reason the generation is: a replay of the same run writes the same
    bytes to the same key and is adopted, while a document disagreeing with its address is refused
    rather than overwritten. The bodies carry no clock, so a re-run replays rather than conflicts.
    """
    digest = sha256_of(payload)
    relative_path = f"{lane_root}/availability/evidence/day={day.isoformat()}/{name}-{digest}.json"
    store.put_immutable(relative_path, payload, content_type=JSON_CONTENT_TYPE)
    return EvidenceReceipt(key=relative_path, sha256=digest)


def _inventory_root(cells: Sequence[ForecastCell]) -> str:
    """Digest the exact cell inventory this run was fetched for; the generation's identity cites it."""
    inventory = [{"cell_id": cell.cell_id, "latitude": cell.latitude, "longitude": cell.longitude} for cell in cells]
    return sha256_digest(canonical_json(inventory))


__all__ = [
    "MAX_FORECAST_CELLS",
    "MERGED_SUPPORT",
    "MODEL_INIT_HOUR",
    "NO_READINGS_REASON",
    "BatchAnswer",
    "ForecastCell",
    "RungWrite",
    "WeatherForecastDailyReceipt",
    "WeatherForecastRunError",
    "run_weather_forecast_daily",
]
