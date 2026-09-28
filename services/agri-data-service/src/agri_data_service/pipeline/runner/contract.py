"""The config-lane contract (spec §4.2, DRAFT until the Phase-2 re-freeze): strategy Protocols and value types.

Strategies import this module and nothing else from `pipeline/runner/` (S14). See
`pipeline/runner/AGENTS.md` "The contract" for every type's rationale and the draft deviations.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, Protocol, get_args, runtime_checkable
from urllib.parse import urlencode

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date, datetime

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.foundation.lane_config.models import LaneConfig, ProviderConfig
    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.foundation.region.manifest import Region

TurnMode = Literal["forward", "gap-fill", "transform"]
TURN_MODES: Final[tuple[TurnMode, ...]] = get_args(TurnMode)

#: S5: why a day the turn owed was not written. Days behind the provider edge are the alarm's only input.
UnwrittenReason = Literal[
    "unsettled",
    "deferred_quota",
    "deferred_budget",
    "upstream_unavailable",
    "refused_partial",
    "retention_exceeded",
    "strategy_error",
    # Another run holds the stream-day's lane-day lock; the next turn takes it (M4).
    "contended",
    # The base rung is published but the coarse rungs could not be derived this turn (H1).
    "ladder_owed",
]
UNWRITTEN_REASONS: Final[frozenset[UnwrittenReason]] = frozenset(get_args(UnwrittenReason))

#: G0's probe statuses, reused verbatim (spec §4.4 status table).
ProbeStatus = Literal["ok", "invalid", "blind", "unavailable", "deferred"]
PROBE_STATUSES: Final[frozenset[ProbeStatus]] = frozenset(get_args(ProbeStatus))

#: S6: where the turn's edge came from; `lag_fallback` is `today - publication_lag_days`.
EdgeSource = Literal["probe", "lag_fallback"]


# --- Requests and responses ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceRequest:
    """One request unit, the retry grain (a tile, a cell chunk, a release file); unique by `unit` within a turn."""

    #: Stable within a turn and across turns for the same question; checkpoints and reports key on it.
    unit: str
    #: A provider endpoint name from `lanes/_providers/<provider>.toml`, never a host.
    endpoint: str
    #: Every day this unit's response answers; may be wider than the days the runner asked for.
    days: tuple[date, ...]
    #: Query parameters, key-free: the provider client adds the key and picks the host.
    parameters: tuple[tuple[str, str], ...] = ()
    #: The parameters whose values settle a day; checkpoint eligibility is judged per parameter.
    requested_parameters: frozenset[str] = frozenset()
    #: Binds a checkpoint to the support (lattice cells) this unit was planned over.
    support_digest: str = ""

    def query(self) -> dict[str, str]:
        """The parameters as a mapping, in the order the strategy declared them."""
        return dict(self.parameters)

    def credential_free_url(self, provider: ProviderConfig) -> str:
        """The unit's URL on the provider's FREE host: the weight, checkpoint and report key. Never carries a key."""
        endpoint = provider.endpoints[self.endpoint]
        return f"https://{endpoint.host}{endpoint.path}?{urlencode(sorted(self.parameters))}"


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """One answered send: the body bytes, the credential-free URL it was read from, and its retrieval instant."""

    body: bytes
    request_url: str
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class SourceResponse:
    """A unit's verified answer, as `fetch` parsed it; the runner never interprets `payload`."""

    request: SourceRequest
    body: bytes
    retrieved_at: datetime
    #: Requested parameters whose values were all present; a checkpoint keeps only a fully valued answer.
    complete_parameters: frozenset[str] = frozenset()
    #: Whatever the strategy parsed out of `body`; opaque to the runner.
    payload: object = None
    #: True when the runner replayed this answer from a checkpoint instead of the network.
    restored: bool = False

    @property
    def digest(self) -> str:
        """SHA-256 of the original response bytes: the S11 source-receipt input."""
        return hashlib.sha256(self.body).hexdigest()

    @property
    def checkpoint_eligible(self) -> bool:
        """Never retain a not-yet-mirrored null: every requested parameter must be complete."""
        requested = self.request.requested_parameters
        return bool(self.body) and bool(requested) and requested <= self.complete_parameters


class ProviderClient(Protocol):
    """The one HTTP seam (f1-providers' `ingest/provider_client.py`). One call is ONE logical attempt.

    The runner owns the per-unit retry ladder and the 429 series (spec §4.3 step 4), so a client
    raises `SourceThrottledError` / `SourceUnavailableError` (or `ingest/http.py`'s typed errors)
    instead of re-sending a 429 or 5xx itself. See `pipeline/runner/AGENTS.md` "One retry ladder".
    """

    async def get(self, endpoint: str, parameters: Mapping[str, str], *, probe: bool = False) -> ProviderResponse: ...


# --- Typed fetch errors (quota refusals and 5xx never reach `settle`) ---------------------------


class SourceFetchError(Exception):
    """A unit's fetch failed in a way the runner classifies; never shown to `settle`."""


class SourceThrottledError(SourceFetchError):
    """HTTP 429 or a provider quota refusal: the 429 series, then `deferred_quota`."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class SourceUnavailableError(SourceFetchError):
    """A 5xx, timeout or transport failure: exponential retries, then `upstream_unavailable`."""


class TurnBudgetExhaustedError(SourceFetchError):
    """A send would take the turn past its mode's weighted-call cap (S19); nothing was sent."""


class ProviderConfigurationError(Exception):
    """A required provider key is empty or rejected (401/403): a named config error, exit 78 (FR-2)."""


