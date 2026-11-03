"""Year-sharded successful evaluation times, independent of publication quality."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.canonical import canonical_json
from agri_data_service.foundation.region.identity import geography_digest, identity_digest
from agri_data_service.pipeline.parquet.availability_primitives import (
    AvailabilityMalformedError,
    AvailabilityUnavailableError,
)
from agri_data_service.pipeline.runner.receipts import TurnReceiptError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.pipeline.runner.receipts import ConditionalObjectStorage

MAX_BYTES: Final = 64 * 1024
MAX_DAYS: Final = 366
CAS_ATTEMPTS: Final = 8
VERSION: Final = "lane-source-rechecks-v1"


def recheck_scope(lane: LaneConfig, region: Region, support: frozenset[str]) -> str:
    """Bind scheduling to the actual support and an explicit source interpretation version."""
    if not support or lane.recurring_cost is None or lane.source is None:
        raise TurnReceiptError("recurring cost requires explicit source support and contract")
    return identity_digest(
        "source-rechecks",
        {
            "geography": geography_digest(region),
            "support": sorted(support),
            "strategy": lane.strategy,
            "source_contract": lane.recurring_cost.source_contract,
            "source": lane.source.model_dump(mode="json", exclude={"history", "lifecycle"}),
        },
    )


def recheck_key(stream: str, scope: str, year: int) -> str:
    if (
        re.fullmatch(r"[a-z0-9][a-z0-9._-]*", stream) is None
        or re.fullmatch(r"[a-f0-9]{64}", scope) is None
        or not date.min.year <= year <= date.max.year
    ):
        raise TurnReceiptError("invalid source-recheck scope")
    return f"lane-source-rechecks/v1/{stream}/{scope}/{year:04d}.json"


def _decode(payload: bytes, scope: str, year: int) -> dict[date, datetime]:
    if len(payload) > MAX_BYTES:
        return {}
    try:
        document = json.loads(payload)
        if (
            not isinstance(document, dict)
            or set(document) != {"version", "scope", "year", "checks"}
            or document["version"] != VERSION
            or document["scope"] != scope
            or not isinstance(document["year"], int)
            or isinstance(document["year"], bool)
            or document["year"] != year
            or not isinstance(document["checks"], dict)
            or len(document["checks"]) > MAX_DAYS
        ):
            return {}
        entries: dict[date, datetime] = {}
        for named_day, timestamp in document["checks"].items():
            day, checked = date.fromisoformat(named_day), datetime.fromisoformat(timestamp)
            if day.year != year or day.isoformat() != named_day or checked.utcoffset() != timedelta(0):
                return {}
            entries[day] = checked.astimezone(UTC)
        return entries
    except (ValueError, TypeError, OverflowError):
        return {}


@dataclass(frozen=True, slots=True)
class SourceRechecks:
    """The same conditional storage adapter serves production and memory turns."""

    storage: ConditionalObjectStorage

    def _read(self, stream: str, scope: str, year: int) -> tuple[dict[date, datetime], str | None]:
        try:
            stored = self.storage.read(recheck_key(stream, scope, year), max_bytes=MAX_BYTES)
        except AvailabilityMalformedError:
            return {}, ""
        except AvailabilityUnavailableError as error:
            if error.code != "availability_oversized":
                raise
            return {}, ""
        if stored is None:
            return {}, None
        payload, etag = getattr(stored, "payload", None), getattr(stored, "etag", None)
        if not isinstance(payload, bytes) or not isinstance(etag, str) or not etag:
            return {}, ""
        return _decode(payload, scope, year), etag

    def checked(self, streams: Sequence[str], scope: str, first: date, last: date) -> dict[date, datetime]:
        """Only days evaluated successfully for every requested stream have a scheduling time."""
        result: dict[date, datetime] | None = None
        for stream in streams:
            entries: dict[date, datetime] = {}
            for year in range(first.year, last.year + 1):
                shard, _ = self._read(stream, scope, year)
                entries.update({day: stamp for day, stamp in shard.items() if first <= day <= last})
            result = (
                entries
                if result is None
                else {day: min(stamp, entries[day]) for day, stamp in result.items() if day in entries}
            )
        return result or {}

    def confirm(
        self,
        stream: str,
        scope: str,
        day: date,
        checked_at: datetime,
        *,
        now: datetime | Callable[[], datetime] | None = None,
    ) -> None:
        """CAS merge, preserving newer checks and concurrent days even when turns finish out of order."""
        self.confirm_many(stream, scope, {day: checked_at}, now=now)

    def confirm_many(
        self,
        stream: str,
        scope: str,
        checks: Mapping[date, datetime],
        *,
        now: datetime | Callable[[], datetime] | None = None,
    ) -> None:
        """Flush one successful publication batch using at most one swap per touched year."""
        current = _current_time(now)
        if current.utcoffset() != timedelta(0) or any(
            stamp.utcoffset() != timedelta(0) or stamp > current for stamp in checks.values()
        ):
            raise TurnReceiptError("source-recheck time must be UTC")
        for year in sorted({day.year for day in checks}):
            self._merge_year(
                stream, scope, year, {day: stamp for day, stamp in checks.items() if day.year == year}, now
            )

    def _merge_year(
        self,
        stream: str,
        scope: str,
        year: int,
        checks: Mapping[date, datetime],
        now: datetime | Callable[[], datetime] | None,
    ) -> None:
        for _attempt in range(CAS_ATTEMPTS):
            entries, etag = self._read(stream, scope, year)
            if etag == "":
                return  # An unreadable comparison token cannot authorize replacing a corrupt object.
            current = _current_time(now)
            updates = {
                day: stamp
                for day, stamp in checks.items()
                if day not in entries or entries[day] < stamp or entries[day] > current
            }
            if not updates:
                return
            entries.update(updates)
            payload = canonical_json(
                {
                    "version": VERSION,
                    "scope": scope,
                    "year": year,
                    "checks": {key.isoformat(): value.isoformat() for key, value in entries.items()},
                }
            ).encode()
            if len(payload) > MAX_BYTES:
                raise TurnReceiptError("source-recheck index exceeds size bound")
            if self.storage.compare_and_swap(
                recheck_key(stream, scope, year), payload, expected_etag=etag, content_type="application/json"
            ):
                return
        raise TurnReceiptError("source-recheck index lost every conditional swap")


def _current_time(now: datetime | Callable[[], datetime] | None) -> datetime:
    return datetime.now(UTC) if now is None else now() if callable(now) else now


def due_days(
    days: Sequence[date], checks: Mapping[date, datetime], *, now: datetime, interval_days: int
) -> tuple[tuple[date, ...], dict[date, datetime]]:
    """Missing and future times are due; successful checks order the rest fairly."""
    waiting = {
        day: checks[day] + timedelta(days=interval_days)
        for day in days
        if day in checks and checks[day] <= now < checks[day] + timedelta(days=interval_days)
    }
    oldest = datetime.min.replace(tzinfo=UTC)
    due = sorted(
        (day for day in days if day not in waiting),
        key=lambda day: (checks.get(day, oldest) if checks.get(day, oldest) <= now else oldest, day),
    )
    return tuple(due), waiting
