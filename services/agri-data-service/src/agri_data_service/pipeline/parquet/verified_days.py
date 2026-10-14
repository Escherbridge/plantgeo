"""Which lane-days are VERIFIED -- manual-correction only -- and the rewrite policy every automated writer obeys.

One register per lane (`objectstore.verified_days_register_path`), written only by the manual
verbs, read at most once per lane per turn. Rationale, the definition and why it is a register and
not the index's `digested` class: `AGENTS.md` in this directory, "Verified days are manual-correction
only".
"""

from __future__ import annotations

import json
import time
import weakref
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import TYPE_CHECKING, Final, Literal, cast

import structlog

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.foundation.parquet.lane_contract import nature_has_time_axis
from agri_data_service.pipeline.parquet.availability_primitives import (
    DIGESTED_PROVENANCE,
    JSON_CONTENT_TYPE,
    _format_datetime,
    _parse_datetime,
    _require_sha256,
)
from agri_data_service.pipeline.parquet.objectstore import (
    MAX_VERIFIED_DAYS_REGISTER_BYTES,
    ObjectStore,
    availability_lane_root,
    verified_days_register_path,
)
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from agri_data_service.foundation.parquet.lane_contract import LaneNature
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.pipeline.parquet.availability_documents import AvailabilityRow
    from agri_data_service.pipeline.parquet.availability_storage import AvailabilityStorage

logger = structlog.get_logger()

RewritePolicy = Literal["automated", "manual_correction"]

#: Every scheduled or backfill path: a verified day is refused, and an unreadable register refuses overwrites.
AUTOMATED_REWRITE: Final[RewritePolicy] = "automated"
#: A manual verb run with `--correct-verified`: a verified day is rewritten, then released from the register.
MANUAL_CORRECTION: Final[RewritePolicy] = "manual_correction"

#: The one word for "this day is verified; only a manual correction may change it" -- a lane-day
#: outcome (`gap_fill_contract.LaneDayOutcome`), an S11 rewrite reason (`runner/writer.py`) and the
#: prefix of every refusal detail, so a log search finds all three.
VERIFIED_REFUSAL: Final = "verified_manual_correction_only"
#: The structured log key a manual correction of a verified day emits, beside its register trace.
VERIFIED_DAY_CORRECTED_EVENT: Final = "plantgeo_verified_day_corrected"
VERIFIED_DAYS_SCHEMA_VERSION: Final = "verified-lane-days-v1"

#: Why a day in a registration range was NOT registered; the dry-run report names each one.
SKIP_NOT_INDEXED: Final = "not_indexed"
SKIP_MANIFEST_TRUSTED: Final = "manifest_trusted"
SKIP_GOVERNED_ABSENCE: Final = "governed_absence"
SKIP_MIXED: Final = "mixed"

#: A store re-reads a lane's register after this long, so a long-lived store cannot hold a stale answer.
_MEMO_SECONDS: Final = 300.0
_MAX_REGISTER_WRITE_ATTEMPTS: Final = 4
_REGISTER_FIELDS: Final = frozenset({"corrections", "days", "lane_root", "schema_version"})
_DAY_FIELDS: Final = frozenset(
    {"base_completion_sha256", "day", "generation_key", "ladder_sha256", "verified_at", "verified_by"}
)
_CORRECTION_FIELDS: Final = frozenset(
    {
        "corrected_at",
        "day",
        "new_base_completion_sha256",
        "old_base_completion_sha256",
        "run_id",
        "verified_ladder_sha256",
    }
)
#: Present only on a release by hand (`availability-release-verified-days`), so older records still parse.
_OPTIONAL_CORRECTION_FIELDS: Final = frozenset({"reason"})


class VerifiedDaysError(RuntimeError):
    """A register write was refused: malformed bytes already held, an oversized result, or a lost CAS race."""


