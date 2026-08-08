"""Warehouse writer for geo.features: layer resolve, advisory lock, insert, in-place refresh, realtime publish."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Protocol

import structlog
from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.ingest.geometry import (
    GEOMETRY_VERSION_ACTIONS,
    FeatureGeometryLink,
    count_geometry_actions,
    feature_geometry_request,
    link_feature_geometry,
    undatable_natural_keys,
    upsert_geometry_versions,
)
from agri_data_service.ingest.realtime import REALTIME_CHANNEL_PATTERN

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.ingest.geometry import GeometryVersionRequest, GridCell
    from agri_data_service.ingest.identity import FeatureIdentity
    from agri_data_service.ingest.realtime import RealtimePublisher

logger = structlog.get_logger()

INSERT_BATCH_SIZE: Final = 100
MAX_LAYER_REFERENCE_LENGTH: Final = 100
MAX_EXTERNAL_ID_LENGTH: Final = 500

UUID_PATTERN: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class IngestContractError(ValueError):
    """Raised when a feature write's layer, identity or channel falls outside the bounded ingestion contract."""


class MissingIngestionLayerError(LookupError):
    """Raised when the configured layer name or id has no row in geo.layers."""


@dataclass(frozen=True, slots=True)
class FeatureWrite:
    """One upstream record ready for geo.features, carrying the identity that keys it."""

    layer_reference: str
    identity: FeatureIdentity
    properties: Mapping[str, object]
    channel: str
    grid_cell: GridCell | None = None

    @property
    def external_id(self) -> str:
        """The `properties->>'id'` value: the producer-local id, byte-identical to the TypeScript featureId."""
        return self.identity.producer_local_id

    @property
    def natural_key(self) -> str:
        """The producer-namespaced warehouse identity the Type-2 geometry dimension keys versions by."""
        return self.identity.natural_key

    def stored_properties(self) -> dict[str, object]:
        """The exact jsonb payload written to geo.features, with the external id pinned last."""
        return {**self.properties, "id": self.external_id}


class FeatureWriter(Protocol):
    """The seam every ingestion job writes through, so a job test needs no database."""

    async def __call__(self, writes: Sequence[FeatureWrite]) -> int:
        """Persist the writes and return the number of rows inserted plus genuinely-changed refreshes."""
        ...


# One line, one WHERE, no binds beyond the reference itself: extracting these buys no traceability,
# since the whole statement is already visible at its call site. See sql/AGENTS.md.
_RESOLVE_LAYER_BY_ID = text("SELECT id FROM geo.layers WHERE id = CAST(:layer_reference AS uuid) LIMIT 1")

_RESOLVE_LAYER_BY_NAME = text("SELECT id FROM geo.layers WHERE name = :layer_reference LIMIT 1")

_LOCK_EVENT_KEYS = text(load_query_sql("ingest/lock_event_keys.sql")).bindparams(
    bindparam("event_keys", type_=ARRAY(Text))
)

_SELECT_EXISTING_EXTERNAL_IDS = text(load_query_sql("ingest/select_existing_external_ids.sql")).bindparams(
    bindparam("external_ids", type_=ARRAY(Text))
)

_INSERT_FEATURES = text(load_query_sql("ingest/insert_features.sql")).bindparams(
    bindparam("payloads", type_=ARRAY(Text))
)

_REFRESH_FEATURES = text(load_query_sql("ingest/refresh_features.sql")).bindparams(
    bindparam("external_ids", type_=ARRAY(Text)),
    bindparam("next_properties", type_=ARRAY(Text)),
)


def feature_event_key(layer_id: str, external_id: str) -> str:
    """The feature-side advisory lock key: one row's `layer_id:external_id`, disjoint from geometry's space."""
    return f"{layer_id}:{external_id}"


