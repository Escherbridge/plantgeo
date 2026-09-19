"""FR-4a: writing a partition does not publish it. The generation, the pointer, and the lost race."""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pyarrow.parquet as pq
import pytest

from plantgeo_ml_service.foundation.parquet_paths import (
    availability_lane_root,
    availability_pointer_path,
    availability_retry_path,
)
from plantgeo_ml_service.pipeline.availability_publisher import (
    POINTER_RETRY_BUDGET,
    AvailabilityPublishError,
    InMemoryPointerStore,
    StoredPointer,
    build_generation,
    conditional_put_is_supported,
    pointer_for,
    publish_generation,
    required_rungs_are_the_full_ladder,
    reset_conditional_put_support,
)
from plantgeo_ml_service.pipeline.object_store import InMemoryObjectStoreBackend, ObjectKeyError, ObjectStore
from plantgeo_ml_service.warehouse.availability import (
    AVAILABILITY_INDEX_SCHEMA,
    AVAILABILITY_METADATA_KEYS,
    AVAILABILITY_REQUIRED_RUNGS,
    POINTER_MAX_BYTES,
    AvailabilityConfig,
    AvailabilityDocumentError,
    AvailabilityRow,
    EvidenceReceipt,
    lane_identity_for,
    selectable_days,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

DAY = date(2026, 9, 18)
CEILING = date(2026, 9, 19)
CREATED_AT = datetime(2026, 9, 19, 0, 0, 0, tzinfo=UTC)
LANE_ROOT = availability_lane_root("signal", "forecast")


def _config() -> AvailabilityConfig:
    return AvailabilityConfig(
        identity=lane_identity_for("signal", "forecast", verified_source_inventory_root="a" * 64),
        source_ceiling=CEILING,
        bootstrap_receipt=EvidenceReceipt(
            key=f"{LANE_ROOT}/availability/bootstrap/_BOOTSTRAPPED.json", sha256="b" * 64
        ),
    )


def _row(
    rung: int, *, day: date = DAY, terminal_state: str = "published", nature: str = "daily_series"
) -> AvailabilityRow:
    day_prefix = f"{LANE_ROOT}/zoom={rung:02d}/year={day.year:04d}/month={day.month:02d}/day={day.day:02d}"
    absent = terminal_state == "governed_absence"
    return AvailabilityRow(
        lane="signal",
        product="forecast",
        nature=nature,  # type: ignore[arg-type]  # the case set exercises a nature the lane refuses
        day=day,
        rung=rung,
        terminal_state=terminal_state,  # type: ignore[arg-type]
        row_count=0 if absent else 1_234,
        source_receipt=EvidenceReceipt(key=f"{LANE_ROOT}/availability/evidence/source.json", sha256="c" * 64),
        terminal_receipt=EvidenceReceipt(
            key=f"{LANE_ROOT}/availability/evidence/terminal-{rung:02d}.json", sha256="d" * 64
        ),
        data_receipts=() if absent else (EvidenceReceipt(key=f"{day_prefix}/part-0.parquet", sha256="f" * 64),),
        completion_receipt=None if absent else EvidenceReceipt(key=f"{day_prefix}/_complete.json", sha256="e" * 64),
        absence_reason="source_empty" if absent else None,
        source_ceiling=CEILING,
        published_at=CREATED_AT,
    )


def _full_ladder() -> tuple[AvailabilityRow, ...]:
    return tuple(_row(rung) for rung in AVAILABILITY_REQUIRED_RUNGS)


def test_the_required_rungs_are_the_whole_published_ladder() -> None:
    assert AVAILABILITY_REQUIRED_RUNGS == (0, 5, 9, 13)
    assert required_rungs_are_the_full_ladder(_config())


def test_a_built_generation_carries_the_index_schema_and_every_metadata_key() -> None:
    generation = build_generation(_config(), _full_ladder(), created_at=CREATED_AT)

    parquet_file = pq.ParquetFile(io.BytesIO(generation.payload))
    metadata = parquet_file.schema_arrow.metadata or {}
    assert parquet_file.schema_arrow.remove_metadata().equals(AVAILABILITY_INDEX_SCHEMA)
    assert set(metadata) - {b"ARROW:schema"} == set(AVAILABILITY_METADATA_KEYS)
    assert parquet_file.metadata.num_rows == len(AVAILABILITY_REQUIRED_RUNGS)


def test_a_generation_is_content_addressed_and_deterministic() -> None:
    left = build_generation(_config(), _full_ladder(), created_at=CREATED_AT)
    right = build_generation(_config(), _full_ladder(), created_at=CREATED_AT)

    assert left.sha256 == right.sha256
    assert pointer_for(left, layer="signal", kind="forecast").generation_key.endswith(
        f"generation={left.sha256}/availability.parquet"
    )


def test_a_day_missing_a_rung_is_refused_rather_than_published_partially() -> None:
    """A partially indexed day would be selectable at a resolution nobody wrote."""
    partial = tuple(_row(rung) for rung in (0, 5, 9))

    with pytest.raises(AvailabilityPublishError, match="authoritative ladder"):
        build_generation(_config(), partial, created_at=CREATED_AT)


def test_a_day_mixing_terminal_states_across_its_ladder_is_refused() -> None:
    mixed = (*(_row(rung) for rung in (0, 5, 9)), _row(13, terminal_state="governed_absence"))

    with pytest.raises(AvailabilityPublishError, match="mixes terminal states"):
        build_generation(_config(), mixed, created_at=CREATED_AT)


def test_a_generation_with_no_rows_publishes_nothing_and_says_so() -> None:
    with pytest.raises(AvailabilityPublishError):
        build_generation(_config(), (), created_at=CREATED_AT)


def test_a_published_row_without_a_completion_receipt_is_refused_at_construction() -> None:
    with pytest.raises(AvailabilityDocumentError, match="completion receipt"):
        AvailabilityRow(
            lane="signal",
            product="forecast",
            nature="daily_series",
            day=DAY,
            rung=13,
            terminal_state="published",
            row_count=1,
            source_receipt=EvidenceReceipt(key=f"{LANE_ROOT}/s.json", sha256="c" * 64),
            terminal_receipt=EvidenceReceipt(key=f"{LANE_ROOT}/t.json", sha256="d" * 64),
            data_receipts=(),
            completion_receipt=None,
            absence_reason=None,
            source_ceiling=CEILING,
            published_at=CREATED_AT,
        )


def test_a_governed_absence_carrying_rows_is_refused() -> None:
    with pytest.raises(AvailabilityDocumentError, match="row_count=0"):
        AvailabilityRow(
            lane="signal",
            product="forecast",
            nature="daily_series",
            day=DAY,
            rung=13,
            terminal_state="governed_absence",
            row_count=7,
            source_receipt=EvidenceReceipt(key=f"{LANE_ROOT}/s.json", sha256="c" * 64),
            terminal_receipt=EvidenceReceipt(key=f"{LANE_ROOT}/t.json", sha256="d" * 64),
            data_receipts=(),
            completion_receipt=None,
            absence_reason="source_empty",
            source_ceiling=CEILING,
            published_at=CREATED_AT,
        )


def test_a_day_past_its_source_ceiling_is_refused() -> None:
    with pytest.raises(AvailabilityDocumentError, match="source ceiling"):
        _row(13, day=date(2026, 9, 20))


def test_publishing_writes_the_generation_then_advances_the_pointer() -> None:
    store = ObjectStore(backend=InMemoryObjectStoreBackend())
    pointers = InMemoryPointerStore()
    generation = build_generation(_config(), _full_ladder(), created_at=CREATED_AT)

    receipt = publish_generation(store, pointers, generation, layer="signal", kind="forecast")

    assert receipt.outcome == "advanced"
    assert receipt.attempts == 1
    assert receipt.pointer_key == availability_pointer_path("signal", "forecast")
    assert store.backend.get(store.absolute_key(receipt.generation_key)) == generation.payload
    assert pointers.read_pointer(receipt.pointer_key) is not None


def test_the_generation_object_lands_before_the_pointer_so_a_refusal_leaves_no_dangling_reference() -> None:
    store = ObjectStore(backend=InMemoryObjectStoreBackend())
    generation = build_generation(_config(), _full_ladder(), created_at=CREATED_AT)

    class _AlwaysLoses(InMemoryPointerStore):
        def compare_and_set(self, key: str, payload: bytes, *, expected_etag: str | None) -> bool:  # noqa: ARG002
            """Refuse every write, standing in for a peer that keeps winning the race."""
            return False

    receipt = publish_generation(store, _AlwaysLoses(), generation, layer="signal", kind="forecast")

    assert receipt.outcome == "lost_race"
    assert receipt.attempts == POINTER_RETRY_BUDGET + 1
    assert store.backend.get(store.absolute_key(receipt.generation_key)) == generation.payload


def test_a_lost_race_is_retried_exactly_once_and_then_succeeds() -> None:
    store = ObjectStore(backend=InMemoryObjectStoreBackend())
    generation = build_generation(_config(), _full_ladder(), created_at=CREATED_AT)

    class _LosesOnce(InMemoryPointerStore):
        losses: int = 0

        def compare_and_set(self, key: str, payload: bytes, *, expected_etag: str | None) -> bool:
            """Lose the first attempt, win the second: the one recoverable shape of contention."""
            if self.losses == 0:
                self.losses = 1
                return False
            return super().compare_and_set(key, payload, expected_etag=expected_etag)

    receipt = publish_generation(store, _LosesOnce(), generation, layer="signal", kind="forecast")

    assert receipt.outcome == "advanced"
    assert receipt.attempts == 2  # noqa: PLR2004


def test_the_two_kinds_of_one_lane_never_contend_for_one_pointer() -> None:
    """The observed promotion lane and this service's forecast publisher have separate roots."""
    assert availability_lane_root("vegetation", "forecast") != availability_lane_root("vegetation", "observed")
    assert availability_pointer_path("vegetation", "forecast") != availability_pointer_path("vegetation", "observed")


def test_a_day_whose_whole_ladder_agrees_is_selectable() -> None:
    generation = build_generation(_config(), _full_ladder(), created_at=CREATED_AT)
    pointer = pointer_for(generation, layer="signal", kind="forecast")

    assert selectable_days(pointer, generation.rows) == (DAY,)


@pytest.fixture(autouse=True)
def _without_a_latched_pointer_store() -> Iterator[None]:
    """The conditional-put latch is process-wide by design, so each case proves its OWN state."""
    reset_conditional_put_support()
    yield
    reset_conditional_put_support()


def test_the_pointer_is_written_through_the_stores_own_prefixed_path() -> None:
    """B3: a scratch dry run may never advance the published lane's `_LATEST.json`."""
    store = ObjectStore(backend=InMemoryObjectStoreBackend(), prefix="ml/scratch/x/")
    pointers = InMemoryPointerStore()
    generation = build_generation(_config(), _full_ladder(), created_at=CREATED_AT)

    receipt = publish_generation(store, pointers, generation, layer="signal", kind="forecast")

    assert receipt.pointer_key == f"ml/scratch/x/{availability_pointer_path('signal', 'forecast')}"
    assert set(pointers.pointers) == {receipt.pointer_key}
    assert store.backend.get(store.absolute_key(receipt.generation_key)) == generation.payload


def test_a_prefix_that_would_escape_its_root_is_refused_before_any_pointer_moves() -> None:
    store = ObjectStore(backend=InMemoryObjectStoreBackend(), prefix="ml/scratch/")
    pointers = InMemoryPointerStore()

    with pytest.raises(ObjectKeyError):
        store.absolute_key(f"../{availability_pointer_path('signal', 'forecast')}")
    assert pointers.pointers == {}


def test_a_lost_race_rebinds_to_the_head_it_re_read_rather_than_re_putting_a_stale_payload() -> None:
    """M4: re-putting the payload built against the old head is the lost update the retry prevents."""
    store = ObjectStore(backend=InMemoryObjectStoreBackend())
    pointers = InMemoryPointerStore()
    peer = build_generation(_config(), _full_ladder(), created_at=CREATED_AT)
    publish_generation(store, pointers, peer, layer="signal", kind="forecast")
    mine = build_generation(_config(), _full_ladder(), created_at=CREATED_AT.replace(hour=1))

    class _FlipsTheEtagOnce(InMemoryPointerStore):
        flipped: bool = False

        def compare_and_set(self, key: str, payload: bytes, *, expected_etag: str | None) -> bool:
            """Lose once by moving the head under the caller, exactly as a peer publisher would."""
            if not self.flipped:
                self.flipped = True
                return False
            return super().compare_and_set(key, payload, expected_etag=expected_etag)

    racing = _FlipsTheEtagOnce(pointers=pointers.pointers, revision=pointers.revision)
    receipt = publish_generation(store, racing, mine, layer="signal", kind="forecast")

    assert receipt.outcome == "advanced"
    assert receipt.attempts == 2  # noqa: PLR2004
    head = json.loads(racing.pointers[receipt.pointer_key].payload)
    assert head["generation_sha256"] == mine.sha256
    assert head["prior_generation_sha256"] == peer.sha256


def test_republishing_the_generation_that_is_already_the_head_refuses_instead_of_self_referencing() -> None:
    store = ObjectStore(backend=InMemoryObjectStoreBackend())
    pointers = InMemoryPointerStore()
    generation = build_generation(_config(), _full_ladder(), created_at=CREATED_AT)
    publish_generation(store, pointers, generation, layer="signal", kind="forecast")

    receipt = publish_generation(store, pointers, generation, layer="signal", kind="forecast")

    assert receipt.outcome == "lost_race"


def test_a_store_that_ignores_if_match_is_caught_on_read_back_and_refuses_every_later_publish() -> None:
    """M5: a 200 that did not honour its condition turns compare-and-set into last-write-wins."""
    store = ObjectStore(backend=InMemoryObjectStoreBackend())

    class _AnswersWithoutWriting(InMemoryPointerStore):
        def compare_and_set(self, key: str, payload: bytes, *, expected_etag: str | None) -> bool:
            """Answer 200 to every put but persist only the first: the shape a read-back catches."""
            if key not in self.pointers:
                return super().compare_and_set(key, payload, expected_etag=expected_etag)
            return True

    pointers = _AnswersWithoutWriting()
    first = build_generation(_config(), _full_ladder(), created_at=CREATED_AT)
    second = build_generation(_config(), _full_ladder(), created_at=CREATED_AT.replace(hour=2))
    assert publish_generation(store, pointers, first, layer="signal", kind="forecast").outcome == "advanced"

    receipt = publish_generation(store, pointers, second, layer="signal", kind="forecast")

    assert receipt.outcome == "conditional_put_unsupported"
    assert not conditional_put_is_supported()
    with pytest.raises(AvailabilityPublishError, match="last-write-wins"):
        publish_generation(store, pointers, second, layer="signal", kind="forecast")


def test_a_pointer_body_past_its_ceiling_is_refused_rather_than_parsed() -> None:
    """M9: a body larger than the pointer contract is not a pointer, whatever it decodes to."""
    pointers = InMemoryPointerStore()
    key = availability_pointer_path("signal", "forecast")
    pointers.pointers[key] = StoredPointer(payload=b"{" + b"x" * POINTER_MAX_BYTES, etag='"1"')

    with pytest.raises(AvailabilityPublishError, match="at most"):
        pointers.read_pointer(key)


def test_a_row_published_after_its_generation_was_created_is_refused() -> None:
    """M6: a generation cannot index a terminal state it could not have observed."""
    future = tuple(_row(rung) for rung in AVAILABILITY_REQUIRED_RUNGS)

    with pytest.raises(AvailabilityPublishError, match="published after"):
        build_generation(_config(), future, created_at=CREATED_AT.replace(year=2025))


def test_a_row_redeclaring_the_lanes_nature_is_refused() -> None:
    rows = (*(_row(rung) for rung in (0, 5, 9)), _row(13, nature="release_series"))

    with pytest.raises(AvailabilityPublishError, match="nature"):
        build_generation(_config(), rows, created_at=CREATED_AT)


def test_a_lost_race_leaves_the_pending_claim_the_retry_sweep_looks_for() -> None:
    """M8: a day whose publication lost the race still owes its availability step, and says so."""
    store = ObjectStore(backend=InMemoryObjectStoreBackend())
    generation = build_generation(_config(), _full_ladder(), created_at=CREATED_AT)

    class _AlwaysLoses(InMemoryPointerStore):
        def compare_and_set(self, key: str, payload: bytes, *, expected_etag: str | None) -> bool:  # noqa: ARG002
            """Refuse every write, standing in for a peer that keeps winning the race."""
            return False

    receipt = publish_generation(store, _AlwaysLoses(), generation, layer="signal", kind="forecast")

    assert receipt.outcome == "lost_race"
    claim = store.read_object(availability_retry_path("signal", "forecast", DAY))
    assert claim is not None
    assert json.loads(claim)["generation_sha256"] == generation.sha256
