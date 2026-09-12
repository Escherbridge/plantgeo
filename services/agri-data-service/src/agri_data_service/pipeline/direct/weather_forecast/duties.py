"""Durable bounded forecast duty APIs and unregistered schedule descriptors; see AGENTS.md."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, model_validator

from agri_data_service.pipeline.direct.weather_forecast.artifacts import read_run
from agri_data_service.pipeline.direct.weather_forecast.remote_artifacts import (
    MAX_CAS_ATTEMPTS,
    RemoteArtifactError,
    RemoteRetentionPolicy,
    RemoteRunEntry,
    canonical_bytes,
    delete_retired_entry,
    digest,
    load_catalog,
    product_root,
    publish_prepared_run,
    read_remote_run,
)
from agri_data_service.warehouse.weather_forecast.contracts import FrozenContract, SafeToken, Text, UTCInstant

if TYPE_CHECKING:
    from agri_data_service.ingest.http import BoundedResponse
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage, StoredAvailabilityObject
    from agri_data_service.pipeline.parquet.objectstore import ObjectStoreBackend

MAX_DISCOVERIES_PER_DUTY = 4
MAX_PUBLICATIONS_PER_DUTY = 1
MAX_RECONCILIATIONS_PER_DUTY = 4
MAX_PRUNES_PER_DUTY = 2
MAX_PENDING_WORK = 48
MAX_WORK_STATE_BYTES = 256 * 1024
MAX_STATUS_BYTES = 128 * 1024
MAX_COOLDOWN_SECONDS = 24 * 60 * 60
RETENTION_PRUNE_DELAY = timedelta(hours=24)
MAX_WORK_ATTEMPTS = 8
SHA_LENGTH = 64


@dataclass(frozen=True, slots=True)
class DutyDescriptor:
    """One proposed schedule; integration must register each duty separately."""

    name: str
    cron_utc: str
    purpose: str
    maximum_items: int


DUTY_DESCRIPTORS = (
    DutyDescriptor(
        name="weather-forecast-forward",
        cron_utc="17 * * * *",
        purpose="discover exact provider runs and publish at most one admitted run",
        maximum_items=MAX_PUBLICATIONS_PER_DUTY,
    ),
    DutyDescriptor(
        name="weather-forecast-repair",
        cron_utc="47 */6 * * *",
        purpose="verify retained runs, author recoverable work, and prune bounded expired retention work",
        maximum_items=MAX_RECONCILIATIONS_PER_DUTY,
    ),
    DutyDescriptor(
        name="weather-forecast-status",
        cron_utc="7 */3 * * *",
        purpose="publish run and valid-time coverage with durable work and cooldown status",
        maximum_items=1,
    ),
)


class ForecastCandidate(FrozenContract):
    """An exact provider run identity discovered before any source adapter is invoked."""

    product_id: SafeToken
    run_id: SafeToken
    provider: SafeToken
    model: SafeToken
    model_version: Text | None
    model_init_at: UTCInstant
    provider_issued_at: UTCInstant | None

    @model_validator(mode="after")
    def issue_follows_initialization(self) -> ForecastCandidate:
        if self.provider_issued_at is not None and self.provider_issued_at < self.model_init_at:
            raise ValueError("provider issue cannot precede model initialization")
        return self


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """A locally verified artifact coordinate returned for one exact candidate."""

    root: Path
    product_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class DiscoveryBatch:
    """A bounded run census and optional provider delay already parsed from its response."""

    candidates: tuple[ForecastCandidate, ...]
    retry_after_seconds: int | None = None
    reason: str = "provider_requested_cooldown"

    def __post_init__(self) -> None:
        if len(self.candidates) > MAX_DISCOVERIES_PER_DUTY:
            raise ValueError("forecast discovery batch exceeds its run ceiling")
        if self.retry_after_seconds is not None:
            _bounded_delay(self.retry_after_seconds)
            if self.candidates:
                raise ValueError("a provider cooldown response cannot also admit discovered runs")


class ProviderDeferred(RuntimeError):
    """Carry a provider-directed retry delay into durable duty state."""

    def __init__(self, retry_after_seconds: int, reason: str = "provider_requested_cooldown") -> None:
        super().__init__(reason)
        self.retry_after_seconds = _bounded_delay(retry_after_seconds)
        self.reason = reason

    @classmethod
    def from_response(cls, response: BoundedResponse, *, now: UTCInstant) -> ProviderDeferred:
        """Consume the shared bounded transport's preserved Retry-After value."""
        delay = provider_retry_after(response, now=now)
        if delay is None:
            raise ValueError("bounded response does not carry a usable Retry-After value")
        return cls(delay)


