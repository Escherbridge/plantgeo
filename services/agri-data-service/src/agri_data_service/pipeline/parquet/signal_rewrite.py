"""Guarded retirement of stale signal Parquet days before their governed re-export.

Sibling of `vegetation_rewrite.py`, generalizing its approach rather than its code: same manifest
pinning, same shape-detected preflight, same z13/z9/z5/z0 retraction order, same bounded retry and
lane-day locking. It is a parallel module rather than an extraction of a shared core because the two
lanes' guard sets genuinely differ: the signal plane has no `pipeline/direct/` writer and therefore
no publication barrier to hold (vegetation's `postgres_vegetation_publication_barrier` exists to
serialize this kind of rewrite against `pipeline/direct/vegetation/backfill.py`, which signal has no
counterpart to), and its legacy non-null column set (`cell_id` only) differs from vegetation's
(`cell_id` and `observation_checksum`), so the two "current schema minus the coordinate columns"
derivations are already parameterized independently in each module. Forcing both into one generic
core would mean threading an optional no-op barrier and a per-lane legacy-column set through a
shared function for a savings of a few dozen lines -- not a genuine shared behaviour.

Commit `8ce71fd` (2026-08-24) added `cell_longitude`/`cell_latitude` to `SIGNAL_PLANE_SCHEMA` and
relaxed `cell_id` from non-null to nullable in the same change (the coarse rungs null it; the base
z13 rung still may not -- `SIGNAL_PLANE_TIER_DERIVATION.base_non_null_columns` enforces that at
write time). Every base-rung object written before that commit therefore lacks both coordinate
columns AND carries a non-null `cell_id`, which is `LEGACY_SIGNAL_BASE_SCHEMA` below. Coarse rungs
can never be derived from those objects because `GridAggregation` reads the coordinate columns, and
Parquet objects are immutable, so the fix is to retract the stale rungs and let the ordinary drain
re-export them with the now-correct SQL.

THE OPERATOR SEQUENCE this module exists to enable:

  1. `parquet-rewrite-signal --manifest <pinned-days.json> --expected-day-count N \\
        --manifest-sha256 <sha> --apply`
     Retracts the complete z13/z9/z5/z0 ladder for every manifest day whose z13 object is either
     the exact legacy shape above or already a clean missing checkpoint. Dry-run (the default)
     performs every listing, read and lock, and changes nothing.
  2. `parquet-drain --layer signal --selection missing`
     Re-exports the base rung with positions, via the already-correct
     `sql/pipeline/signal_plane_day_export.sql`.
  3. `parquet-drain --layer signal --selection ladder`
     Derives z9/z5/z0 from the freshly correct base.

Estimated scope, restated as an inference rather than a fact: ~222 lane-days (the signal-plane
ladder-incomplete count) is the best current guess, roughly 888 objects across four tiers -- but
this module's own dry-run output is what turns that guess into a measurement.

DISCOVERY, the step before step 1: nothing above produces the manifest step 1 pins to. The
census walk (`census_signal_rewrite_days` / `parquet-rewrite-signal-census`) fills that gap by
calling `preflight_signal_rewrite_day` -- UNMODIFIED, so the discovery walk and the destructive
rewrite can never disagree about what one day is -- across a bounded calendar window, and sorts
every day it visits into exactly three buckets: `legacy` (rewritable), `current` (already fixed),
or `refused` (a named reason: already-current is split out via `SignalRewriteAlreadyCurrent`, so
only genuine anomalies -- unexpected schema, absent/incomplete/conflicted tier state, an unreadable
object, or a day with no z13 marker at all -- land here). It is read-only: no lock, no retraction,
no `--apply`. Its only side effect is writing the manifest file the rewrite verb consumes, byte-for-
byte, so the operator sequence above gains a real step 0:

  0. `parquet-rewrite-signal-census --first-day <d0> --last-day <d1> [--max-days N] \\
        --manifest-out <path>`
     Prints a JSON summary (`complete: false` if `--max-days` or `--last-day` cut the walk short)
     and, when it found at least one `legacy` day, writes `<path>` and echoes the exact
     `parquet-rewrite-signal --manifest <path> --expected-day-count ... --manifest-sha256 ...`
     invocation to run next.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final, Literal, TypeVar

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.paths import (
    MAX_GAP_WINDOW_DAYS,
    partition_day_statuses,
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.parquet.gap_fill import _lane_day_lock_key, postgres_lane_day_lock
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.warehouse.parquet.schema import SIGNAL_PLANE_STREAM, observed_stream_schema
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER, DERIVED_ZOOM_TIERS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from contextlib import AbstractAsyncContextManager
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus, PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, SurplusPruneResult

SIGNAL_REWRITE_LAYER: Final = SIGNAL_PLANE_STREAM
SIGNAL_REWRITE_KIND: Final[PartitionKind] = "observed"
SIGNAL_REWRITE_MANIFEST_VERSION: Final = 1
SIGNAL_REWRITE_MAX_MANIFEST_BYTES: Final = 128_000
SIGNAL_REWRITE_MAX_DAYS: Final = 5_000
SIGNAL_REWRITE_MAX_ATTEMPTS: Final = 10
SIGNAL_REWRITE_MAX_RETRY_SECONDS: Final = 30.0
_SHA256_HEX_LENGTH: Final = 64
SIGNAL_COORDINATE_COLUMNS: Final = frozenset({"cell_longitude", "cell_latitude"})
# Only `cell_id` was relaxed to nullable by 8ce71fd. Unlike vegetation, signal has no
# `observation_checksum` column at all, so the legacy non-null set is smaller by that one field.
_LEGACY_NON_NULL_COLUMNS: Final = frozenset({"cell_id"})
SIGNAL_REWRITE_ZOOM_TIERS: Final[tuple[ZoomTier, ...]] = (BASE_ZOOM_TIER, *DERIVED_ZOOM_TIERS)

_CURRENT_SIGNAL_SCHEMA: Final = observed_stream_schema(SIGNAL_REWRITE_LAYER).arrow_schema
LEGACY_SIGNAL_BASE_SCHEMA: Final = pa.schema(
    [
        pa.field(
            field.name,
            field.type,
            nullable=False if field.name in _LEGACY_NON_NULL_COLUMNS else field.nullable,
        )
        for field in _CURRENT_SIGNAL_SCHEMA
        if field.name not in SIGNAL_COORDINATE_COLUMNS
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


class SignalRewriteRefusal(ValueError):  # noqa: N818 - a refusal is an operator verdict
    """A manifest target is not one of the two shapes this destructive operation accepts."""


class SignalRewriteAlreadyCurrent(SignalRewriteRefusal):
    """The z13 base already carries the coordinate columns.

    A subclass rather than a message the census string-matches: `_rewrite_one_day` still catches it
    as an ordinary `SignalRewriteRefusal` (outcome stays `rejected`, message unchanged), while
    `census_signal_rewrite_days` catches this specific type to classify the day `current` instead of
    lumping it in with genuine anomalies under `refused`.
    """


class _RetryExhausted(RuntimeError):  # noqa: N818 - internal terminal retry state
    def __init__(self, *, attempts: int, operation: str, error: Exception) -> None:
        super().__init__(f"{operation} failed after {attempts} attempt(s): {type(error).__name__}: {error}")
        self.attempts = attempts


@dataclass(frozen=True, slots=True)
class SignalRewriteManifest:
    """One externally pinned, exact set of signal days approved for destructive rewrite."""

    days: tuple[date, ...]
    sha256: str
    byte_count: int
    layer: str = SIGNAL_REWRITE_LAYER
    kind: PartitionKind = SIGNAL_REWRITE_KIND
    schema_version: int = SIGNAL_REWRITE_MANIFEST_VERSION

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
class SignalRewritePreflight:
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
class SignalRewriteDayResult:
    """One manifest day's terminal result, suitable for a progress line and final summary."""

    day: date
    outcome: RewriteDayOutcome
    removed_keys: tuple[str, ...] = ()
    retry_count: int = 0
    detail: str | None = None
    preflight: SignalRewritePreflight | None = None

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
class SignalRewriteSummary:
    """The bounded manifest walk and every day-level outcome it produced."""

    run_id: str
    manifest: SignalRewriteManifest
    dry_run: bool
    days: tuple[SignalRewriteDayResult, ...]

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
            "kind": SIGNAL_REWRITE_KIND,
            "layer": SIGNAL_REWRITE_LAYER,
            "manifest": self.manifest.to_report(),
            "removed_key_count": sum(len(day.removed_keys) for day in self.days),
            "run_id": self.run_id,
            "zoom_tiers": list(SIGNAL_REWRITE_ZOOM_TIERS),
        }