@dataclass(frozen=True, slots=True)
class VerifiedDay:
    """One day the owner verified: what its index ladder held when a manual verb registered it."""

    day: date
    #: SHA-256 over the day's `(rung, terminal-evidence digest)` pairs; see `ladder_sha256`.
    ladder_sha256: str
    base_completion_sha256: str | None
    generation_key: str
    verified_at: datetime
    verified_by: str

    def to_wire(self) -> dict[str, object]:
        """Return the register projection."""
        return {
            "base_completion_sha256": self.base_completion_sha256,
            "day": self.day.isoformat(),
            "generation_key": self.generation_key,
            "ladder_sha256": self.ladder_sha256,
            "verified_at": _format_datetime(self.verified_at),
            "verified_by": self.verified_by,
        }

    @classmethod
    def from_wire(cls, value: object) -> VerifiedDay:
        """Parse one register entry, refusing any other shape."""
        mapping = _exact_mapping(value, _DAY_FIELDS, "verified day")
        ladder = _text(mapping["ladder_sha256"], "ladder_sha256")
        _require_sha256(ladder, "ladder_sha256")
        return cls(
            day=date.fromisoformat(_text(mapping["day"], "day")),
            ladder_sha256=ladder,
            base_completion_sha256=_optional_sha256(mapping["base_completion_sha256"], "base_completion_sha256"),
            generation_key=_text(mapping["generation_key"], "generation_key"),
            verified_at=_parse_datetime(mapping["verified_at"], "verified_at"),
            verified_by=_text(mapping["verified_by"], "verified_by"),
        )


@dataclass(frozen=True, slots=True)
class VerifiedDayCorrection:
    """The durable trace one manual correction (or release) of a verified day leaves: the day, the run, both digests.

    A release by hand records `new_base_completion_sha256=None` (nothing rewritten yet) and its `reason`.
    """

    day: date
    corrected_at: datetime
    run_id: str
    verified_ladder_sha256: str
    old_base_completion_sha256: str | None
    new_base_completion_sha256: str | None
    reason: str | None = None

    def to_wire(self) -> dict[str, object]:
        """Return the register projection; `reason` only when there is one."""
        wire: dict[str, object] = {
            "corrected_at": _format_datetime(self.corrected_at),
            "day": self.day.isoformat(),
            "new_base_completion_sha256": self.new_base_completion_sha256,
            "old_base_completion_sha256": self.old_base_completion_sha256,
            "run_id": self.run_id,
            "verified_ladder_sha256": self.verified_ladder_sha256,
        }
        if self.reason is not None:
            wire["reason"] = self.reason
        return wire

    @classmethod
    def from_wire(cls, value: object) -> VerifiedDayCorrection:
        """Parse one correction record, refusing any other shape."""
        mapping = _exact_mapping(value, _CORRECTION_FIELDS, "correction", optional=_OPTIONAL_CORRECTION_FIELDS)
        reason = mapping.get("reason")
        return cls(
            day=date.fromisoformat(_text(mapping["day"], "day")),
            corrected_at=_parse_datetime(mapping["corrected_at"], "corrected_at"),
            run_id=_text(mapping["run_id"], "run_id"),
            verified_ladder_sha256=_text(mapping["verified_ladder_sha256"], "verified_ladder_sha256"),
            old_base_completion_sha256=_optional_sha256(
                mapping["old_base_completion_sha256"], "old_base_completion_sha256"
            ),
            new_base_completion_sha256=_optional_sha256(
                mapping["new_base_completion_sha256"], "new_base_completion_sha256"
            ),
            reason=None if reason is None else _text(reason, "reason"),
        )


@dataclass(frozen=True, slots=True)
class VerifiedDays:
    """One lane's register as read this turn; `unreadable` set means the read failed and overwrites fail closed."""

    lane: str
    kind: PartitionKind
    days: Mapping[date, VerifiedDay] = field(default_factory=dict)
    corrections: tuple[VerifiedDayCorrection, ...] = ()
    unreadable: str | None = None

    def is_verified(self, day: date) -> bool:
        """True when the register names this day; an unreadable register names none (the seam fails closed)."""
        return day in self.days

    def split(self, days: Iterable[date]) -> tuple[list[date], list[date]]:
        """Return `(unverified, verified)` in the caller's order: what automation may take, and what it may not."""
        unverified: list[date] = []
        verified: list[date] = []
        for day in days:
            (verified if day in self.days else unverified).append(day)
        return unverified, verified


