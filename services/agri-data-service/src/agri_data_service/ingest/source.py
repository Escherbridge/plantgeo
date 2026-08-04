"""The ingestion source contract: the shape a producer fills in instead of writing a new pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from agri_data_service.ingest.policy import is_fresh_observation

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from datetime import datetime, timedelta

    import httpx

    from agri_data_service.ingest.geometry import GridCell
    from agri_data_service.ingest.writer import FeatureWrite

    UpstreamRecord = Mapping[str, object]
    FetchCallable = Callable[["FetchRequest"], Awaitable[Sequence[UpstreamRecord]]]
    HistoryFetchCallable = Callable[["FetchRequest", "HistoryWindow"], Awaitable[Sequence[UpstreamRecord]]]
    BuildWriteCallable = Callable[[UpstreamRecord, "FetchRequest"], "FeatureWrite | None"]
    LayerReferenceCallable = Callable[[], str]

# "feature" is one upstream record to one place; "grid_cell" is many samples landing on a shared raster cell.
SourceShape = Literal["feature", "grid_cell"]


class SourceContractError(ValueError):
    """Raised when a source's declared shape and the writes it produces disagree."""


class HistoryUnavailableError(NotImplementedError):
    """Raised when a backfill asks a source for a past window it has said, in typed terms, it cannot serve."""


@dataclass(frozen=True, slots=True)
class HistoryWindow:
    """A half-open past window a backfill walks: start inclusive, end exclusive, both tz-aware."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        """Refuse a naive or inverted window rather than silently walking the wrong span."""
        if self.start.utcoffset() is None or self.end.utcoffset() is None:
            raise ValueError("history window bounds must include a timezone")
        if self.start >= self.end:
            raise ValueError("history window start must precede its end")


@dataclass(frozen=True, slots=True)
class HistoryCapability:
    """Whether a source can serve a past window, and the typed reason when it cannot."""

    supported: bool
    reason: str | None = None
    earliest: datetime | None = None

    def __post_init__(self) -> None:
        """A refusal must carry its reason; "no history" with no explanation is the silent return we forbid."""
        if not self.supported and not (self.reason or "").strip():
            raise SourceContractError("a source without history must state why")

    def require(self, source_name: str, window: HistoryWindow) -> None:
        """Raise the typed refusal when a backfill asks for history this source cannot serve."""
        if not self.supported:
            raise HistoryUnavailableError(f"{source_name} serves no history: {self.reason}")
        if self.earliest is not None and window.start < self.earliest:
            raise HistoryUnavailableError(f"{source_name} serves no history before {self.earliest.isoformat()}")


@dataclass(frozen=True, slots=True)
class FreshnessRule:
    """How long an observation stays ingestible, and whether an undated record may be written at all."""

    max_observation_age: timedelta | None = None
    accepts_undated_records: bool = False

    def accepts(self, observed_at: datetime | None, now: datetime | None = None) -> bool:
        """True when this observation is inside the source's window; an undated record needs explicit consent."""
        if observed_at is None:
            return self.accepts_undated_records
        if self.max_observation_age is None:
            return True
        return is_fresh_observation(observed_at, self.max_observation_age, now)


@dataclass(frozen=True, slots=True)
class FetchRequest:
    """Everything a fetch needs that is not the source's own configuration."""

    bbox: str
    max_records: int
    now: datetime | None = None
    client: httpx.AsyncClient | None = None


class IngestionSource(Protocol):
    """The shape every source fills in, so adding one is filling in a shape rather than writing a pipeline."""

    @property
    def source_name(self) -> str:
        """The token this source reports as `IngestionJobResult.source`, e.g. "nasa-firms"."""
        ...

    @property
    def producer(self) -> str:
        """The identity.py producer token that namespaces every natural key this source mints, e.g. "firms"."""
        ...

    @property
    def channel(self) -> str:
        """The realtime channel a written row invalidates, e.g. "layer:fire-detections"."""
        ...

    @property
    def shape(self) -> SourceShape:
        """Whether one record is one place, or many samples share a raster grid cell."""
        ...

    @property
    def freshness(self) -> FreshnessRule:
        """The rule that decides whether an observation is recent enough to write."""
        ...

    def layer_reference(self) -> str:
        """Resolve the target geo.layers name or id at call time, so a cron environment change needs no restart."""
        ...

    def history_capability(self) -> HistoryCapability:
        """State, in typed terms, whether this source can serve a past window and from when."""
        ...

    async def fetch_current(self, request: FetchRequest) -> Sequence[UpstreamRecord]:
        """Fetch the source's current window: whatever it publishes as "now"."""
        ...

    async def fetch_history(self, request: FetchRequest, window: HistoryWindow) -> Sequence[UpstreamRecord]:
        """Fetch one past window, raising HistoryUnavailableError when the source serves no history."""
        ...

    def build_write(self, record: UpstreamRecord, request: FetchRequest) -> FeatureWrite | None:
        """Map one upstream record to a FeatureWrite carrying its FeatureIdentity, or None to reject it."""
        ...