class WorkItem(FrozenContract):
    """One bounded, replayable unit of publication, repair, or delayed retention work."""

    kind: Literal["publish", "repair", "prune"]
    work_id: SafeToken
    candidate: ForecastCandidate | None = None
    retired_entry: RemoteRunEntry | None = None
    not_before: UTCInstant
    attempts: Annotated[int, Field(strict=True, ge=0, le=MAX_WORK_ATTEMPTS)] = 0
    last_error: Text | None = None

    @model_validator(mode="after")
    def one_payload(self) -> WorkItem:
        if self.kind in {"publish", "repair"} and (self.candidate is None or self.retired_entry is not None):
            raise ValueError("publish and repair work require only an exact candidate")
        if self.kind == "prune" and (self.retired_entry is None or self.candidate is not None):
            raise ValueError("prune work requires only a retired run entry")
        return self


class ForecastWorkState(FrozenContract):
    """The durable planner state that survives scheduler and process restarts."""

    schema_version: Literal["weather-forecast-work-state/v1"] = "weather-forecast-work-state/v1"
    product_id: SafeToken
    updated_at: UTCInstant
    last_discovered_init_at: UTCInstant | None = None
    cooldown_until: UTCInstant | None = None
    cooldown_reason: Text | None = None
    pending: Annotated[tuple[WorkItem, ...], Field(max_length=MAX_PENDING_WORK)] = ()

    @model_validator(mode="after")
    def coherent_state(self) -> ForecastWorkState:
        if (self.cooldown_until is None) != (self.cooldown_reason is None):
            raise ValueError("cooldown instant and reason must be stored together")
        identities = [item.work_id for item in self.pending]
        if len(set(identities)) != len(identities):
            raise ValueError("forecast work state contains duplicate work identities")
        if tuple(sorted(self.pending, key=_work_order)) != self.pending:
            raise ValueError("forecast work state must be deterministically ordered")
        return self


@dataclass(frozen=True, slots=True)
class DutyResult:
    """One observable bounded duty outcome."""

    status: str
    discovered: int = 0
    published: int = 0
    authored: int = 0
    pruned: int = 0
    pending: int = 0
    detail: str | None = None


class RunAvailability(FrozenContract):
    """One retained run's exact half-open valid-time availability."""

    run_id: SafeToken
    model_version: Text | None
    model_init_at: UTCInstant
    provider_issued_at: UTCInstant | None
    published_at: UTCInstant
    valid_start: UTCInstant
    valid_end: UTCInstant
    row_count: Annotated[int, Field(strict=True, ge=1)]


class ForecastStatus(FrozenContract):
    """Content-addressed coverage and planner status for one product."""

    schema_version: Literal["weather-forecast-status/v1"] = "weather-forecast-status/v1"
    product_id: SafeToken
    created_at: UTCInstant
    active_run_id: SafeToken | None
    runs: Annotated[tuple[RunAvailability, ...], Field(max_length=16)]
    pending_publish: Annotated[int, Field(strict=True, ge=0, le=MAX_PENDING_WORK)]
    pending_repair: Annotated[int, Field(strict=True, ge=0, le=MAX_PENDING_WORK)]
    pending_prune: Annotated[int, Field(strict=True, ge=0, le=MAX_PENDING_WORK)]
    cooldown_until: UTCInstant | None
    cooldown_reason: Text | None


class StatusPointer(FrozenContract):
    """Mutable pointer to one immutable content-addressed status document."""

    schema_version: Literal["weather-forecast-status-pointer/v1"] = "weather-forecast-status-pointer/v1"
    product_id: SafeToken
    status_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    created_at: UTCInstant


DiscoveryPort = Callable[[UTCInstant | None, int], DiscoveryBatch]
MaterializePort = Callable[[ForecastCandidate], PreparedRun]


def work_state_key(product_id: str) -> str:
    return f"{product_root(product_id)}/_WORK.json"


def status_pointer_key(product_id: str) -> str:
    return f"{product_root(product_id)}/_STATUS.json"


def status_object_key(sha256: str) -> str:
    if len(sha256) != SHA_LENGTH or any(character not in "0123456789abcdef" for character in sha256):
        raise ValueError("invalid forecast status identity")
    return f"weather-forecast/schema=v1/status/sha256={sha256}.json"