async def lock_feature_event_keys(session: AsyncSession, event_keys: Sequence[str]) -> None:
    """Take the transaction-scoped feature lock per row, sorted, BEFORE any geometry lock.

    Both the forward writer and the repair pass must acquire in this order. The writer locks the
    feature rows, updates them, and only then takes the geometry key; a repair that took the
    geometry key first and then updated the same rows would meet it head-on and Postgres would
    abort one with 40P01 -- turning the hourly cron red, or killing a repair part way. See
    ingest/AGENTS.md "backfill.py".
    """
    if not event_keys:
        return
    await session.execute(_LOCK_EVENT_KEYS, {"event_keys": sorted(set(event_keys))})


def encode_stored_properties(write: FeatureWrite) -> str:
    """Serialise the stored jsonb payload, refusing the non-finite numbers jsonb cannot hold."""
    return json.dumps(write.stored_properties(), allow_nan=False, sort_keys=True, separators=(",", ":"))


def realtime_message(row_id: str, write: FeatureWrite) -> dict[str, object]:
    """Build the map-invalidation message in the GeoJSON Feature shape the TypeScript publisher emitted."""
    properties = write.stored_properties()
    return {
        "type": "Feature",
        "id": row_id,
        "properties": properties,
        "geometry": properties.get("geometry"),
    }


def validate_feature_write(write: FeatureWrite) -> None:
    """Reject a write whose layer reference, external id or channel is outside the bounded contract."""
    if not 0 < len(write.layer_reference) <= MAX_LAYER_REFERENCE_LENGTH:
        raise IngestContractError("ingestion layer reference is outside the bounded contract")
    external_id = write.external_id
    if not external_id.strip() or len(external_id) > MAX_EXTERNAL_ID_LENGTH:
        raise IngestContractError("ingestion feature identity is outside the bounded contract")
    if REALTIME_CHANNEL_PATTERN.match(write.channel) is None:
        raise IngestContractError("ingestion channel is outside the bounded contract")


async def resolve_layer_id(session: AsyncSession, layer_reference: str) -> str:
    """Resolve an operator-friendly layer name, or a uuid, without weakening the geo.features foreign key."""
    statement = _RESOLVE_LAYER_BY_ID if UUID_PATTERN.match(layer_reference) else _RESOLVE_LAYER_BY_NAME
    row = (await session.execute(statement, {"layer_reference": layer_reference})).first()
    if row is None:
        raise MissingIngestionLayerError(f"configured ingestion layer does not exist: {layer_reference}")
    return str(row.id)


def empty_geometry_action_tally() -> dict[str, int]:
    """A zeroed tally, so a batch that versioned nothing still reports all four actions rather than an absence."""
    return dict.fromkeys(GEOMETRY_VERSION_ACTIONS, 0)


async def _maintain_batch_geometry(
    session: AsyncSession,
    batch: Sequence[FeatureWrite],
    feature_id_by_external_id: Mapping[str, str],
    run_clock: datetime,
) -> dict[str, int]:
    """Version every place this batch touched, repoint its features, and report what the dimension actually did."""
    requests: list[GeometryVersionRequest] = []
    linked_places: list[tuple[str, str]] = []
    for write in batch:
        feature_id = feature_id_by_external_id.get(write.external_id)
        if feature_id is None:
            continue
        request = feature_geometry_request(write.identity, feature_id, write.grid_cell)
        requests.append(request)
        linked_places.append((feature_id, request.natural_key))
    if not requests:
        return empty_geometry_action_tally()

    outcomes = await upsert_geometry_versions(session, requests, run_clock)
    undatable = undatable_natural_keys(outcomes)
    if undatable:
        # A shape that moved with no instant to date the move re-detects on every tick; naming the places is
        # the only way an operator learns geo.features.geom and geo.geometry.geom have diverged permanently.
        logger.warning("geometry_version_undatable", count=len(undatable), natural_keys=undatable)
    await link_feature_geometry(
        session,
        [
            FeatureGeometryLink(feature_id=feature_id, geometry_id=outcomes[natural_key].geometry_id)
            for feature_id, natural_key in linked_places
            if natural_key in outcomes
        ],
    )
    return count_geometry_actions(outcomes)


