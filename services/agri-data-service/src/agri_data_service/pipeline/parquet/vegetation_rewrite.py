"""Guarded retirement of stale vegetation Parquet days before their governed re-export."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final, Literal, TypeVar

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.paths import (
    partition_day_statuses,
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.parquet.gap_fill import _lane_day_lock_key, postgres_lane_day_lock
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.warehouse.parquet.schema import observed_stream_schema
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER, DERIVED_ZOOM_TIERS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from contextlib import AbstractAsyncContextManager
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus, PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, SurplusPruneResult

VEGETATION_REWRITE_LAYER: Final = "vegetation"
VEGETATION_REWRITE_KIND: Final[PartitionKind] = "observed"
VEGETATION_REWRITE_MANIFEST_VERSION: Final = 1
VEGETATION_REWRITE_MAX_MANIFEST_BYTES: Final = 128_000
VEGETATION_REWRITE_MAX_DAYS: Final = 5_000
VEGETATION_REWRITE_MAX_ATTEMPTS: Final = 10
VEGETATION_REWRITE_MAX_RETRY_SECONDS: Final = 30.0
_SHA256_HEX_LENGTH: Final = 64
VEGETATION_COORDINATE_COLUMNS: Final = frozenset({"cell_longitude", "cell_latitude"})
_LEGACY_NON_NULL_COLUMNS: Final = frozenset({"cell_id", "observation_checksum"})
VEGETATION_REWRITE_ZOOM_TIERS: Final[tuple[ZoomTier, ...]] = (BASE_ZOOM_TIER, *DERIVED_ZOOM_TIERS)

_CURRENT_VEGETATION_SCHEMA: Final = observed_stream_schema(VEGETATION_REWRITE_LAYER).arrow_schema
LEGACY_VEGETATION_BASE_SCHEMA: Final = pa.schema(
    [
        pa.field(
            field.name,
            field.type,
            nullable=False if field.name in _LEGACY_NON_NULL_COLUMNS else field.nullable,
        )
        for field in _CURRENT_VEGETATION_SCHEMA
        if field.name not in VEGETATION_COORDINATE_COLUMNS
    ]
)

RewriteDayOutcome = Literal[
    "would_retract",
    "would_resume",
    "retracted",
    "already_retracted",
    "contended",
    "rejected",
    "failed",
]
BaseRewriteState = Literal["legacy", "missing"]
_T = TypeVar("_T")


class VegetationRewriteRefusal(ValueError):  # noqa: N818 - a refusal is an operator verdict
    """A manifest target is not one of the two shapes this destructive operation accepts."""


class _RetryExhausted(RuntimeError):  # noqa: N818 - internal terminal retry state
    def __init__(self, *, attempts: int, operation: str, error: Exception) -> None:
        super().__init__(f"{operation} failed after {attempts} attempt(s): {type(error).__name__}: {error}")
        self.attempts = attempts


@dataclass(frozen=True, slots=True)
class VegetationRewriteManifest:
    """One externally pinned, exact set of vegetation days approved for destructive rewrite."""

    days: tuple[date, ...]
    sha256: str
    byte_count: int
    layer: str = VEGETATION_REWRITE_LAYER
    kind: PartitionKind = VEGETATION_REWRITE_KIND
    schema_version: int = VEGETATION_REWRITE_MANIFEST_VERSION

    def to_report(self) -> dict[str, object]:
        return {
            "byte_count": self.byte_count,
            "day_count": len(self.days),
            "first_day": self.days[0].isoformat(),
            "kind": self.kind,
            "last_day": self.days[-1].isoformat(),
            "layer": self.layer,
            "schema_version": self.schema_version,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class VegetationRewritePreflight:
    """The complete locked object-state decision made before any tier is retracted."""

    base_state: BaseRewriteState
    tier_statuses: tuple[tuple[ZoomTier, PartitionDayStatus], ...]

    @property
    def all_missing(self) -> bool:
        return all(status == "missing" for _, status in self.tier_statuses)

    def to_report(self) -> dict[str, object]:
        return {
            "base_state": self.base_state,
            "tier_statuses": {f"z{tier}": status for tier, status in self.tier_statuses},
        }


@dataclass(frozen=True, slots=True)
class VegetationRewriteDayResult:
    """One manifest day's terminal result, suitable for a progress line and final summary."""

    day: date
    outcome: RewriteDayOutcome
    removed_keys: tuple[str, ...] = ()
    retry_count: int = 0
    detail: str | None = None
    preflight: VegetationRewritePreflight | None = None

    @property
    def failed(self) -> bool:
        return self.outcome in {"contended", "rejected", "failed"}

    def to_report(self) -> dict[str, object]:
        return {
            "day": self.day.isoformat(),
            "detail": self.detail,
            "outcome": self.outcome,
            "preflight": None if self.preflight is None else self.preflight.to_report(),
            "removed_key_count": len(self.removed_keys),
            "removed_keys": list(self.removed_keys),
            "retry_count": self.retry_count,
        }


