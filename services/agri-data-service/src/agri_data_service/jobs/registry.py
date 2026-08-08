"""The durable job contract: the shapes a lane fills in, and the registry a stored handler token resolves through."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, Protocol

if TYPE_CHECKING:
    from datetime import datetime

EMPTY_JSON_OBJECT: Final[Mapping[str, object]] = MappingProxyType({})

# The `agri.job_definition` server defaults, restated so a spec that fills in only name/version/handler
# produces the same policy a hand-inserted row would. Drifting one of these away from the Alembic
# default does not fail anything loudly -- it just makes a spec-built lane retry a different number of
# times than an operator reading the migration expects. See models/jobs.py JobDefinition.
DEFAULT_QUEUE_NAME: Final = "default"
DEFAULT_SCHEDULE_TIMEZONE: Final = "UTC"
DEFAULT_MAX_ATTEMPTS: Final = 5
DEFAULT_LEASE_SECONDS: Final = 300
DEFAULT_TIME_BUDGET_SECONDS: Final = 240

# 30s doubling to a one-hour ceiling. The densest cron cadence in infra/cron-*/railway.json is
# every 15 minutes, so the first retry lands inside the same tick and costs nothing; the ceiling stops
# a source that is down for a day from waking on every tick and spending its whole attempt budget
# before the outage ends. There is deliberately no jitter -- see jobs/AGENTS.md "Backoff has no jitter".
DEFAULT_INITIAL_BACKOFF_SECONDS: Final = 30.0
DEFAULT_BACKOFF_MULTIPLIER: Final = 2.0
DEFAULT_MAXIMUM_BACKOFF_SECONDS: Final = 3600.0

# `job_definition.handler` is VARCHAR(500) and `job_work_item.kind` VARCHAR(100); a token longer than
# its column does not truncate, it aborts the INSERT, so the spec refuses it before it reaches SQL.
HANDLER_TOKEN_MAX_LENGTH: Final = 500
WORK_ITEM_KIND_MAX_LENGTH: Final = 100
SHARD_KEY_MAX_LENGTH: Final = 500

JobOutcomeKind = Literal["progressed", "completed", "failed", "deferred", "yielded"]

_OUTCOME_KINDS: Final[tuple[JobOutcomeKind, ...]] = ("progressed", "completed", "failed", "deferred", "yielded")

# The reason a handler's own budget yield records when it names none. A yield is neither a failure nor a
# wait on upstream: it means the handler judged that its next unit of work does not fit in the slice's
# remaining clock. The runtime parks the shard on the same primitive a deferral uses, so a yield never
# spends the retry budget -- stopping for the clock is not failing. See jobs/AGENTS.md "budget yield".
HANDLER_YIELD_REASON: Final = "handler stopped short of the slice time budget"


class JobSpecificationError(ValueError):
    """Raised when a declared job shape cannot be written to the ledger without violating a constraint."""


class UnknownJobHandlerError(LookupError):
    """Raised when a job_definition.handler token resolves to no registered callable."""


class DuplicateJobHandlerError(ValueError):
    """Raised when two lanes claim the same handler token, which would make the winner load-order dependent."""


def _require_text(value: str, *, field_name: str, limit: int) -> str:
    """Refuse an empty or over-long identifier here rather than as a constraint violation mid-transaction."""
    text_value = value.strip()
    if not text_value:
        raise JobSpecificationError(f"{field_name} must not be empty")
    if len(text_value) > limit:
        raise JobSpecificationError(f"{field_name} must be at most {limit} characters")
    return text_value


def _require_positive(value: int, *, field_name: str) -> int:
    """Refuse a non-positive value the ledger's CHECK constraints reject anyway."""
    if value <= 0:
        raise JobSpecificationError(f"{field_name} must be greater than zero")
    return value


def _require_fraction(value: float, *, field_name: str) -> float:
    """Hold a progress value inside the [0, 1] range both progress_fraction CHECK constraints enforce."""
    if not 0.0 <= value <= 1.0:
        raise JobSpecificationError(f"{field_name} must be between 0 and 1 inclusive")
    return value


