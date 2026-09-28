"""The durable monthly source-usage receipt (spec §4.9.2, WQ-6, FR-34): `receipts/source-usage/<YYYY-MM>.json`.

Written once per closed UTC month to the existing object store, so a database rebuild never erases the usage
history `agri.job_attempt.metrics` holds. The figures are `usage_report.py`'s own (`month_to_date`, the one
loader of its SQL, and the per-lane rollup), read for the closed month. See execution/AGENTS.md, "Monthly
usage receipt".
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal, Protocol

import structlog

from agri_data_service.execution.usage_report import group_usage_rows, month_to_date, provider_usage_rows
from agri_data_service.foundation.observability.vocabulary import POOL_LABELS

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

RECEIPT_DIRECTORY: Final = "receipts/source-usage/"
RECEIPT_CONTENT_TYPE: Final = "application/json"
RECEIPT_SCHEMA_VERSION: Final = 1
#: A closed month's receipt waits this long into the next month, so a turn that started before midnight and
#: was still `running` (and therefore uncharged) at the boundary has settled before the month is frozen.
RECEIPT_SETTLE_DELAY: Final = timedelta(days=1)
#: Sibling copy of `job_executor_service.py::CHARGE_BASIS_VARIABLE` (that module imports this one).
CHARGE_BASIS_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_CHARGE_BASIS"
EVENT_USAGE_RECEIPT_WRITTEN: Final = "plantgeo_job_executor_usage_receipt_written"

ReceiptOutcome = Literal["written", "already_present", "not_yet_settled"]


class ReceiptBackend(Protocol):
    """The two object-store calls a receipt needs; `pipeline/parquet/objectstore.py::ObjectStoreBackend` has both."""

    def put(self, key: str, payload: bytes, *, content_type: str) -> None: ...

    def size_of(self, key: str) -> int | None: ...


def month_start(month: date) -> datetime:
    """The first UTC instant of `month` (any day of it)."""
    return datetime(month.year, month.month, 1, tzinfo=UTC)


def following_month_start(month: date) -> datetime:
    """The first UTC instant after `month` ends: the half-open window's upper bound."""
    start = month_start(month)
    return (start + timedelta(days=32)).replace(day=1)


def closed_month(now: datetime) -> date:
    """The most recent UTC month that has closed at `now`: the month before `now`'s own."""
    current = now.astimezone(UTC)
    first_of_current = date(current.year, current.month, 1)
    previous = first_of_current - timedelta(days=1)
    return date(previous.year, previous.month, 1)


def receipt_path(month: date) -> str:
    """`receipts/source-usage/<YYYY-MM>.json`, relative to the store's prefix."""
    return f"{RECEIPT_DIRECTORY}{month:%Y-%m}.json"


@dataclass(frozen=True, slots=True)
class UsageReceiptStore:
    """The receipt's corner of the existing object store: its backend and the store-wide key prefix."""

    backend: ReceiptBackend
    prefix: str = ""

    @classmethod
    def from_settings(cls) -> UsageReceiptStore:
        """The bucket the Parquet lanes write to; raises when object storage is not configured."""
        from agri_data_service.config import settings  # noqa: PLC0415 - read at call time, never at import
        from agri_data_service.pipeline.parquet.objectstore import (  # noqa: PLC0415 - boto only when used
            BotoObjectStoreBackend,
            ObjectStore,
        )

        backend = BotoObjectStoreBackend.from_credentials(settings.require_object_store())
        return cls(backend=backend, prefix=ObjectStore(backend, prefix=settings.object_store_prefix).prefix)

    def key_for(self, month: date) -> str:
        return f"{self.prefix}{receipt_path(month)}"

    def exists(self, month: date) -> bool:
        return self.backend.size_of(self.key_for(month)) is not None

    def write(self, month: date, payload: bytes) -> str:
        key = self.key_for(month)
        self.backend.put(key, payload, content_type=RECEIPT_CONTENT_TYPE)
        return key


async def build_month_receipt(session: AsyncSession, *, month: date, now: datetime) -> dict[str, object]:
    """One closed month's usage: every pool's month-to-date figures and the per-lane rollup, read for that month."""
    since = month_start(month)
    until = following_month_start(month)
    pools = {pool: await month_to_date(session, pool=pool, now=since) for pool in sorted(POOL_LABELS)}
    rows = await provider_usage_rows(session, since=since, until=until, lane_ids=None, pool=None)
    return {
        "event": "plantgeo_source_usage_receipt",
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "month": f"{month:%Y-%m}",
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "generated_at": now.astimezone(UTC).isoformat(),
        "charge_basis": os.environ.get(CHARGE_BASIS_VARIABLE, "metered").strip().casefold() or "metered",
        "pools": pools,
        "by_lane": group_usage_rows(rows, by="lane"),
    }


async def write_closed_month_receipt(
    session: AsyncSession, *, store: UsageReceiptStore, now: datetime
) -> tuple[ReceiptOutcome, date]:
    """Write the last closed month's receipt unless it already exists: once per closed month, never rewritten.

    Waits `RECEIPT_SETTLE_DELAY` into the new month first. The ledger reads run in their own savepoint; the
    object-store calls run off the event loop.
    """
    month = closed_month(now)
    if now.astimezone(UTC) < following_month_start(month) + RECEIPT_SETTLE_DELAY:
        return "not_yet_settled", month
    if await asyncio.to_thread(store.exists, month):
        return "already_present", month
    async with session.begin_nested():
        receipt = await build_month_receipt(session, month=month, now=now)
    payload = json.dumps(receipt, sort_keys=True, default=str).encode("utf-8")
    key = await asyncio.to_thread(store.write, month, payload)
    logger.info(EVENT_USAGE_RECEIPT_WRITTEN, month=f"{month:%Y-%m}", key=key, bytes=len(payload))
    return "written", month


__all__ = [
    "RECEIPT_CONTENT_TYPE",
    "RECEIPT_DIRECTORY",
    "RECEIPT_SCHEMA_VERSION",
    "RECEIPT_SETTLE_DELAY",
    "ReceiptBackend",
    "ReceiptOutcome",
    "UsageReceiptStore",
    "build_month_receipt",
    "closed_month",
    "following_month_start",
    "month_start",
    "receipt_path",
    "write_closed_month_receipt",
]