def render_verified_days(register: VerifiedDays) -> bytes:
    """Serialize one lane's register canonically: days ascending, corrections in the order they happened."""
    return canonical_json(
        {
            "corrections": [correction.to_wire() for correction in register.corrections],
            "days": [register.days[day].to_wire() for day in sorted(register.days)],
            "lane_root": availability_lane_root(register.lane, register.kind),
            "schema_version": VERIFIED_DAYS_SCHEMA_VERSION,
        }
    ).encode("utf-8")


def parse_verified_days(payload: bytes, *, lane: str, kind: PartitionKind) -> VerifiedDays:
    """Parse one lane's register bytes, refusing a document that names another lane or repeats a day."""
    mapping = _exact_mapping(json.loads(payload.decode("utf-8")), _REGISTER_FIELDS, "verified-days register")
    if mapping["schema_version"] != VERIFIED_DAYS_SCHEMA_VERSION:
        raise ValueError(f"verified-days register schema {mapping['schema_version']!r} is unknown")
    if mapping["lane_root"] != availability_lane_root(lane, kind):
        raise ValueError(f"verified-days register names {mapping['lane_root']!r}, not {lane}/{kind}")
    entries = [VerifiedDay.from_wire(item) for item in _list(mapping["days"], "days")]
    days = {entry.day: entry for entry in entries}
    if len(days) != len(entries):
        raise ValueError("verified-days register names one day twice")
    corrections = tuple(VerifiedDayCorrection.from_wire(item) for item in _list(mapping["corrections"], "corrections"))
    return VerifiedDays(lane=lane, kind=kind, days=days, corrections=corrections)


def read_verified_days(source: ObjectStore | AvailabilityStorage, *, lane: str, kind: PartitionKind) -> VerifiedDays:
    """ONE GET of the lane's register. A failed or malformed read comes back `unreadable`, never raised."""
    try:
        if isinstance(source, ObjectStore):
            payload = source.read_verified_days_register(lane, kind)
        else:
            stored = source.read(verified_days_register_path(lane, kind), max_bytes=MAX_VERIFIED_DAYS_REGISTER_BYTES)
            payload = None if stored is None else stored.payload
    except Exception as error:  # fail closed at the seam; the read itself never stops a turn
        return VerifiedDays(lane=lane, kind=kind, unreadable=f"{type(error).__name__}: {error}")
    if payload is None:
        return VerifiedDays(lane=lane, kind=kind)
    try:
        return parse_verified_days(payload, lane=lane, kind=kind)
    except (ValueError, TypeError, KeyError) as error:
        return VerifiedDays(lane=lane, kind=kind, unreadable=f"malformed register: {error}")


# Memoized per store instance -- a store is built per turn -- so one turn pays ONE GET per lane.
_TURN_READS: weakref.WeakKeyDictionary[ObjectStore, dict[tuple[str, str], tuple[float, VerifiedDays]]] = (
    weakref.WeakKeyDictionary()
)


def verified_days_for(
    store: ObjectStore,
    *,
    lane: str,
    kind: PartitionKind,
    monotonic: Callable[[], float] = time.monotonic,
) -> VerifiedDays:
    """The lane's register as this store last read it: at most one GET per lane per `_MEMO_SECONDS`."""
    try:
        per_store = _TURN_READS.setdefault(store, {})
    except TypeError:  # a store that cannot be weakly referenced is simply read every time
        return read_verified_days(store, lane=lane, kind=kind)
    now = monotonic()
    held = per_store.get((lane, kind))
    if held is not None and now - held[0] < _MEMO_SECONDS:
        return held[1]
    register = read_verified_days(store, lane=lane, kind=kind)
    per_store[(lane, kind)] = (now, register)
    return register


