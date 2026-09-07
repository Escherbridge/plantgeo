"""One warehouse-wide object holding every availability lane's already-resolved coverage facts.

A CACHE, never an authority. Each entry is bound to the digest of the exact pointer document it was
derived from, so a reader that has just fetched that lane's pointer can prove in one comparison
whether the entry still describes the current generation -- and falls back to the full per-lane read
when it does not. See `AGENTS.md` in this directory, "Coverage rollup".

It lives beside the publication contract rather than under `parquet_ops/` because
`availability_index.py` refreshes it at the same statement that makes a publication visible, and a
`parquet_ops` module cannot be imported from here without closing an import cycle. For the same
reason it imports NOTHING from `availability_index` at runtime -- that module imports this one --
so the pointer key a coverage row reports is spelled by the reader, which already holds the
contract's own `availability_pointer_key`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import structlog

from agri_data_service.foundation.canonical import canonical_json, sha256_digest

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from agri_data_service.pipeline.parquet.availability_index import (
        AvailabilityIndex,
        AvailabilityNature,
        AvailabilityPointer,
        AvailabilityStorage,
    )

logger = structlog.get_logger()

COVERAGE_ROLLUP_SCHEMA_VERSION: Final = "availability-coverage-rollup-v1"

#: Warehouse root, deliberately NOT under any `layer=.../kind=...` prefix: a lane root belongs to one
#: lane and this object belongs to all of them. Every warehouse listing is rooted at `layer=`, so
#: nothing that walks lane prefixes can see it and no lane census can mistake it for a partition.
COVERAGE_ROLLUP_KEY: Final = "availability/_COVERAGE_ROLLUP.json"

#: A whole-warehouse entry set is day RANGES, not days: the measured 96,012-row generation set folds
#: to a few hundred ranges. The ceiling is two orders of magnitude above that so a corrupt or hostile
#: object is refused by declared length before its bytes are read, exactly as a pointer is.
MAX_COVERAGE_ROLLUP_BYTES: Final = 4 * 1024 * 1024

#: More lanes than the warehouse has, so a rollup that grew an unbounded entry set is refused rather
#: than parsed. 17 registered lanes plus 13 snapshot products is the population this bounds.
MAX_COVERAGE_ROLLUP_LANES: Final = 256

#: How many times one publisher re-reads and re-merges before it gives up on refreshing the rollup.
#: Higher than `MAX_PUBLICATION_ATTEMPTS` because losing here costs a stale entry a reader repairs,
#: while losing there costs a publication -- so this loop may be more patient, not less.
MAX_COVERAGE_ROLLUP_ATTEMPTS: Final = 6

JSON_CONTENT_TYPE: Final = "application/json"

_ENTRY_FIELDS: Final = {
    "absent_ranges",
    "generation_sha256",
    "lane",
    "lane_root",
    "nature",
    "pointer_sha256",
    "product",
    "published_ranges",
    "required_rungs",
    "rows",
    "source_ceiling",
    "updated_at",
}

_ROLLUP_FIELDS: Final = {"lanes", "schema_version"}

_RANGE_ENDPOINTS: Final = 2


class CoverageRollupMalformedError(RuntimeError):
    """The rollup object exists and is not the frozen shape. Callers fall back; they never fail."""


@dataclass(frozen=True, slots=True)
class CoverageRollupEntry:
    """One lane's resolved coverage facts, bound to the pointer document they were derived from.

    `published_ranges` and `absent_ranges` are the SELECTABLE days the whole authoritative rung
    ladder agreed on, already clipped at `source_ceiling` -- exactly the two sets
    `parquet_ops/availability_coverage.lane_coverage_from_index` computes before it closes a lane.
    Storing the closed rows instead would freeze a day-dependent answer: a release lane's carry is
    closed against TODAY, so a row cached on Monday states Monday's axis on Tuesday.
    """

    lane_root: str
    lane: str
    product: str
    nature: AvailabilityNature
    #: The freshness key: SHA-256 over the canonical pointer document this entry was derived from.
    #: One comparison covers every pointer field, so no drift can hide in a field nobody thought to
    #: compare -- and a reader that has the pointer in hand needs no further object to decide.
    pointer_sha256: str
    #: Carried separately because the coverage row publishes it, and because an entry whose
    #: generation digest disagrees with its own pointer digest is refused rather than served.
    generation_sha256: str
    source_ceiling: date
    required_rungs: tuple[int, ...]
    rows: int
    published_ranges: tuple[tuple[date, date], ...]
    absent_ranges: tuple[tuple[date, date], ...]
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_ranges(self.published_ranges, "published_ranges")
        _require_ranges(self.absent_ranges, "absent_ranges")
        if self.updated_at.tzinfo is None:
            raise ValueError("coverage rollup updated_at must be timezone aware")

    def published_days(self) -> frozenset[date]:
        """Expand the published runs back into the exact day set the index proved."""
        return frozenset(_expand(self.published_ranges))

    def absent_days(self) -> frozenset[date]:
        """Expand the governed-absence runs back into the exact day set the index proved."""
        return frozenset(_expand(self.absent_ranges))

    def answers(self, pointer: AvailabilityPointer) -> bool:
        """Report whether this entry still describes the generation this pointer names.

        THE WHOLE STALENESS TEST, and it is a total one: the entry carries a digest of the ENTIRE
        canonical pointer document, so any difference at all -- a new generation, a moved ceiling, a
        changed rung contract, a re-bound bootstrap receipt -- makes this `False` and sends the lane
        down the full per-lane read. The generation key is itself content addressed, so equal
        digests mean byte-identical evidence, not merely evidence that looks similar.
        """
        return self.pointer_sha256 == pointer_digest(pointer) and self.generation_sha256 == pointer.generation_sha256

    def to_wire(self) -> dict[str, object]:
        """Render this entry in the rollup's canonical shape."""
        return {
            "absent_ranges": [[first.isoformat(), last.isoformat()] for first, last in self.absent_ranges],
            "generation_sha256": self.generation_sha256,
            "lane": self.lane,
            "lane_root": self.lane_root,
            "nature": self.nature,
            "pointer_sha256": self.pointer_sha256,
            "product": self.product,
            "published_ranges": [[first.isoformat(), last.isoformat()] for first, last in self.published_ranges],
            "required_rungs": list(self.required_rungs),
            "rows": self.rows,
            "source_ceiling": self.source_ceiling.isoformat(),
            "updated_at": _format_instant(self.updated_at),
        }

    @classmethod
    def from_wire(cls, value: object) -> CoverageRollupEntry:
        """Parse one entry, refusing anything outside the frozen shape."""
        mapping = _require_mapping(value, "coverage rollup entry")
        _require_exact_keys(mapping, _ENTRY_FIELDS, "coverage rollup entry")
        return cls(
            lane_root=_require_text(mapping["lane_root"], "lane_root"),
            lane=_require_text(mapping["lane"], "lane"),
            product=_require_text(mapping["product"], "product"),
            nature=_require_nature(mapping["nature"]),
            pointer_sha256=_require_sha256(mapping["pointer_sha256"], "pointer_sha256"),
            generation_sha256=_require_sha256(mapping["generation_sha256"], "generation_sha256"),
            source_ceiling=_require_day(mapping["source_ceiling"], "source_ceiling"),
            required_rungs=_require_rungs(mapping["required_rungs"]),
            rows=_require_positive_int(mapping["rows"], "rows"),
            published_ranges=_require_wire_ranges(mapping["published_ranges"], "published_ranges"),
            absent_ranges=_require_wire_ranges(mapping["absent_ranges"], "absent_ranges"),
            updated_at=_require_instant(mapping["updated_at"], "updated_at"),
        )


