"""Resolve the newest captured source state; no calendar-shaped historical claims."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
from agri_data_service.pipeline.direct.land_context.products import ARCHIVE_ROOT, SHA256_HEX_LENGTH
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage, StoredAvailabilityObject
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

STATE_KEY = f"{ARCHIVE_ROOT}/_STATE.json"


def read_state(storage: AvailabilityStorage) -> tuple[StoredAvailabilityObject | None, dict[str, Any]]:
    stored = storage.read(STATE_KEY, max_bytes=16 * 1024)
    if stored is None:
        return None, {"schema": "blm-pnw-publication-state/v1", "pending": None, "published": None}
    state = json.loads(stored.payload)
    if not isinstance(state, dict) or state.get("schema") != "blm-pnw-publication-state/v1":
        raise ValueError("BLM publication state is malformed")
    for key in ("pending", "published"):
        value = state.get(key)
        if value is not None:
            if not isinstance(value, dict) or set(value) != {"day", "captured_at", "manifest_sha256", "content_sha256"}:
                raise ValueError("BLM publication state has malformed snapshot coordinates")
            date.fromisoformat(value["day"])
            captured = datetime.fromisoformat(value["captured_at"])
            if captured.tzinfo is None or captured.date().isoformat() != value["day"]:
                raise ValueError("BLM publication state has inconsistent capture coordinates")
            for digest_key in ("manifest_sha256", "content_sha256"):
                identity = value[digest_key]
                if (
                    not isinstance(identity, str)
                    or len(identity) != SHA256_HEX_LENGTH
                    or any(c not in "0123456789abcdef" for c in identity)
                ):
                    raise ValueError("BLM publication state has malformed content identity")
    return stored, state


async def read_land_context_source_watermark(
    session: AsyncSession,
    store: ObjectStore,
    *,
    today: date,
) -> SourceWatermark:
    """Expose captured work as owed until every registered product finishes its ladder."""
    del session, store
    _stored, state = read_state(BotoAvailabilityStorage.from_settings())
    snapshot = state["pending"] or state["published"]
    if snapshot is None:
        raise ValueError("BLM source has no validated capture yet; run land_context forward")
    day = date.fromisoformat(snapshot["day"])
    if day > today:
        raise ValueError("BLM capture cannot be available before it was acquired")
    return SourceWatermark(
        day=day,
        basis=f"BLM captured source content {snapshot['content_sha256']}; no historic source version is inferred",
    )