def read_work_state(
    storage: AvailabilityStorage, *, product_id: str, now: UTCInstant
) -> tuple[StoredAvailabilityObject | None, ForecastWorkState]:
    """Read strict planner state or return an empty state for first execution."""
    stored = storage.read(work_state_key(product_id), max_bytes=MAX_WORK_STATE_BYTES)
    if stored is None:
        return None, ForecastWorkState(product_id=product_id, updated_at=now)
    try:
        state = ForecastWorkState.model_validate_json(stored.payload)
    except ValueError as exc:
        raise RemoteArtifactError("weather forecast work state is malformed") from exc
    if state.product_id != product_id:
        raise RemoteArtifactError("weather forecast work state product mismatch")
    return stored, state


def provider_retry_after(response: BoundedResponse, *, now: UTCInstant) -> int | None:
    """Turn the shared transport's Retry-After value into a bounded durable delay."""
    return _parse_retry_after(getattr(response, "retry_after", None), now)


def record_provider_cooldown(
    storage: AvailabilityStorage,
    *,
    product_id: str,
    now: UTCInstant,
    retry_after_seconds: int,
    reason: str,
) -> ForecastWorkState:
    """Persist provider backoff by CAS so a restarted scheduler observes it before fetching."""
    delay = _bounded_delay(retry_after_seconds)

    def update(state: ForecastWorkState) -> ForecastWorkState:
        until = now + timedelta(seconds=delay)
        if state.cooldown_until is not None and state.cooldown_until > until:
            until = state.cooldown_until
        return state.model_copy(update={"updated_at": now, "cooldown_until": until, "cooldown_reason": reason})

    return _mutate_state(storage, product_id=product_id, now=now, update=update)


def author_discovery_work(
    storage: AvailabilityStorage,
    *,
    product_id: str,
    candidates: tuple[ForecastCandidate, ...],
    now: UTCInstant,
) -> ForecastWorkState:
    """Persist a bounded exact-run census before any source materializer may run."""
    if len(candidates) > MAX_DISCOVERIES_PER_DUTY:
        raise ValueError("forecast discovery exceeded its per-duty run ceiling")
    unique = {candidate.run_id: candidate for candidate in candidates}
    if len(unique) != len(candidates):
        raise ValueError("forecast discovery returned duplicate run identities")
    for candidate in candidates:
        if candidate.product_id != product_id or candidate.model_init_at > now:
            raise ValueError("forecast discovery candidate has the wrong product or a future initialization")

    def update(state: ForecastWorkState) -> ForecastWorkState:
        pending = {item.work_id: item for item in state.pending}
        for candidate in candidates:
            work_id = f"publish.{candidate.run_id}"
            current = pending.get(work_id)
            proposed = WorkItem(kind="publish", work_id=work_id, candidate=candidate, not_before=now)
            if current is not None and current.candidate != candidate:
                raise RemoteArtifactError("one discovered run identity changed semantic coordinates")
            pending.setdefault(work_id, proposed)
        if len(pending) > MAX_PENDING_WORK:
            raise RemoteArtifactError("forecast work queue reached its retention ceiling")
        latest = state.last_discovered_init_at
        for candidate in candidates:
            if latest is None or candidate.model_init_at > latest:
                latest = candidate.model_init_at
        return state.model_copy(
            update={
                "updated_at": now,
                "last_discovered_init_at": latest,
                "pending": tuple(sorted(pending.values(), key=_work_order)),
            }
        )

    return _mutate_state(storage, product_id=product_id, now=now, update=update)


