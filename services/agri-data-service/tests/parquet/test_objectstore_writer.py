"""The object store and partition writer, exercised end to end against an in-memory backend.

Every call here names a ZOOM TIER, because the store has no call that does not. The tests that
matter most are the ones where the same layer, stream and day exist at two tiers at once: nothing in
a blended answer looks wrong, so only an explicit assertion that the tiers stay apart can catch it.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.config import ObjectStoreCredentials, Settings
from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    missing_partition_days,
    partition_path,
)
from agri_data_service.foundation.parquet.zoom import ZoomTier, ZoomTierError
from agri_data_service.pipeline.parquet.objectstore import (
    PARQUET_CONTENT_TYPE,
    BotoObjectStoreBackend,
    EmptyPartitionError,
    ListedObject,
    ListedPartition,
    ObjectStore,
    ParquetSchemaMismatchError,
    conform_to_stream_schema,
    oldest_export_instant,
    polars_storage_options,
)
from agri_data_service.warehouse.parquet.schema import (
    FORECAST_PROVENANCE_FIELDS,
    SIGNAL_PLANE_SCHEMA,
    SIGNAL_PLANE_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

OBJECT_STORE_ENV_NAMES = (
    "OBJECT_STORE_ENDPOINT_URL",
    "OBJECT_STORE_BUCKET",
    "OBJECT_STORE_ACCESS_KEY_ID",
    "OBJECT_STORE_SECRET_ACCESS_KEY",
    "OBJECT_STORE_PREFIX",
    "OBJECT_STORE_REGION",
)

JULY_FOURTH = date(2026, 7, 4)
EXPECTED_ROW_COUNT = 3
EXPECTED_YEAR_KEY_COUNT = 2

# The tier a lane's own export writes, and a coarser rung derived from it. Named by minimum zoom, as
# the ladder is; z11 below is a legitimate REQUEST and an illegitimate tier, which is the difference
# the store must refuse to blur.
BASE_TIER: ZoomTier = 13
DETAIL_TIER: ZoomTier = 9
WHOLE_WORLD_TIER: ZoomTier = 0
UNPUBLISHED_ZOOM = 11

# Three instants on ONE UTC day: the whole point is that the day cannot tell them apart.
EARLIEST = datetime(2026, 7, 4, 1, 0, tzinfo=UTC)
EXPORTED_AT = datetime(2026, 7, 4, 6, 0, tzinfo=UTC)
RE_EXPORTED_AT = datetime(2026, 7, 4, 18, 30, tzinfo=UTC)
NAIVE_INSTANT = datetime(2026, 7, 4, 6, 0)  # noqa: DTZ001 - the untrusted shape the parser must refuse


class StubS3Api:
    """A `_S3Api` serving canned `list_objects_v2` pages, so the untrusted-response parse runs whole."""

    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages = pages
        self.requests: list[dict[str, object]] = []
        self.deletes: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> object:  # noqa: ARG002 - the `_S3Api` shape it must satisfy
        raise AssertionError("this stub only lists")

    def delete_object(self, **kwargs: object) -> object:
        # Recorded APART from `requests`, which indexes `pages`; one shared list would desync paging.
        self.deletes.append(dict(kwargs))
        return {}

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        self.requests.append(dict(kwargs))
        return self.pages[len(self.requests) - 1]

    def head_object(self, **kwargs: object) -> dict[str, object]:  # noqa: ARG002 - same
        raise AssertionError("this stub only lists")


class RecordingBackend:
    """In-memory `ObjectStoreBackend`: the whole writer runs with no network and no credentials.

    Every put stamps `stamps_puts_at`, which defaults to `None` -- an UNKNOWN export instant, the
    same thing a backend that cannot report one gives. Tests that care about sub-day currency pin
    the instant with `set_last_modified`; nothing here ever reads the wall clock, because a listing
    whose instants moved between runs would make those tests decide differently each time.
    """

    def __init__(self, *, stamps_puts_at: datetime | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.last_modified: dict[str, datetime | None] = {}
        self.stamps_puts_at = stamps_puts_at
        self.deleted: list[str] = []
        self.refuses_delete_of: set[str] = set()

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        self.objects[key] = payload
        self.content_types[key] = content_type
        self.last_modified[key] = self.stamps_puts_at

    def get(self, key: str) -> bytes | None:
        """Return one object's bytes, or `None` when nothing was put there.

        `None` rather than a raise, matching `BotoObjectStoreBackend.get`: an absent key is a
        concurrent prune, which the tier derivation is required to survive.
        """
        return self.objects.get(key)

    def delete(self, key: str) -> None:
        """Remove one object, or refuse it when the test pinned this key as unremovable."""
        if key in self.refuses_delete_of:
            raise OSError(f"bucket refused to delete {key}")
        self.deleted.append(key)
        self.objects.pop(key, None)
        self.content_types.pop(key, None)
        self.last_modified.pop(key, None)

    def set_last_modified(self, key: str, instant: datetime | None) -> None:
        """Pin one already-written key's export instant, as a re-export would move it."""
        if key not in self.objects:
            raise KeyError(f"cannot stamp {key!r}: nothing has been put there")
        self.last_modified[key] = instant

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield ListedObject(key=key, last_modified=self.last_modified.get(key))

    def size_of(self, key: str) -> int | None:
        payload = self.objects.get(key)
        return None if payload is None else len(payload)