@dataclass(frozen=True, slots=True)
class VegetationRewriteSummary:
    """The bounded manifest walk and every day-level outcome it produced."""

    run_id: str
    manifest: VegetationRewriteManifest
    dry_run: bool
    days: tuple[VegetationRewriteDayResult, ...]

    @property
    def failed(self) -> bool:
        return any(day.failed for day in self.days)

    def to_report(self) -> dict[str, object]:
        counts: dict[str, int] = {}
        for day in self.days:
            counts[day.outcome] = counts.get(day.outcome, 0) + 1
        return {
            "counts": {key: counts[key] for key in sorted(counts)},
            "days": [day.to_report() for day in self.days],
            "dry_run": self.dry_run,
            "failed": self.failed,
            "kind": VEGETATION_REWRITE_KIND,
            "layer": VEGETATION_REWRITE_LAYER,
            "manifest": self.manifest.to_report(),
            "removed_key_count": sum(len(day.removed_keys) for day in self.days),
            "run_id": self.run_id,
            "zoom_tiers": list(VEGETATION_REWRITE_ZOOM_TIERS),
        }


def load_vegetation_rewrite_manifest(  # noqa: PLR0912 - every branch is an independent destructive-input guard
    path: Path, *, expected_day_count: int, expected_sha256: str
) -> VegetationRewriteManifest:
    """Read one exact manifest once, pinning its raw bytes by count and external SHA-256."""
    if not 1 <= expected_day_count <= VEGETATION_REWRITE_MAX_DAYS:
        raise ValueError(f"expected-day-count must be between 1 and {VEGETATION_REWRITE_MAX_DAYS}")
    if len(expected_sha256) != _SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in expected_sha256
    ):
        raise ValueError("manifest-sha256 must be a lowercase SHA-256 digest")
    with path.open("rb") as manifest_file:
        payload = manifest_file.read(VEGETATION_REWRITE_MAX_MANIFEST_BYTES + 1)
    if len(payload) > VEGETATION_REWRITE_MAX_MANIFEST_BYTES:
        raise ValueError(f"vegetation rewrite manifest exceeds the {VEGETATION_REWRITE_MAX_MANIFEST_BYTES}-byte limit")
    actual_sha256 = sha256_digest(payload)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"vegetation rewrite manifest SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("vegetation rewrite manifest must be UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("vegetation rewrite manifest must be a JSON object")
    expected_keys = {"schema_version", "layer", "kind", "days"}
    if set(value) != expected_keys:
        raise ValueError("vegetation rewrite manifest must contain only schema_version, layer, kind, and days")
    if value["schema_version"] != VEGETATION_REWRITE_MANIFEST_VERSION:
        raise ValueError(f"vegetation rewrite manifest schema_version must be {VEGETATION_REWRITE_MANIFEST_VERSION}")
    if value["layer"] != VEGETATION_REWRITE_LAYER or value["kind"] != VEGETATION_REWRITE_KIND:
        raise ValueError("vegetation rewrite manifest is restricted to vegetation/observed")
    raw_days = value["days"]
    if not isinstance(raw_days, list) or not raw_days or len(raw_days) > VEGETATION_REWRITE_MAX_DAYS:
        raise ValueError(
            f"vegetation rewrite manifest days must contain between 1 and {VEGETATION_REWRITE_MAX_DAYS} entries"
        )
    if any(not isinstance(item, str) for item in raw_days):
        raise ValueError("vegetation rewrite manifest days must be ISO calendar-date strings")
    try:
        days = tuple(date.fromisoformat(item) for item in raw_days)
    except ValueError as exc:
        raise ValueError("vegetation rewrite manifest days must be ISO calendar-date strings") from exc
    if [day.isoformat() for day in days] != raw_days:
        raise ValueError("vegetation rewrite manifest days must use canonical YYYY-MM-DD spelling")
    if tuple(sorted(set(days))) != days:
        raise ValueError("vegetation rewrite manifest days must be sorted and unique")
    if len(days) != expected_day_count:
        raise ValueError(
            f"vegetation rewrite manifest holds {len(days)} day(s), not expected-day-count={expected_day_count}"
        )
    return VegetationRewriteManifest(days=days, sha256=actual_sha256, byte_count=len(payload))