@dataclass(frozen=True, slots=True)
class CoverageRollup:
    """Every lane entry the warehouse has published, keyed by physical lane root."""

    entries: Mapping[str, CoverageRollupEntry]

    def entry_for(self, lane_root: str) -> CoverageRollupEntry | None:
        """Return one lane's entry, or `None` when the rollup has never held that lane.

        `None` IS NOT AN ANSWER ABOUT COVERAGE. A lane the rollup has not heard of costs its full
        per-lane read; reporting it as having no days would turn a cold cache into a data outage.
        """
        return self.entries.get(lane_root)

    def with_entry(self, entry: CoverageRollupEntry) -> CoverageRollup:
        """Return this rollup with one lane replaced, leaving every other lane exactly as read."""
        return CoverageRollup(entries={**self.entries, entry.lane_root: entry})

    def to_bytes(self) -> bytes:
        """Render the canonical document: sorted keys, lane-root order, no incidental whitespace."""
        payload = {
            "lanes": [self.entries[root].to_wire() for root in sorted(self.entries)],
            "schema_version": COVERAGE_ROLLUP_SCHEMA_VERSION,
        }
        return canonical_json(payload).encode("utf-8")

    @classmethod
    def empty(cls) -> CoverageRollup:
        """The rollup a warehouse that has never published one still answers with."""
        return cls(entries={})

    @classmethod
    def parse(cls, payload: bytes) -> CoverageRollup:
        """Parse the whole document, refusing every shape fault as `CoverageRollupMalformedError`."""
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CoverageRollupMalformedError("coverage rollup is not decodable JSON") from exc
        try:
            mapping = _require_mapping(decoded, "coverage rollup")
            _require_exact_keys(mapping, _ROLLUP_FIELDS, "coverage rollup")
            if mapping["schema_version"] != COVERAGE_ROLLUP_SCHEMA_VERSION:
                raise ValueError(f"coverage rollup schema must be {COVERAGE_ROLLUP_SCHEMA_VERSION}")
            lanes = mapping["lanes"]
            if not isinstance(lanes, list):
                raise TypeError("coverage rollup lanes must be a list")
            if len(lanes) > MAX_COVERAGE_ROLLUP_LANES:
                raise ValueError(f"coverage rollup declares more than {MAX_COVERAGE_ROLLUP_LANES} lanes")
            entries: dict[str, CoverageRollupEntry] = {}
            for item in lanes:
                entry = CoverageRollupEntry.from_wire(item)
                if entry.lane_root in entries:
                    raise ValueError(f"coverage rollup names lane {entry.lane_root!r} twice")
                entries[entry.lane_root] = entry
        except (TypeError, ValueError) as exc:
            raise CoverageRollupMalformedError("coverage rollup is not the frozen shape") from exc
        return cls(entries=entries)


