"""Type-2 geometry dimension adapter: open, confirm or supersede a place's shape inside the caller's transaction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal
from uuid import uuid4

from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY

from agri_data_service.ingest.identity import MAX_NATURAL_KEY_LENGTH

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.ingest.identity import FeatureIdentity

# `observed_at is None` means "this producer supplied no honest observation time", which the dimension
# spells '-infinity', never `datetime.min` (year 1 is a real, sortable instant and this is not).
NEGATIVE_INFINITY_TIMESTAMP: Final = "-infinity"

# Keeps the geometry lock space disjoint from writer.py's `layer_id:external_id` feature lock space.
GEOMETRY_LOCK_NAMESPACE: Final = "geo.geometry"

MAX_GRID_NAME_LENGTH: Final = 100
MAX_CELL_KEY_LENGTH: Final = 180

POINT_KIND: Final = "point"
POLYGON_KIND: Final = "polygon"
LINE_KIND: Final = "line"
GRID_CELL_KIND: Final = "grid_cell"

GeometryVersionAction = Literal["opened", "confirmed", "superseded", "undatable"]

# Every action the adapter can take, so a tally always carries all four keys and never a sparse dict.
GEOMETRY_VERSION_ACTIONS: Final[tuple[GeometryVersionAction, ...]] = (
    "opened",
    "confirmed",
    "superseded",
    "undatable",
)


class GeometryContractError(ValueError):
    """Raised when a geometry version request falls outside the dimension's declared contract."""


class MissingGeometryError(LookupError):
    """Raised when a request names a place whose geometry cannot be resolved; never version a shape we lack."""


@dataclass(frozen=True, slots=True)
class GridCell:
    """One raster grid cell, which the dimension stores as a geometry row in its own right."""

    grid_name: str
    cell_key: str
    resolution_metres: int
    geojson: str

    def __post_init__(self) -> None:
        """Enforce the column widths and the positive resolution `ck_geometry_cell_fields` requires."""
        if not 0 < len(self.grid_name) <= MAX_GRID_NAME_LENGTH:
            raise GeometryContractError(f"grid_name must be 1-{MAX_GRID_NAME_LENGTH} characters")
        if not 0 < len(self.cell_key) <= MAX_CELL_KEY_LENGTH:
            raise GeometryContractError(f"cell_key must be 1-{MAX_CELL_KEY_LENGTH} characters")
        if self.resolution_metres <= 0:
            raise GeometryContractError("resolution_metres must be a positive number of metres")
        if not self.geojson.strip():
            raise GeometryContractError("a grid cell must carry the GeoJSON of the cell itself")


@dataclass(frozen=True, slots=True)
class StoredFeatureGeometry:
    """Take the geometry PostGIS already normalised onto a geo.features row, never re-parsing its GeoJSON."""

    feature_id: str


@dataclass(frozen=True, slots=True)
class GeoJsonGeometry:
    """Take the geometry from RFC 7946 GeoJSON text, for a place that owns no geo.features row."""

    geojson: str


GeometrySource = StoredFeatureGeometry | GeoJsonGeometry


@dataclass(frozen=True, slots=True)
class GeometryVersionRequest:
    """One place, the shape claimed for it now, and the observation time that would date a new version."""

    natural_key: str
    producer: str
    observed_at: datetime | None
    source: GeometrySource
    grid_cell: GridCell | None = None

    def __post_init__(self) -> None:
        """Enforce the producer namespacing `ck_geometry_natural_key_namespaced` enforces in the database."""
        if not self.natural_key.startswith(f"{self.producer}:"):
            raise GeometryContractError("natural_key must be namespaced by its producer token")
        if len(self.natural_key) > MAX_NATURAL_KEY_LENGTH:
            raise GeometryContractError(f"natural_key must not exceed {MAX_NATURAL_KEY_LENGTH} characters")
        if self.observed_at is not None and self.observed_at.utcoffset() is None:
            raise GeometryContractError("observed_at must include a timezone")


@dataclass(frozen=True, slots=True)
class GeometryVersionOutcome:
    """The version a place now points at, and what the adapter did to get there."""

    natural_key: str
    geometry_id: str
    action: GeometryVersionAction


