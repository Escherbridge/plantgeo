"""Reproduce `sql/pipeline/lane_watermark_fire_perimeters.sql` against the object store, not PostgreSQL.

THIS MODULE IS WHY THE LANE IS NOT A DAY LOOP. Its registration is `static_lookup`, so the partition
day is a VERSION STAMP and the version owed comes from a SOURCE WATERMARK -- "the lane writes ONE
snapshot dated at whatever day this query reports, and nothing at all while a partition dated at or
after that day already exists, so a tick the cron skipped costs nothing, because no day ever carried
an obligation" (that SQL file's own header). The verdict is then settled by the SAME shared function
the generic driver uses, `foundation/parquet/lane_contract.py::resolve_static_lane`; nothing about
the rule is re-implemented here, only the reading of the watermark itself.

## What the SQL reads, and what stands in for it here

The PostgreSQL watermark is `GREATEST` of three change events over the published population:

    max(feature.updated_at)             -- an attribute changed (containment, acreage, severity, name)
    max(feature.created_at)             -- a brand-new incident appeared
    max(geometry.version_valid_from)    -- the Type-2 chain minted a new polygon version

All three are WAREHOUSE clocks. None of them is WFIGS' own clock: WFIGS `_Current` publishes no
change timestamp for an attribute revision at all, and the fields it does date (`polygonDateTime`,
`fireDiscoveryDateTime`) move only for the geometry and the incident's discovery. What actually made
`updated_at` a change clock rather than a poll clock is a GATE, not a source field --
`sql/ingest/refresh_features.sql:136-137`:

    AND (feature.properties - 'geometry' - 'geometry_repaired')
        IS DISTINCT FROM (CAST(pending.next_properties AS jsonb) - 'geometry')

-- the hourly poll that finds an incident unchanged moves nothing. The direct reproduction is
therefore the same gate applied to the same comparison, with the published PARQUET VERSION standing
in for the row that is being compared against:

    the fetched population, conformed, differs from the newest published version
        -> the set changed, and the change instant is source.fetched_at
    it does not differ
        -> the set has not changed since that version was captured, and the watermark is sticky at
           that version's own capture instant, exactly as max(updated_at) is sticky

ALL THREE OF THE SQL'S EVENTS COLLAPSE INTO THAT ONE COMPARISON, and more completely than the SQL
manages. A new incident is a new `unique_fire_identifier` in the digested set (`created_at`). An
attribute revision is a changed column (`updated_at`). A new polygon is changed `geometry_wkb` --
and here the digest is STRICTLY MORE SENSITIVE than the chain it replaces: that SQL file names its
own blind spot, an `undatable` geometry revision where "the same divergence is re-detected on the
next tick, and the next, forever" and none of the three columns moves. A byte comparison of the
shape has no such state to freeze. That is a divergence in this writer's favour and it is stated
rather than hidden, because it means the direct lane may publish a version the PostgreSQL lane would
not have -- for a real polygon change PostgreSQL genuinely failed to record.

`geo.geometry.last_confirmed_at` is not read on either side, for the same reason: it advances on
every re-confirmation of unchanged ground, and a poll clock in a version stamp is the fabrication
this model exists to refuse.

## The costs, named

Comparing content rather than reading a clock costs ONE READ-BACK of the newest published version's
base rung (roughly 23 MB) per turn, on top of the roughly 11 MB WFIGS walk. The row-count
short-circuit below removes it whenever an incident appeared or closed, which is most of fire
season; a containment-only day pays it. That is the price of a change detector that needs no
database, and it is the whole reason this package can exist while `geo.features` is dropped.

## The first turn after cutover always publishes

A version written by the PostgreSQL lane carries real `geo.features.id` uuids in `feature_id`, while
this writer carries `direct:<uniqueFireIdentifier>`, so the digests cannot match however identical
the perimeters are. The first direct turn therefore always writes one version. That is correct
rather than merely tolerable -- it is the bridge-then-cut boundary made visible, and after it the
comparison is direct-against-direct.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
from agri_data_service.foundation.parquet.paths import (
    completed_partition_days,
    try_parse_absence_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.direct.fire_perimeters.products import FIRE_PERIMETERS_DIRECT_KIND
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import oldest_export_instant
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_SCHEMA, FIRE_PERIMETERS_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.pipeline.direct.fire_perimeters.rows import FirePerimeterPopulation
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: The two columns a version stamps ON a population rather than reading OUT of it, excluded from the
#: content digest by construction. `snapshot_day` is the version stamp itself and differs between any
#: two versions by definition; `updated_at` is this writer's capture instant and advances with every
#: publication. Digesting either would make every comparison report "changed" and turn the watermark
#: back into the run clock this lane was re-registered to escape.
VERSION_STAMP_COLUMNS: Final[tuple[str, ...]] = ("snapshot_day", "updated_at")

#: Every other registered column, in schema order. Derived from the schema rather than listed, so a
#: column added to `warehouse/schemas/fire_perimeters.py` joins the comparison the day it lands
#: instead of being silently invisible to change detection.
DIGESTED_COLUMNS: Final[tuple[str, ...]] = tuple(
    name for name in FIRE_PERIMETERS_SCHEMA.column_names if name not in VERSION_STAMP_COLUMNS
)

#: The column both sides sort on before digesting, so the answer cannot depend on part ordering.
#: `read_partition` already concatenates parts in integer index order -- it parses the index out of
#: the path rather than sorting the paths, which matters because part names are UNPADDED and
#: `part-10` sorts before `part-9` as text. Sorting here anyway means this comparison never has to
#: depend on that being true elsewhere.
DIGEST_SORT_COLUMN: Final = "unique_fire_identifier"

_NULL_TAG: Final = b"\x00"
_BYTES_TAG: Final = b"\x01"
_TEXT_TAG: Final = b"\x02"
_FLOAT_TAG: Final = b"\x03"
_DATE_TAG: Final = b"\x04"
_INSTANT_TAG: Final = b"\x05"
_FIELD_LENGTH_BYTES: Final = 8
#: How much of the digest a basis string quotes. The full 64 hex characters make an operator-facing
#: sentence unreadable and prove nothing more: the comparison is done on the whole value.
_DIGEST_EXCERPT: Final = 12


class FirePerimetersWatermarkError(RuntimeError):
    """Raised when the object store cannot be read well enough to decide what version is owed."""


def _encode(value: object) -> bytes:
    """Encode one column value into unambiguous, type-tagged bytes.

    Tagged and length-prefixed rather than joined by a separator, so no value can impersonate a field
    boundary: a `unique_fire_identifier` containing the separator would otherwise let two different
    populations hash identically, which is the one failure this comparison may not have.
    """
    if value is None:
        return _NULL_TAG
    if isinstance(value, bytes):
        return _BYTES_TAG + value
    if isinstance(value, str):
        return _TEXT_TAG + value.encode("utf-8")
    if isinstance(value, bool):  # before int/float: a bool is an int in Python and no column holds one
        raise FirePerimetersWatermarkError("a boolean reached the content digest; no registered column holds one")
    if isinstance(value, float | int):
        # `repr` of a float is its shortest round-tripping form and is stable across processes; the
        # value itself round-trips exactly through Parquet's float64, so two equal populations encode
        # identically.
        return _FLOAT_TAG + repr(float(value)).encode("ascii")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise FirePerimetersWatermarkError("a timezone-naive instant reached the content digest")
        return _INSTANT_TAG + value.astimezone(UTC).isoformat().encode("ascii")
    if isinstance(value, date):  # after datetime: a datetime IS a date
        return _DATE_TAG + value.isoformat().encode("ascii")
    raise FirePerimetersWatermarkError(f"a {type(value).__name__} reached the content digest with no encoding")


def content_digest(rows: Sequence[Mapping[str, object]]) -> str:
    """Digest one population's CONTENT -- every registered column except the two version stamps.

    Both sides of every comparison in this module go through this one function: the freshly
    conformed population as `FirePerimeterPopulation.rows`, and the published version as the
    `to_pylist()` of the table read back out of the object store. Two spellings of "what does this
    population contain" is how a change detector comes to disagree with itself.
    """
    hasher = hashlib.sha256()
    for row in sorted(rows, key=lambda entry: str(entry[DIGEST_SORT_COLUMN])):
        for column in DIGESTED_COLUMNS:
            payload = _encode(row.get(column))
            hasher.update(len(payload).to_bytes(_FIELD_LENGTH_BYTES, "big"))
            hasher.update(payload)
    return hasher.hexdigest()


def table_content_digest(table: pa.Table) -> str:
    """Digest a version read back out of the object store, through the identical encoding."""
    missing = [column for column in DIGESTED_COLUMNS if column not in table.column_names]
    if missing:
        raise FirePerimetersWatermarkError(
            f"the published version is missing digested column(s) {missing}; it was written under a different "
            "schema and cannot be compared against a population conformed to this one"
        )
    return content_digest(table.select(list(DIGESTED_COLUMNS)).to_pylist())


@dataclass(frozen=True, slots=True)
class PublishedLadder:
    """What the base rung's listing says this lane already holds, before anything is read back.

    `newest_data_day` counts only days that ASSERTED completion, for the reason
    `gap_fill._static_lane_census` states: a version killed part-way through uploading, with every
    part newer than the watermark, would otherwise resolve the lane `current` on top of half a
    snapshot. `stranded_days` is what that filter set aside -- part files with neither a completion
    marker nor an absence marker, which only an admin may retract, because re-exporting one today
    would date the CURRENT population as that past version.
    """

    newest_data_day: date | None
    newest_data_instant: datetime | None
    newest_marker_day: date | None
    version_count: int
    stranded_days: tuple[date, ...]


def read_published_ladder(store: ObjectStore) -> PublishedLadder:
    """List the base rung once and classify every version day it names.

    ONE whole-stream listing, not a per-month walk: a `static_lookup` lane holds VERSIONS rather than
    a calendar, and the newest one can sit in any month, so narrowing by year/month would need the
    answer before it could ask the question. `drought/forward.py` walks months because a
    `release_series` knows its own window; this lane does not have one.
    """
    try:
        listed = store.list_partition_objects(FIRE_PERIMETERS_STREAM, FIRE_PERIMETERS_DIRECT_KIND, LANE_BASE_ZOOM_TIER)
    except Exception as error:
        raise FirePerimetersWatermarkError(
            f"could not list the fire-perimeters base rung: {type(error).__name__}: {error}"
        ) from error
    part_days = {
        parsed.day
        for entry in listed
        if (parsed := try_parse_partition_path(entry.relative_path)) is not None
        and parsed.layer == FIRE_PERIMETERS_STREAM
        and parsed.kind == FIRE_PERIMETERS_DIRECT_KIND
        and parsed.zoom == LANE_BASE_ZOOM_TIER
    }
    marker_days = {
        marker.day
        for entry in listed
        if (marker := try_parse_absence_marker_path(entry.relative_path)) is not None
        and marker.layer == FIRE_PERIMETERS_STREAM
        and marker.kind == FIRE_PERIMETERS_DIRECT_KIND
        and marker.zoom == LANE_BASE_ZOOM_TIER
    }
    complete_days = part_days & completed_partition_days(
        (entry.relative_path for entry in listed),
        layer=FIRE_PERIMETERS_STREAM,
        kind=FIRE_PERIMETERS_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
    )
    newest_day = max(complete_days, default=None)
    return PublishedLadder(
        newest_data_day=newest_day,
        newest_data_instant=(
            None
            if newest_day is None
            else oldest_export_instant(
                listed,
                layer=FIRE_PERIMETERS_STREAM,
                kind=FIRE_PERIMETERS_DIRECT_KIND,
                zoom=LANE_BASE_ZOOM_TIER,
                day=newest_day,
            )
        ),
        newest_marker_day=max(marker_days, default=None),
        version_count=len(complete_days),
        stranded_days=tuple(sorted(part_days - complete_days - marker_days, reverse=True)),
    )


@dataclass(frozen=True, slots=True)
class PublishedVersion:
    """One published version read back: how many rows it holds, when it was captured, and its digest.

    `captured_at` is `max(updated_at)` over the version -- for a version this package wrote, the
    fetch instant of the run that wrote it, since every row in one snapshot shares it. That is the
    direct analogue of `max(feature.updated_at)` in the SQL watermark, read out of the warehouse of
    record for a lane whose warehouse of record is now Parquet.
    """

    day: date
    row_count: int
    captured_at: datetime | None
    digest: str


def read_published_version(store: ObjectStore, day: date) -> PublishedVersion:
    """Read one published version's base rung back and digest it. The only ~23 MB read in the turn."""
    try:
        table = store.read_partition(FIRE_PERIMETERS_STREAM, FIRE_PERIMETERS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
    except Exception as error:
        raise FirePerimetersWatermarkError(
            f"could not read published version {day.isoformat()} back: {type(error).__name__}: {error}"
        ) from error
    captured = table.column("updated_at").to_pylist() if "updated_at" in table.column_names else []
    return PublishedVersion(
        day=day,
        row_count=table.num_rows,
        captured_at=max((value for value in captured if value is not None), default=None),
        digest=table_content_digest(table),
    )


def completed_row_count(store: ObjectStore, day: date) -> int | None:
    """Read one version's CLAIMED row count out of its completion marker -- one small GET, no parts.

    This is what makes the short-circuit a saving rather than a formality: the marker is a few
    hundred bytes and the parts it describes are roughly 23 MB, so a version whose count already
    disagrees with the fetch is proven different without downloading it. `None` means the marker is
    unreadable or absent, which sends the caller down the full read-back rather than guessing.
    """
    try:
        marker = store.read_completion_marker(
            FIRE_PERIMETERS_STREAM, FIRE_PERIMETERS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day
        )
    except Exception as error:
        raise FirePerimetersWatermarkError(
            f"could not read version {day.isoformat()}'s completion marker: {type(error).__name__}: {error}"
        ) from error
    return None if marker is None else marker.row_count


@dataclass(frozen=True, slots=True)
class DirectWatermarkReading:
    """The watermark this turn read, plus the evidence a report prints beside it."""

    watermark: SourceWatermark
    fresh_digest: str
    fresh_rows: int
    #: `None` when no published version was read back -- either none exists, or the row-count
    #: short-circuit already proved the content differs and spared the download.
    published: PublishedVersion | None
    #: True when the published version's CLAIMED row count alone settled it, so its ~23 MB of parts
    #: were never downloaded. Diagnostic, and the one number that says what a turn actually cost.
    short_circuited: bool


def read_direct_watermark(
    store: ObjectStore,
    population: FirePerimeterPopulation,
    ladder: PublishedLadder,
) -> DirectWatermarkReading:
    """Decide when the published WFIGS incident-perimeter set last changed, from content alone.

    The four answers, in the order they are reached:

    1. THE FETCH IS EMPTY -> `SourceWatermark(day=None)`, which `resolve_static_lane` reports as
       `source_empty`: "the source holds no rows to version, so there is nothing to snapshot". This
       is the same answer the SQL gives (`watermark_at` is NULL when `count(*)` is 0), and it is the
       safe one: a transient empty WFIGS answer leaves the previous version serving rather than
       publishing a blank map. It also never reaches `write_partition`, whose `EmptyPartitionError`
       the generic driver would convert into a governed absence -- and `resolve_static_lane` is
       explicit that for a static lane an absence at a version day is a FAILED READ to retry, never
       coverage.
    2. NOTHING IS PUBLISHED YET -> the fetch instant is the change instant; the first version is owed.
    3. THE NEWEST VERSION'S COMPLETION MARKER CLAIMS A DIFFERENT ROW COUNT -> the content differs,
       necessarily, and the ~23 MB read-back is skipped for one small marker GET.
    4. OTHERWISE the version is read back and digested. Equal digests make the watermark STICKY at
       that version's own capture instant, which is what makes `resolve_static_lane` answer
       `current` and write nothing; unequal digests promote the fetch instant.
    """
    fresh_digest = content_digest(population.rows)
    fresh_rows = len(population.rows)
    if fresh_rows == 0:
        return DirectWatermarkReading(
            watermark=SourceWatermark(
                day=None,
                basis=(
                    f"fire-perimeters: the WFIGS _Current walk returned 0 perimeters, so there is no population "
                    f"to version (fetched at {population.fetched_at.isoformat()})"
                ),
            ),
            fresh_digest=fresh_digest,
            fresh_rows=0,
            published=None,
            short_circuited=False,
        )
    if ladder.newest_data_day is None:
        return DirectWatermarkReading(
            watermark=_changed_watermark(population, fresh_digest, fresh_rows, "no version is published yet"),
            fresh_digest=fresh_digest,
            fresh_rows=fresh_rows,
            published=None,
            short_circuited=False,
        )
    claimed_rows = completed_row_count(store, ladder.newest_data_day)
    if claimed_rows is not None and claimed_rows != fresh_rows:
        return DirectWatermarkReading(
            watermark=_changed_watermark(
                population,
                fresh_digest,
                fresh_rows,
                f"version {ladder.newest_data_day.isoformat()}'s completion marker claims {claimed_rows} "
                f"perimeters against this fetch's {fresh_rows}, so the populations differ and it was never "
                "read back",
            ),
            fresh_digest=fresh_digest,
            fresh_rows=fresh_rows,
            published=None,
            short_circuited=True,
        )
    published = read_published_version(store, ladder.newest_data_day)
    if published.digest != fresh_digest or published.row_count != fresh_rows:
        return DirectWatermarkReading(
            watermark=_changed_watermark(
                population,
                fresh_digest,
                fresh_rows,
                f"version {published.day.isoformat()} holds {published.row_count} perimeters digesting to "
                f"{published.digest[:_DIGEST_EXCERPT]}",
            ),
            fresh_digest=fresh_digest,
            fresh_rows=fresh_rows,
            published=published,
            short_circuited=False,
        )
    return DirectWatermarkReading(
        watermark=_unchanged_watermark(published, fresh_rows, export_instant=ladder.newest_data_instant),
        fresh_digest=fresh_digest,
        fresh_rows=fresh_rows,
        published=published,
        short_circuited=False,
    )


def _changed_watermark(
    population: FirePerimeterPopulation,
    fresh_digest: str,
    fresh_rows: int,
    comparison: str,
) -> SourceWatermark:
    """The population differs from what is published, so THIS fetch is the change instant.

    The instant is `fetched_at` and not any field WFIGS supplied, because WFIGS supplies none for an
    attribute revision -- see the module docstring. It is the same KIND of clock the SQL watermark
    reads: `feature.updated_at` is likewise the instant a poller recorded a change, not an instant
    the publisher declared. The only difference is the poll interval, and the change gate is
    identical on both sides.
    """
    return SourceWatermark(
        day=population.fetched_at.date(),
        instant=population.fetched_at,
        basis=(
            f"fire-perimeters: WFIGS _Current fetched at {population.fetched_at.isoformat()} over {fresh_rows} "
            f"perimeters digesting to {fresh_digest[:_DIGEST_EXCERPT]}; {comparison}. The set CHANGED, so this "
            "fetch instant is the change instant -- the same gate sql/ingest/refresh_features.sql:136-137 "
            "applies to make geo.features.updated_at a change clock rather than a poll clock"
        ),
    )


def _unchanged_watermark(
    published: PublishedVersion,
    fresh_rows: int,
    *,
    export_instant: datetime | None,
) -> SourceWatermark:
    """The population is byte-identical to the published version, so the watermark stays put.

    STICKY, exactly as `max(feature.updated_at)` is sticky: an hourly poll that finds nothing changed
    moves neither. The instant returned is the published version's own capture instant, which makes
    `resolve_static_lane` compare it against that version's export instant and answer `current`.

    THE ONE GUARD, and it closes a spin loop rather than a correctness hole. `captured_at` comes from
    this container's clock and the export instant comes from the object store's, so a container
    running ahead of the store by more than one fetch-to-upload gap would make the export look
    EARLIER than the capture it performed -- `_resolve_watermark_day` would then report `stale` and
    the lane would re-publish an identical ~23 MB version every tick, forever, on a green report. A
    capture instant later than the export that wrote it is impossible in real time, so it is treated
    as unusable rather than believed: the watermark drops to day resolution, `resolve_static_lane`
    answers `current` and says it is a day-resolution answer, and the skew is named in the basis.
    """
    captured_at = published.captured_at
    skew = ""
    if captured_at is not None and export_instant is not None and captured_at > export_instant:
        skew = (
            f" The recorded capture instant {captured_at.isoformat()} is LATER than the export instant "
            f"{export_instant.isoformat()} that wrote it, which cannot happen in real time; it is discarded as "
            "clock skew and this answer drops to day resolution rather than re-publishing an identical version"
        )
        captured_at = None
    origin = (
        "no capture instant"
        if captured_at is None
        else f"captured at {captured_at.isoformat()} (max(updated_at) over that version)"
    )
    return SourceWatermark(
        day=published.day,
        instant=captured_at,
        basis=(
            f"fire-perimeters: WFIGS _Current returned {fresh_rows} perimeters digesting to "
            f"{published.digest[:_DIGEST_EXCERPT]}, identical to published version {published.day.isoformat()}, "
            f"{origin}. The set has NOT changed since that version, so the watermark stays there -- the same "
            f"stickiness max(feature.updated_at) has when an hourly poll finds an incident unchanged.{skew}"
        ),
    )


__all__ = [
    "DIGESTED_COLUMNS",
    "DIGEST_SORT_COLUMN",
    "VERSION_STAMP_COLUMNS",
    "DirectWatermarkReading",
    "FirePerimetersWatermarkError",
    "PublishedLadder",
    "PublishedVersion",
    "completed_row_count",
    "content_digest",
    "read_direct_watermark",
    "read_published_ladder",
    "read_published_version",
    "table_content_digest",
]
