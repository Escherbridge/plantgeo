"""Conform one Oregon OEM capture to `EVACUATION_ZONES_SCHEMA`, WKB-repaired through DuckDB spatial.

WHAT THE RETIRED `geo.geometry` JOIN ACTUALLY CONTRIBUTED, since this module is what has to replace
it. `sql/pipeline/evacuation_zones_day_export.sql:76-78` LEFT JOINs the Type-2 geometry dimension,
but it does NOT read geometry from it: line 70 takes `ST_AsBinary(feature.geom)` off the FEATURE row,
"the same column the tile function renders" (that file's own header, lines 37-40). The join
contributes exactly three provenance columns, and each one is reproduced or refused below on its own
terms:

1. `geometry_version_id` (`geometry.geometry_id::text`) is `str(uuid4())`, minted in Python at
   `ingest/geometry.py:315,319`. It is a random surrogate carrying no upstream fact and no
   information a direct fetch lacks. Reproduced as a DETERMINISTIC, `direct:`-namespaced content
   token -- see `direct_geometry_version_id`.
2. `geometry_version_valid_from` is `request.observed_at` (`ingest/geometry.py:237,349`), and for
   THIS lane `observed_at` is Oregon's own `created_date` and nothing else
   (`build_evacuation_zone_identity`, `ingest/evacuation_zones.py:352-374`). So the column is a
   duplicate of `observed_at` on every row, spelled `-infinity` where `observed_at` is NULL
   (`ingest/geometry.py:25`). Reproduced from `observed_at` directly.
3. `geometry_last_confirmed_at` is the ingesting run's own clock -- `ingest_features` passes one
   `run_clock` for the whole call (`ingest/writer.py:294`) into
   `sql/ingest/confirm_geometry_versions.sql`. It is a poll clock by design and by that file's own
   header ("when did a run last see this version still agreeing with upstream"). Reproduced as this
   capture's `fetched_at`, which is the same statement about the same act.

AND THE CHAIN IS FROZEN FOR THIS LANE, which is why reproducing it is a repair rather than a loss.
A supersession requires `resolved.observed_at > open_version.version_valid_from`
(`sql/ingest/classify_geometry_versions.sql:181`). Here both sides are the SAME immutable
`created_date`, so the comparison is false for every shape change Oregon ever makes to an existing
area: `_plan_versions` files it as `undatable` (`ingest/geometry.py:328-331`), the chain is left
untouched, and `confirm_geometry_versions.sql` deliberately does not advance `last_confirmed_at`
either. The dimension's copy of an evacuation polygon is therefore pinned at the first shape ever
seen. `geo.features.geom` is no better: it is written by the
`geo_features_sync_geom` trigger, which is `BEFORE INSERT OR UPDATE OF properties`, and
`sql/ingest/refresh_features.sql:136-137` gates that UPDATE on
`(properties - 'geometry' - 'geometry_repaired') IS DISTINCT FROM (next_properties - 'geometry')` --
geometry stripped from BOTH sides. A shape-only revision therefore fires no UPDATE, so the trigger
never runs and the new shape lands in neither table.

This module's `content_digest` INCLUDES `geometry_wkb`, which is the whole repair: the direct
writer's change test sees a shape-only revision that the Postgres pair provably cannot.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.pipeline.direct.evacuation_zones.products import (
    DIRECT_PRODUCER,
    DIRECT_SOURCE_LITERAL,
    MAX_ROWS_PER_PART,
)
from agri_data_service.pipeline.direct.evacuation_zones.support import (
    EvacuationZonesGeometryError,
    evacuation_zones_geometry_session,
    repair_zone_geometries_to_wkb,
)
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.warehouse.schemas.evacuation_zones import EVACUATION_ZONES_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from agri_data_service.pipeline.direct.evacuation_zones.source import EvacuationZonesSource

#: Namespaces a direct row's `geometry_version_id` as never a genuine `geo.geometry.geometry_id`.
#: The same discriminator discipline `drought/rows.py::DIRECT_AREA_ID_PREFIX` applies to `area_id`:
#: a reader checking only the column's SHAPE would otherwise cast this string to a uuid and join it
#: back to a dimension row that never existed. The token is `direct:<natural_key>:<sha256(wkb)[:16]>`
#: -- deterministic, so republishing an unchanged snapshot produces byte-identical values, and it
#: names ONE VERSION OF ONE PLACE, which is precisely what `geo.geometry.geometry_id` means
#: (`drizzle/0008_geometry_dimension.sql:1-6`). Unlike the uuid it replaces, it also CHANGES when the
#: shape changes, which the Postgres chain cannot do for this lane (see the module docstring).
DIRECT_GEOMETRY_VERSION_PREFIX: Final = "direct"
_GEOMETRY_VERSION_DIGEST_CHARACTERS: Final = 16

#: The columns a content comparison is allowed to look at: everything Oregon determines, and nothing
#: this repo's clock determines. Excluded, each for its own reason:
#:   snapshot_day                -- the version stamp being decided; including it would make every
#:                                  snapshot differ from every other by construction.
#:   geometry_version_id         -- a pure function of `natural_key` and `geometry_wkb`, both below.
#:   geometry_version_valid_from -- identical to `observed_at`, which is below.
#:   geometry_last_confirmed_at  -- this run's fetch clock.
#:   feature_updated_at          -- when a row last CHANGED, which is the answer this digest computes.
#:   data_available_at           -- 100% NULL on every published row today.
#: This is the direct analogue of `refresh_features.sql`'s own change gate, with the one difference
#: that gate got wrong for this lane: geometry is IN the comparison, not stripped out of it.
CONTENT_DIGEST_COLUMNS: Final[tuple[str, ...]] = (
    "global_id",
    "natural_key",
    "producer",
    "evacuation_area_name",
    "fire_name",
    "county",
    "hazard_type",
    "evacuation_level",
    "evacuation_level_label",
    "severity",
    "structures_within",
    "addresses_within",
    "population_within",
    "editor_name",
    "observed_at",
    "source",
    "geometry_wkb",
)


class EvacuationZonesRowError(RuntimeError):
    """Raised when a captured area cannot be conformed to the published row contract."""


def natural_key_for(global_id: str) -> str:
    """Build the producer-namespaced warehouse key, in the one shape `FeatureIdentity` mints.

    `f"{producer}:{producer_local_id}"`, one ASCII colon and nothing else
    (`drizzle/0008_geometry_dimension.sql:9-19`). Restated here rather than built through
    `FeatureIdentity` because that dataclass also carries `observed_at` validation this row builder
    has already performed, and constructing one per zone purely to read `.natural_key` off it would
    tie the writer to the ingest identity module's constructor signature.
    """
    return f"{DIRECT_PRODUCER}:{global_id}"


def direct_geometry_version_id(natural_key: str, geometry_wkb: bytes) -> str:
    """Build the deterministic, `direct:`-namespaced shape-version token a direct row carries.

    Digests the PUBLISHED bytes (post-repair WKB) rather than the raw upstream GeoJSON, so the token
    identifies the shape a reader will actually receive. The consequence, recorded rather than
    hidden: a DuckDB upgrade that changed WKB encoding would move every token once and read as one
    whole-snapshot change. That costs one extra republication of an unchanged population, which is
    the safe direction for a life-safety layer.
    """
    digest = sha256(geometry_wkb).hexdigest()[:_GEOMETRY_VERSION_DIGEST_CHARACTERS]
    return f"{DIRECT_GEOMETRY_VERSION_PREFIX}:{natural_key}:{digest}"


def _require_global_id(zone: Mapping[str, object]) -> str:
    """Return the upstream GlobalID, refusing a zone the parse layer should already have rejected."""
    global_id = zone.get("globalId")
    if not isinstance(global_id, str) or not global_id.strip():
        raise EvacuationZonesRowError(
            "an evacuation area reached the row builder with no GlobalID; identity is never synthesised"
        )
    return global_id


def _optional_instant(zone: Mapping[str, object]) -> datetime | None:
    """Return Oregon's own `created_date` as this row's `observed_at`, or None when it published none.

    NEVER `last_edited_date`, and never the wall clock. Oregon's sync re-stamps an unchanged area's
    edit clock every few minutes, so dating by it would make the whole layer look freshly observed on
    every poll (`ingest/evacuation_zones.py:352-362`); an area Oregon never dated stays honestly
    undated (`tests/test_ingest_evacuation_zones.py:146-148`).
    """
    created_at = zone.get("createdAt")
    if created_at is None:
        return None
    if not isinstance(created_at, datetime):
        raise EvacuationZonesRowError("createdAt must be an upstream instant when present")
    return created_at


def evacuation_zones_table(
    source: EvacuationZonesSource,
    *,
    snapshot_day: date,
    previous_updated_at: Mapping[str, datetime] | None = None,
) -> pa.Table:
    """Build the base-rung Arrow table for one capture, repairing every polygon through DuckDB spatial.

    `previous_updated_at` carries `feature_updated_at` forward per natural key from the snapshot this
    one supersedes, so that column keeps meaning what `geo.features.updated_at` meant: WHEN THIS ROW
    LAST CHANGED, not when the writer last ran. A key absent from the mapping -- a brand-new area, or
    the first snapshot this lane ever published -- is stamped with this capture's `fetched_at`, which
    is the first instant this repo can honestly claim to have seen it. The caller supplies the
    mapping only for rows whose content is unchanged; a changed row must be stamped now, and
    `forward.py` is what knows which is which.

    Returned already conformed and sorted to the registered grain (`snapshot_day`, `natural_key`) so
    that slicing it into parts afterwards preserves one global order across every part file -- the
    same reason `pipeline/lanes/evacuation_zones.py::read_evacuation_zones_snapshot` conforms early
    rather than leaving it to `write_partition`.
    """
    carried_forward = dict(previous_updated_at or {})
    geometries: list[tuple[str, Mapping[str, object]]] = []
    for zone in source.zones:
        geometry = zone.get("geometry")
        if not isinstance(geometry, dict):
            raise EvacuationZonesRowError(
                f"evacuation area {_require_global_id(zone)} carries no polygon; "
                "require_polygon_geometry should have refused it upstream"
            )
        geometries.append((natural_key_for(_require_global_id(zone)), geometry))

    if geometries:
        with evacuation_zones_geometry_session() as session:
            repaired = repair_zone_geometries_to_wkb(session, geometries)
    else:
        repaired = {}

    rows: list[dict[str, object]] = []
    for zone in source.zones:
        natural_key = natural_key_for(_require_global_id(zone))
        geometry_wkb = repaired.get(natural_key)
        if geometry_wkb is None:  # pragma: no cover - repair_zone_geometries_to_wkb already refuses
            raise EvacuationZonesGeometryError(f"no repaired geometry came back for {natural_key}")
        observed_at = _optional_instant(zone)
        rows.append(
            {
                "global_id": _require_global_id(zone),
                "natural_key": natural_key,
                "producer": DIRECT_PRODUCER,
                "snapshot_day": snapshot_day,
                "evacuation_area_name": zone.get("evacuationAreaName"),
                "fire_name": zone.get("fireName"),
                "county": zone.get("county"),
                "hazard_type": zone.get("hazardType"),
                "evacuation_level": zone.get("evacuationLevel"),
                "evacuation_level_label": zone.get("evacuationLevelLabel"),
                "severity": zone.get("severity"),
                "structures_within": zone.get("structuresWithin"),
                "addresses_within": zone.get("addressesWithin"),
                "population_within": zone.get("populationWithin"),
                "editor_name": zone.get("editorName"),
                "observed_at": observed_at,
                "source": DIRECT_SOURCE_LITERAL,
                "geometry_wkb": geometry_wkb,
                "geometry_version_id": direct_geometry_version_id(natural_key, geometry_wkb),
                # A duplicate of `observed_at` by construction -- see the module docstring, point 2.
                # NULL, never the dimension's `-infinity`, where Oregon supplied no creation stamp:
                # the Arrow column is nullable for exactly this case, and year 0001 is a real,
                # sortable instant that a reader would take for a measurement.
                "geometry_version_valid_from": observed_at,
                # Every row in this capture was seen upstream at `fetched_at`, so every row is
                # confirmed at it. Strictly more honest than the column it replaces, which freezes
                # the moment a shape change goes undatable -- see the module docstring, point 3.
                "geometry_last_confirmed_at": source.fetched_at,
                # 100% NULL on all 651 published rows today (docs/lanes/evacuation-zones.md section
                # 5); no producer supplies it, so it is carried forward unpopulated, never assumed.
                "data_available_at": None,
                "feature_updated_at": carried_forward.get(natural_key, source.fetched_at),
            }
        )

    raw = pa.Table.from_pylist(rows, schema=EVACUATION_ZONES_SCHEMA.arrow_schema)
    return conform_to_stream_schema(raw, EVACUATION_ZONES_SCHEMA)


def _digest_value(value: object) -> bytes:  # noqa: PLR0911 - one return per cell type, as `agent/tools.py::_json_safe`
    """Render one cell as unambiguous, self-delimiting bytes so two different values never collide."""
    if value is None:
        return b"N;"
    if isinstance(value, bytes):
        return b"B" + str(len(value)).encode("ascii") + b":" + value + b";"
    if isinstance(value, bool):  # before int: bool is an int subclass
        return b"L" + (b"1" if value else b"0") + b";"
    if isinstance(value, int):
        return b"I" + str(value).encode("ascii") + b";"
    if isinstance(value, float):
        return b"F" + repr(value).encode("ascii") + b";"
    if isinstance(value, datetime):
        return b"T" + value.isoformat().encode("ascii") + b";"
    text = str(value).encode("utf-8")
    return b"S" + str(len(text)).encode("ascii") + b":" + text + b";"


def content_digest(table: pa.Table) -> str:
    """Digest the SOURCE-DETERMINED content of one snapshot, order-independently.

    This is the direct writer's whole change test, and it is what makes the retired Postgres
    watermark unnecessary: `sql/pipeline/lane_watermark_evacuation_zones.sql` had to ask three
    columns of two tables when the change had already been recorded by
    `refresh_features.sql`'s own gate, and even then could not see a shape-only revision (module
    docstring). Comparing this digest against the newest published snapshot answers the same
    question -- "has the reference set changed" -- from the two things a direct writer actually
    holds: what the source says now, and what was last published.

    Sorted by `natural_key` before hashing, so a table assembled from part files in any order, or
    read back from a differently-parted export, digests identically.
    """
    if table.num_rows == 0:
        # Distinct from any non-empty digest, and stable: a statewide quiet day published as a
        # governed absence has to compare equal to the next quiet day's capture.
        return sha256(b"evacuation-zones:empty-snapshot").hexdigest()
    projected = table.select(list(CONTENT_DIGEST_COLUMNS)).sort_by([("natural_key", "ascending")])
    running = sha256()
    for batch in projected.to_batches():
        columns = [column.to_pylist() for column in batch.columns]
        for index in range(batch.num_rows):
            for column in columns:
                running.update(_digest_value(column[index]))
            running.update(b"\n")
    return running.hexdigest()


def apply_carried_forward_updated_at(table: pa.Table, carried: Mapping[str, datetime]) -> pa.Table:
    """Return `table` with `feature_updated_at` restored from `carried` for every key it names.

    Applied AFTER the table is built rather than threaded into the builder, because whether a row is
    unchanged can only be known once its content digest exists, and that digest is computed from the
    built table. `feature_updated_at` is deliberately outside `CONTENT_DIGEST_COLUMNS`, so patching
    it here cannot change the snapshot digest a caller has already computed.
    """
    if not carried:
        return table
    keys = table.column("natural_key").to_pylist()
    stamps = table.column("feature_updated_at").to_pylist()
    patched = [carried.get(str(key), stamp) for key, stamp in zip(keys, stamps, strict=True)]
    index = table.schema.get_field_index("feature_updated_at")
    return table.set_column(index, table.schema.field(index), pa.array(patched, type=table.schema.field(index).type))


def updated_at_by_natural_key(table: pa.Table) -> dict[str, datetime]:
    """Read one published snapshot's `feature_updated_at` per key, for carrying unchanged rows forward."""
    keys = table.column("natural_key").to_pylist()
    stamps = table.column("feature_updated_at").to_pylist()
    return {str(key): stamp for key, stamp in zip(keys, stamps, strict=True) if stamp is not None}