# --- Settlement ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Written:
    """The day carries values: `present_units` of `expected_units` (cells, stations, release rows)."""

    expected_units: int
    present_units: int
    #: The day's own source digest (S11). `None` lets the runner digest the day's rows instead; a
    #: strategy whose rows carry a retrieval instant must set it, or every refetch reads as a revision.
    source_digest: str | None = None

    @property
    def partial(self) -> bool:
        """Fewer units than expected: refused under `refuse`, written-and-rechecked otherwise."""
        return self.present_units < self.expected_units


@dataclass(frozen=True, slots=True)
class Absent:
    """The source has nothing for the day, and `proof` says why that is settled (mirrored past, or retention)."""

    reason: str
    proof: str


@dataclass(frozen=True, slots=True)
class Unsettled:
    """The day is not settled yet; the next turn asks again."""

    reason: UnwrittenReason = "unsettled"
    detail: str = ""


Settlement = Written | Absent | Unsettled

# --- Edge probe ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProbeWindow:
    """The days a probe may look at: at most 14, ending at the turn's candidate edge (S19)."""

    first: date
    last: date


@dataclass(frozen=True, slots=True)
class ProviderEdge:
    """What a probe saw: the valued days in its window, its status, and the edge they imply (S6)."""

    status: ProbeStatus
    window: ProbeWindow
    valued_days: frozenset[date] = frozenset()
    detail: str | None = None
    source: EdgeSource = "probe"

    @property
    def edge(self) -> date | None:
        """The newest valued day, or `None` when the probe saw none."""
        return max(self.valued_days) if self.valued_days else None


# --- Day context --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StreamDayState:
    """One stream-day as the runner's full-ladder census and turn receipt see it."""

    status: PartitionDayStatus
    source_digest: str | None = None
    present_units: int | None = None
    expected_units: int | None = None
    input_digests: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DayContext:
    """What `settle` and `derive` may know about a day, built from the census the turn already paid for."""

    day: date
    states: Mapping[str, StreamDayState]
    #: Per stream, the census window's days that carry values, ascending.
    published_days: Mapping[str, tuple[date, ...]]
    #: Per stream, the census window's governed absences, ascending.
    absent_days: Mapping[str, tuple[date, ...]]
    edge: ProviderEdge | None = None
    #: Days at or before this are immutable history (a snapshot another writer owns); never rewritten.
    immutable_through: date | None = None
    #: The oldest day the source still serves; older days are `retention_exceeded`.
    retention_floor: date | None = None
    #: The streams this lane writes, in `[[streams]]` order.
    output_streams: tuple[str, ...] = ()
    #: A transform's inputs in precedence order (`inputs` order): input lane id -> its streams.
    input_streams: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def later_published_day(self, stream: str) -> date | None:
        """The mirrored-past proof: the earliest later day this stream already publishes with values."""
        return next((later for later in self.published_days.get(stream, ()) if later > self.day), None)

    def receipt_digest(self, stream: str) -> str | None:
        """The source digest this day was last written from (S11), or `None`."""
        state = self.states.get(stream)
        return None if state is None else state.source_digest

    def short_day_units(self, stream: str) -> int | None:
        """The present units of a partial written day, or `None` when the day is not a short day."""
        state = self.states.get(stream)
        if state is None or state.present_units is None or state.expected_units is None:
            return None
        return state.present_units if state.present_units < state.expected_units else None


@dataclass(frozen=True, slots=True)
class Derivation:
    """A transform's answer for one day: one table per output stream (a bare table for a one-stream lane), or `None`."""

    table: pa.Table | Mapping[str, pa.Table] | None
    #: Input streams whose rows for this day are wholly superseded; pruned only when `[pruning]` is on.
    superseded_inputs: tuple[str, ...] = ()


# --- Strategy Protocols -------------------------------------------------------------------------


@runtime_checkable
class IngestStrategy(Protocol):
    """One source's irreducible behaviour for one lane; the runner owns everything else (D2)."""

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> Sequence[SourceRequest]: ...

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse: ...

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement: ...

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table | Mapping[str, pa.Table]: ...


@runtime_checkable
class EdgeProbingStrategy(Protocol):
    """Optional (S6); required for a settled weighted-provider lane firing more than once a day (S19)."""

    async def probe_edge(self, client: ProviderClient, window: ProbeWindow) -> ProviderEdge: ...


@runtime_checkable
class ReleaseCalendarStrategy(Protocol):
    """Optional: a `release_series` names its release days so the others are never owed."""

    def release_days(self, first: date, last: date) -> Sequence[date]: ...


@runtime_checkable
class TransformStrategy(Protocol):
    """A derived fact lane (D4): one day's inputs, in `inputs` order, in; one derived table out."""

    def derive(self, day: date, inputs: Mapping[str, pa.Table | None], context: DayContext) -> Derivation: ...


__all__ = [
    "PROBE_STATUSES",
    "TURN_MODES",
    "UNWRITTEN_REASONS",
    "Absent",
    "DayContext",
    "Derivation",
    "EdgeProbingStrategy",
    "EdgeSource",
    "IngestStrategy",
    "ProbeStatus",
    "ProbeWindow",
    "ProviderClient",
    "ProviderConfigurationError",
    "ProviderEdge",
    "ProviderResponse",
    "ReleaseCalendarStrategy",
    "Settlement",
    "SourceFetchError",
    "SourceRequest",
    "SourceResponse",
    "SourceThrottledError",
    "SourceUnavailableError",
    "StreamDayState",
    "TransformStrategy",
    "TurnBudgetExhaustedError",
    "TurnMode",
    "Unsettled",
    "UnwrittenReason",
    "Written",
]