def load_signal_rewrite_manifest(  # noqa: PLR0912 - every branch is an independent destructive-input guard
    path: Path, *, expected_day_count: int, expected_sha256: str
) -> SignalRewriteManifest:
    """Read one exact manifest once, pinning its raw bytes by count and external SHA-256."""
    if not 1 <= expected_day_count <= SIGNAL_REWRITE_MAX_DAYS:
        raise ValueError(f"expected-day-count must be between 1 and {SIGNAL_REWRITE_MAX_DAYS}")
    if len(expected_sha256) != _SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in expected_sha256
    ):
        raise ValueError("manifest-sha256 must be a lowercase SHA-256 digest")
    with path.open("rb") as manifest_file:
        payload = manifest_file.read(SIGNAL_REWRITE_MAX_MANIFEST_BYTES + 1)
    if len(payload) > SIGNAL_REWRITE_MAX_MANIFEST_BYTES:
        raise ValueError(f"signal rewrite manifest exceeds the {SIGNAL_REWRITE_MAX_MANIFEST_BYTES}-byte limit")
    actual_sha256 = sha256_digest(payload)
    if actual_sha256 != expected_sha256:
        raise ValueError(f"signal rewrite manifest SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("signal rewrite manifest must be UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("signal rewrite manifest must be a JSON object")
    expected_keys = {"schema_version", "layer", "kind", "days"}
    if set(value) != expected_keys:
        raise ValueError("signal rewrite manifest must contain only schema_version, layer, kind, and days")
    if value["schema_version"] != SIGNAL_REWRITE_MANIFEST_VERSION:
        raise ValueError(f"signal rewrite manifest schema_version must be {SIGNAL_REWRITE_MANIFEST_VERSION}")
    if value["layer"] != SIGNAL_REWRITE_LAYER or value["kind"] != SIGNAL_REWRITE_KIND:
        raise ValueError("signal rewrite manifest is restricted to signal/observed")
    raw_days = value["days"]
    if not isinstance(raw_days, list) or not raw_days or len(raw_days) > SIGNAL_REWRITE_MAX_DAYS:
        raise ValueError(f"signal rewrite manifest days must contain between 1 and {SIGNAL_REWRITE_MAX_DAYS} entries")
    if any(not isinstance(item, str) for item in raw_days):
        raise ValueError("signal rewrite manifest days must be ISO calendar-date strings")
    try:
        days = tuple(date.fromisoformat(item) for item in raw_days)
    except ValueError as exc:
        raise ValueError("signal rewrite manifest days must be ISO calendar-date strings") from exc
    if [day.isoformat() for day in days] != raw_days:
        raise ValueError("signal rewrite manifest days must use canonical YYYY-MM-DD spelling")
    if tuple(sorted(set(days))) != days:
        raise ValueError("signal rewrite manifest days must be sorted and unique")
    if len(days) != expected_day_count:
        raise ValueError(
            f"signal rewrite manifest holds {len(days)} day(s), not expected-day-count={expected_day_count}"
        )
    return SignalRewriteManifest(days=days, sha256=actual_sha256, byte_count=len(payload))


def preflight_signal_rewrite_day(store: ObjectStore, day: date) -> SignalRewritePreflight:
    """Accept only the known legacy base schema or a cleanly missing resume checkpoint."""
    listed_keys_by_tier = {
        tier: store.list_partition_keys(
            SIGNAL_REWRITE_LAYER,
            SIGNAL_REWRITE_KIND,
            tier,
            year=day.year,
            month=day.month,
        )
        for tier in SIGNAL_REWRITE_ZOOM_TIERS
    }
    keys_by_tier = {
        tier: tuple(key for key in listed_keys_by_tier[tier] if _key_is_for_day(key, tier=tier, day=day))
        for tier in SIGNAL_REWRITE_ZOOM_TIERS
    }
    statuses = tuple(
        (
            tier,
            partition_day_statuses(
                layer=SIGNAL_REWRITE_LAYER,
                kind=SIGNAL_REWRITE_KIND,
                zoom=tier,
                first_day=day,
                last_day=day,
                keys=keys_by_tier[tier],
            )[day],
        )
        for tier in SIGNAL_REWRITE_ZOOM_TIERS
    )
    status_by_tier: Mapping[ZoomTier, PartitionDayStatus] = dict(statuses)
    for tier in SIGNAL_REWRITE_ZOOM_TIERS:
        detail = _unapproved_rung_detail(status_by_tier[tier], keys_by_tier[tier])
        if detail is not None:
            raise SignalRewriteRefusal(f"refusing signal/observed z{tier} {day.isoformat()}: {detail}")

    base_status = status_by_tier[BASE_ZOOM_TIER]
    if base_status == "missing":
        return SignalRewritePreflight(base_state="missing", tier_statuses=statuses)

    base_table = store.read_partition(SIGNAL_REWRITE_LAYER, SIGNAL_REWRITE_KIND, BASE_ZOOM_TIER, day)
    if base_table.schema.equals(_CURRENT_SIGNAL_SCHEMA, check_metadata=False):
        raise SignalRewriteAlreadyCurrent(
            f"refusing signal/observed z{BASE_ZOOM_TIER} {day.isoformat()}: data already has the current "
            "coordinate-bearing schema"
        )
    if not base_table.schema.equals(LEGACY_SIGNAL_BASE_SCHEMA, check_metadata=False):
        names = sorted(base_table.schema.names)
        raise SignalRewriteRefusal(
            f"refusing signal/observed z{BASE_ZOOM_TIER} {day.isoformat()}: schema is not the exact known "
            f"legacy coordinate-less schema; columns={names}"
        )
    return SignalRewritePreflight(base_state="legacy", tier_statuses=statuses)


def _unapproved_rung_detail(status: PartitionDayStatus, keys: tuple[str, ...]) -> str | None:
    """Name why one rung is not a clean rewrite checkpoint, or `None` when it is.

    Mirrors `vegetation_rewrite._unapproved_rung_detail`: a marker whose parts are gone reads
    `incomplete` rather than `missing` now that a bare completion assertion no longer counts as a
    finished rung, so the two layouts sharing that status are told apart from the listing this
    preflight already holds: residue with parts is a truncated export, residue without them is
    marker-only.
    """
    if status in {"absent", "conflict"}:
        return f"state is {status}"
    if status == "incomplete" or (status == "missing" and keys):
        if _holds_part_files(keys):
            return "state is incomplete"
        return "marker-only residue is not a clean missing checkpoint"
    return None


def _holds_part_files(keys: tuple[str, ...]) -> bool:
    """True when an already tier- and day-filtered listing carries at least one part file."""
    return any(try_parse_partition_path(key) is not None for key in keys)


def _key_is_for_day(key: str, *, tier: ZoomTier, day: date) -> bool:
    parsed = (
        try_parse_partition_path(key) or try_parse_absence_marker_path(key) or try_parse_completion_marker_path(key)
    )
    return bool(
        parsed is not None
        and parsed.layer == SIGNAL_REWRITE_LAYER
        and parsed.kind == SIGNAL_REWRITE_KIND
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
        except SignalRewriteRefusal:
            raise
        except Exception as error:  # the retry boundary deliberately includes object-store SDK errors
            last_error = error
            if attempt < max_attempts and retry_base_seconds > 0:
                delay = min(retry_base_seconds * (2 ** (attempt - 1)), SIGNAL_REWRITE_MAX_RETRY_SECONDS)
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
                SIGNAL_REWRITE_LAYER, SIGNAL_REWRITE_KIND, tier, day
            )
        except Exception as error:
            last_error = error
        else:
            removed.extend(result.removed)
            if not result.failures:
                return tuple(dict.fromkeys(removed)), attempt - 1
            last_error = RuntimeError("; ".join(result.failures))
        if attempt < max_attempts and retry_base_seconds > 0:
            delay = min(retry_base_seconds * (2 ** (attempt - 1)), SIGNAL_REWRITE_MAX_RETRY_SECONDS)
            await sleep(delay)
    assert last_error is not None
    raise _RetryExhausted(
        attempts=max_attempts,
        operation=f"retract signal/observed z{tier} {day.isoformat()}",
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
) -> SignalRewriteDayResult:
    retry_count = 0
    registration = LANE_REGISTRY[SIGNAL_REWRITE_LAYER]
    try:
        async with lane_day_lock(session, _lane_day_lock_key(registration, day)) as granted:
            if not granted:
                return SignalRewriteDayResult(
                    day=day,
                    outcome="contended",
                    detail="another writer holds this signal lane-day; nothing was changed",
                )
            try:
                preflight, preflight_retries = await _retry_operation(
                    lambda: preflight_signal_rewrite_day(store, day),
                    operation_name=f"preflight signal/observed {day.isoformat()}",
                    max_attempts=max_attempts,
                    retry_base_seconds=retry_base_seconds,
                    sleep=sleep,
                )
            except SignalRewriteRefusal as error:
                return SignalRewriteDayResult(day=day, outcome="rejected", detail=str(error))
            except _RetryExhausted as error:
                return SignalRewriteDayResult(
                    day=day,
                    outcome="failed",
                    retry_count=max(0, error.attempts - 1),
                    detail=str(error),
                )
            retry_count += preflight_retries
            if preflight.all_missing:
                return SignalRewriteDayResult(
                    day=day,
                    outcome="already_retracted",
                    retry_count=retry_count,
                    detail="all four zoom tiers are already cleanly missing",
                    preflight=preflight,
                )
            if dry_run:
                outcome: RewriteDayOutcome = "would_retract" if preflight.base_state == "legacy" else "would_resume"
                return SignalRewriteDayResult(
                    day=day,
                    outcome=outcome,
                    retry_count=retry_count,
                    detail="preflight passed; dry-run left every object unchanged",
                    preflight=preflight,
                )

            removed: list[str] = []
            for tier in SIGNAL_REWRITE_ZOOM_TIERS:
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
                    return SignalRewriteDayResult(
                        day=day,
                        outcome="failed",
                        removed_keys=tuple(removed),
                        retry_count=retry_count + max(0, error.attempts - 1),
                        detail=str(error),
                        preflight=preflight,
                    )
                removed.extend(tier_removed)
                retry_count += tier_retries
            return SignalRewriteDayResult(
                day=day,
                outcome="retracted",
                removed_keys=tuple(removed),
                retry_count=retry_count,
                detail="all four zoom tiers are now missing and selectable for governed re-export",
                preflight=preflight,
            )
    except Exception as error:
        return SignalRewriteDayResult(
            day=day,
            outcome="failed",
            retry_count=retry_count,
            detail=f"lane-day lock or session failed: {type(error).__name__}: {error}",
        )
    finally:
        with suppress(Exception):
            await session.rollback()


async def rewrite_signal_manifest(  # noqa: PLR0913 - the controls are the destructive-operation contract
    session: AsyncSession,
    store: ObjectStore,
    *,
    manifest: SignalRewriteManifest,
    run_id: str,
    dry_run: bool = True,
    max_attempts: int = 3,
    retry_base_seconds: float = 1.0,
    lane_day_lock: Callable[[AsyncSession, str], AbstractAsyncContextManager[bool]] = postgres_lane_day_lock,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_day: Callable[[SignalRewriteDayResult], None] | None = None,
) -> SignalRewriteSummary:
    """Walk a pinned manifest sequentially; the bucket itself is the resumable day checkpoint.

    Unlike `rewrite_vegetation_manifest`, this holds no publication barrier: the signal plane has no
    `pipeline/direct/` writer and no publication queue racing this retraction, only the ordinary
    lane-day advisory lock the exporter and ladder repair also take.
    """
    if manifest.layer != SIGNAL_REWRITE_LAYER or manifest.kind != SIGNAL_REWRITE_KIND:
        raise ValueError("signal rewrite is restricted to signal/observed")
    if not 1 <= len(manifest.days) <= SIGNAL_REWRITE_MAX_DAYS:
        raise ValueError("signal rewrite manifest must contain a bounded non-empty day set")
    if not 1 <= max_attempts <= SIGNAL_REWRITE_MAX_ATTEMPTS:
        raise ValueError(f"max-attempts must be between 1 and {SIGNAL_REWRITE_MAX_ATTEMPTS}")
    if not 0 <= retry_base_seconds <= SIGNAL_REWRITE_MAX_RETRY_SECONDS:
        raise ValueError(f"retry-base-seconds must be between 0 and {SIGNAL_REWRITE_MAX_RETRY_SECONDS:g}")
    results: list[SignalRewriteDayResult] = []
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
    return SignalRewriteSummary(
        run_id=run_id,
        manifest=manifest,
        dry_run=dry_run,
        days=tuple(results),
    )


# ---------------------------------------------------------------------------------------------
# DISCOVERY: read-only census over a calendar window, producing the manifest step 1 above pins.
# ---------------------------------------------------------------------------------------------

SignalCensusClassification = Literal["legacy", "current", "refused"]
# A census walking further than a manifest could ever hold is already off its own rails; the same
# 5,000-day ceiling that bounds `SignalRewriteManifest` bounds how many calendar days one census
# invocation will classify. Re-run with a shifted `--first-day` for the next chunk of a longer lane.
SIGNAL_CENSUS_MAX_DAYS: Final = SIGNAL_REWRITE_MAX_DAYS


@dataclass(frozen=True, slots=True)
class SignalCensusDayFinding:
    """One walked day's shape verdict: which of the three buckets it landed in, and why."""

    day: date
    classification: SignalCensusClassification
    reason: str | None

    def to_report(self) -> dict[str, object]:
        return {"day": self.day.isoformat(), "classification": self.classification, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class SignalCensusManifestEmission:
    """The exact bytes `load_signal_rewrite_manifest` will re-read, plus the pin it re-verifies by."""

    payload: bytes
    sha256: str
    day_count: int

    def ready_command(self, *, manifest_path: str) -> str:
        """The complete, copy-pasteable next invocation -- no hand-editing between the two verbs."""
        return (
            "uv run --no-sync agri-service data parquet-rewrite-signal "
            f"--manifest {manifest_path} --expected-day-count {self.day_count} --manifest-sha256 {self.sha256}"
        )

    def to_report(self) -> dict[str, object]:
        return {"day_count": self.day_count, "byte_count": len(self.payload), "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class SignalCensusSummary:
    """One bounded calendar walk and every day-level verdict it produced.

    `complete` is the load-bearing field: it is `True` only when the walk reached
    `requested_last_day` on its own, so a caller can never mistake a `--max-days`- or
    `--last-day`-truncated partial census for a finished one just because it exited zero.
    """

    run_id: str
    requested_first_day: date
    requested_last_day: date
    walked_day_count: int
    complete: bool
    findings: tuple[SignalCensusDayFinding, ...]

    @property
    def walked_last_day(self) -> date | None:
        return self.findings[-1].day if self.findings else None

    @property
    def legacy_days(self) -> tuple[date, ...]:
        return tuple(finding.day for finding in self.findings if finding.classification == "legacy")

    def manifest_emission(self) -> SignalCensusManifestEmission | None:
        """`None` when nothing legacy was found this walk; never raises on an empty census."""
        days = self.legacy_days
        if not days:
            return None
        payload = build_signal_census_manifest_bytes(days)
        return SignalCensusManifestEmission(payload=payload, sha256=sha256_digest(payload), day_count=len(days))

    def to_report(self) -> dict[str, object]:
        counts: dict[str, int] = {"current": 0, "legacy": 0, "refused": 0}
        for finding in self.findings:
            counts[finding.classification] += 1
        emission = self.manifest_emission()
        walked_last_day = self.walked_last_day
        return {
            "complete": self.complete,
            "counts": counts,
            "findings": [finding.to_report() for finding in self.findings],
            "layer": SIGNAL_REWRITE_LAYER,
            "kind": SIGNAL_REWRITE_KIND,
            "manifest": None if emission is None else emission.to_report(),
            "requested_first_day": self.requested_first_day.isoformat(),
            "requested_last_day": self.requested_last_day.isoformat(),
            "run_id": self.run_id,
            "walked_day_count": self.walked_day_count,
            "walked_first_day": self.requested_first_day.isoformat() if self.findings else None,
            "walked_last_day": None if walked_last_day is None else walked_last_day.isoformat(),
        }


def build_signal_census_manifest_bytes(days: tuple[date, ...]) -> bytes:
    """Serialize legacy days into the EXACT bytes `load_signal_rewrite_manifest` accepts unedited.

    Sorted, deduplicated, and re-bounded by the same two limits `load_signal_rewrite_manifest`
    itself enforces on the other end (`SIGNAL_REWRITE_MAX_DAYS` and
    `SIGNAL_REWRITE_MAX_MANIFEST_BYTES`) -- so a census that overshoots fails HERE, with a message
    naming which budget it broke, rather than at the rewrite verb after the operator already trusts
    the manifest it wrote.
    """
    ordered = tuple(sorted(set(days)))
    if not ordered:
        raise ValueError("cannot build a signal rewrite manifest from zero legacy days")
    if len(ordered) > SIGNAL_REWRITE_MAX_DAYS:
        raise ValueError(
            f"census found {len(ordered)} legacy day(s), exceeding the {SIGNAL_REWRITE_MAX_DAYS}-day manifest limit"
        )
    payload = json.dumps(
        {
            "schema_version": SIGNAL_REWRITE_MANIFEST_VERSION,
            "layer": SIGNAL_REWRITE_LAYER,
            "kind": SIGNAL_REWRITE_KIND,
            "days": [day.isoformat() for day in ordered],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > SIGNAL_REWRITE_MAX_MANIFEST_BYTES:
        raise ValueError(
            f"census manifest of {len(payload)} bytes exceeds the {SIGNAL_REWRITE_MAX_MANIFEST_BYTES}-byte limit"
        )
    return payload


def _classify_signal_census_day(store: ObjectStore, day: date) -> SignalCensusDayFinding:
    """Classify one calendar day by calling `preflight_signal_rewrite_day` UNCHANGED.

    Every exception is caught, not just `SignalRewriteRefusal`: a census exists to survive a bad
    day and finish its bounded walk, so an object-store fault or a corrupt read on ONE day becomes
    a `refused` finding (`unreadable: ...`) rather than an aborted census. `preflight_signal_rewrite_day`
    itself takes no lock and holds no session, so this never contends with a live drain or a
    concurrent rewrite -- and never needs to.
    """
    try:
        preflight = preflight_signal_rewrite_day(store, day)
    except SignalRewriteAlreadyCurrent as error:
        return SignalCensusDayFinding(day=day, classification="current", reason=str(error))
    except SignalRewriteRefusal as error:
        return SignalCensusDayFinding(day=day, classification="refused", reason=str(error))
    except Exception as error:  # a census must survive one unreadable day to finish its walk
        return SignalCensusDayFinding(
            day=day,
            classification="refused",
            reason=f"unreadable: {type(error).__name__}: {error}",
        )
    if preflight.base_state == "legacy":
        return SignalCensusDayFinding(day=day, classification="legacy", reason=None)
    # base_state == "missing": no z13 marker at all -- neither the legacy nor the current shape,
    # and not raised by `preflight_signal_rewrite_day` because a cleanly missing day is its own
    # resumable-rewrite checkpoint, not an anomaly. For discovery it is still not a candidate, so it
    # is named and refused rather than silently absent from every one of the three buckets.
    return SignalCensusDayFinding(
        day=day,
        classification="refused",
        reason=(
            f"no z{BASE_ZOOM_TIER} marker recorded for {day.isoformat()}: missing checkpoint, not legacy or "
            "current data"
        ),
    )


def census_signal_rewrite_days(
    store: ObjectStore,
    *,
    run_id: str,
    first_day: date,
    last_day: date,
    max_days: int | None = None,
) -> SignalCensusSummary:
    """Walk `[first_day, last_day]` oldest-first, classifying every day, bounded and resumable.

    Read-only: no lock, no retraction, no write of any kind to the signal lane -- safe to run
    repeatedly and concurrently with itself, a drain, or a rewrite. `max_days` (like
    `parquet-vegetation-absence-ladders --max-days`) caps this ONE invocation to its oldest N days;
    a caller wanting the next chunk re-invokes with `first_day` advanced past `walked_last_day`.
    `SignalCensusSummary.complete` is `False` whenever `max_days` -- or the window itself running
    past `SIGNAL_CENSUS_MAX_DAYS` -- cut the walk short of `last_day`, so a partial census is never
    reported as if it were whole.
    """
    if last_day < first_day:
        raise ValueError(f"census window {first_day.isoformat()}..{last_day.isoformat()} runs backwards")
    span = (last_day - first_day).days + 1
    if span > MAX_GAP_WINDOW_DAYS:
        raise ValueError(f"census window of {span} days exceeds the {MAX_GAP_WINDOW_DAYS}-day budget")
    if max_days is not None and not 1 <= max_days <= SIGNAL_CENSUS_MAX_DAYS:
        raise ValueError(f"max-days must be between 1 and {SIGNAL_CENSUS_MAX_DAYS}")
    bound = min(max_days, SIGNAL_CENSUS_MAX_DAYS) if max_days is not None else SIGNAL_CENSUS_MAX_DAYS
    walked = min(span, bound)
    findings = tuple(_classify_signal_census_day(store, first_day + timedelta(days=offset)) for offset in range(walked))
    return SignalCensusSummary(
        run_id=run_id,
        requested_first_day=first_day,
        requested_last_day=last_day,
        walked_day_count=len(findings),
        complete=(walked == span),
        findings=findings,
    )