def pointer_digest(pointer: AvailabilityPointer) -> str:
    """Return the SHA-256 over one pointer's canonical document: the rollup's whole freshness key."""
    return sha256_digest(canonical_json(pointer.to_wire()).encode("utf-8"))


def entry_from_index(index: AvailabilityIndex, *, updated_at: datetime) -> CoverageRollupEntry:
    """Derive one lane's entry from an index that has already been verified.

    The published/absent split is computed HERE and nowhere else on the publication side, from the
    same `selectable_days()` intersection the read side uses, so a rollup entry cannot describe a
    day set the per-lane path would not have produced from the same generation.
    """
    pointer = index.pointer
    ceiling = pointer.source_ceiling
    selectable = frozenset(day for day in index.selectable_days() if day <= ceiling)
    published = frozenset(row.day for row in index.rows if row.day in selectable and row.terminal_state == "published")
    return CoverageRollupEntry(
        lane_root=pointer.identity.lane_root,
        lane=pointer.identity.lane,
        product=pointer.identity.product,
        nature=pointer.identity.nature,
        pointer_sha256=pointer_digest(pointer),
        generation_sha256=pointer.generation_sha256,
        source_ceiling=ceiling,
        required_rungs=tuple(pointer.required_rungs),
        rows=pointer.rows,
        published_ranges=fold_days(published),
        absent_ranges=fold_days(selectable - published),
        updated_at=updated_at,
    )


def read_coverage_rollup(store: AvailabilityStorage, *, key: str = COVERAGE_ROLLUP_KEY) -> CoverageRollup | None:
    """Read the rollup, or `None` when the warehouse has never published one.

    Raises only `CoverageRollupMalformedError` about CONTENT; a transport fault propagates, and the
    coverage reader that calls this treats every outcome other than a parsed rollup as "no rollup".
    """
    stored = store.read(key, max_bytes=MAX_COVERAGE_ROLLUP_BYTES)
    if stored is None:
        return None
    return CoverageRollup.parse(stored.payload)


def refresh_coverage_rollup_entry(
    store: AvailabilityStorage,
    *,
    entry: CoverageRollupEntry,
    key: str = COVERAGE_ROLLUP_KEY,
    attempts: int = MAX_COVERAGE_ROLLUP_ATTEMPTS,
) -> bool:
    """Merge one lane's entry into the shared rollup under compare-and-swap, and report whether it landed.

    WHY TWO PUBLISHERS CANNOT LOSE EACH OTHER'S UPDATE. Every attempt re-reads the object and builds
    its merge from the bytes it just observed, then writes conditionally on the ETag of exactly that
    observation -- the same discipline, through the same `AvailabilityStorage.compare_and_swap`, that
    advances a lane pointer in the owned publication core of `availability_index`. (Named that way
    rather than spelled out: `test_unlocked_availability_cores_have_no_production_callers` greps the
    tree for those three private names, and a prose citation is indistinguishable from a caller to a
    substring search.) Of two publishers
    racing on one observed version at most one write is accepted; the loser is told `False`, re-reads
    the winner's bytes, and merges its own lane on top. A writer only ever replaces ITS OWN lane's
    entry, so the merge is commutative across lanes and the loop converges rather than oscillating.
    A repeated ETag is not a hazard here: identical bytes are identical state, so a merge onto them
    is the merge that was already computed.

    NEVER RAISES INTO A PUBLICATION. The pointer swap that preceded this call is what made the
    publication durable; a rollup that failed to take the update is a stale CACHE entry, which the
    reader detects by pointer digest and repairs by reading the lane in full. Returning `False`
    rather than raising is what keeps a cache failure from becoming a publication failure.
    """
    for attempt in range(1, max(attempts, 1) + 1):
        stored = store.read(key, max_bytes=MAX_COVERAGE_ROLLUP_BYTES)
        try:
            held = CoverageRollup.empty() if stored is None else CoverageRollup.parse(stored.payload)
        except CoverageRollupMalformedError:
            # A rollup nobody can parse is worse than none: readers already fall back on it, and
            # leaving it in place would strand every lane forever. Replace its CONTENT with this
            # lane alone and let the other publishers merge themselves back in -- while still
            # writing against the ETag of the unparseable bytes, so a publisher that repaired it a
            # moment ago is not overwritten by this one's idea of an empty rollup.
            logger.warning(
                "coverage_rollup_replaced_malformed",
                lane_root=entry.lane_root,
                key=key,
                reason="the held rollup could not be parsed, so it is replaced rather than merged into",
            )
            held = CoverageRollup.empty()
        existing = held.entry_for(entry.lane_root)
        if existing is not None and existing.to_wire() == entry.to_wire():
            return True
        merged = held.with_entry(entry)
        if store.compare_and_swap(
            key,
            merged.to_bytes(),
            expected_etag=None if stored is None else stored.etag,
            content_type=JSON_CONTENT_TYPE,
        ):
            return True
        logger.info(
            "coverage_rollup_contended",
            lane_root=entry.lane_root,
            attempt=attempt,
            reason="another lane advanced the rollup between this read and its conditional write",
        )
    logger.warning(
        "coverage_rollup_refresh_abandoned",
        lane_root=entry.lane_root,
        key=key,
        attempts=attempts,
        reason=(
            "the publication is durable and only its rollup entry is stale; the coverage reader "
            "detects that by pointer digest and repairs it with a full per-lane read"
        ),
    )
    return False