def signal_rows(*, cell_ids: tuple[str, ...] = ("c3", "c1", "c2")) -> pa.Table:
    """Build a signal-plane table in deliberately non-schema column order and non-grain row order."""
    count = len(cell_ids)
    return pa.table(
        {
            "normalized_value": pa.array([2.5, 0.5, 1.5][:count], pa.float64()),
            "cell_id": pa.array(list(cell_ids), pa.large_string()),
            "observed_day": pa.array([JULY_FOURTH] * count, pa.date32()),
            "support_key": pa.array(["surface"] * count, pa.string()),
            "signal_name": pa.array(["precipitation"] * count, pa.string()),
            "normalized_unit": pa.array(["mm/day"] * count, pa.string()),
            "observation_count": pa.array([1] * count, pa.int64()),
            "newest_observed_at": pa.array(
                [datetime(2026, 7, 4, 12, tzinfo=UTC)] * count, pa.timestamp("us", tz="UTC")
            ),
            "coverage_fraction": pa.array([1.0] * count, pa.float64()),
            "allowed_client_exposure": pa.array([False] * count, pa.bool_()),
            "cell_longitude": pa.array([-116.0 - index * 0.01 for index in range(count)], pa.float64()),
            "cell_latitude": pa.array([43.0 + index * 0.01 for index in range(count)], pa.float64()),
        }
    )


def with_forecast_provenance(table: pa.Table, *, issued_on: date, horizon_days: int = 1) -> pa.Table:
    """Append the six `kind=forecast` provenance columns to an observed-shaped table.

    A forecast partition is the observed columns PLUS provenance, so a test that writes one from an
    observed fixture has to say what issued it. Before `get_stream_schema` took a kind, the two
    contracts were the same object and these tests wrote observed rows under `kind=forecast`; that
    is the very confusion the per-kind lookup removes, so the fixtures state the provenance now.
    """
    count = table.num_rows
    for field in FORECAST_PROVENANCE_FIELDS:
        values: list[object] = {
            "forecast_run_id": ["f" * 64] * count,
            "random_seed": [20260823] * count,
            "ensemble_size": [64] * count,
            "horizon_days": [horizon_days] * count,
            "issued_on": [issued_on] * count,
            "quantile": [0.5] * count,
        }[field.name]
        table = table.append_column(field, pa.array(values, field.type))
    return table


def test_write_partition_lands_at_the_frozen_key_and_returns_a_receipt() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipt = store.write_partition(
        signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH
    )

    expected_key = partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH)
    assert receipt.key == expected_key
    assert receipt.relative_path == expected_key
    assert list(backend.objects) == [expected_key]
    assert backend.content_types[expected_key] == PARQUET_CONTENT_TYPE
    assert receipt.stream == SIGNAL_PLANE_STREAM
    assert receipt.kind == "observed"
    # A receipt that cannot say which tier it wrote is not provenance: the same layer, stream and day
    # exist four times over, and only this field separates them once the receipt leaves the writer.
    assert receipt.zoom == BASE_TIER
    assert receipt.day == JULY_FOURTH
    assert receipt.row_count == EXPECTED_ROW_COUNT
    assert receipt.byte_count == len(backend.objects[expected_key])
    assert receipt.sha256 == sha256_digest(backend.objects[expected_key])