@dataclass(frozen=True, slots=True)
class FeatureGeometryLink:
    """One geo.features row and the geometry version it should point at."""

    feature_id: str
    geometry_id: str


@dataclass(frozen=True, slots=True)
class _Classification:
    """What the database says about one requested place before anything is written."""

    natural_key: str
    geometry_missing: bool
    open_geometry_id: str | None
    geometry_unchanged: bool
    successor_is_datable: bool


@dataclass(frozen=True, slots=True)
class _PlannedVersion:
    """A version about to be inserted, with its identifier minted ahead of the write."""

    geometry_id: str
    request: GeometryVersionRequest


@dataclass(frozen=True, slots=True)
class _Supersession:
    """A version about to be closed, naming the successor that must be written in the same transaction."""

    natural_key: str
    closed_at: str
    successor_id: str


@dataclass(frozen=True, slots=True)
class _VersionPlan:
    """The partition of one batch into the four things the adapter can honestly do to a version chain."""

    opened: list[_PlannedVersion] = field(default_factory=list)
    supersessions: list[_Supersession] = field(default_factory=list)
    successors: list[_PlannedVersion] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    undatable: list[str] = field(default_factory=list)


_LOCK_GEOMETRY_KEYS = text(
    """
    SELECT pg_advisory_xact_lock(hashtext(:lock_namespace || ':' || natural_key))
    FROM unnest(CAST(:natural_keys AS text[])) AS locks(natural_key)
    ORDER BY natural_key
    """
).bindparams(bindparam("natural_keys", type_=ARRAY(Text)))

# `open_version.geom = resolved.geom` is PostGIS exact equality (same coordinates in the same order), the
# cheap path that carries virtually every tick. ST_Equals is the topological fallback that stops a
# re-encoded but identical shape from minting a version. See ingest/AGENTS.md "geometry.py".
_CLASSIFY_GEOMETRY_VERSIONS = text(
    """
    WITH request AS (
        SELECT requested.natural_key,
               NULLIF(requested.feature_id, '') AS feature_id,
               NULLIF(requested.geojson, '') AS geojson,
               CAST(requested.observed_at AS timestamptz) AS observed_at
        FROM unnest(
                 CAST(:natural_keys AS text[]),
                 CAST(:feature_ids AS text[]),
                 CAST(:geojsons AS text[]),
                 CAST(:observed_ats AS text[])
             ) AS requested(natural_key, feature_id, geojson, observed_at)
    ),
    resolved AS (
        SELECT request.natural_key,
               request.observed_at,
               CASE
                   WHEN request.feature_id IS NOT NULL THEN feature.geom
                   ELSE ST_SetSRID(ST_GeomFromGeoJSON(request.geojson), 4326)
               END AS geom
        FROM request
        LEFT JOIN geo.features AS feature ON feature.id = CAST(request.feature_id AS uuid)
    )
    SELECT resolved.natural_key AS natural_key,
           resolved.geom IS NULL AS geometry_missing,
           open_version.geometry_id AS open_geometry_id,
           ((open_version.geom = resolved.geom) OR ST_Equals(open_version.geom, resolved.geom))
               AS geometry_unchanged,
           (resolved.observed_at > open_version.version_valid_from) AS successor_is_datable
    FROM resolved
    LEFT JOIN geo.geometry AS open_version
      ON open_version.natural_key = resolved.natural_key
     AND open_version.version_valid_to IS NULL
    """
).bindparams(
    bindparam("natural_keys", type_=ARRAY(Text)),
    bindparam("feature_ids", type_=ARRAY(Text)),
    bindparam("geojsons", type_=ARRAY(Text)),
    bindparam("observed_ats", type_=ARRAY(Text)),
)

