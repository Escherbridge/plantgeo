"""Yearly source-completeness proofs; missing or incompatible proofs remain owed."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.canonical import canonical_json
from agri_data_service.pipeline.runner.receipts import TurnReceiptError

if TYPE_CHECKING:
    from agri_data_service.pipeline.runner.receipts import ConditionalObjectStorage, DayReceipt

COMPLETENESS_PREFIX: Final = "lane-source-completeness/v1"
COMPLETENESS_VERSION: Final = "lane-source-completeness-v1"
COMPLETENESS_MAX_BYTES: Final = 64 * 1024
COMPLETENESS_CAS_ATTEMPTS: Final = 8
_MAX_YEAR_DAYS: Final = 366


def coverage_digest(unit_ids: frozenset[str]) -> str:
    """Bind a completeness proof to the strategy's current expected support."""
    if not unit_ids or any(not unit for unit in unit_ids):
        raise TurnReceiptError("source coverage must name nonempty units")
    return hashlib.sha256(canonical_json(sorted(unit_ids)).encode()).hexdigest()


def unresolved_digest(unit_ids: frozenset[str]) -> str:
    """Mark a day fully answered under this support but unresolved; never equal to its `coverage_digest`."""
    coverage_digest(unit_ids)  # the same support validation
    return hashlib.sha256(canonical_json({"unresolved": sorted(unit_ids)}).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProofDays:
    """Days proven complete, and days fully answered but unresolved (still owed), under one support."""

    complete: frozenset[date]
    unresolved: frozenset[date]


def completeness_key(stream: str, year: int) -> str:
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]*", stream) is None or not date.min.year <= year <= date.max.year:
        raise TurnReceiptError("invalid source-completeness scope")
    return f"{COMPLETENESS_PREFIX}/{stream}/{year:04d}.json"


def _decode(payload: bytes, stream: str, year: int) -> dict[str, str]:
    if len(payload) > COMPLETENESS_MAX_BYTES:
        raise TurnReceiptError("source-completeness proof exceeds its size limit")
    try:
        document = json.loads(payload)
    except ValueError as error:
        raise TurnReceiptError("source-completeness proof is not JSON") from error
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "stream", "year", "complete_days"}
        or document["schema_version"] != COMPLETENESS_VERSION
        or document["stream"] != stream
        or not isinstance(document["year"], int)
        or isinstance(document["year"], bool)
        or document["year"] != year
    ):
        raise TurnReceiptError("source-completeness proof has invalid schema or scope")
    entries = document["complete_days"]
    if not isinstance(entries, dict) or len(entries) > _MAX_YEAR_DAYS:
        raise TurnReceiptError("source-completeness proof has invalid day entries")
    for named_day, digest in entries.items():
        try:
            day = date.fromisoformat(named_day)
        except (ValueError, TypeError) as error:
            raise TurnReceiptError("source-completeness proof has an invalid day") from error
        if (
            day.year != year
            or day.isoformat() != named_day
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise TurnReceiptError("source-completeness proof has invalid day coverage")
    return dict(entries)


@dataclass(frozen=True, slots=True)
class SourceCompleteness:
    """CAS-merged proofs written only while the publisher holds the lane-day lock."""

    storage: ConditionalObjectStorage

    def _read(self, stream: str, year: int) -> tuple[dict[str, str], str | None]:
        stored = self.storage.read(completeness_key(stream, year), max_bytes=COMPLETENESS_MAX_BYTES)
        if stored is None:
            return {}, None
        payload, etag = getattr(stored, "payload", None), getattr(stored, "etag", None)
        if not isinstance(payload, bytes) or not isinstance(etag, str) or not etag:
            raise TurnReceiptError("source-completeness proof has no payload or comparison token")
        return _decode(payload, stream, year), etag

    def proof_days(self, stream: str, first: date, last: date, expected: frozenset[str]) -> ProofDays:
        """One read per year: the proven days and the unresolved days in `[first, last]`."""
        proven, unresolved = coverage_digest(expected), unresolved_digest(expected)
        complete: set[date] = set()
        marked: set[date] = set()
        for year in range(first.year, last.year + 1):
            entries, _ = self._read(stream, year)
            for named_day, entry in entries.items():
                day = date.fromisoformat(named_day)
                if not first <= day <= last:
                    continue
                if entry == proven:
                    complete.add(day)
                elif entry == unresolved:
                    marked.add(day)
        return ProofDays(complete=frozenset(complete), unresolved=frozenset(marked))

    def complete_days(self, stream: str, first: date, last: date, expected: frozenset[str]) -> frozenset[date]:
        return self.proof_days(stream, first, last, expected).complete

    def invalidate(self, stream: str, day: date) -> None:
        """Remove proof before any partition mutation; a crash leaves the day source-owed."""
        self._update(stream, day, None)

    def confirm(self, receipt: DayReceipt) -> None:
        """Record full support after its matching receipt is durable: a proof, or the unresolved marker."""
        expected, present = receipt.expected_unit_ids, receipt.present_unit_ids
        if (
            receipt.publication_state == "complete"
            and expected
            and expected == present
            and len(expected) == receipt.expected_units == receipt.present_units
        ):
            digest = coverage_digest(expected) if receipt.source_resolved else unresolved_digest(expected)
            self._update(receipt.stream, receipt.day, digest)

    def _update(self, stream: str, day: date, digest: str | None) -> None:
        key = completeness_key(stream, day.year)
        for _attempt in range(COMPLETENESS_CAS_ATTEMPTS):
            entries, etag = self._read(stream, day.year)
            if digest is None:
                if day.isoformat() not in entries:
                    return
                del entries[day.isoformat()]
            else:
                entries[day.isoformat()] = digest
            payload = canonical_json(
                {"schema_version": COMPLETENESS_VERSION, "stream": stream, "year": day.year, "complete_days": entries}
            ).encode()
            if len(payload) > COMPLETENESS_MAX_BYTES:
                raise TurnReceiptError("source-completeness proof exceeds its size limit")
            if self.storage.compare_and_swap(key, payload, expected_etag=etag, content_type="application/json"):
                return
        raise TurnReceiptError(f"source-completeness proof for {stream}/{day.year} lost every swap")