def test_written_bytes_carry_the_registered_schema_sorted_to_the_grain() -> None:
    """Column order, types and clustering are what the compression and every reader depend on."""
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipt = store.write_partition(
        signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH
    )
    table = pq.read_table(io.BytesIO(backend.objects[receipt.key]))

    assert table.schema.equals(SIGNAL_PLANE_SCHEMA.arrow_schema)
    assert table.column("cell_id").to_pylist() == ["c1", "c2", "c3"]
    assert table.column("normalized_value").to_pylist() == [0.5, 1.5, 2.5]


def test_written_bytes_use_zstd() -> None:
    """Measured at 695,338 B against snappy's 874,945 B on one real month."""
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipt = store.write_partition(
        signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH
    )
    metadata = pq.ParquetFile(io.BytesIO(backend.objects[receipt.key])).metadata

    assert metadata.row_group(0).column(0).compression == "ZSTD"


def test_the_store_prefix_wraps_the_layout_without_disturbing_it() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend, prefix="sandbox")

    receipt = store.write_partition(
        signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH
    )

    assert store.prefix == "sandbox/"
    assert receipt.key == f"sandbox/{receipt.relative_path}"
    assert store.relative_key(receipt.key) == receipt.relative_path
    with pytest.raises(ValueError, match="prefix"):
        store.relative_key("elsewhere/part-0.parquet")


def test_listing_returns_relative_paths_that_feed_gap_detection() -> None:
    """This is the whole point of the layout: a missing day is a missing path, never a scan."""
    backend = RecordingBackend()
    store = ObjectStore(backend, prefix="sandbox")
    for day in (date(2026, 7, 1), date(2026, 7, 3)):
        store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=day)
    store.write_partition(
        with_forecast_provenance(signal_rows(), issued_on=date(2026, 7, 1)),
        layer=SIGNAL_PLANE_STREAM,
        kind="forecast",
        zoom=BASE_TIER,
        day=date(2026, 7, 2),
    )
    backend.put(
        "sandbox/layer=signal/kind=observed/zoom=13/year=2026/month=07/day=02/manifest.json",
        b"{}",
        content_type="application/json",
    )

    keys = store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", BASE_TIER)

    assert keys == (
        partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, date(2026, 7, 1)),
        partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, date(2026, 7, 3)),
    )
    assert missing_partition_days(
        layer=SIGNAL_PLANE_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        first_day=date(2026, 7, 1),
        last_day=date(2026, 7, 3),
        keys=keys,
    ) == (date(2026, 7, 2),)


def test_listing_narrows_by_year_and_month() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(
        signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=date(2026, 6, 30)
    )
    store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)

    assert (
        len(store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, year=2026)) == EXPECTED_YEAR_KEY_COUNT
    )
    assert store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, year=2026, month=7) == (
        partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH),
    )
    with pytest.raises(ValueError, match="requires the year"):
        store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, month=7)


def test_a_listing_surfaces_the_export_instant_beside_every_key() -> None:
    """The day in a key is a version stamp, so WHEN a part file was exported is the only sub-day signal."""
    backend = RecordingBackend()
    store = ObjectStore(backend, prefix="sandbox")
    store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)
    relative_path = partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH)
    backend.set_last_modified(store.key_for(relative_path), EXPORTED_AT)

    listed = store.list_partition_objects(SIGNAL_PLANE_STREAM, "observed", BASE_TIER)

    assert listed == (ListedPartition(relative_path=relative_path, last_modified=EXPORTED_AT),)
    # The key-only view still answers, and now DERIVES from that one listing rather than making its own.
    assert store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", BASE_TIER) == (relative_path,)