# ck_geometry_supersede is immediate, so version_valid_to and superseded_by are written together and the
# successor's identifier is minted before its row exists; the DEFERRABLE FK validates it at COMMIT.
_CLOSE_GEOMETRY_VERSIONS = text(
    """
    UPDATE geo.geometry AS closing
    SET version_valid_to = CAST(supersession.closed_at AS timestamptz),
        superseded_by = CAST(supersession.successor_id AS uuid)
    FROM unnest(
             CAST(:natural_keys AS text[]),
             CAST(:closed_ats AS text[]),
             CAST(:successor_ids AS text[])
         ) AS supersession(natural_key, closed_at, successor_id)
    WHERE closing.natural_key = supersession.natural_key
      AND closing.version_valid_to IS NULL
    RETURNING closing.geometry_id
    """
).bindparams(
    bindparam("natural_keys", type_=ARRAY(Text)),
    bindparam("closed_ats", type_=ARRAY(Text)),
    bindparam("successor_ids", type_=ARRAY(Text)),
)

# An unmapped GeometryType yields a NULL geom_kind and trips the NOT NULL, which is the same refusal
# scripts/backfill-geometry.sql makes: never store a version whose kind the dimension does not name.
_INSERT_GEOMETRY_VERSIONS = text(
    """
    WITH request AS (
        SELECT requested.geometry_id,
               requested.natural_key,
               requested.producer,
               CAST(requested.version_valid_from AS timestamptz) AS version_valid_from,
               NULLIF(requested.feature_id, '') AS feature_id,
               NULLIF(requested.geojson, '') AS geojson,
               NULLIF(requested.grid_name, '') AS grid_name,
               NULLIF(requested.cell_key, '') AS cell_key,
               CAST(NULLIF(requested.resolution_metres, '') AS integer) AS resolution_metres
        FROM unnest(
                 CAST(:geometry_ids AS text[]),
                 CAST(:natural_keys AS text[]),
                 CAST(:producers AS text[]),
                 CAST(:version_valid_froms AS text[]),
                 CAST(:feature_ids AS text[]),
                 CAST(:geojsons AS text[]),
                 CAST(:grid_names AS text[]),
                 CAST(:cell_keys AS text[]),
                 CAST(:resolution_metres AS text[])
             ) AS requested(
                 geometry_id, natural_key, producer, version_valid_from,
                 feature_id, geojson, grid_name, cell_key, resolution_metres
             )
    ),
    resolved AS (
        SELECT request.*,
               CASE
                   WHEN request.feature_id IS NOT NULL THEN feature.geom
                   ELSE ST_SetSRID(ST_GeomFromGeoJSON(request.geojson), 4326)
               END AS geom
        FROM request
        LEFT JOIN geo.features AS feature ON feature.id = CAST(request.feature_id AS uuid)
    )
    INSERT INTO geo.geometry (
        geometry_id, natural_key, version_valid_from, version_valid_to,
        geom_kind, geom, centroid, grid_name, cell_key, resolution_m, producer, last_confirmed_at
    )
    SELECT CAST(resolved.geometry_id AS uuid),
           resolved.natural_key,
           resolved.version_valid_from,
           NULL,
           CASE
               WHEN resolved.grid_name IS NOT NULL THEN 'grid_cell'
               WHEN GeometryType(resolved.geom) IN ('POINT', 'MULTIPOINT') THEN 'point'
               WHEN GeometryType(resolved.geom) IN ('POLYGON', 'MULTIPOLYGON') THEN 'polygon'
               WHEN GeometryType(resolved.geom) IN ('LINESTRING', 'MULTILINESTRING') THEN 'line'
           END,
           resolved.geom,
           ST_Centroid(resolved.geom),
           resolved.grid_name,
           resolved.cell_key,
           resolved.resolution_metres,
           resolved.producer,
           CAST(:run_clock AS timestamptz)
    FROM resolved
    ON CONFLICT (natural_key) WHERE version_valid_to IS NULL DO NOTHING
    RETURNING geometry_id, natural_key
    """
).bindparams(
    bindparam("geometry_ids", type_=ARRAY(Text)),
    bindparam("natural_keys", type_=ARRAY(Text)),
    bindparam("producers", type_=ARRAY(Text)),
    bindparam("version_valid_froms", type_=ARRAY(Text)),
    bindparam("feature_ids", type_=ARRAY(Text)),
    bindparam("geojsons", type_=ARRAY(Text)),
    bindparam("grid_names", type_=ARRAY(Text)),
    bindparam("cell_keys", type_=ARRAY(Text)),
    bindparam("resolution_metres", type_=ARRAY(Text)),
)