def preflight_vegetation_rewrite_day(store: ObjectStore, day: date) -> VegetationRewritePreflight:
    """Accept only the known legacy base schema or a cleanly missing resume checkpoint."""
    listed_keys_by_tier = {
        tier: store.list_partition_keys(
            VEGETATION_REWRITE_LAYER,
            VEGETATION_REWRITE_KIND,
            tier,
            year=day.year,
            month=day.month,
        )
        for tier in VEGETATION_REWRITE_ZOOM_TIERS
    }
    keys_by_tier = {
        tier: tuple(key for key in listed_keys_by_tier[tier] if _key_is_for_day(key, tier=tier, day=day))
        for tier in VEGETATION_REWRITE_ZOOM_TIERS
    }
    statuses = tuple(
        (
            tier,
            partition_day_statuses(
                layer=VEGETATION_REWRITE_LAYER,
                kind=VEGETATION_REWRITE_KIND,
                zoom=tier,
                first_day=day,
                last_day=day,
                keys=keys_by_tier[tier],
            )[day],
        )
        for tier in VEGETATION_REWRITE_ZOOM_TIERS
    )
    status_by_tier: Mapping[ZoomTier, PartitionDayStatus] = dict(statuses)
    for tier in VEGETATION_REWRITE_ZOOM_TIERS:
        status = status_by_tier[tier]
        if status in {"absent", "conflict", "incomplete"}:
            raise VegetationRewriteRefusal(f"refusing vegetation/observed z{tier} {day.isoformat()}: state is {status}")
        if status == "missing" and keys_by_tier[tier]:
            raise VegetationRewriteRefusal(
                f"refusing vegetation/observed z{tier} {day.isoformat()}: marker-only residue is not a clean "
                "missing checkpoint"
            )

    base_status = status_by_tier[BASE_ZOOM_TIER]
    if base_status == "missing":
        return VegetationRewritePreflight(base_state="missing", tier_statuses=statuses)

    base_table = store.read_partition(VEGETATION_REWRITE_LAYER, VEGETATION_REWRITE_KIND, BASE_ZOOM_TIER, day)
    if base_table.schema.equals(_CURRENT_VEGETATION_SCHEMA, check_metadata=False):
        raise VegetationRewriteRefusal(
            f"refusing vegetation/observed z{BASE_ZOOM_TIER} {day.isoformat()}: data already has the current "
            "coordinate-bearing schema"
        )
    if not base_table.schema.equals(LEGACY_VEGETATION_BASE_SCHEMA, check_metadata=False):
        names = sorted(base_table.schema.names)
        raise VegetationRewriteRefusal(
            f"refusing vegetation/observed z{BASE_ZOOM_TIER} {day.isoformat()}: schema is not the exact known "
            f"legacy coordinate-less schema; columns={names}"
        )
    return VegetationRewritePreflight(base_state="legacy", tier_statuses=statuses)


