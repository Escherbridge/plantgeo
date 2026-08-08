"""One governed provenance upsert: DataSource -> SourceRelease -> Artifact -> ReleaseSet.

See `execution/AGENTS.md` §"The governed provenance upsert" for which call sites adopted this module,
which kept a private copy, and why.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import defer

from agri_data_service.models.provenance import (
    Artifact,
    DataSource,
    ReleaseSet,
    ReleaseSetItem,
    SourceRelease,
    SourceReviewState,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession


def assert_contract_fields(record: object, expected: Mapping[str, object], *, conflict_message: str) -> None:
    """Refuse a stored governed row whose fields disagree with the contract the caller re-derived."""
    if any(getattr(record, field) != value for field, value in expected.items()):
        raise ValueError(conflict_message)


async def advisory_lock(session: AsyncSession, key: str) -> None:
    """Serialize one governed identity for the rest of the caller-owned transaction."""
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))


async def find_data_source(session: AsyncSession, source_key: str) -> DataSource | None:
    """Return the data source registered under this key, or None when nothing claims it yet."""
    return (await session.execute(select(DataSource).where(DataSource.key == source_key))).scalar_one_or_none()


async def require_active_data_source(session: AsyncSession, source_key: str, *, inactive_message: str) -> DataSource:
    """Return the data source only when it is both approved and active."""
    source = await find_data_source(session, source_key)
    if source is None or source.review_state != SourceReviewState.APPROVED or not source.is_active:
        raise ValueError(inactive_message)
    return source


async def ensure_data_source(
    session: AsyncSession,
    candidate: DataSource,
    *,
    expected: Mapping[str, object],
    inactive_message: str,
    conflict_message: str,
) -> tuple[DataSource, bool]:
    """Register `candidate` once under its key, or return the stored row that already governs it.

    `expected` is the field set re-verified on replay. It is deliberately a caller argument rather than a
    fixed list: the historical lanes pin `configuration` while source ingestion does not, and silently
    unifying them would either loosen one governed refusal or tighten the other.
    """
    source = await find_data_source(session, candidate.key)
    if source is None:
        session.add(candidate)
        await session.flush()
        return candidate, False
    if source.review_state != SourceReviewState.APPROVED or not source.is_active:
        raise ValueError(inactive_message)
    assert_contract_fields(source, expected, conflict_message=conflict_message)
    return source, True


async def ensure_source_release(
    session: AsyncSession,
    candidate: SourceRelease,
    *,
    expected: Mapping[str, object],
    conflict_message: str,
) -> tuple[SourceRelease, bool]:
    """Write `candidate` once under `uq_source_release_identity`, or return the row already holding it.

    Identity is read off the candidate itself -- data source, source version, payload checksum and
    transform version -- so a caller cannot search under one identity and then insert another.
    """
    existing = (
        await session.execute(
            select(SourceRelease).where(
                SourceRelease.data_source_id == candidate.data_source_id,
                SourceRelease.source_version == candidate.source_version,
                SourceRelease.payload_checksum == candidate.payload_checksum,
                SourceRelease.transform_version == candidate.transform_version,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(candidate)
        await session.flush()
        return candidate, False
    assert_contract_fields(existing, expected, conflict_message=conflict_message)
    return existing, True


def require_validation_timestamp(release: SourceRelease, *, message: str) -> None:
    """Refuse a release that claims validity without recording when it was validated."""
    if release.validated_at is None:
        raise ValueError(message)


async def ensure_artifact(
    session: AsyncSession,
    candidate: Artifact,
    *,
    expected: Mapping[str, object],
    defer_content_bytes: bool,
    conflict_message: str,
) -> tuple[Artifact, bool]:
    """Write `candidate` once under its (uri, checksum) identity, or return the artifact already there.

    `defer_content_bytes` is required rather than defaulted. When the comparison does not read
    `content_bytes` the blob must stay unloaded: the WHERE clause already pins `checksum_sha256`, and
    the `inline_artifact_checksum_matches` / `inline_artifact_size_matches` CHECKs make that equality
    proof of content identity. Dragging the blob back only to re-prove it cost up to 64 MB per
    idempotent replay -- on exactly the path these lanes resume through. Callers that genuinely compare
    the bytes (or assert their absence) must pass False.
    """
    statement = select(Artifact).where(
        Artifact.uri == candidate.uri, Artifact.checksum_sha256 == candidate.checksum_sha256
    )
    if defer_content_bytes:
        statement = statement.options(defer(Artifact.content_bytes))
    existing = (await session.execute(statement)).scalar_one_or_none()
    if existing is None:
        session.add(candidate)
        await session.flush()
        return candidate, False
    assert_contract_fields(existing, expected, conflict_message=conflict_message)
    return existing, True


async def find_release_set(
    session: AsyncSession,
    *,
    logical_key: str,
    manifest_checksum: str,
    conflict_message: str,
) -> ReleaseSet | None:
    """Resolve the one release set claimed by this key or checksum, refusing a split identity."""
    matches = (
        (
            await session.execute(
                select(ReleaseSet).where(
                    (ReleaseSet.logical_key == logical_key) | (ReleaseSet.manifest_checksum == manifest_checksum)
                )
            )
        )
        .scalars()
        .all()
    )
    if len(matches) > 1:
        raise ValueError(conflict_message)
    return matches[0] if matches else None


async def release_set_member_ids(session: AsyncSession, release_set_id: uuid.UUID) -> set[uuid.UUID]:
    """Return every source release currently bound to one release set."""
    return set(
        (
            await session.execute(
                select(ReleaseSetItem.source_release_id).where(ReleaseSetItem.release_set_id == release_set_id)
            )
        ).scalars()
    )