def forget_verified_days(store: ObjectStore) -> None:
    """Drop this store's memoized registers, so the next question re-reads them (after a register write)."""
    _TURN_READS.pop(store, None)


def automated_rewrite_refusal(  # noqa: PLR0913 - one lane-day coordinate per arg, plus the policy
    store: ObjectStore,
    *,
    lane: str,
    nature: LaneNature,
    kind: PartitionKind,
    day: date,
    policy: RewritePolicy,
) -> str | None:
    """Return why this write may not touch `day`, or `None` when it may. Fails closed for an EXISTING day only.

    A static lane has no availability index, so nothing about it can be verified and no GET is spent.
    """
    if policy == MANUAL_CORRECTION or not nature_has_time_axis(nature):
        return None
    register = verified_days_for(store, lane=lane, kind=kind)
    if register.is_verified(day):
        return (
            f"{VERIFIED_REFUSAL}: {lane} {day.isoformat()} is verified "
            f"({register.days[day].verified_by}); only a manual correction (--correct-verified) may rewrite it"
        )
    if register.unreadable is None:
        return None
    holds = _holds_published_content(store, lane=lane, kind=kind, day=day)
    if holds is False:
        return None
    state = "already holds published content" if holds else "could not be proven new"
    return (
        f"{VERIFIED_REFUSAL}: {lane}'s verified-days register could not be read ({register.unreadable}) and "
        f"{day.isoformat()} {state}, so an automated rewrite is refused fail-closed; a missing day is still written"
    )


def correction_target(  # noqa: PLR0913 - one lane-day coordinate per arg, plus the policy
    store: ObjectStore,
    *,
    lane: str,
    nature: LaneNature,
    kind: PartitionKind,
    day: date,
    policy: RewritePolicy,
) -> VerifiedDay | None:
    """Return the register entry a MANUAL correction is about to overwrite, or `None` when nothing verified is."""
    if policy != MANUAL_CORRECTION or not nature_has_time_axis(nature):
        return None
    return verified_days_for(store, lane=lane, kind=kind).days.get(day)


def base_completion_sha256(store: ObjectStore, *, lane: str, kind: PartitionKind, day: date) -> str | None:
    """The digest of the base rung's completion marker as it stands, or `None` when there is none (or no read)."""
    try:
        found = store.read_completion_receipt(lane, kind, BASE_ZOOM_TIER, day)
    except Exception:  # the trace records what it could see; a failed read is recorded as unknown
        return None
    return None if found is None else found.sha256


def record_correction(  # noqa: PLR0913 - one coordinate of the corrected day per arg
    storage: AvailabilityStorage,
    store: ObjectStore,
    *,
    lane: str,
    kind: PartitionKind,
    verified: VerifiedDay,
    run_id: str,
    corrected_at: datetime,
    old_base_completion_sha256: str | None,
) -> str:
    """Release a manually corrected day from the register, leave its trace, log it, and return the note.

    NEVER RAISES: the day is already rewritten. A failed release leaves the old entry standing, which
    keeps automation away from the day (the safe direction) and is said in the returned note.
    """
    correction = VerifiedDayCorrection(
        day=verified.day,
        corrected_at=corrected_at,
        run_id=run_id,
        verified_ladder_sha256=verified.ladder_sha256,
        old_base_completion_sha256=old_base_completion_sha256,
        new_base_completion_sha256=base_completion_sha256(store, lane=lane, kind=kind, day=verified.day),
    )
    try:
        release_verified_day(storage, lane=lane, kind=kind, correction=correction)
    except Exception as error:  # the rewrite stands; the stale entry fails closed until a re-run releases it
        logger.error(
            VERIFIED_DAY_CORRECTED_EVENT,
            lane=lane,
            kind=kind,
            released=False,
            error=f"{type(error).__name__}: {error}",
            **correction.to_wire(),
        )
        return (
            f"{verified.day.isoformat()}: corrected, but its verified-days entry could not be released "
            f"({type(error).__name__}: {error}); automation still refuses the day until a re-run releases it"
        )
    finally:
        forget_verified_days(store)
    logger.info(VERIFIED_DAY_CORRECTED_EVENT, lane=lane, kind=kind, released=True, **correction.to_wire())
    return (
        f"{verified.day.isoformat()}: verified day corrected by hand and released from the register "
        f"(base marker {old_base_completion_sha256} -> {correction.new_base_completion_sha256}); "
        "it needs re-verifying (availability-verify-days)"
    )