# Staleness only: "the last run that saw this version unchanged upstream", never a validity bound.
_CONFIRM_GEOMETRY_VERSIONS = text(
    """
    UPDATE geo.geometry
    SET last_confirmed_at = CAST(:run_clock AS timestamptz)
    WHERE natural_key = ANY(CAST(:natural_keys AS text[]))
      AND version_valid_to IS NULL
    """
).bindparams(bindparam("natural_keys", type_=ARRAY(Text)))

_SELECT_CURRENT_GEOMETRY_IDS = text(
    """
    SELECT natural_key, geometry_id
    FROM geo.geometry
    WHERE natural_key = ANY(CAST(:natural_keys AS text[]))
      AND version_valid_to IS NULL
    """
).bindparams(bindparam("natural_keys", type_=ARRAY(Text)))

# geo_features_sync_geom is BEFORE INSERT OR UPDATE *OF properties*, so repointing costs no re-parse.
_LINK_FEATURE_GEOMETRY = text(
    """
    UPDATE geo.features AS feature
    SET geometry_id = CAST(link.geometry_id AS uuid)
    FROM unnest(CAST(:feature_ids AS text[]), CAST(:geometry_ids AS text[])) AS link(feature_id, geometry_id)
    WHERE feature.id = CAST(link.feature_id AS uuid)
      AND feature.geometry_id IS DISTINCT FROM CAST(link.geometry_id AS uuid)
    RETURNING feature.id
    """
).bindparams(
    bindparam("feature_ids", type_=ARRAY(Text)),
    bindparam("geometry_ids", type_=ARRAY(Text)),
)


def timestamptz_literal(moment: datetime | None) -> str:
    """Render an observation instant for a timestamptz bind, spelling "no honest observation" as -infinity."""
    return NEGATIVE_INFINITY_TIMESTAMP if moment is None else moment.isoformat()


def geometry_key_for(identity: FeatureIdentity, grid_cell: GridCell | None = None) -> str:
    """The dimension key for a place: the enduring entity behind the observation, or the shared grid cell."""
    if grid_cell is None:
        return identity.entity_key
    return f"{identity.producer}:{grid_cell.grid_name}:{grid_cell.cell_key}"


def geometry_source_for(feature_id: str, grid_cell: GridCell | None = None) -> GeometrySource:
    """A celled place is versioned by its own cell polygon; an uncelled one by the geometry PostGIS already holds."""
    if grid_cell is None:
        return StoredFeatureGeometry(feature_id=feature_id)
    return GeoJsonGeometry(geojson=grid_cell.geojson)


def feature_geometry_request(
    identity: FeatureIdentity,
    feature_id: str,
    grid_cell: GridCell | None = None,
) -> GeometryVersionRequest:
    """Build one feature's version request: the place it names, the shape claimed for it, and when it was seen."""
    return GeometryVersionRequest(
        natural_key=geometry_key_for(identity, grid_cell),
        producer=identity.producer,
        observed_at=identity.observed_at,
        source=geometry_source_for(feature_id, grid_cell),
        grid_cell=grid_cell,
    )


def _source_columns(source: GeometrySource) -> tuple[str, str]:
    """Split a geometry source into the feature-id and GeoJSON binds, empty meaning "not this shape"."""
    if isinstance(source, StoredFeatureGeometry):
        return source.feature_id, ""
    return "", source.geojson


def _observes_later_than(candidate: GeometryVersionRequest, incumbent: GeometryVersionRequest) -> bool:
    """True when the candidate carries a strictly later observation; an undated request never displaces a dated one."""
    if candidate.observed_at is None:
        return False
    return incumbent.observed_at is None or candidate.observed_at > incumbent.observed_at


def _unique_by_natural_key(requests: Sequence[GeometryVersionRequest]) -> dict[str, GeometryVersionRequest]:
    """Collapse a batch to one request per place, keeping the latest observation of that place in the batch."""
    unique: dict[str, GeometryVersionRequest] = {}
    for request in requests:
        incumbent = unique.get(request.natural_key)
        if incumbent is None or _observes_later_than(request, incumbent):
            unique[request.natural_key] = request
    return unique


