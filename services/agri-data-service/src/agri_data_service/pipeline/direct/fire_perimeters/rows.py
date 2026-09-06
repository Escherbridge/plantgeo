"""Conform one fetched WFIGS population to `FIRE_PERIMETERS_SCHEMA`, geometry converted to WKB.

EVERY COLUMN IS DERIVED FROM THE EXACT DOCUMENT POSTGRESQL WOULD HAVE STORED, not from a second
reading of the ArcGIS payload. `ingest.wfigs.build_perimeter_write` builds the `FeatureWrite` whose
`stored_properties()` IS the `geo.features.properties` jsonb (`ingest/writer.py:81-83`), and
`sql/pipeline/fire_perimeters_day_export.sql` reads every attribute column out of that same jsonb by
key. Going through the shared builder rather than re-projecting the parsed record is what makes the
two populations comparable at all: the identity rule, the property names, the severity bucket and
the timestamp rendering are then one implementation, not two that agree today.

THE THREE COLUMNS THAT CANNOT COME FROM UPSTREAM, and what each is instead:

* `feature_id`  -- `geo.features.id` is a database-minted uuid and there is no PostgreSQL row behind
                   a direct fetch. It becomes `direct:<uniqueFireIdentifier>`, namespaced exactly as
                   `drought/rows.py::direct_area_id` namespaces its own, so a reader who joined this
                   column back to `geo.features.id` on the strength of its shape is reading a
                   namespace that never existed there.
* `updated_at`  -- `geo.features.updated_at` is when the hourly poller RECORDED a change. Here it is
                   `source.fetched_at`, when THIS writer captured the population. That is the same
                   kind of clock, not a poll clock: `watermark.py` publishes a version only when the
                   population actually differs from the one already published, so this column
                   advances on change and holds still otherwise -- which is precisely what
                   `sql/ingest/refresh_features.sql:136-137`'s `IS DISTINCT FROM` gate buys the
                   PostgreSQL column. It is also the LAST-CHANGE INSTANT the next turn reads back.
* `status`      -- `geo.features.status` has no writer: `src/lib/server/db/schema.ts:226` defaults it
                   to `'published'` and this producer's ingestion never sets it, so the export's
                   `features.status = 'published'` predicate selects every row this producer ever
                   wrote. The constant here reproduces that default rather than inventing a gate.

`data_available_at` stays NULL, which is not a shortcut: it is 100% NULL in production across every
layer, and `build_fire_perimeter_identity` supplies none, so a value here would be invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.identity import MissingNativeKeyError
from agri_data_service.ingest.wfigs import build_perimeter_write, resolve_fire_perimeters_layer_name
from agri_data_service.pipeline.direct.fire_perimeters.support import (
    fire_perimeter_geometry_session,
    perimeter_geometries_to_wkb,
)
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.pipeline.direct.fire_perimeters.source import FirePerimetersSource

#: Namespaces a direct row's `feature_id` as never a genuine `geo.features.id`. See the module
#: docstring, and `drought/rows.py::DIRECT_AREA_ID_PREFIX` for the convention this follows.
DIRECT_FEATURE_ID_PREFIX: Final = "direct"

#: `geo.features.status`' schema default, reproduced. See the module docstring.
PUBLISHED_STATUS: Final = "published"

#: The COALESCE chain of `geo.feature_observation_day`
#: (`drizzle/0018_fire_discovery_observation_day.sql`), in its exact order. The first two keys are
#: structurally absent from this producer's document -- `build_perimeter_write` writes neither
#: `observedAt` nor `updatedAt` -- so the chain resolves at `polygonDateTime` and falls to
#: `fireDiscoveryDateTime` for the perimeters WFIGS publishes with a JSON-null polygon time (13 of
#: 119 at the 2026-08-07 production measurement). They are listed anyway because this is a
#: TRANSCRIPTION of that function, and a transcription that silently drops two keys is a
#: reimplementation waiting to disagree the day the document grows one.
OBSERVATION_DAY_KEYS: Final[tuple[str, ...]] = (
    "observedAt",
    "updatedAt",
    "polygonDateTime",
    "fireDiscoveryDateTime",
)

#: `named.day ~ '^\\d{4}-\\d{2}-\\d{2}$'`, verbatim. `re.fullmatch` is used below rather than
#: `re.match` so the trailing `$` means what it means in PostgreSQL, where `$` does not admit a
#: trailing newline the way Python's `$` does.
_ISO_DAY_PATTERN: Final = re.compile(r"\d{4}-\d{2}-\d{2}")

#: `docs/lanes/fire-perimeters.md` #5 measured 130,583 B of geometry per published row on average.
#: Under the snapshot shape EVERY row lands in EVERY export, so one part would carry the whole
#: ~23 MB population unless it is split. RESTATED from `pipeline/lanes/fire_perimeters.py` rather
#: than imported, and deliberately: that module reads PostgreSQL and is deleted by the cutover this
#: package exists to enable, so importing its constant would make this writer un-deletable-from.
#: 8 MiB keeps each part comfortably under `WFIGS_BOUNDS`' own 16 MiB response cap
#: (`ingest/wfigs.py:81`), with headroom for Parquet's own framing. Never size this lane by row
#: count -- size it by geometry bytes.
MAX_PART_PAYLOAD_BYTES: Final = 8 * 1024 * 1024


class FirePerimeterRowError(RuntimeError):
    """Raised when a fetched perimeter cannot be conformed to the registered schema at all."""


def direct_feature_id(unique_fire_identifier: str) -> str:
    """Build the deterministic, `direct:`-namespaced id a direct row carries in place of a real one.

    Deliberately carries NO date component, unlike `drought/rows.py::direct_area_id`. Drought's key
    is `(valid_date, dm_category)` because a USDM release class is only unique within its release;
    a WFIGS incident is keyed by the bare `uniqueFireIdentifier` with no date component
    (`ingest/identity.py::build_fire_perimeter_identity`), which is the fact that made this lane a
    snapshot rather than a series. Folding the version day in would also churn this column on every
    version and make `watermark.py`'s content digest change whenever the stamp did.
    """
    return f"{DIRECT_FEATURE_ID_PREFIX}:{unique_fire_identifier}"


def publisher_named_day(properties: Mapping[str, object]) -> date | None:
    """Reproduce `geo.feature_observation_day(feature_properties)` exactly, including its NULLs.

    THE FIRST TEN CHARACTERS OF THE NAMED STRING, NEVER A PARSED INSTANT. The SQL function's own
    comment forbids `(...)::timestamptz::date` and `(... AT TIME ZONE 'UTC')::date` because an
    instant-based conversion moves 6,279 of 16,743 production water-gauge rows onto the day after
    the one they name. WFIGS timestamps are rendered UTC (`epoch_milliseconds_to_iso`), so the two
    would agree here today -- and the string prefix is still what is implemented, because this
    column feeds the same client date filter and the rule is the rule.

    A KEY PRESENT-BUT-UNUSABLE STOPS THE CHAIN; ONLY AN ABSENT ONE CONTINUES IT. `COALESCE` in
    PostgreSQL steps past SQL NULL, and `properties ->> 'k'` is SQL NULL when the key is absent OR
    its value is JSON null -- but an EMPTY STRING is not NULL, so `COALESCE` stops on it, `substring`
    yields `''`, the regex fails, and the function returns NULL WITHOUT trying the next key. The loop
    below reproduces that by breaking on any `str`, empty included, and continuing only on `None`.

    A row this returns `None` for is NOT dropped. `drizzle/0018...:39-40`: such a row "is treated as
    undated by the client filter, which shows it at every date rather than hiding it", and
    `src/lib/map/tile-layer-date-filter.ts`'s `["!", ["has", "observed_day"]]` is that filter. The
    retired `daily_series` export dropped these rows outright -- its `= :observed_day` equality can
    never match NULL -- so it served strictly fewer perimeters than Martin drew. `observed_day` is
    nullable in the Arrow schema for exactly this row.
    """
    named: str | None = None
    for key in OBSERVATION_DAY_KEYS:
        value = properties.get(key)
        if isinstance(value, str):
            named = value[:10]
            break
    if named is None or _ISO_DAY_PATTERN.fullmatch(named) is None:
        return None
    try:
        # `pg_input_is_valid(named.day, 'date')`: the regex proves the SHAPE, this proves the day
        # EXISTS. `to_date('2026-02-31', ...)` raises in PostgreSQL and one raise inside ST_AsMVT
        # blanks an entire tile, which is why the function guards it there and why it is guarded here.
        return date.fromisoformat(named)
    except ValueError:
        return None


def _upstream_instant(properties: Mapping[str, object], key: str) -> datetime | None:
    """Cast one stored ISO timestamp exactly as `(properties ->> key)::timestamptz` casts it.

    An unparseable string RAISES rather than degrading to NULL, matching PostgreSQL: `::timestamptz`
    over a malformed value aborts the export, it does not quietly null the column. Nothing upstream
    can produce one today -- `epoch_milliseconds_to_iso` emits a JavaScript-rendered instant or
    `None` -- so reaching this is evidence the parse contract moved, which is worth stopping for.
    """
    value = properties.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise FirePerimeterRowError(f"{key} came back as {type(value).__name__}, which ::timestamptz cannot cast")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise FirePerimeterRowError(f"{key}={value!r} is not a timestamp ::timestamptz could cast") from error
    if parsed.tzinfo is None:
        raise FirePerimeterRowError(
            f"{key}={value!r} carries no timezone; storing it as UTC would assume a zone WFIGS never named"
        )
    return parsed


def _optional_float(properties: Mapping[str, object], key: str) -> float | None:
    """Cast one stored number exactly as `(properties ->> key)::double precision` casts it."""
    value = properties.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FirePerimeterRowError(f"{key} came back as {type(value).__name__}, which ::double precision cannot cast")
    return float(value)


def _optional_text(properties: Mapping[str, object], key: str) -> str | None:
    """Read one stored string exactly as `properties ->> key` reads it, JSON null included."""
    value = properties.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise FirePerimeterRowError(f"{key} came back as {type(value).__name__}, not the text ->> would yield")
    return value


@dataclass(frozen=True, slots=True)
class FirePerimeterPopulation:
    """One conformed WFIGS population, WITHOUT its version stamp, plus what it refused on the way.

    `rows` carries every schema column except `snapshot_day`, which `fire_perimeters_table` stamps
    on. The split is what lets `watermark.py` digest the CONTENT of a population before any version
    day has been chosen for it -- the day is decided FROM the digest, so a population that already
    carried one would be circular.

    `rejected` and `collapsed` reproduce two counts the PostgreSQL job also produced:
    `run_fire_perimeters_ingestion_job`'s `details["rejected"]` for a record whose identity would not
    build, and the last-write-wins collapse `geo.features`' one-row-per-`external_id` shape performs
    on a repeated fire identifier within one walk.
    """

    rows: tuple[dict[str, object], ...]
    fetched_at: datetime
    rejected: int
    collapsed: int


def fire_perimeter_population(source: FirePerimetersSource) -> FirePerimeterPopulation:
    """Conform one fetched WFIGS capture, converting every geometry in a single DuckDB round trip.

    A REPEATED `uniqueFireIdentifier` WITHIN ONE WALK COLLAPSES, LAST ONE WINNING, and that matches
    PostgreSQL rather than diverging from it: `geo.features` holds one row per `external_id`
    (`sql/ingest/select_existing_external_ids.sql` / `refresh_features.sql` key on it), so a second
    record for the same incident refreshes the first rather than adding a row. The count is carried
    on the result instead of being silently absorbed.

    THE ORDER IS THE EXPORT'S ORDER: `ORDER BY unique_fire_identifier`, the same clause
    `sql/pipeline/fire_perimeters_day_export.sql` ends with and the registered grain's own trailing
    column. `snapshot_day` leads the grain but is constant within a version, so sorting on the
    identifier alone reproduces the full sort -- and it is applied HERE rather than left to
    `write_partition`, so that slicing the table into parts afterwards preserves one global order
    across every part file instead of independently re-sorting each.
    """
    layer_name = resolve_fire_perimeters_layer_name()
    documents: dict[str, dict[str, object]] = {}
    rejected = 0
    collapsed = 0
    for perimeter in source.perimeters:
        try:
            write = build_perimeter_write(perimeter, layer_name)
        except (MissingNativeKeyError, ValueError):  # pragma: no cover - the builder catches its own
            write = None
        if write is None:
            rejected += 1
            continue
        if write.external_id in documents:
            collapsed += 1
        documents[write.external_id] = write.stored_properties()

    ordered = sorted(documents.items())
    identities = [identity for identity, _ in ordered]
    with fire_perimeter_geometry_session() as session:
        geometry_wkb = perimeter_geometries_to_wkb(
            session,
            [properties.get("geometry") for _, properties in ordered],
            identities,
        )

    # Annotated rather than inferred: without the annotation each literal infers its own narrow
    # value union (`str | date | float | bytes | None | datetime`), and `dict` is invariant in its
    # value type, so the tuple would not satisfy `FirePerimeterPopulation.rows`'s declared
    # `tuple[dict[str, object], ...]`. The annotation is the type context the literals build under.
    rows: tuple[dict[str, object], ...] = tuple(
        {
            "feature_id": direct_feature_id(identity),
            "unique_fire_identifier": identity,
            "observed_day": publisher_named_day(properties),
            "incident_name": _optional_text(properties, "incidentName"),
            "irwin_id": _optional_text(properties, "irwinId"),
            "fire_discovery_at": _upstream_instant(properties, "fireDiscoveryDateTime"),
            "polygon_at": _upstream_instant(properties, "polygonDateTime"),
            "gis_acres": _optional_float(properties, "gisAcres"),
            "fire_cause": _optional_text(properties, "fireCause"),
            "incident_type_category": _optional_text(properties, "incidentTypeCategory"),
            "poo_state": _optional_text(properties, "pooState"),
            "percent_contained": _optional_float(properties, "percentContained"),
            "severity": _optional_text(properties, "severity"),
            "status": PUBLISHED_STATUS,
            "data_available_at": None,
            "updated_at": source.fetched_at,
            "geometry_wkb": wkb,
        }
        for (identity, properties), wkb in zip(ordered, geometry_wkb, strict=True)
    )
    return FirePerimeterPopulation(rows=rows, fetched_at=source.fetched_at, rejected=rejected, collapsed=collapsed)


def fire_perimeters_table(population: FirePerimeterPopulation, *, snapshot_day: date) -> pa.Table:
    """Stamp one conformed population with its version day and build the base-rung Arrow table.

    `snapshot_day` is a VERSION STAMP, not an observation day: it comes from `watermark.py`, never
    from the cron's run date and never from any row's own timestamp. Re-building the same population
    under the same day is exactly how that version is corrected
    (`foundation/parquet/lane_contract.py`).
    """
    return pa.Table.from_pylist(
        [{**row, "snapshot_day": snapshot_day} for row in population.rows],
        schema=FIRE_PERIMETERS_SCHEMA.arrow_schema,
    )


def chunk_row_indices_by_geometry_bytes(geometry_lengths: Sequence[int], *, max_bytes: int) -> list[list[int]]:
    """Split row positions into contiguous runs whose summed geometry bytes stay under `max_bytes`.

    RESTATED from `pipeline/lanes/fire_perimeters.py` rather than imported, for the reason
    `MAX_PART_PAYLOAD_BYTES` gives: that module reads PostgreSQL and the cutover deletes it.

    Every chunk holds at least one row -- a single perimeter whose own WKB already exceeds the
    budget is not split further, mirroring `ingest/wfigs.py`'s own per-page backstop.

    AN EMPTY INPUT RETURNS `[[]]`, ONE EMPTY CHUNK, AND THAT IS LOAD-BEARING rather than a quirk of
    the seed value: it guarantees the caller always makes at least one `write_partition` call. Here
    that guarantee is a backstop rather than the mechanism -- `adapter.py` refuses an empty
    population by name before reaching this -- because for a `static_lookup` the driver's
    `EmptyPartitionError`-to-governed-absence conversion is the WRONG answer, not the right one.
    """
    chunks: list[list[int]] = [[]]
    running = 0
    for index, length in enumerate(geometry_lengths):
        if running and running + length > max_bytes:
            chunks.append([])
            running = 0
        chunks[-1].append(index)
        running += length
    return chunks


__all__ = [
    "DIRECT_FEATURE_ID_PREFIX",
    "MAX_PART_PAYLOAD_BYTES",
    "OBSERVATION_DAY_KEYS",
    "PUBLISHED_STATUS",
    "FirePerimeterPopulation",
    "FirePerimeterRowError",
    "chunk_row_indices_by_geometry_bytes",
    "direct_feature_id",
    "fire_perimeter_population",
    "fire_perimeters_table",
    "publisher_named_day",
]