def run_forward_duty(  # noqa: PLR0913
    storage: AvailabilityStorage,
    *,
    product_id: str,
    now: UTCInstant,
    discover: DiscoveryPort,
    materialize: MaterializePort,
    retention: RemoteRetentionPolicy | None = None,
) -> DutyResult:
    """Discover bounded exact runs, respect durable cooldown, and publish at most one run."""
    _stored, initial = read_work_state(storage, product_id=product_id, now=now)
    if initial.cooldown_until is not None and now < initial.cooldown_until:
        return DutyResult(status="cooldown", pending=len(initial.pending), detail=initial.cooldown_until.isoformat())
    try:
        batch = discover(initial.last_discovered_init_at, MAX_DISCOVERIES_PER_DUTY)
    except ProviderDeferred as deferred:
        state = record_provider_cooldown(
            storage,
            product_id=product_id,
            now=now,
            retry_after_seconds=deferred.retry_after_seconds,
            reason=deferred.reason,
        )
        if state.cooldown_until is None:
            raise RemoteArtifactError("durable provider cooldown was not recorded")
        return DutyResult(
            status="cooldown_recorded",
            pending=len(state.pending),
            detail=state.cooldown_until.isoformat(),
        )
    if batch.retry_after_seconds is not None:
        state = record_provider_cooldown(
            storage,
            product_id=product_id,
            now=now,
            retry_after_seconds=batch.retry_after_seconds,
            reason=batch.reason,
        )
        if state.cooldown_until is None:
            raise RemoteArtifactError("durable provider cooldown was not recorded")
        return DutyResult(
            status="cooldown_recorded",
            pending=len(state.pending),
            detail=state.cooldown_until.isoformat(),
        )
    state = author_discovery_work(storage, product_id=product_id, candidates=batch.candidates, now=now)
    due = [item for item in state.pending if item.kind in {"publish", "repair"} and item.not_before <= now]
    if not due:
        return DutyResult(status="checked", discovered=len(batch.candidates), pending=len(state.pending))
    item = due[0]
    if item.candidate is None:
        raise RemoteArtifactError("publish or repair work lost its exact run identity")
    try:
        prepared = materialize(item.candidate)
        if (prepared.product_id, prepared.run_id) != (item.candidate.product_id, item.candidate.run_id):
            raise RemoteArtifactError("materializer returned a different forecast run identity")
        manifest, _series = read_run(root=prepared.root, product_id=prepared.product_id, run_id=prepared.run_id)
        if (
            manifest.run.provider != item.candidate.provider
            or manifest.run.model != item.candidate.model
            or manifest.run.model_version != item.candidate.model_version
            or manifest.run.model_init_at != item.candidate.model_init_at
            or manifest.run.provider_issued_at != item.candidate.provider_issued_at
        ):
            raise RemoteArtifactError("materialized forecast does not match the exact discovered run identity")
        result = publish_prepared_run(
            storage,
            local_root=prepared.root,
            product_id=prepared.product_id,
            run_id=prepared.run_id,
            published_at=now,
            retention=retention,
        )
    except ProviderDeferred as deferred:
        state = _record_retry(storage, product_id=product_id, now=now, item=item, detail=deferred.reason)
        state = record_provider_cooldown(
            storage,
            product_id=product_id,
            now=now,
            retry_after_seconds=deferred.retry_after_seconds,
            reason=deferred.reason,
        )
        return DutyResult(status="cooldown_recorded", discovered=len(batch.candidates), pending=len(state.pending))
    except (OSError, RemoteArtifactError, ValueError) as exc:
        state = _record_retry(storage, product_id=product_id, now=now, item=item, detail=str(exc))
        return DutyResult(
            status="publication_deferred", discovered=len(batch.candidates), pending=len(state.pending), detail=str(exc)
        )
    state = _complete_publication(
        storage,
        product_id=product_id,
        now=now,
        completed=item,
        retired=result.retired,
    )
    return DutyResult(
        status="published" if result.advanced else "replayed",
        discovered=len(batch.candidates),
        published=1,
        authored=len(result.retired),
        pending=len(state.pending),
    )