async def _ingest_resolved_batch(
    session: AsyncSession,
    layer_id: str,
    batch: Sequence[FeatureWrite],
    run_clock: datetime,
) -> tuple[list[tuple[str, FeatureWrite]], dict[str, int]]:
    """Lock, insert and refresh one bounded batch in a single transaction, returning the rows and version actions."""
    write_by_external_id = {write.external_id: write for write in batch}
    external_ids = sorted(write_by_external_id)
    written: list[tuple[str, FeatureWrite]] = []
    feature_id_by_external_id: dict[str, str] = {}
    geometry_actions = empty_geometry_action_tally()
    try:
        await lock_feature_event_keys(
            session,
            [feature_event_key(layer_id, external_id) for external_id in external_ids],
        )
        existing_rows = await session.execute(
            _SELECT_EXISTING_EXTERNAL_IDS,
            {"layer_id": layer_id, "external_ids": external_ids},
        )
        for row in existing_rows.all():
            feature_id_by_external_id.setdefault(str(row.external_id), str(row.id))
        existing = set(feature_id_by_external_id)

        pending = [write for external_id, write in write_by_external_id.items() if external_id not in existing]
        refreshable = [write for external_id, write in write_by_external_id.items() if external_id in existing]

        if pending:
            inserted = await session.execute(
                _INSERT_FEATURES,
                {"layer_id": layer_id, "payloads": [encode_stored_properties(write) for write in pending]},
            )
            for row in inserted.all():
                write = write_by_external_id.get(row.external_id)
                if write is not None:
                    feature_id_by_external_id[str(row.external_id)] = str(row.id)
                    written.append((str(row.id), write))

        if refreshable:
            refreshed = await session.execute(
                _REFRESH_FEATURES,
                {
                    "layer_id": layer_id,
                    "external_ids": [write.external_id for write in refreshable],
                    "next_properties": [encode_stored_properties(write) for write in refreshable],
                },
            )
            # One counted row per changed external id, in `refreshable` order: a layer holding duplicate
            # rows for one external id still updates every one of them, and still counts and publishes
            # once, exactly as the per-row statement's `.first()` did.
            refreshed_id_by_external_id: dict[str, str] = {}
            for row in refreshed.all():
                refreshed_id_by_external_id.setdefault(str(row.external_id), str(row.id))
            written.extend(
                (refreshed_id_by_external_id[write.external_id], write)
                for write in refreshable
                if write.external_id in refreshed_id_by_external_id
            )

        geometry_actions = await _maintain_batch_geometry(session, batch, feature_id_by_external_id, run_clock)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return written, geometry_actions


async def ingest_features(
    session: AsyncSession,
    writes: Sequence[FeatureWrite],
    publisher: RealtimePublisher | None = None,
    run_clock: datetime | None = None,
) -> int:
    """Persist bounded batches, then emit one best-effort realtime invalidation per written row."""
    by_layer: dict[str, list[FeatureWrite]] = {}
    for write in writes:
        validate_feature_write(write)
        by_layer.setdefault(write.layer_reference, []).append(write)

    # One clock for the whole call, so every version this run confirms carries the same last_confirmed_at.
    confirmed_at = run_clock if run_clock is not None else datetime.now(UTC)
    written_total = 0
    geometry_actions = empty_geometry_action_tally()
    for layer_reference, layer_writes in by_layer.items():
        layer_id = await resolve_layer_id(session, layer_reference)
        for offset in range(0, len(layer_writes), INSERT_BATCH_SIZE):
            written, batch_actions = await _ingest_resolved_batch(
                session,
                layer_id,
                layer_writes[offset : offset + INSERT_BATCH_SIZE],
                confirmed_at,
            )
            written_total += len(written)
            for action, count in batch_actions.items():
                geometry_actions[action] += count
            if publisher is not None:
                for row_id, write in written:
                    await publisher.publish(write.channel, realtime_message(row_id, write))
    if any(geometry_actions.values()):
        logger.info("geometry_versions_maintained", **geometry_actions)
    return written_total


def bind_feature_writer(session: AsyncSession, publisher: RealtimePublisher | None = None) -> FeatureWriter:
    """Bind a session and publisher into the single-argument writer every job calls."""

    async def write_features(writes: Sequence[FeatureWrite]) -> int:
        return await ingest_features(session, writes, publisher)

    return write_features