def test_a_listing_returns_one_tier_and_the_ladder_is_never_blended() -> None:
    """THE SEMANTIC TRAP: one tier-less listing hands a reader four resolutions of the same day.

    Same layer, same stream, same day, three tiers. A blended listing returns three keys, every one
    of them parses, nothing raises -- and a reader that unions them reports one day's population
    three times over at three different generalizations. The only defence is that no call can ask
    for a stream without naming a tier.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend, prefix="sandbox")
    for tier in (BASE_TIER, DETAIL_TIER, WHOLE_WORLD_TIER):
        store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=tier, day=JULY_FOURTH)

    per_tier = {
        tier: store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", tier)
        for tier in (BASE_TIER, DETAIL_TIER, WHOLE_WORLD_TIER)
    }

    assert per_tier == {
        BASE_TIER: (partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH),),
        DETAIL_TIER: (partition_path(SIGNAL_PLANE_STREAM, "observed", DETAIL_TIER, JULY_FOURTH),),
        WHOLE_WORLD_TIER: (partition_path(SIGNAL_PLANE_STREAM, "observed", WHOLE_WORLD_TIER, JULY_FOURTH),),
    }
    # Three objects, three tiers, one day: the tiers coexist rather than overwrite, which is what
    # makes a tier-less listing a blend rather than a duplicate.
    assert len(backend.objects) == len(per_tier)
    assert (
        store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", DETAIL_TIER, year=2026, month=7)
        == per_tier[DETAIL_TIER]
    )


def test_a_zoom_that_is_not_a_published_tier_is_refused_at_every_store_entry_point() -> None:
    """z11 is a legitimate REQUEST and an illegitimate tier; only the ladder may name a directory.

    Resolving a request to a rung is `serving_zoom_tier`'s job. If the store accepted z11 it would
    write under a prefix no reader resolves, and the rows would be stranded rather than served.
    """
    store = ObjectStore(RecordingBackend())

    for call in (
        lambda: store.partition_exists(SIGNAL_PLANE_STREAM, "observed", UNPUBLISHED_ZOOM, JULY_FOURTH),  # type: ignore[arg-type]
        lambda: store.absence_exists(SIGNAL_PLANE_STREAM, "observed", UNPUBLISHED_ZOOM, JULY_FOURTH),  # type: ignore[arg-type]
        lambda: store.day_key_prefix(SIGNAL_PLANE_STREAM, "observed", UNPUBLISHED_ZOOM, JULY_FOURTH),  # type: ignore[arg-type]
        lambda: store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", UNPUBLISHED_ZOOM),  # type: ignore[arg-type]
        lambda: store.write_partition(
            signal_rows(),
            layer=SIGNAL_PLANE_STREAM,
            kind="observed",
            zoom=UNPUBLISHED_ZOOM,  # type: ignore[arg-type]
            day=JULY_FOURTH,
        ),
    ):
        with pytest.raises(ZoomTierError, match="not one of the published tiers"):
            call()


def day_export_instant(store: ObjectStore, day: date) -> datetime | None:
    """Answer a stream-day's export instant exactly as `_static_lane_census` does: one listing, then fold.

    The store deliberately ships no day-scoped convenience wrapper for this. The census already holds
    a whole-stream listing and folds it per day, so a wrapper would have been public API with no
    production caller and a second listing per lane-day for the tests that used it.
    """
    return oldest_export_instant(
        store.list_partition_objects(SIGNAL_PLANE_STREAM, "observed", BASE_TIER),
        layer=SIGNAL_PLANE_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=day,
    )


def test_a_stream_days_export_instant_is_the_oldest_of_its_part_files() -> None:
    """A re-export that rewrote part-0 and left an older part-1 published a MIXTURE, and must report as one."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    for part_index, instant in ((0, RE_EXPORTED_AT), (1, EXPORTED_AT)):
        store.write_partition(
            signal_rows(),
            layer=SIGNAL_PLANE_STREAM,
            kind="observed",
            zoom=BASE_TIER,
            day=JULY_FOURTH,
            part_index=part_index,
        )
        key = store.key_for(partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, part_index))
        backend.set_last_modified(key, instant)

    assert day_export_instant(store, JULY_FOURTH) == EXPORTED_AT