def _key_is_for_day(key: str, *, tier: ZoomTier, day: date) -> bool:
    parsed = (
        try_parse_partition_path(key) or try_parse_absence_marker_path(key) or try_parse_completion_marker_path(key)
    )
    return bool(
        parsed is not None
        and parsed.layer == VEGETATION_REWRITE_LAYER
        and parsed.kind == VEGETATION_REWRITE_KIND
        and parsed.zoom == tier
        and parsed.day == day
    )


async def _retry_operation(  # noqa: UP047 - TypeVar keeps support aligned with the service
    operation: Callable[[], _T],
    *,
    operation_name: str,
    max_attempts: int,
    retry_base_seconds: float,
    sleep: Callable[[float], Awaitable[None]],
) -> tuple[_T, int]:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return operation(), attempt - 1
        except VegetationRewriteRefusal:
            raise
        except Exception as error:  # the retry boundary deliberately includes object-store SDK errors
            last_error = error
            if attempt < max_attempts and retry_base_seconds > 0:
                delay = min(retry_base_seconds * (2 ** (attempt - 1)), VEGETATION_REWRITE_MAX_RETRY_SECONDS)
                await sleep(delay)
    assert last_error is not None
    raise _RetryExhausted(attempts=max_attempts, operation=operation_name, error=last_error)


async def _retract_tier_with_retry(  # noqa: PLR0913 - bounded retry seam
    store: ObjectStore,
    *,
    tier: ZoomTier,
    day: date,
    max_attempts: int,
    retry_base_seconds: float,
    sleep: Callable[[float], Awaitable[None]],
) -> tuple[tuple[str, ...], int]:
    removed: list[str] = []
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result: SurplusPruneResult = store.retract_partition_tier(
                VEGETATION_REWRITE_LAYER, VEGETATION_REWRITE_KIND, tier, day
            )
        except Exception as error:
            last_error = error
        else:
            removed.extend(result.removed)
            if not result.failures:
                return tuple(dict.fromkeys(removed)), attempt - 1
            last_error = RuntimeError("; ".join(result.failures))
        if attempt < max_attempts and retry_base_seconds > 0:
            delay = min(retry_base_seconds * (2 ** (attempt - 1)), VEGETATION_REWRITE_MAX_RETRY_SECONDS)
            await sleep(delay)
    assert last_error is not None
    raise _RetryExhausted(
        attempts=max_attempts,
        operation=f"retract vegetation/observed z{tier} {day.isoformat()}",
        error=last_error,
    )


