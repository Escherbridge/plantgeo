"""The one release-set finalization every historical lane shares, and the guard rail around it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agri_data_service.execution.historical_writer._results import HistoricalReleaseSetResult
from agri_data_service.execution.historical_writer._shared import (
    HISTORICAL_SOURCE_INACTIVE_MESSAGE,
    _utc_now_or_value,
)
from agri_data_service.execution.provenance import (
    advisory_lock,
    find_release_set,
    release_set_member_ids,
    require_active_data_source,
)
from agri_data_service.models.provenance import ReleaseSet, ReleaseSetItem, ReleaseSetState

if TYPE_CHECKING:
    import uuid
    from collections.abc import Awaitable, Callable, Iterable
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.execution.historical_writer._results import ReleaseSetIdentity


async def _finalize_historical_release_set(  # noqa: PLR0913
    session: AsyncSession,
    *,
    identity: ReleaseSetIdentity,
    manifest_checksum: str,
    receipt_times: Iterable[datetime],
    source_key: str,
    required_release_ids: Callable[[uuid.UUID], Awaitable[set[uuid.UUID]]],
    validated_at: datetime | None,
) -> HistoricalReleaseSetResult:
    """Refuse a backdated release set, lock its key, then validate the membership its receipts demand."""
    if any(retrieved_at > identity.as_of_time for retrieved_at in receipt_times):
        raise ValueError("release_set_as_of must not precede a persisted source receipt")
    await advisory_lock(session, f"historical-release-set:{identity.logical_key}")
    source = await require_active_data_source(session, source_key, inactive_message=HISTORICAL_SOURCE_INACTIVE_MESSAGE)
    expected_ids = await required_release_ids(source.id)
    return await _finalize_release_set(
        session,
        identity,
        manifest_checksum=manifest_checksum,
        expected_ids=expected_ids,
        validated_at=validated_at,
    )


async def _finalize_release_set(
    session: AsyncSession,
    identity: ReleaseSetIdentity,
    *,
    manifest_checksum: str,
    expected_ids: set[uuid.UUID],
    validated_at: datetime | None,
) -> HistoricalReleaseSetResult:
    """Atomically validate one release set's membership; the warehouse trigger freezes it afterwards."""
    release_set_key = identity.logical_key
    release_set = await find_release_set(
        session,
        logical_key=release_set_key,
        manifest_checksum=manifest_checksum,
        conflict_message="release set key and manifest checksum identify different historical release sets",
    )
    if release_set is None:
        release_set = ReleaseSet(
            logical_key=release_set_key,
            as_of_time=identity.as_of_time,
            manifest_checksum=manifest_checksum,
            state=ReleaseSetState.DRAFT,
            description=identity.description,
        )
        session.add(release_set)
        await session.flush()
        idempotent = False
    elif (
        release_set.logical_key != release_set_key
        or release_set.manifest_checksum != manifest_checksum
        or release_set.as_of_time != identity.as_of_time
        or release_set.description != identity.description
    ):
        raise ValueError("historical release set identity is already governed by different content")
    elif release_set.state in {ReleaseSetState.VALIDATED, ReleaseSetState.PUBLISHED}:
        members = await release_set_member_ids(session, release_set.id)
        if members != expected_ids:
            raise ValueError("finalized historical release set has different source membership")
        return HistoricalReleaseSetResult(
            release_set_id=release_set.id,
            manifest_checksum=manifest_checksum,
            source_release_count=len(expected_ids),
            idempotent=True,
        )
    elif release_set.state != ReleaseSetState.DRAFT:
        raise ValueError("historical release set is not in a mutable draft state")
    else:
        idempotent = False

    existing_members = await release_set_member_ids(session, release_set.id)
    unexpected_members = existing_members.difference(expected_ids)
    if unexpected_members:
        raise ValueError("historical draft release set contains unexpected source releases")
    for source_release_id in sorted(expected_ids.difference(existing_members), key=str):
        session.add(ReleaseSetItem(release_set_id=release_set.id, source_release_id=source_release_id))
    await session.flush()
    members = await release_set_member_ids(session, release_set.id)
    if members != expected_ids:
        raise ValueError("historical release set did not retain complete source membership")
    release_set.state = ReleaseSetState.VALIDATED
    release_set.validated_at = _utc_now_or_value(validated_at)
    await session.flush()
    return HistoricalReleaseSetResult(
        release_set_id=release_set.id,
        manifest_checksum=manifest_checksum,
        source_release_count=len(expected_ids),
        idempotent=idempotent,
    )