def row_content_digests(table: pa.Table) -> dict[str, str]:
    """Digest each row's source-determined content on its own, keyed by natural key.

    Per-ROW rather than per-snapshot, so `forward.py` can tell which zones actually moved and carry
    `feature_updated_at` forward only for the ones that did not -- reproducing what
    `refresh_features.sql`'s row-scoped UPDATE did, where a statewide re-stamp would not.
    """
    if table.num_rows == 0:
        return {}
    projected = table.select(list(CONTENT_DIGEST_COLUMNS))
    key_index = CONTENT_DIGEST_COLUMNS.index("natural_key")
    digests: dict[str, str] = {}
    for batch in projected.to_batches():
        columns = [column.to_pylist() for column in batch.columns]
        for index in range(batch.num_rows):
            running = sha256()
            for column in columns:
                running.update(_digest_value(column[index]))
            digests[str(columns[key_index][index])] = running.hexdigest()
    return digests


def split_into_parts(table: pa.Table) -> tuple[pa.Table, ...]:
    """Slice an already grain-sorted table into row-bounded parts, preserving that order.

    Parts are handed to `write_partition(part_index=...)` in this order, so nothing here ever parses
    a part order back out of `part-N` key text -- the unpadded-`part-10`-sorts-before-`part-9` trap
    has no way into this package's WRITE path. `parity.py` and `forward.py` read part lists only
    through `store.read_partition` / `foundation/parquet/paths.py`, both of which parse the index.
    """
    return tuple(table.slice(offset, MAX_ROWS_PER_PART) for offset in range(0, table.num_rows, MAX_ROWS_PER_PART))


__all__ = [
    "CONTENT_DIGEST_COLUMNS",
    "DIRECT_GEOMETRY_VERSION_PREFIX",
    "EvacuationZonesRowError",
    "apply_carried_forward_updated_at",
    "content_digest",
    "direct_geometry_version_id",
    "evacuation_zones_table",
    "natural_key_for",
    "row_content_digests",
    "split_into_parts",
    "updated_at_by_natural_key",
]