def run_repair_duty(
    storage: AvailabilityStorage,
    *,
    product_id: str,
    now: UTCInstant,
    backend: ObjectStoreBackend | None = None,
    object_prefix: str = "",
) -> DutyResult:
    """Verify bounded retained runs, author exact repair work, and optionally drain due prune work."""
    _catalog_object, catalog = load_catalog(storage, product_id)
    authored: list[WorkItem] = []
    for entry in catalog.runs[-MAX_RECONCILIATIONS_PER_DUTY:]:
        try:
            manifest, _series, _verified = read_remote_run(storage, product_id=product_id, run_id=entry.run_id)
        except (RemoteArtifactError, ValueError) as exc:
            candidate = ForecastCandidate(
                product_id=product_id,
                run_id=entry.run_id,
                provider=entry.provider,
                model=entry.model,
                model_version=entry.model_version,
                model_init_at=entry.model_init_at,
                provider_issued_at=entry.provider_issued_at,
            )
            authored.append(
                WorkItem(
                    kind="repair",
                    work_id=f"repair.{entry.run_id}",
                    candidate=candidate,
                    not_before=now,
                    last_error=str(exc),
                )
            )
            continue
        if (
            manifest.run.provider != entry.provider
            or manifest.run.model != entry.model
            or manifest.run.model_version != entry.model_version
        ):
            raise RemoteArtifactError("retained remote run changed provider, model, or model-version identity")
    state = _merge_authored_work(storage, product_id=product_id, now=now, authored=tuple(authored))
    pruned = 0
    if backend is not None:
        due_prunes = [
            candidate
            for candidate in state.pending
            if candidate.kind == "prune" and candidate.not_before <= now
        ]
        for item in due_prunes[:MAX_PRUNES_PER_DUTY]:
            if item.retired_entry is None:
                raise RemoteArtifactError("prune work lost its retired run inventory")
            delete_retired_entry(
                storage,
                backend,
                product_id=product_id,
                entry=item.retired_entry,
                object_prefix=object_prefix,
            )
            state = _remove_work(storage, product_id=product_id, now=now, work_id=item.work_id)
            pruned += 1
    return DutyResult(
        status="reconciled",
        authored=len(authored),
        pruned=pruned,
        pending=len(state.pending),
    )


def run_status_duty(storage: AvailabilityStorage, *, product_id: str, now: UTCInstant) -> DutyResult:
    """Publish one immutable coverage/status snapshot and conditionally advance its pointer."""
    _catalog_object, catalog = load_catalog(storage, product_id)
    _work_object, work = read_work_state(storage, product_id=product_id, now=now)
    status = ForecastStatus(
        product_id=product_id,
        created_at=now,
        active_run_id=catalog.active_run_id,
        runs=tuple(
            RunAvailability(
                run_id=entry.run_id,
                model_version=entry.model_version,
                model_init_at=entry.model_init_at,
                provider_issued_at=entry.provider_issued_at,
                published_at=entry.published_at,
                valid_start=entry.valid_start,
                valid_end=entry.valid_end,
                row_count=entry.row_count,
            )
            for entry in catalog.runs
        ),
        pending_publish=sum(item.kind == "publish" for item in work.pending),
        pending_repair=sum(item.kind == "repair" for item in work.pending),
        pending_prune=sum(item.kind == "prune" for item in work.pending),
        cooldown_until=work.cooldown_until,
        cooldown_reason=work.cooldown_reason,
    )
    payload = canonical_bytes(status.model_dump(mode="json"))
    if len(payload) > MAX_STATUS_BYTES:
        raise RemoteArtifactError("forecast status exceeds its byte ceiling")
    sha256 = digest(payload)
    storage.put_immutable(status_object_key(sha256), payload, content_type="application/json")
    pointer = StatusPointer(product_id=product_id, status_sha256=sha256, created_at=now)
    pointer_payload = canonical_bytes(pointer.model_dump(mode="json"))
    key = status_pointer_key(product_id)
    for _attempt in range(MAX_CAS_ATTEMPTS):
        stored = storage.read(key, max_bytes=MAX_STATUS_BYTES)
        if stored is not None and stored.payload == pointer_payload:
            return DutyResult(status="status_replayed", pending=len(work.pending))
        if storage.compare_and_swap(
            key,
            pointer_payload,
            expected_etag=None if stored is None else stored.etag,
            content_type="application/json",
        ):
            return DutyResult(status="status_published", pending=len(work.pending))
    raise RemoteArtifactError("forecast status pointer remained contended after bounded retries")


def _mutate_state(
    storage: AvailabilityStorage,
    *,
    product_id: str,
    now: UTCInstant,
    update: Callable[[ForecastWorkState], ForecastWorkState],
) -> ForecastWorkState:
    for _attempt in range(MAX_CAS_ATTEMPTS):
        stored, state = read_work_state(storage, product_id=product_id, now=now)
        target = ForecastWorkState.model_validate(update(state).model_dump(mode="json"))
        payload = canonical_bytes(target.model_dump(mode="json"))
        if len(payload) > MAX_WORK_STATE_BYTES:
            raise RemoteArtifactError("forecast work state exceeds its byte ceiling")
        if stored is not None and stored.payload == payload:
            return target
        if storage.compare_and_swap(
            work_state_key(product_id),
            payload,
            expected_etag=None if stored is None else stored.etag,
            content_type="application/json",
        ):
            return target
    raise RemoteArtifactError("forecast work state remained contended after bounded retries")