def record_verified_days(
    storage: AvailabilityStorage, *, lane: str, kind: PartitionKind, entries: Sequence[VerifiedDay]
) -> VerifiedDays:
    """Add (or refresh) register entries under compare-and-swap; the manual verbs' only write path."""

    def add(current: VerifiedDays) -> VerifiedDays:
        return replace(current, days={**current.days, **{entry.day: entry for entry in entries}})

    return _update_register(storage, lane=lane, kind=kind, change=add)


def release_verified_day(
    storage: AvailabilityStorage, *, lane: str, kind: PartitionKind, correction: VerifiedDayCorrection
) -> VerifiedDays:
    """Remove one corrected day from the register and append its correction trace, under compare-and-swap."""
    return release_verified_days(storage, lane=lane, kind=kind, corrections=(correction,))


def release_verified_days(
    storage: AvailabilityStorage, *, lane: str, kind: PartitionKind, corrections: Sequence[VerifiedDayCorrection]
) -> VerifiedDays:
    """Remove each day from the register and append its trace in ONE compare-and-swap; all or none land."""
    released = {correction.day for correction in corrections}

    def release(current: VerifiedDays) -> VerifiedDays:
        remaining = {day: entry for day, entry in current.days.items() if day not in released}
        return replace(current, days=remaining, corrections=(*current.corrections, *corrections))

    return _update_register(storage, lane=lane, kind=kind, change=release)


def ladder_sha256(rows: Sequence[AvailabilityRow]) -> str:
    """Digest one day's index ladder: every rung's terminal-evidence receipt, which binds its parts and marker."""
    return sha256_digest(
        canonical_json(
            [
                {"rung": row.rung, "terminal_receipt_sha256": row.terminal_receipt.sha256}
                for row in sorted(rows, key=lambda row: row.rung)
            ]
        )
    )


def verified_entries(  # noqa: PLR0913 - the head's rows, the range, and the three stamps every entry carries
    rows: Sequence[AvailabilityRow],
    *,
    days: Iterable[date],
    required_rungs: tuple[int, ...],
    generation_key: str,
    verified_at: datetime,
    verified_by: str,
) -> tuple[tuple[VerifiedDay, ...], dict[date, str]]:
    """Build an entry for each day whose WHOLE ladder is published and digested; name why every other day is not."""
    by_day: dict[date, list[AvailabilityRow]] = {}
    wanted = set(days)
    for row in rows:
        if row.day in wanted:
            by_day.setdefault(row.day, []).append(row)
    entries: list[VerifiedDay] = []
    skipped: dict[date, str] = {}
    for day in sorted(wanted):
        held = sorted(by_day.get(day, ()), key=lambda row: row.rung)
        reason = _unverifiable_reason(held, required_rungs=required_rungs)
        if reason is not None:
            skipped[day] = reason
            continue
        base = next((row for row in held if row.rung == BASE_ZOOM_TIER), None)
        entries.append(
            VerifiedDay(
                day=day,
                ladder_sha256=ladder_sha256(held),
                base_completion_sha256=(
                    None if base is None or base.completion_receipt is None else base.completion_receipt.sha256
                ),
                generation_key=generation_key,
                verified_at=verified_at,
                verified_by=verified_by,
            )
        )
    return tuple(entries), skipped