def test_one_unknown_instant_leaves_the_whole_stream_day_unanswered() -> None:
    """A partly unattributed set is not a freshness claim; so is a day holding no part file at all."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)
    backend.set_last_modified(
        store.key_for(partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH)), EXPORTED_AT
    )
    store.write_partition(
        signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH, part_index=1
    )  # left unstamped, exactly as a backend that cannot report the instant leaves it

    assert day_export_instant(store, JULY_FOURTH) is None
    assert day_export_instant(store, date(2026, 7, 3)) is None


def re_export(store: ObjectStore, backend: RecordingBackend, *, part_count: int, at: datetime) -> None:
    """Write `part_count` parts over one day, stamp them all, then prune what this export did not write."""
    for part_index in range(part_count):
        store.write_partition(
            signal_rows(),
            layer=SIGNAL_PLANE_STREAM,
            kind="observed",
            zoom=BASE_TIER,
            day=JULY_FOURTH,
            part_index=part_index,
        )
        backend.set_last_modified(
            store.key_for(partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, part_index)), at
        )
    store.prune_surplus_parts(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, written_part_count=part_count)


def test_a_shrinking_re_export_removes_the_parts_it_no_longer_wrote() -> None:
    """THE BLOCKER. Orphans left by a smaller re-export serve superseded rows and latch the lane stale.

    Four parts at T1, then a re-export writing two at T2. Without the prune, `part-2` and `part-3`
    survive holding the SUPERSEDED population, and `oldest_export_instant` -- a `min()` across the
    day -- keeps answering T1, so every later tick judges the lane stale and re-exports forever.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend, prefix="sandbox")
    re_export(store, backend, part_count=4, at=EXPORTED_AT)

    re_export(store, backend, part_count=2, at=RE_EXPORTED_AT)

    assert store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", BASE_TIER) == (
        partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, 0),
        partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, 1),
    )
    # The latch is gone with them: min() and max() over the day now agree on the newer export.
    assert day_export_instant(store, JULY_FOURTH) == RE_EXPORTED_AT


def test_the_prune_touches_only_this_layer_kind_tier_day_and_surplus_index() -> None:
    """A prune that could reach another lane, kind, TIER, day, part still in use, or a marker is not shippable."""
    backend = RecordingBackend()
    store = ObjectStore(backend, prefix="sandbox")
    survivors = (
        partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, 0),  # still written by this export
        partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, date(2026, 7, 3), 2),  # another day
        partition_path("watersheds", "observed", BASE_TIER, JULY_FOURTH, 2),  # another layer
        partition_path(SIGNAL_PLANE_STREAM, "forecast", BASE_TIER, JULY_FOURTH, 2),  # another kind
        # The same lane, stream, day and surplus index at a COARSER tier. It is a different resolution
        # of this day, not an older export of it, so this prune has no business reaching it -- and the
        # export that wrote it never told this prune how many parts IT wrote.
        partition_path(SIGNAL_PLANE_STREAM, "observed", DETAIL_TIER, JULY_FOURTH, 1),
        absence_marker_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH),  # never a part file
    )
    doomed = partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, 1)
    for relative_path in (*survivors, doomed):
        backend.put(store.key_for(relative_path), b"parquet", content_type=PARQUET_CONTENT_TYPE)

    result = store.prune_surplus_parts(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, written_part_count=1)

    assert result.removed == (doomed,)
    assert result.failures == ()
    assert backend.deleted == [store.key_for(doomed)]
    assert all(store.key_for(relative_path) in backend.objects for relative_path in survivors)


def test_a_prune_refuses_to_be_told_the_export_wrote_nothing() -> None:
    """`written_part_count=0` would delete the whole day; a prune may only ever trail a completed write."""
    store = ObjectStore(RecordingBackend())
    with pytest.raises(ValueError, match="every part of the day"):
        store.prune_surplus_parts(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, written_part_count=0)


def test_a_refused_delete_is_reported_and_never_raised() -> None:
    """The rows this export wrote are correct, so a prune failure reports the survivor, never undoes them."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    orphan = partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, 1)
    for relative_path in (partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, 0), orphan):
        backend.put(store.key_for(relative_path), b"parquet", content_type=PARQUET_CONTENT_TYPE)
    backend.refuses_delete_of.add(store.key_for(orphan))

    result = store.prune_surplus_parts(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, written_part_count=1)

    assert result.removed == ()
    assert len(result.failures) == 1
    assert orphan in result.failures[0]
    assert "still published beside this export" in result.failures[0]
    assert store.key_for(orphan) in backend.objects  # reported, not silently believed gone
    assert result.report is not None
    assert orphan in result.report


def test_an_unlistable_day_reports_the_orphan_rather_than_failing_the_export() -> None:
    """A prune that cannot even list must say the orphan may survive; silence would read as a clean prune."""

    class UnlistableBackend(RecordingBackend):
        def list_objects(self, prefix: str) -> Iterator[ListedObject]:
            raise OSError(f"bucket unreachable while listing {prefix}")

    store = ObjectStore(UnlistableBackend())

    result = store.prune_surplus_parts(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, written_part_count=1)

    assert result.removed == ()
    assert "OSError" in result.failures[0]
    assert "may still be published beside this one" in result.failures[0]


def test_a_clean_prune_with_nothing_to_remove_says_nothing() -> None:
    """No orphan, no failure, no line in the lane's detail -- a prune is silent when it is a no-op."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    backend.put(
        store.key_for(partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, 0)),
        b"parquet",
        content_type=PARQUET_CONTENT_TYPE,
    )

    result = store.prune_surplus_parts(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH, written_part_count=1)

    assert (result.removed, result.failures, result.report) == ((), (), None)