@dataclass(frozen=True, slots=True)
class FunctionSource:
    """An IngestionSource composed from a module's existing callables, so a live source adapts without a rewrite."""

    source_name: str
    producer: str
    channel: str
    freshness: FreshnessRule
    resolve_layer_reference: LayerReferenceCallable
    fetch_current_records: FetchCallable
    build_feature_write: BuildWriteCallable
    history: HistoryCapability
    fetch_history_records: HistoryFetchCallable | None = None
    shape: SourceShape = "feature"

    def layer_reference(self) -> str:
        """Resolve the target layer through the module's own call-time environment read."""
        return self.resolve_layer_reference()

    def history_capability(self) -> HistoryCapability:
        """Report the declared capability; a source with no history fetcher is refused before it is called."""
        if self.history.supported and self.fetch_history_records is None:
            raise SourceContractError(f"{self.source_name} claims history but supplies no history fetcher")
        return self.history

    async def fetch_current(self, request: FetchRequest) -> Sequence[UpstreamRecord]:
        """Fetch the current window through the module's own fetcher."""
        return await self.fetch_current_records(request)

    async def fetch_history(self, request: FetchRequest, window: HistoryWindow) -> Sequence[UpstreamRecord]:
        """Fetch one past window, refusing in typed terms rather than returning an empty list."""
        self.history_capability().require(self.source_name, window)
        if self.fetch_history_records is None:  # pragma: no cover - history_capability already refused.
            raise HistoryUnavailableError(f"{self.source_name} supplies no history fetcher")
        return await self.fetch_history_records(request, window)

    def build_write(self, record: UpstreamRecord, request: FetchRequest) -> FeatureWrite | None:
        """Map one upstream record through the module's own builder."""
        return self.build_feature_write(record, request)


def grid_cell_of(source: IngestionSource, write: FeatureWrite) -> GridCell | None:
    """Return the raster cell a write belongs to, holding a source to the shape it declared."""
    grid_cell = write.grid_cell
    if source.shape == "grid_cell" and grid_cell is None:
        raise SourceContractError(f"{source.source_name} declares grid_cell shape but produced an uncelled write")
    if source.shape == "feature" and grid_cell is not None:
        raise SourceContractError(f"{source.source_name} declares feature shape but produced a celled write")
    return grid_cell


def accepted_writes(
    source: IngestionSource,
    records: Sequence[UpstreamRecord],
    request: FetchRequest,
) -> tuple[list[FeatureWrite], int]:
    """Map a fetched window to writes, applying the source's freshness rule and counting what it rejected."""
    accepted: list[FeatureWrite] = []
    rejected = 0
    for record in records:
        write = source.build_write(record, request)
        if write is None or not source.freshness.accepts(write.identity.observed_at, request.now):
            rejected += 1
            continue
        grid_cell_of(source, write)
        accepted.append(write)
    return accepted, rejected


@dataclass(frozen=True, slots=True)
class SelectedWrites:
    """One fetched window reduced to the writes a run will persist, and what the reduction cost."""

    writes: list[FeatureWrite]
    rejected: int
    truncated: bool


def _truncation_rank(entry: tuple[int, FeatureWrite]) -> tuple[int, float, int]:
    """Rank a write for survival: dated outranks undated, later observation outranks earlier, arrival breaks ties."""
    position, write = entry
    observed_at = write.identity.observed_at
    if observed_at is None:
        return (0, 0.0, position)
    return (1, observed_at.timestamp(), position)


def select_writes(
    source: IngestionSource,
    records: Sequence[UpstreamRecord],
    request: FetchRequest,
) -> SelectedWrites:
    """Map, filter and cap one fetched window; a bitten cap drops the OLDEST records, never an arrival slice."""
    accepted, rejected = accepted_writes(source, records, request)
    if len(accepted) <= request.max_records:
        return SelectedWrites(writes=accepted, rejected=rejected, truncated=False)
    survivors = sorted(enumerate(accepted), key=_truncation_rank, reverse=True)[: request.max_records]
    kept_positions = {position for position, _ in survivors}
    return SelectedWrites(
        writes=[write for position, write in enumerate(accepted) if position in kept_positions],
        rejected=rejected,
        truncated=True,
    )