def _unverifiable_reason(held: Sequence[AvailabilityRow], *, required_rungs: tuple[int, ...]) -> str | None:
    if not held:
        return SKIP_NOT_INDEXED
    if tuple(row.rung for row in held) != tuple(sorted(required_rungs)):
        return SKIP_MIXED
    if any(row.terminal_state != "published" for row in held):
        return SKIP_GOVERNED_ABSENCE if all(row.terminal_state == "governed_absence" for row in held) else SKIP_MIXED
    if any(row.provenance != DIGESTED_PROVENANCE for row in held):
        return SKIP_MANIFEST_TRUSTED
    return None


def _holds_published_content(store: ObjectStore, *, lane: str, kind: PartitionKind, day: date) -> bool | None:
    """True when the base rung holds a marker, an absence or a part; `None` when that could not be read."""
    try:
        return (
            store.read_completion_marker(lane, kind, BASE_ZOOM_TIER, day) is not None
            or store.absence_exists(lane, kind, BASE_ZOOM_TIER, day)
            or store.partition_exists(lane, kind, BASE_ZOOM_TIER, day)
        )
    except Exception:  # unknown is not "new": the caller refuses the overwrite
        return None


def _update_register(
    storage: AvailabilityStorage,
    *,
    lane: str,
    kind: PartitionKind,
    change: Callable[[VerifiedDays], VerifiedDays],
) -> VerifiedDays:
    """Read-modify-write the register under its ETag; a malformed held register is refused, never overwritten."""
    key = verified_days_register_path(lane, kind)
    for _attempt in range(_MAX_REGISTER_WRITE_ATTEMPTS):
        stored = storage.read(key, max_bytes=MAX_VERIFIED_DAYS_REGISTER_BYTES)
        try:
            current = (
                VerifiedDays(lane=lane, kind=kind)
                if stored is None
                else parse_verified_days(stored.payload, lane=lane, kind=kind)
            )
        except (ValueError, TypeError, KeyError) as error:
            raise VerifiedDaysError(f"{key} holds a malformed register; an admin must repair it: {error}") from error
        updated = change(current)
        payload = render_verified_days(updated)
        if len(payload) > MAX_VERIFIED_DAYS_REGISTER_BYTES:
            raise VerifiedDaysError(f"{key} would exceed {MAX_VERIFIED_DAYS_REGISTER_BYTES} bytes")
        if storage.compare_and_swap(
            key,
            payload,
            expected_etag=None if stored is None else stored.etag,
            content_type=JSON_CONTENT_TYPE,
        ):
            return updated
    raise VerifiedDaysError(f"{key} stayed contended across {_MAX_REGISTER_WRITE_ATTEMPTS} attempts")


def _exact_mapping(
    value: object, fields: frozenset[str], label: str, *, optional: frozenset[str] = frozenset()
) -> dict[str, object]:
    if not isinstance(value, dict) or not fields <= set(value) <= fields | optional:
        raise ValueError(f"{label} must be an object with exactly: {', '.join(sorted(fields))}")
    return cast("dict[str, object]", value)


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return cast("list[object]", value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty trimmed string")
    return value


def _optional_sha256(value: object, label: str) -> str | None:
    if value is None:
        return None
    text = _text(value, label)
    _require_sha256(text, label)
    return text


__all__ = [
    "AUTOMATED_REWRITE",
    "MANUAL_CORRECTION",
    "SKIP_GOVERNED_ABSENCE",
    "SKIP_MANIFEST_TRUSTED",
    "SKIP_MIXED",
    "SKIP_NOT_INDEXED",
    "VERIFIED_DAYS_SCHEMA_VERSION",
    "VERIFIED_DAY_CORRECTED_EVENT",
    "VERIFIED_REFUSAL",
    "RewritePolicy",
    "VerifiedDay",
    "VerifiedDayCorrection",
    "VerifiedDays",
    "VerifiedDaysError",
    "automated_rewrite_refusal",
    "base_completion_sha256",
    "correction_target",
    "forget_verified_days",
    "ladder_sha256",
    "parse_verified_days",
    "read_verified_days",
    "record_correction",
    "record_verified_days",
    "release_verified_day",
    "release_verified_days",
    "render_verified_days",
    "verified_days_for",
    "verified_entries",
]