async def lock_geometry_natural_keys(session: AsyncSession, natural_keys: Sequence[str]) -> None:
    """Take the transaction-scoped advisory lock per place, in sorted order, so concurrent runs cannot deadlock."""
    if not natural_keys:
        return
    await session.execute(
        _LOCK_GEOMETRY_KEYS,
        {"lock_namespace": GEOMETRY_LOCK_NAMESPACE, "natural_keys": sorted(set(natural_keys))},
    )


async def _classify(
    session: AsyncSession,
    requests: Sequence[GeometryVersionRequest],
) -> dict[str, _Classification]:
    """Ask the database, in one round trip, what each place's chain looks like against the claimed shape."""
    sources = [_source_columns(request.source) for request in requests]
    rows = await session.execute(
        _CLASSIFY_GEOMETRY_VERSIONS,
        {
            "natural_keys": [request.natural_key for request in requests],
            "feature_ids": [feature_id for feature_id, _ in sources],
            "geojsons": [geojson for _, geojson in sources],
            "observed_ats": [timestamptz_literal(request.observed_at) for request in requests],
        },
    )
    return {
        str(row.natural_key): _Classification(
            natural_key=str(row.natural_key),
            geometry_missing=bool(row.geometry_missing),
            open_geometry_id=None if row.open_geometry_id is None else str(row.open_geometry_id),
            geometry_unchanged=row.geometry_unchanged is True,
            successor_is_datable=row.successor_is_datable is True,
        )
        for row in rows.all()
    }


def _plan_versions(
    requests: Mapping[str, GeometryVersionRequest],
    classifications: Mapping[str, _Classification],
) -> _VersionPlan:
    """Partition the batch into open, confirm, supersede and undatable without touching the database."""
    plan = _VersionPlan()
    for natural_key, request in requests.items():
        classification = classifications.get(natural_key)
        if classification is None or classification.geometry_missing:
            raise MissingGeometryError(f"no geometry could be resolved for {natural_key}")
        if classification.open_geometry_id is None:
            plan.opened.append(_PlannedVersion(geometry_id=str(uuid4()), request=request))
        elif classification.geometry_unchanged:
            plan.confirmed.append(natural_key)
        elif classification.successor_is_datable:
            successor = _PlannedVersion(geometry_id=str(uuid4()), request=request)
            plan.successors.append(successor)
            plan.supersessions.append(
                _Supersession(
                    natural_key=natural_key,
                    closed_at=timestamptz_literal(request.observed_at),
                    successor_id=successor.geometry_id,
                )
            )
        else:
            # The shape moved but the producer supplied no instant later than the open version's own
            # start, so there is no honest boundary to cut the chain at. Leave the chain untouched.
            plan.undatable.append(natural_key)
    return plan


async def _insert_versions(
    session: AsyncSession,
    versions: Sequence[_PlannedVersion],
    run_clock: datetime,
) -> None:
    """Insert the planned open versions, taking geometry and centroid from PostGIS rather than from Python."""
    sources = [_source_columns(version.request.source) for version in versions]
    cells = [version.request.grid_cell for version in versions]
    await session.execute(
        _INSERT_GEOMETRY_VERSIONS,
        {
            "geometry_ids": [version.geometry_id for version in versions],
            "natural_keys": [version.request.natural_key for version in versions],
            "producers": [version.request.producer for version in versions],
            "version_valid_froms": [timestamptz_literal(version.request.observed_at) for version in versions],
            "feature_ids": [feature_id for feature_id, _ in sources],
            "geojsons": [geojson for _, geojson in sources],
            "grid_names": ["" if cell is None else cell.grid_name for cell in cells],
            "cell_keys": ["" if cell is None else cell.cell_key for cell in cells],
            "resolution_metres": ["" if cell is None else str(cell.resolution_metres) for cell in cells],
            # asyncpg type-checks a scalar timestamptz bind in Python before the SQL CAST runs, so the
            # run clock goes over as a datetime. Only the observation values above are text, because
            # `-infinity` has no datetime spelling. See ingest/AGENTS.md "geometry.py".
            "run_clock": run_clock,
        },
    )