def _number_field(payload: Mapping[str, object], key: str, fallback: float) -> float:
    """Read one numeric field out of an untrusted JSONB blob, refusing a value that is not a number."""
    if key not in payload:
        return fallback
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise JobSpecificationError(f"retry_policy.{key} must be a number")
    return float(value)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """The exponential wait a failed attempt serves before its work item becomes claimable again."""

    initial_backoff_seconds: float = DEFAULT_INITIAL_BACKOFF_SECONDS
    backoff_multiplier: float = DEFAULT_BACKOFF_MULTIPLIER
    maximum_backoff_seconds: float = DEFAULT_MAXIMUM_BACKOFF_SECONDS

    def __post_init__(self) -> None:
        """Refuse a policy that cannot produce a growing, bounded wait."""
        if self.initial_backoff_seconds <= 0:
            raise JobSpecificationError("initial_backoff_seconds must be greater than zero")
        if self.backoff_multiplier < 1:
            raise JobSpecificationError("backoff_multiplier must be at least 1")
        if self.maximum_backoff_seconds < self.initial_backoff_seconds:
            raise JobSpecificationError("maximum_backoff_seconds must be at least initial_backoff_seconds")

    def backoff_seconds(self, attempt_number: int) -> float:
        """Seconds to wait after `attempt_number` failed attempts; the first failure waits the initial delay."""
        if attempt_number < 1:
            raise JobSpecificationError("attempt_number must be at least 1")
        delay = self.initial_backoff_seconds * (self.backoff_multiplier ** (attempt_number - 1))
        return min(delay, self.maximum_backoff_seconds)

    def to_json(self) -> dict[str, float]:
        """Render the policy as the JSONB object `job_definition.retry_policy` stores."""
        return {
            "initial_backoff_seconds": self.initial_backoff_seconds,
            "backoff_multiplier": self.backoff_multiplier,
            "maximum_backoff_seconds": self.maximum_backoff_seconds,
        }

    @classmethod
    def from_json(cls, value: object) -> RetryPolicy:
        """Read a stored retry_policy blob as untrusted input, falling back to the default per absent field."""
        if value is None:
            return DEFAULT_RETRY_POLICY
        if not isinstance(value, Mapping):
            raise JobSpecificationError("retry_policy must be a JSON object")
        payload: Mapping[str, object] = {str(key): item for key, item in value.items()}
        return cls(
            initial_backoff_seconds=_number_field(payload, "initial_backoff_seconds", DEFAULT_INITIAL_BACKOFF_SECONDS),
            backoff_multiplier=_number_field(payload, "backoff_multiplier", DEFAULT_BACKOFF_MULTIPLIER),
            maximum_backoff_seconds=_number_field(payload, "maximum_backoff_seconds", DEFAULT_MAXIMUM_BACKOFF_SECONDS),
        )


DEFAULT_RETRY_POLICY: Final = RetryPolicy()


@dataclass(frozen=True, slots=True)
class JobDefinitionSpec:
    """The declarative shape one durable lane fills in; `ensure_job_definition` turns it into a ledger row."""

    name: str
    version: str
    handler: str
    queue_name: str = DEFAULT_QUEUE_NAME
    schedule: str | None = None
    schedule_timezone: str = DEFAULT_SCHEDULE_TIMEZONE
    enabled: bool = True
    concurrency_key: str | None = None
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    time_budget_seconds: int = DEFAULT_TIME_BUDGET_SECONDS
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY
    parameters: Mapping[str, object] = field(default=EMPTY_JSON_OBJECT)

    def __post_init__(self) -> None:
        """Refuse a spec the ledger's CHECK constraints would reject, before it reaches a transaction."""
        _require_text(self.name, field_name="name", limit=150)
        _require_text(self.version, field_name="version", limit=100)
        _require_text(self.handler, field_name="handler", limit=HANDLER_TOKEN_MAX_LENGTH)
        _require_positive(self.max_attempts, field_name="max_attempts")
        _require_positive(self.lease_seconds, field_name="lease_seconds")
        _require_positive(self.time_budget_seconds, field_name="time_budget_seconds")
        if self.lease_seconds <= self.time_budget_seconds:
            # A lease shorter than one slice means the worker's own lease expires while it is still
            # working, another worker claims the item behind it, and the first worker's next checkpoint
            # is refused by the fence. That is a self-inflicted fence loss on every single slice.
            raise JobSpecificationError("lease_seconds must exceed time_budget_seconds")