def test_the_boto_backend_deletes_through_delete_object() -> None:
    """The real backend's delete is one `delete_object` at the bucket and key the layout named."""
    client = StubS3Api([])
    backend = BotoObjectStoreBackend(bucket="tiles", client=client)

    backend.delete("prefix/layer=signal/part-3.parquet")

    assert client.deletes == [{"Bucket": "tiles", "Key": "prefix/layer=signal/part-3.parquet"}]


def test_the_export_instant_reads_only_that_streams_own_part_files_on_that_day_at_that_tier() -> None:
    """Another lane, kind, TIER, day, and an absence marker must not answer this question.

    The coarse-tier entry is the one that matters: a derived tier is written after the base it came
    from, so folding it in would answer a `min()` with a clock that belongs to the derivation step
    and quietly move a static lane's freshness claim onto the wrong export.
    """
    listed = (
        ListedPartition(
            relative_path=partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH),
            last_modified=EXPORTED_AT,
        ),
        ListedPartition(
            relative_path=partition_path("watersheds", "observed", BASE_TIER, JULY_FOURTH), last_modified=EARLIEST
        ),
        ListedPartition(
            relative_path=partition_path(SIGNAL_PLANE_STREAM, "forecast", BASE_TIER, JULY_FOURTH),
            last_modified=EARLIEST,
        ),
        ListedPartition(
            relative_path=partition_path(SIGNAL_PLANE_STREAM, "observed", DETAIL_TIER, JULY_FOURTH),
            last_modified=EARLIEST,
        ),
        ListedPartition(
            relative_path=partition_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, date(2026, 7, 3)),
            last_modified=EARLIEST,
        ),
        ListedPartition(
            relative_path=absence_marker_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH),
            last_modified=EARLIEST,
        ),
    )

    assert (
        oldest_export_instant(listed, layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)
        == EXPORTED_AT
    )
    assert (
        oldest_export_instant((), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH) is None
    )


def test_the_boto_listing_keeps_last_modified_across_continuation_pages() -> None:
    """`list_objects_v2` already carries the instant on every entry; paging and narrowing must not drop it."""
    stub = StubS3Api(
        [
            {"Contents": [{"Key": "first", "LastModified": EXPORTED_AT}], "NextContinuationToken": "page-2"},
            {
                "Contents": [
                    {"Key": "naive", "LastModified": NAIVE_INSTANT},
                    {"Key": "absent"},
                    {"Key": "not-an-instant", "LastModified": "2026-07-04T06:00:00Z"},
                    {"NoKeyAtAll": True},
                    "not-a-mapping",
                ]
            },
        ]
    )

    listed = list(BotoObjectStoreBackend(bucket="plantgeo-warehouse", client=stub).list_objects("layer=signal/"))

    assert listed == [
        ListedObject(key="first", last_modified=EXPORTED_AT),
        # Unknown beats wrong: a naive, absent, or mistyped instant degrades currency to day resolution,
        # where a guessed zone would shift it by up to a day.
        ListedObject(key="naive", last_modified=None),
        ListedObject(key="absent", last_modified=None),
        ListedObject(key="not-an-instant", last_modified=None),
    ]
    assert [request.get("ContinuationToken") for request in stub.requests] == [None, "page-2"]


def test_partition_exists_answers_without_downloading() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)

    assert not store.partition_exists(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH)
    store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)
    assert store.partition_exists(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH)
    assert not store.partition_exists(SIGNAL_PLANE_STREAM, "forecast", BASE_TIER, JULY_FOURTH)


def test_a_missing_column_is_refused_rather_than_written_thin() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    thin = signal_rows().drop_columns(["coverage_fraction"])

    with pytest.raises(ParquetSchemaMismatchError):
        store.write_partition(thin, layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)
    assert backend.objects == {}