def _record_retry(
    storage: AvailabilityStorage, *, product_id: str, now: UTCInstant, item: WorkItem, detail: str
) -> ForecastWorkState:
    def update(state: ForecastWorkState) -> ForecastWorkState:
        pending = {candidate.work_id: candidate for candidate in state.pending}
        current = pending.get(item.work_id)
        if current is None:
            return state.model_copy(update={"updated_at": now})
        attempts = min(current.attempts + 1, MAX_WORK_ATTEMPTS)
        delay = min(6 * 60 * 60, 60 * (2 ** min(attempts, 8)))
        pending[item.work_id] = current.model_copy(
            update={"attempts": attempts, "not_before": now + timedelta(seconds=delay), "last_error": detail[:2048]}
        )
        return state.model_copy(update={"updated_at": now, "pending": tuple(sorted(pending.values(), key=_work_order))})

    return _mutate_state(storage, product_id=product_id, now=now, update=update)


def _complete_publication(
    storage: AvailabilityStorage,
    *,
    product_id: str,
    now: UTCInstant,
    completed: WorkItem,
    retired: tuple[RemoteRunEntry, ...],
) -> ForecastWorkState:
    def update(state: ForecastWorkState) -> ForecastWorkState:
        pending = {item.work_id: item for item in state.pending if item.work_id != completed.work_id}
        for entry in retired:
            work = WorkItem(
                kind="prune",
                work_id=f"prune.{entry.run_id}.{entry.manifest_sha256[:12]}",
                retired_entry=entry,
                not_before=now + RETENTION_PRUNE_DELAY,
            )
            pending.setdefault(work.work_id, work)
        if len(pending) > MAX_PENDING_WORK:
            raise RemoteArtifactError("retention cleanup work exceeds the durable queue ceiling")
        return state.model_copy(
            update={
                "updated_at": now,
                "cooldown_until": None,
                "cooldown_reason": None,
                "pending": tuple(sorted(pending.values(), key=_work_order)),
            }
        )

    return _mutate_state(storage, product_id=product_id, now=now, update=update)


def _merge_authored_work(
    storage: AvailabilityStorage,
    *,
    product_id: str,
    now: UTCInstant,
    authored: tuple[WorkItem, ...],
) -> ForecastWorkState:
    def update(state: ForecastWorkState) -> ForecastWorkState:
        pending = {item.work_id: item for item in state.pending}
        for item in authored:
            current = pending.get(item.work_id)
            if current is not None and current.candidate != item.candidate:
                raise RemoteArtifactError("repair work identity changed its forecast run coordinates")
            pending.setdefault(item.work_id, item)
        if len(pending) > MAX_PENDING_WORK:
            raise RemoteArtifactError("repair work exceeds the durable queue ceiling")
        return state.model_copy(update={"updated_at": now, "pending": tuple(sorted(pending.values(), key=_work_order))})

    return _mutate_state(storage, product_id=product_id, now=now, update=update)


def _remove_work(
    storage: AvailabilityStorage, *, product_id: str, now: UTCInstant, work_id: str
) -> ForecastWorkState:
    def update(state: ForecastWorkState) -> ForecastWorkState:
        return state.model_copy(
            update={"updated_at": now, "pending": tuple(item for item in state.pending if item.work_id != work_id)}
        )

    return _mutate_state(storage, product_id=product_id, now=now, update=update)


def _parse_retry_after(value: object, now: UTCInstant) -> int | None:
    if value is None:
        return None
    seconds: float
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        seconds = float(value)
    elif isinstance(value, timedelta):
        seconds = value.total_seconds()
    elif isinstance(value, datetime):
        instant = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        seconds = (instant.astimezone(UTC) - now).total_seconds()
    elif isinstance(value, str):
        if value.strip().isdigit():
            seconds = float(value.strip())
        else:
            try:
                instant = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return None
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=UTC)
            seconds = (instant.astimezone(UTC) - now).total_seconds()
    else:
        return None
    if seconds <= 0:
        return 1
    return _bounded_delay(int(seconds + 0.999999))


def _bounded_delay(seconds: int) -> int:
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
        raise ValueError("provider cooldown must be a positive whole-second delay")
    return min(seconds, MAX_COOLDOWN_SECONDS)


def _work_order(item: WorkItem) -> tuple[UTCInstant, str]:
    return item.not_before, item.work_id