async def _resolve_current_geometry_ids(session: AsyncSession, natural_keys: Sequence[str]) -> dict[str, str]:
    """Re-read the open version per place, so the identifier a caller links is the one the database kept."""
    rows = await session.execute(_SELECT_CURRENT_GEOMETRY_IDS, {"natural_keys": list(natural_keys)})
    return {str(row.natural_key): str(row.geometry_id) for row in rows.all()}


def _actions_by_natural_key(plan: _VersionPlan) -> dict[str, GeometryVersionAction]:
    """Name what the plan did to each place, so a caller can count versions without re-deriving them."""
    actions: dict[str, GeometryVersionAction] = {}
    for version in plan.opened:
        actions[version.request.natural_key] = "opened"
    for supersession in plan.supersessions:
        actions[supersession.natural_key] = "superseded"
    for natural_key in plan.confirmed:
        actions[natural_key] = "confirmed"
    for natural_key in plan.undatable:
        actions[natural_key] = "undatable"
    return actions


async def upsert_geometry_versions(
    session: AsyncSession,
    requests: Sequence[GeometryVersionRequest],
    run_clock: datetime,
) -> dict[str, GeometryVersionOutcome]:
    """Maintain every requested place's version chain in the caller's transaction, returning the current version."""
    unique = _unique_by_natural_key(requests)
    if not unique:
        return {}

    natural_keys = sorted(unique)
    await lock_geometry_natural_keys(session, natural_keys)
    classifications = await _classify(session, [unique[natural_key] for natural_key in natural_keys])
    plan = _plan_versions(unique, classifications)

    if plan.supersessions:
        await session.execute(
            _CLOSE_GEOMETRY_VERSIONS,
            {
                "natural_keys": [supersession.natural_key for supersession in plan.supersessions],
                "closed_ats": [supersession.closed_at for supersession in plan.supersessions],
                "successor_ids": [supersession.successor_id for supersession in plan.supersessions],
            },
        )
    if plan.opened or plan.successors:
        await _insert_versions(session, [*plan.opened, *plan.successors], run_clock)
    if plan.confirmed:
        await session.execute(
            _CONFIRM_GEOMETRY_VERSIONS,
            {"natural_keys": plan.confirmed, "run_clock": run_clock},
        )

    current = await _resolve_current_geometry_ids(session, natural_keys)
    actions = _actions_by_natural_key(plan)
    return {
        natural_key: GeometryVersionOutcome(
            natural_key=natural_key,
            geometry_id=geometry_id,
            action=actions.get(natural_key, "confirmed"),
        )
        for natural_key, geometry_id in current.items()
    }


async def upsert_geometry_version(
    session: AsyncSession,
    request: GeometryVersionRequest,
    run_clock: datetime,
) -> GeometryVersionOutcome:
    """Maintain one place's version chain; the batch form is the same work with one round trip per step."""
    outcomes = await upsert_geometry_versions(session, [request], run_clock)
    outcome = outcomes.get(request.natural_key)
    if outcome is None:
        raise MissingGeometryError(f"no geometry version resolved for {request.natural_key}")
    return outcome


def count_geometry_actions(outcomes: Mapping[str, GeometryVersionOutcome]) -> dict[str, int]:
    """Count what the dimension did this batch, so a caller reports versions maintained rather than rows touched."""
    counts: dict[str, int] = dict.fromkeys(GEOMETRY_VERSION_ACTIONS, 0)
    for outcome in outcomes.values():
        counts[outcome.action] += 1
    return counts


def undatable_natural_keys(outcomes: Mapping[str, GeometryVersionOutcome]) -> list[str]:
    """Name every place whose shape moved with no instant to date the move, so the divergence is never silent."""
    return sorted(natural_key for natural_key, outcome in outcomes.items() if outcome.action == "undatable")


async def link_feature_geometry(session: AsyncSession, links: Sequence[FeatureGeometryLink]) -> int:
    """Repoint geo.features at the geometry versions just resolved, skipping the rows already pointing there."""
    if not links:
        return 0
    repointed = await session.execute(
        _LINK_FEATURE_GEOMETRY,
        {
            "feature_ids": [link.feature_id for link in links],
            "geometry_ids": [link.geometry_id for link in links],
        },
    )
    return len(repointed.all())