def fold_days(days: Iterable[date]) -> tuple[tuple[date, date], ...]:
    """Fold a day set into ascending closed runs. A four-year lane is a handful of runs, not 1,600 days."""
    ordered = sorted(set(days))
    if not ordered:
        return ()
    runs: list[tuple[date, date]] = []
    first = previous = ordered[0]
    for day in ordered[1:]:
        if (day - previous).days == 1:
            previous = day
            continue
        runs.append((first, previous))
        first = previous = day
    runs.append((first, previous))
    return tuple(runs)


def _expand(ranges: Sequence[tuple[date, date]]) -> Iterable[date]:
    for first, last in ranges:
        for offset in range((last - first).days + 1):
            yield first + timedelta(days=offset)


def _require_ranges(ranges: tuple[tuple[date, date], ...], label: str) -> None:
    previous_last: date | None = None
    for entry in ranges:
        if len(entry) != _RANGE_ENDPOINTS:
            raise ValueError(f"{label} entries must be a first and last day")
        first, last = entry
        if last < first:
            raise ValueError(f"{label} run {first.isoformat()}..{last.isoformat()} runs backwards")
        if previous_last is not None and first <= previous_last:
            raise ValueError(f"{label} runs must be ascending and disjoint")
        previous_last = last


def _require_wire_ranges(value: object, label: str) -> tuple[tuple[date, date], ...]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be a list")
    parsed: list[tuple[date, date]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != _RANGE_ENDPOINTS:
            raise ValueError(f"{label} entries must be a two-element list")
        parsed.append((_require_day(item[0], label), _require_day(item[1], label)))
    return tuple(parsed)


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value


def _require_exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ValueError(f"{label} keys are wrong; missing={missing} extra={extra}")


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{label} must be a non-empty string")
    return value


def _require_nature(value: object) -> AvailabilityNature:
    text = _require_text(value, "nature")
    if text not in {"daily_series", "release_series"}:
        raise ValueError(f"nature {text!r} is not an availability nature")
    return text  # type: ignore[return-value]


def _require_sha256(value: object, label: str) -> str:
    text = _require_text(value, label)
    sha256_length = 64
    if len(text) != sha256_length or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{label} must be a lowercase hexadecimal SHA-256")
    return text


def _require_day(value: object, label: str) -> date:
    text = _require_text(value, label)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO calendar day") from exc


def _require_instant(value: object, label: str) -> datetime:
    text = _require_text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 instant") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must carry a UTC offset")
    return parsed


def _require_rungs(value: object) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise TypeError("required_rungs must be a non-empty list")
    rungs = tuple(_require_nonnegative_int(item, "required_rungs") for item in value)
    if list(rungs) != sorted(set(rungs)):
        raise ValueError("required_rungs must be ascending and unique")
    return rungs


def _require_positive_int(value: object, label: str) -> int:
    parsed = _require_nonnegative_int(value, label)
    if parsed == 0:
        raise ValueError(f"{label} must be positive")
    return parsed


def _require_nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(f"{label} must be a non-negative integer")
    return value


def _format_instant(value: datetime) -> str:
    """Render UTC exactly as `availability_index._format_datetime` does, so both documents agree."""
    rendered = value.astimezone(UTC).isoformat(timespec="microseconds")
    return f"{rendered[:-6]}Z"