@dataclass(frozen=True, slots=True)
class JobWorkItemSpec:
    """One shard of a run: the smallest unit that can be claimed, checkpointed and resumed on its own."""

    shard_key: str
    kind: str
    payload: Mapping[str, object] = field(default=EMPTY_JSON_OBJECT)
    priority: int = 0
    available_at: datetime | None = None

    def __post_init__(self) -> None:
        """Refuse an unaddressable shard; `shard_key` is the run-scoped idempotency key, not a label."""
        _require_text(self.shard_key, field_name="shard_key", limit=SHARD_KEY_MAX_LENGTH)
        _require_text(self.kind, field_name="kind", limit=WORK_ITEM_KIND_MAX_LENGTH)
        if self.available_at is not None and self.available_at.utcoffset() is None:
            raise JobSpecificationError("available_at must include a timezone")


HeartbeatCallable = Callable[[], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class JobInvocation:
    """Everything a handler is told about the shard it holds, and nothing about the ledger that holds it."""

    shard_key: str
    kind: str
    payload: Mapping[str, object]
    cursor: Mapping[str, object] | None
    parameters: Mapping[str, object]
    attempt_number: int
    max_attempts: int
    progress_fraction: float
    # Seconds left in THIS slice's time budget, measured on the same monotonic clock `run_job_slice`
    # started, snapshotted when this call was built. It is the binding constraint, not the lease: the
    # runtime refuses a budget that is not strictly shorter than `lease_seconds`, so the lease always
    # outlives the budget. It does not tick down inside one call -- the contract is one bounded step per
    # call, and the next call carries a fresh figure. See jobs/AGENTS.md "The budget-aware handler contract".
    seconds_remaining: float
    heartbeat: HeartbeatCallable

    @property
    def is_final_attempt(self) -> bool:
        """True when a failure here dead-letters the shard rather than scheduling another retry."""
        return self.attempt_number >= self.max_attempts

    def has_budget_for(self, seconds: float) -> bool:
        """True when the slice's remaining budget still covers a step this handler estimates at `seconds`."""
        return seconds <= self.seconds_remaining


@dataclass(frozen=True, slots=True)
class JobHandlerOutcome:
    """What one bounded slice of handler work decided: it progressed, completed, failed, or must wait."""

    kind: JobOutcomeKind
    cursor: Mapping[str, object] | None = None
    progress_fraction: float = 0.0
    failure_class: str | None = None
    reason: str | None = None
    resume_at: datetime | None = None
    metrics: Mapping[str, object] = field(default=EMPTY_JSON_OBJECT)

    def __post_init__(self) -> None:
        """Hold each case to the fields it needs; an outcome that cannot be acted on is a silent stall."""
        if self.kind not in _OUTCOME_KINDS:
            raise JobSpecificationError(f"unknown job outcome kind {self.kind!r}")
        _require_fraction(self.progress_fraction, field_name="progress_fraction")
        if self.kind == "progressed" and self.cursor is None:
            # Progress with no cursor is unresumable: the next attempt would restart from the top and
            # the work already done would be repeated forever, one slice at a time.
            raise JobSpecificationError("a progressed outcome must carry the cursor its work resumes from")
        if self.kind == "failed" and not (self.failure_class or "").strip():
            raise JobSpecificationError("a failed outcome must name its failure class")
        if self.kind in {"failed", "deferred"} and not (self.reason or "").strip():
            raise JobSpecificationError(f"a {self.kind} outcome must state its reason")
        if self.kind == "deferred" and self.resume_at is None:
            raise JobSpecificationError("a deferred outcome must state when it becomes workable again")
        if self.kind == "yielded" and self.resume_at is not None:
            # A yield's resume time is "the next tick" by definition. A handler that means "not before
            # T" is describing a deferral, and conflating the two hides which of the clock and upstream
            # actually stalled -- the one thing an operator reading a parked shard needs to know.
            raise JobSpecificationError("a yielded outcome may not pin a resume time; defer instead")
        if self.resume_at is not None and self.resume_at.utcoffset() is None:
            raise JobSpecificationError("resume_at must include a timezone")

    @classmethod
    def progressed(
        cls,
        cursor: Mapping[str, object],
        *,
        progress_fraction: float,
        metrics: Mapping[str, object] = EMPTY_JSON_OBJECT,
    ) -> JobHandlerOutcome:
        """Report a bounded step forward: this cursor is durable, and there is more work on this shard."""
        return cls(kind="progressed", cursor=cursor, progress_fraction=progress_fraction, metrics=metrics)

    @classmethod
    def completed(
        cls,
        *,
        cursor: Mapping[str, object] | None = None,
        metrics: Mapping[str, object] = EMPTY_JSON_OBJECT,
    ) -> JobHandlerOutcome:
        """Report the shard finished; an optional final cursor is checkpointed before the item is closed."""
        return cls(kind="completed", cursor=cursor, progress_fraction=1.0, metrics=metrics)

    @classmethod
    def failed(
        cls,
        failure_class: str,
        reason: str,
        *,
        metrics: Mapping[str, object] = EMPTY_JSON_OBJECT,
    ) -> JobHandlerOutcome:
        """Report a failure the runtime should retry or, on the last attempt, dead-letter."""
        return cls(kind="failed", failure_class=failure_class, reason=reason, metrics=metrics)

    @classmethod
    def deferred(
        cls,
        resume_at: datetime,
        reason: str,
        *,
        cursor: Mapping[str, object] | None = None,
        progress_fraction: float = 0.0,
        metrics: Mapping[str, object] = EMPTY_JSON_OBJECT,
    ) -> JobHandlerOutcome:
        """Report "not yet": upstream has nothing to give until `resume_at`, and this is not a failure.

        A cursor is checkpointed before the shard is parked, so the chunks this attempt did walk before
        upstream said "come back later" are durable and are never re-walked on the next claim.
        """
        return cls(
            kind="deferred",
            cursor=cursor,
            progress_fraction=progress_fraction,
            reason=reason,
            resume_at=resume_at,
            metrics=metrics,
        )

    @classmethod
    def yielded(
        cls,
        *,
        cursor: Mapping[str, object] | None = None,
        progress_fraction: float = 0.0,
        reason: str = HANDLER_YIELD_REASON,
        metrics: Mapping[str, object] = EMPTY_JSON_OBJECT,
    ) -> JobHandlerOutcome:
        """Report "out of clock": this shard is parked for the next tick, and stopping early is not a failure.

        Say this when `invocation.has_budget_for(...)` refuses the next unit of work. The cursor is
        checkpointed before the shard is parked, the shard becomes claimable immediately, and no attempt
        is charged against `max_attempts` -- the runtime raises the ceiling exactly as a deferral does.
        """
        return cls(
            kind="yielded",
            cursor=cursor,
            progress_fraction=progress_fraction,
            reason=reason,
            metrics=metrics,
        )


class JobHandler(Protocol):
    """The shape a durable lane fills in: one bounded, resumable step against one claimed shard."""

    async def __call__(self, invocation: JobInvocation, /) -> JobHandlerOutcome:
        """Do at most one bounded step of this shard's work and report what it decided."""
        ...


class JobHandlerRegistry:
    """Maps a `job_definition.handler` token to the callable that runs it, so a stored row resolves or fails loudly."""

    def __init__(self) -> None:
        """Start empty; every lane registers itself at import time."""
        self._handlers: dict[str, JobHandler] = {}

    def register(self, token: str, handler: JobHandler) -> None:
        """Bind one handler token, refusing a second claim on a token another lane already owns."""
        resolved = _require_text(token, field_name="handler token", limit=HANDLER_TOKEN_MAX_LENGTH)
        existing = self._handlers.get(resolved)
        if existing is not None and existing is not handler:
            raise DuplicateJobHandlerError(f"handler token {resolved!r} is already registered")
        self._handlers[resolved] = handler

    def handler_for(self, token: str) -> JobHandler:
        """Resolve a stored handler token, naming what is registered when it resolves to nothing."""
        handler = self._handlers.get(token)
        if handler is None:
            known = ", ".join(sorted(self._handlers)) or "nothing"
            raise UnknownJobHandlerError(f"no handler registered for {token!r}; registered tokens are {known}")
        return handler

    def tokens(self) -> tuple[str, ...]:
        """Every registered token, sorted, so an operator can diff the registry against the definition table."""
        return tuple(sorted(self._handlers))

    def __contains__(self, token: object) -> bool:
        """True when this token resolves to code without raising."""
        return isinstance(token, str) and token in self._handlers


JOB_HANDLERS: Final = JobHandlerRegistry()


def job_handler[HandlerT: JobHandler](
    token: str,
    *,
    registry: JobHandlerRegistry | None = None,
) -> Callable[[HandlerT], HandlerT]:
    """Decorator that binds a coroutine to a handler token at import time, returning it unchanged."""

    def bind(handler: HandlerT) -> HandlerT:
        (registry if registry is not None else JOB_HANDLERS).register(token, handler)
        return handler

    return bind