def test_a_null_in_a_non_nullable_column_is_refused() -> None:
    """The schema is the null gate: a null normalized_value would be a fabricated measurement."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    holed = signal_rows().set_column(
        signal_rows().schema.get_field_index("normalized_value"),
        "normalized_value",
        pa.array([1.0, None, 2.0], pa.float64()),
    )

    with pytest.raises(ParquetSchemaMismatchError):
        store.write_partition(holed, layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)
    assert backend.objects == {}


def test_a_zero_row_partition_is_refused() -> None:
    """An empty file reads to gap detection as a present day, turning a real hole into coverage."""
    backend = RecordingBackend()
    store = ObjectStore(backend)

    with pytest.raises(EmptyPartitionError):
        store.write_partition(
            SIGNAL_PLANE_SCHEMA.arrow_schema.empty_table(),
            layer=SIGNAL_PLANE_STREAM,
            kind="observed",
            zoom=BASE_TIER,
            day=JULY_FOURTH,
        )
    assert backend.objects == {}


def test_an_unregistered_layer_cannot_be_written() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)

    with pytest.raises(LookupError):
        store.write_partition(signal_rows(), layer="no-such-lane", kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)
    assert backend.objects == {}


def test_conform_absorbs_column_order_and_large_string_from_polars() -> None:
    """Polars hands back `large_string`; absorbing it here stops eleven lanes each discovering it."""
    conformed = conform_to_stream_schema(signal_rows(), SIGNAL_PLANE_SCHEMA)

    assert conformed.schema.equals(SIGNAL_PLANE_SCHEMA.arrow_schema)
    assert conformed.column_names == list(SIGNAL_PLANE_SCHEMA.column_names)


def test_from_settings_fails_closed_and_names_every_missing_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing may reach the network to discover that credentials are absent."""
    for name in OBJECT_STORE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    unconfigured = Settings(_env_file=None)

    with pytest.raises(ValueError, match="object storage is not configured") as caught:
        ObjectStore.from_settings(unconfigured)

    message = str(caught.value)
    assert "OBJECT_STORE_ENDPOINT_URL" in message
    assert "OBJECT_STORE_BUCKET" in message
    assert "OBJECT_STORE_ACCESS_KEY_ID" in message
    assert "OBJECT_STORE_SECRET_ACCESS_KEY" in message


def test_settings_validate_object_store_shape_at_ingress(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in OBJECT_STORE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="credential-free HTTPS"):
        Settings(_env_file=None, object_store_endpoint_url="http://storage.example.com")
    with pytest.raises(ValueError, match="credential-free HTTPS"):
        Settings(_env_file=None, object_store_endpoint_url="https://key:secret@storage.example.com")
    with pytest.raises(ValueError, match="bucket name"):
        Settings(_env_file=None, object_store_bucket="Not A Bucket")
    with pytest.raises(ValueError, match="parent traversal"):
        Settings(_env_file=None, object_store_prefix="../escape")

    normalized = Settings(_env_file=None, object_store_prefix="/sandbox/")
    assert normalized.object_store_prefix == "sandbox/"


def test_require_object_store_returns_complete_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in OBJECT_STORE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    configured = Settings(
        _env_file=None,
        object_store_endpoint_url="https://storage.example.com/",
        object_store_bucket="plantgeo-warehouse",
        object_store_access_key_id="access-key-value",
        object_store_secret_access_key="secret-key-value",
        object_store_region="sjc",
    )

    credentials = configured.require_object_store()

    assert credentials.endpoint_url == "https://storage.example.com"
    assert credentials.bucket == "plantgeo-warehouse"
    assert credentials.region == "sjc"
    assert credentials.access_key_id.get_secret_value() == "access-key-value"
    assert "secret-key-value" not in repr(credentials)


def test_polars_storage_options_shape() -> None:
    credentials = ObjectStoreCredentials(
        endpoint_url="https://storage.example.com",
        region="sjc",
        bucket="plantgeo-warehouse",
        access_key_id="access-key-value",
        secret_access_key="secret-key-value",
    )

    assert polars_storage_options(credentials) == {
        "aws_endpoint_url": "https://storage.example.com",
        "aws_region": "sjc",
        "aws_access_key_id": "access-key-value",
        "aws_secret_access_key": "secret-key-value",
    }