async def _rewrite_one_day(  # noqa: PLR0911, PLR0913 - explicit outcomes and bounded operation controls
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    dry_run: bool,
    max_attempts: int,
    retry_base_seconds: float,
    lane_day_lock: Callable[[AsyncSession, str], AbstractAsyncContextManager[bool]],
    sleep: Callable[[float], Awaitable[None]],
) -> VegetationRewriteDayResult:
    retry_count = 0
    registration = LANE_REGISTRY[VEGETATION_REWRITE_LAYER]
    try:
        async with lane_day_lock(session, _lane_day_lock_key(registration, day)) as granted:
            if not granted:
                return VegetationRewriteDayResult(
                    day=day,
                    outcome="contended",
                    detail="another writer holds this vegetation lane-day; nothing was changed",
                )
            try:
                preflight, preflight_retries = await _retry_operation(
                    lambda: preflight_vegetation_rewrite_day(store, day),
                    operation_name=f"preflight vegetation/observed {day.isoformat()}",
                    max_attempts=max_attempts,
                    retry_base_seconds=retry_base_seconds,
                    sleep=sleep,
                )
            except VegetationRewriteRefusal as error:
                return VegetationRewriteDayResult(day=day, outcome="rejected", detail=str(error))
            except _RetryExhausted as error:
                return VegetationRewriteDayResult(
                    day=day,
                    outcome="failed",
                    retry_count=max(0, error.attempts - 1),
                    detail=str(error),
                )
            retry_count += preflight_retries
            if preflight.all_missing:
                return VegetationRewriteDayResult(
                    day=day,
                    outcome="already_retracted",
                    retry_count=retry_count,
                    detail="all four zoom tiers are already cleanly missing",
                    preflight=preflight,
                )
            if dry_run:
                outcome: RewriteDayOutcome = "would_retract" if preflight.base_state == "legacy" else "would_resume"
                return VegetationRewriteDayResult(
                    day=day,
                    outcome=outcome,
                    retry_count=retry_count,
                    detail="preflight passed; dry-run left every object unchanged",
                    preflight=preflight,
                )

            removed: list[str] = []
            for tier in VEGETATION_REWRITE_ZOOM_TIERS:
                try:
                    tier_removed, tier_retries = await _retract_tier_with_retry(
                        store,
                        tier=tier,
                        day=day,
                        max_attempts=max_attempts,
                        retry_base_seconds=retry_base_seconds,
                        sleep=sleep,
                    )
                except _RetryExhausted as error:
                    return VegetationRewriteDayResult(
                        day=day,
                        outcome="failed",
                        removed_keys=tuple(removed),
                        retry_count=retry_count + max(0, error.attempts - 1),
                        detail=str(error),
                        preflight=preflight,
                    )
                removed.extend(tier_removed)
                retry_count += tier_retries
            return VegetationRewriteDayResult(
                day=day,
                outcome="retracted",
                removed_keys=tuple(removed),
                retry_count=retry_count,
                detail="all four zoom tiers are now missing and selectable for governed re-export",
                preflight=preflight,
            )
    except Exception as error:
        return VegetationRewriteDayResult(
            day=day,
            outcome="failed",
            retry_count=retry_count,
            detail=f"lane-day lock or session failed: {type(error).__name__}: {error}",
        )
    finally:
        with suppress(Exception):
            await session.rollback()


async def rewrite_vegetation_manifest(  # noqa: PLR0913 - the controls are the destructive-operation contract
    session: AsyncSession,
    store: ObjectStore,
    *,
    manifest: VegetationRewriteManifest,
    run_id: str,
    dry_run: bool = True,
    max_attempts: int = 3,
    retry_base_seconds: float = 1.0,
    lane_day_lock: Callable[[AsyncSession, str], AbstractAsyncContextManager[bool]] = postgres_lane_day_lock,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_day: Callable[[VegetationRewriteDayResult], None] | None = None,
) -> VegetationRewriteSummary:
    """Walk a pinned manifest sequentially; the bucket itself is the resumable day checkpoint."""
    if manifest.layer != VEGETATION_REWRITE_LAYER or manifest.kind != VEGETATION_REWRITE_KIND:
        raise ValueError("vegetation rewrite is restricted to vegetation/observed")
    if not 1 <= len(manifest.days) <= VEGETATION_REWRITE_MAX_DAYS:
        raise ValueError("vegetation rewrite manifest must contain a bounded non-empty day set")
    if not 1 <= max_attempts <= VEGETATION_REWRITE_MAX_ATTEMPTS:
        raise ValueError(f"max-attempts must be between 1 and {VEGETATION_REWRITE_MAX_ATTEMPTS}")
    if not 0 <= retry_base_seconds <= VEGETATION_REWRITE_MAX_RETRY_SECONDS:
        raise ValueError(f"retry-base-seconds must be between 0 and {VEGETATION_REWRITE_MAX_RETRY_SECONDS:g}")
    results: list[VegetationRewriteDayResult] = []
    for day in manifest.days:
        result = await _rewrite_one_day(
            session,
            store,
            day=day,
            dry_run=dry_run,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            lane_day_lock=lane_day_lock,
            sleep=sleep,
        )
        results.append(result)
        if on_day is not None:
            on_day(result)
    return VegetationRewriteSummary(
        run_id=run_id,
        manifest=manifest,
        dry_run=dry_run,
        days=tuple(results),
    )
