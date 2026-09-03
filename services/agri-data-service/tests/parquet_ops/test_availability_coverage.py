"""Coverage proven from the published availability index, and never from an object listing.

The census this replaces walked every day prefix of every lane on every cold request. These tests
hold the replacement to the runbook's steady-state promise: one pointer GET, one bounded generation
GET, and NO listing -- `ExplodingListing` raises on any `list_keys`/`iter_stream_keys` call, so a
regression that reaches for a prefix fails here rather than on a production bill.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

import pytest

from agri_data_service.foundation.canonical import canonical_json
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.parquet_ops import availability_coverage as availability_module
from agri_data_service.parquet_ops.availability_coverage import (
    AVAILABILITY_STALE_GRACE_DAYS,
    POINTER_REVALIDATE_SECONDS,
    AvailabilityCoverageReader,
    GenerationCachingStorage,
    lane_coverage_from_index,
    lane_root,
    merge_direct_lane_rows,
    resolve_availability_lanes,
)
from agri_data_service.parquet_ops.coverage import CensusLane, build_coverage
from agri_data_service.parquet_ops.wire import DayRange, WarehouseCoverage
from agri_data_service.pipeline.parquet.availability_index import (
    BOOTSTRAP_MARKER_SCHEMA_VERSION,
    AvailabilityChecksumError,
    AvailabilityIdentity,
    AvailabilityIndex,
    AvailabilityMalformedError,
    AvailabilityPointer,
    AvailabilityRow,
    AvailabilityUnavailableError,
    EvidenceReceipt,
    StoredAvailabilityObject,
    availability_bootstrap_marker_key,
    availability_generation_key,
    availability_pointer_key,
)
from agri_data_service.warehouse.schemas.availability_index import (
    AVAILABILITY_REQUIRED_RUNGS,
    AVAILABILITY_SCHEMA_VERSION,
)
from tests.parquet_ops.fakes import FakeListing

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from agri_data_service.config import CoverageAuthorityPolicy
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

FIXTURE = Path(__file__).resolve().parents[1] / "contract" / "fixtures" / "coverage_availability.json"

SIGNAL_LANE: Final = CensusLane(layer="signal", nature="daily_series", kind="observed")
SOIL_LANE: Final = CensusLane(layer="soil-survey", nature="static_lookup", kind="observed")
DROUGHT_LANE: Final = CensusLane(
    layer="drought", nature="release_series", kind="observed", cadence_days=7, publication_lag_days=4
)
#: A TIME-BEARING lane that never published an index -- what the golden's withheld half is built
#: from, now that a `static_lookup` is censused rather than withheld under either policy.
UNPUBLISHED_LANE: Final = CensusLane(layer="burn-severity", nature="release_series", kind="observed")

NOW: Final = datetime(2026, 8, 25, 4, tzinfo=UTC)

#: One fetch inside the TTL and one after it: the whole staleness budget of the pointer path.
POINTER_FETCHES_ACROSS_ONE_TTL: Final = 2
#: Reads issued at one key to prove a cache holds it; three, so a hit is not a coincidence.
CACHE_PROBE_ATTEMPTS: Final = 3
#: An oversized generation is SERVED both times and RETAINED neither time.
OVERSIZED_GENERATION_FETCHES: Final = 2

#: Digest-shaped constants. Availability validates the SHAPE of a receipt, so a fixed hex string
#: stands in for any real one and keeps these tests clear of the publication path's byte machinery.
DIGEST: Final = "9b1f0c4d2a7e63518c0dfb2e94a7150c3d6b8e2f41905ac7db3e6f82c150a4d7"
OTHER_DIGEST: Final = "1c2d3e4f5061728394a5b6c7d8e9f0011223344556677889900aabbccddeeff0"

#: The fixture's proven lane: five published days, one governed absence, one owed-and-missing day.
CEILING: Final = date(2026, 8, 7)

#: The rung the disagreement tests break, chosen to be the most detailed one a slider would want.
FINEST_RUNG: Final = 13

#: How far a healthy-but-lagging lane's newest terminal day sits behind its declared ceiling.
BEHIND_CEILING_DAYS: Final = 10


class ExplodingListing:
    """A `WarehouseListing` that fails the test the instant anything asks it for object keys."""

    def iter_tier_keys(self, layer: str, kind: PartitionKind, tier: ZoomTier) -> Iterator[str]:
        raise AssertionError(f"the availability path listed {layer}/{kind} at z{tier:02d}")

    def iter_stream_keys(self, layer: str, kind: PartitionKind) -> Iterator[str]:
        raise AssertionError(f"the availability path listed the whole {layer}/{kind} stream")

    def list_keys(
        self,
        layer: str,
        kind: PartitionKind,
        tier: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]:
        del year, month
        raise AssertionError(f"the availability path listed {layer}/{kind} at z{tier:02d}")

    def read_object(self, relative_key: str) -> bytes | None:
        raise AssertionError(f"the availability path read the warehouse object {relative_key!r}")


class CountingStore:
    """An `AvailabilityStorage` that counts reads per key and answers from a dictionary."""

    def __init__(self, objects: dict[str, StoredAvailabilityObject] | None = None) -> None:
        self.reads: list[str] = []
        self.objects: dict[str, StoredAvailabilityObject] = {} if objects is None else objects

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        del max_bytes
        self.reads.append(key)
        return self.objects.get(key)

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        del key, payload, content_type
        raise AssertionError("coverage must never publish")

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool:
        del key, payload, expected_etag, content_type
        raise AssertionError("coverage must never advance a pointer")


class ScriptedReader(AvailabilityCoverageReader):
    """An `AvailabilityCoverageReader` answering from a per-lane script, with no object I/O at all.

    The real reader's pointer TTL and generation reuse are exercised separately. This one exists so a
    coverage test can state "this lane's index is checksum-invalid" in one line. Its STORE is real,
    because `was_bootstrapped` still reads one object through it: the bootstrap-marker probe is what
    tells a lane that never had an index from one that lost its pointer.
    """

    def __init__(
        self,
        answers: dict[str, AvailabilityIndex | Exception],
        store: CountingStore | None = None,
    ) -> None:
        super().__init__(CountingStore() if store is None else store)
        self.answers = answers
        self.reads: list[str] = []

    def read(self, lane: CensusLane, *, now: datetime) -> AvailabilityIndex:
        """Answer the script, recording which lanes were asked."""
        del now
        self.reads.append(lane.layer)
        answer = self.answers[lane.layer]
        if isinstance(answer, Exception):
            raise answer
        return answer


def bootstrap_marker_key(lane: CensusLane) -> str:
    """Where a bootstrapped lane's deterministic marker sits, spelled by the contract itself."""
    return availability_bootstrap_marker_key(lane_root(lane))


def bootstrap_marker_object(lane: CensusLane) -> StoredAvailabilityObject:
    """The exact marker bytes a bootstrap writes: canonical, and derived only from its receipt."""
    root = lane_root(lane)
    payload = canonical_json(
        {
            "bootstrap_receipt_key": f"{root}/availability/bootstrap/receipt={DIGEST}.json",
            "bootstrap_receipt_sha256": DIGEST,
            "lane_root": root,
            "schema_version": BOOTSTRAP_MARKER_SCHEMA_VERSION,
        }
    ).encode("utf-8")
    return StoredAvailabilityObject(payload=payload, etag='"marker"')


def receipt(key: str, digest: str = DIGEST) -> EvidenceReceipt:
    """Build one evidence receipt without touching a store."""
    return EvidenceReceipt(key=key, sha256=digest)


def identity(lane: CensusLane) -> AvailabilityIdentity:
    """Build one lane's availability identity at the exact root a bootstrap would write it to."""
    return AvailabilityIdentity(
        lane_root=lane_root(lane),
        lane=lane.layer,
        product=lane.layer,
        nature="release_series" if lane.nature == "release_series" else "daily_series",
        required_rungs=AVAILABILITY_REQUIRED_RUNGS,
        verified_source_inventory_root=DIGEST,
    )


def terminal_row(
    lane: CensusLane,
    *,
    day: date,
    rung: int,
    terminal_state: str = "published",
    source_ceiling: date = CEILING,
) -> AvailabilityRow:
    """Build one `(day, rung)` terminal row with receipts shaped exactly as the schema requires."""
    published = terminal_state == "published"
    root = lane_root(lane)
    return AvailabilityRow(
        lane=lane.layer,
        product=lane.layer,
        nature="release_series" if lane.nature == "release_series" else "daily_series",
        day=day,
        rung=rung,
        terminal_state="published" if published else "governed_absence",
        row_count=1 if published else 0,
        source_receipt=receipt(f"{root}/evidence/source/{day.isoformat()}.json"),
        terminal_receipt=receipt(f"{root}/evidence/terminal/{day.isoformat()}-z{rung:02d}.json"),
        data_receipts=(receipt(f"{root}/zoom={rung:02d}/{day.isoformat()}/part-00000.parquet"),) if published else (),
        completion_receipt=receipt(f"{root}/zoom={rung:02d}/{day.isoformat()}/_COMPLETE") if published else None,
        absence_reason=None if published else "upstream published nothing for this day",
        source_ceiling=source_ceiling,
        published_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
    )


def index_of(
    lane: CensusLane,
    rows: Sequence[AvailabilityRow],
    *,
    source_ceiling: date = CEILING,
    generation_sha256: str = DIGEST,
) -> AvailabilityIndex:
    """Build one verified index in memory, binding its pointer to the generation key it names."""
    root = lane_root(lane)
    days = [entry.day for entry in rows]
    pointer = AvailabilityPointer(
        schema_version=AVAILABILITY_SCHEMA_VERSION,
        identity=identity(lane),
        required_rungs=AVAILABILITY_REQUIRED_RUNGS,
        generation_key=availability_generation_key(root, generation_sha256),
        generation_sha256=generation_sha256,
        generation_receipt_sha256=OTHER_DIGEST,
        generation_bytes=4096,
        rows=len(rows),
        earliest_terminal_day=min(days),
        latest_terminal_day=max(days),
        source_ceiling=source_ceiling,
        prior_generation_key=None,
        prior_generation_sha256=None,
        created_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
        bootstrap_receipt=receipt(f"{root}/availability/bootstrap/receipt.json"),
    )
    return AvailabilityIndex(pointer=pointer, rows=tuple(rows))


def whole_ladder(
    lane: CensusLane,
    *,
    published: Sequence[date] = (),
    absent: Sequence[date] = (),
    source_ceiling: date = CEILING,
) -> AvailabilityIndex:
    """Build an index whose every authoritative rung agrees, day by day."""
    rows = [
        terminal_row(lane, day=day, rung=rung, terminal_state=state, source_ceiling=source_ceiling)
        for state, days in (("published", published), ("governed_absence", absent))
        for day in days
        for rung in AVAILABILITY_REQUIRED_RUNGS
    ]
    return index_of(lane, sorted(rows, key=lambda entry: (entry.day, entry.rung)), source_ceiling=source_ceiling)


def golden() -> dict[str, object]:
    """The frozen availability-authority coverage payload."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_a_lane_root_is_the_stream_prefix_the_warehouse_already_writes() -> None:
    """Bootstrap and read must derive the SAME root, or a published index is invisible to coverage."""
    assert lane_root(SIGNAL_LANE) == "layer=signal/kind=observed"
    assert availability_pointer_key(lane_root(SIGNAL_LANE)) == "layer=signal/kind=observed/availability/_LATEST.json"


def test_a_valid_index_reproduces_the_frozen_availability_payload() -> None:
    """The golden must be REACHABLE from an index and a withholding, not merely well-shaped."""
    payload = golden()
    reader = ScriptedReader(
        {
            "signal": whole_ladder(
                SIGNAL_LANE,
                published=[date(2026, 8, day) for day in range(1, 6)],
                absent=[date(2026, 8, 6)],
            ),
            "burn-severity": AvailabilityUnavailableError("availability_missing", "no pointer"),
        }
    )
    lanes = (SIGNAL_LANE, UNPUBLISHED_LANE)

    resolution = resolve_availability_lanes(reader, lanes=lanes, policy="availability", now=NOW)
    coverage = WarehouseCoverage(
        generated_at=NOW,
        evaluated_through_day=date(2026, 8, 25),
        lanes=merge_direct_lane_rows(lanes=lanes, resolution=resolution, census_rows=()),
    )

    assert coverage.to_wire() == payload


def test_every_rung_reports_only_days_the_whole_authoritative_set_agrees_on() -> None:
    """A day one rung is missing is not selectable ANYWHERE; z13 must not out-run z00."""
    complete = [terminal_row(SIGNAL_LANE, day=date(2026, 8, 1), rung=rung) for rung in AVAILABILITY_REQUIRED_RUNGS]
    partial = [terminal_row(SIGNAL_LANE, day=date(2026, 8, 2), rung=rung) for rung in AVAILABILITY_REQUIRED_RUNGS[:-1]]

    rows = lane_coverage_from_index(index_of(SIGNAL_LANE, [*complete, *partial]), lane=SIGNAL_LANE)

    assert {entry.zoom for entry in rows} == set(ZOOM_TIERS)
    for entry in rows:
        assert entry.latest_day == date(2026, 8, 1), entry.zoom
        assert entry.published_ranges == (DayRange(first_day=date(2026, 8, 1), last_day=date(2026, 8, 1)),)


def test_a_day_whose_rungs_disagree_on_their_outcome_is_not_selectable() -> None:
    """Three rungs published and one governed-absent is an unresolved lane-day, not a published one."""
    disagreeing = [
        terminal_row(
            SIGNAL_LANE,
            day=date(2026, 8, 1),
            rung=rung,
            terminal_state="governed_absence" if rung == FINEST_RUNG else "published",
        )
        for rung in AVAILABILITY_REQUIRED_RUNGS
    ]

    rows = lane_coverage_from_index(index_of(SIGNAL_LANE, disagreeing), lane=SIGNAL_LANE)

    assert all(entry.earliest_day is None for entry in rows)
    assert all(entry.published_ranges == () for entry in rows)
    assert all(entry.governed_absence_ranges == () for entry in rows)


def test_the_latest_day_never_passes_the_index_s_own_source_ceiling() -> None:
    """The ceiling is the lane's horizon; a day past it would claim data the source cannot have."""
    proven = whole_ladder(
        SIGNAL_LANE,
        published=[date(2026, 8, 1), date(2026, 8, 5)],
        source_ceiling=date(2026, 8, 5),
    )

    rows = lane_coverage_from_index(proven, lane=SIGNAL_LANE)

    for entry in rows:
        assert entry.source_ceiling_day == date(2026, 8, 5)
        assert entry.latest_day is not None
        assert entry.latest_day <= entry.source_ceiling_day


def test_a_lane_behind_the_live_edge_reports_no_phantom_gap_tail() -> None:
    """Closing against `today` would gray a healthy lane whose source simply lags behind it."""
    proven = whole_ladder(
        SIGNAL_LANE,
        published=[date(2026, 8, 1), date(2026, 8, 2)],
        source_ceiling=date(2026, 8, 2),
    )

    rows = lane_coverage_from_index(proven, lane=SIGNAL_LANE)

    assert all(entry.gap_ranges == () for entry in rows), "the ceiling, not today, closes an availability lane"


def test_a_release_lane_keeps_its_registered_carry_and_cadence() -> None:
    """Availability changes the EVIDENCE, never the client contract a lane's nature already fixed."""
    proven = whole_ladder(
        DROUGHT_LANE,
        published=[date(2026, 8, 4), date(2026, 8, 18)],
        source_ceiling=date(2026, 8, 20),
    )

    rows = lane_coverage_from_index(proven, lane=DROUGHT_LANE)

    for entry in rows:
        assert entry.published_ranges == (
            DayRange(first_day=date(2026, 8, 4), last_day=date(2026, 8, 10)),
            DayRange(first_day=date(2026, 8, 18), last_day=date(2026, 8, 20)),
        )
        assert entry.gap_ranges == (DayRange(first_day=date(2026, 8, 11), last_day=date(2026, 8, 17)),)


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        (AvailabilityUnavailableError("availability_missing", "no pointer"), "availability_unpublished"),
        (AvailabilityUnavailableError("availability_stale", "ceiling precedes required"), "availability_stale"),
        (AvailabilityMalformedError("not the frozen schema"), "availability_malformed"),
        (AvailabilityChecksumError("bytes disagree with their receipt"), "availability_checksum_invalid"),
    ],
)
def test_availability_authority_withholds_every_lane_it_cannot_prove(fault: Exception, reason: str) -> None:
    """Fail closed with an explicit reason; a withheld lane stays present and offers nothing."""
    reader = ScriptedReader({"signal": fault})

    resolution = resolve_availability_lanes(reader, lanes=(SIGNAL_LANE,), policy="availability", now=NOW)

    assert resolution.census_lanes == (), "withholding must never fall through to a listing"
    assert [entry.reason for entry in resolution.withheld] == [reason]
    rows = resolution.lanes
    assert len(rows) == len(ZOOM_TIERS)
    for entry in rows:
        assert entry.withheld_reason == reason
        assert entry.coverage_authority == "availability"
        assert entry.earliest_day is None
        assert entry.latest_day is None
        assert entry.published_ranges == ()
        assert entry.gap_ranges == ()
        assert entry.governed_absence_ranges == ()


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        (AvailabilityMalformedError("not the frozen schema"), "availability_malformed"),
        (AvailabilityChecksumError("bytes disagree with their receipt"), "availability_checksum_invalid"),
    ],
)
def test_corruption_is_withheld_in_the_transitional_mode_too(fault: Exception, reason: str) -> None:
    """A corrupt index must not be quietly re-proven by the scan the artifact exists to retire."""
    reader = ScriptedReader({"signal": fault})

    resolution = resolve_availability_lanes(reader, lanes=(SIGNAL_LANE,), policy="census_until_bootstrap", now=NOW)

    assert resolution.census_lanes == ()
    assert [entry.reason for entry in resolution.withheld] == [reason]


def test_the_transitional_mode_censuses_only_the_lanes_that_have_no_pointer_yet() -> None:
    """Bridge-then-cut per lane: a bootstrapped lane stops listing the day its pointer lands."""
    reader = ScriptedReader(
        {
            "signal": whole_ladder(SIGNAL_LANE, published=[date(2026, 8, 1)]),
            "drought": AvailabilityUnavailableError("availability_missing", "no pointer"),
        }
    )
    lanes = (SIGNAL_LANE, DROUGHT_LANE, SOIL_LANE)

    resolution = resolve_availability_lanes(reader, lanes=lanes, policy="census_until_bootstrap", now=NOW)

    assert resolution.census_lanes == (DROUGHT_LANE, SOIL_LANE), "a static lookup never owns an index"
    assert resolution.withheld == ()
    assert {entry.coverage_authority for entry in resolution.lanes} == {"availability"}
    assert {entry.layer for entry in resolution.lanes} == {"signal"}


@pytest.mark.parametrize("policy", ["availability", "census_until_bootstrap"])
def test_a_static_lookup_stays_on_the_census_under_both_policies(policy: str) -> None:
    """A version stamp is not a time axis, so 4a never gives it an index to be withheld against."""
    reader = ScriptedReader({})

    resolution = resolve_availability_lanes(
        reader,
        lanes=(SOIL_LANE,),
        policy=cast("CoverageAuthorityPolicy", policy),
        now=NOW,
    )

    assert reader.reads == [], "a lane with no time axis is decided from its registration alone"
    assert resolution.census_lanes == (SOIL_LANE,)
    assert resolution.lanes == (), "a censused lane's rows come from the listing, not from here"
    assert resolution.withheld == ()


def test_availability_authority_never_reaches_for_an_object_listing() -> None:
    """The tripwire: hand the TIME-BEARING remainder a listing that raises on any call, and it must not."""
    reader = ScriptedReader(
        {
            "signal": whole_ladder(SIGNAL_LANE, published=[date(2026, 8, 1)]),
            "drought": whole_ladder(DROUGHT_LANE, published=[date(2026, 8, 4)]),
        }
    )
    lanes = (SIGNAL_LANE, DROUGHT_LANE)

    resolution = resolve_availability_lanes(reader, lanes=lanes, policy="availability", now=NOW)
    remainder = build_coverage(ExplodingListing(), lanes=resolution.census_lanes, generated_at=NOW)

    assert resolution.census_lanes == ()
    assert remainder.lanes == ()
    assert len(resolution.lanes) == len(lanes) * len(ZOOM_TIERS)


def test_a_lane_behind_its_own_ceiling_reports_the_gap_tail_it_owes() -> None:
    """The whole reason the ceiling is the LANE's: a pinned-to-the-newest-day ceiling can show no tail."""
    ceiling = date(2026, 8, 20)
    newest = ceiling - timedelta(days=BEHIND_CEILING_DAYS)
    proven = whole_ladder(
        SIGNAL_LANE,
        published=[newest - timedelta(days=1), newest],
        source_ceiling=ceiling,
    )

    rows = lane_coverage_from_index(proven, lane=SIGNAL_LANE)

    for entry in rows:
        assert entry.latest_day == newest
        assert entry.source_ceiling_day == ceiling
        assert entry.gap_ranges == (DayRange(first_day=newest + timedelta(days=1), last_day=ceiling),), (
            "every day between the newest terminal day and the declared ceiling was owed"
        )


def test_a_release_lane_is_not_charged_its_publication_lag_twice() -> None:
    """The publisher already subtracted the lag when it declared the ceiling; charging it again hides a gap."""
    lane = CensusLane(
        layer="burn-severity", nature="release_series", kind="observed", cadence_days=7, publication_lag_days=4
    )
    proven = whole_ladder(lane, published=[date(2026, 8, 4)], source_ceiling=date(2026, 8, 25))

    rows = lane_coverage_from_index(proven, lane=lane)

    for entry in rows:
        # Cadence steps from 08-04 up to the ceiling ITSELF. Charging the 4-day lag a second time
        # would stop at 08-21 and silently drop the 08-25 release this lane already owes.
        assert entry.gap_ranges == (
            DayRange(first_day=date(2026, 8, 11), last_day=date(2026, 8, 11)),
            DayRange(first_day=date(2026, 8, 18), last_day=date(2026, 8, 18)),
            DayRange(first_day=date(2026, 8, 25), last_day=date(2026, 8, 25)),
        )


def test_a_bootstrapped_lane_that_lost_its_pointer_is_withheld_rather_than_re_censused() -> None:
    """Falling back here would silently resume the whole-stream listing the index exists to retire."""
    store = CountingStore({bootstrap_marker_key(SIGNAL_LANE): bootstrap_marker_object(SIGNAL_LANE)})
    reader = ScriptedReader({"signal": AvailabilityUnavailableError("availability_missing", "no pointer")}, store)

    resolution = resolve_availability_lanes(reader, lanes=(SIGNAL_LANE,), policy="census_until_bootstrap", now=NOW)

    assert resolution.census_lanes == ()
    assert [entry.reason for entry in resolution.withheld] == ["availability_unpublished"]
    assert {entry.withheld_reason for entry in resolution.lanes} == {"availability_unpublished"}


def test_a_lane_that_was_never_bootstrapped_still_falls_back_to_the_census() -> None:
    """The bridge is only for a lane with NO availability history; the marker is the discriminator."""
    reader = ScriptedReader(
        {"signal": AvailabilityUnavailableError("availability_missing", "no pointer")},
        CountingStore(),
    )

    resolution = resolve_availability_lanes(reader, lanes=(SIGNAL_LANE,), policy="census_until_bootstrap", now=NOW)

    assert resolution.census_lanes == (SIGNAL_LANE,)
    assert resolution.withheld == ()


def test_a_pointer_frozen_beyond_tolerance_is_withheld_as_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    """`availability_stale` was reachable only from an identity mismatch, never from actual staleness."""
    asked: list[date | None] = []

    def fake_read(store: object, **kwargs: object) -> AvailabilityIndex:
        del store
        required = kwargs["required_source_ceiling"]
        asked.append(cast("date | None", required))
        raise AvailabilityUnavailableError("availability_stale", f"ceiling precedes required {required}")

    monkeypatch.setattr(availability_module, "read_latest_availability", fake_read)
    reader = AvailabilityCoverageReader(CountingStore())

    resolution = resolve_availability_lanes(reader, lanes=(DROUGHT_LANE,), policy="availability", now=NOW)

    # 08-25 minus the 4-day lag, minus one publication period (7 + 4) and the declared grace.
    assert asked == [date(2026, 8, 21) - timedelta(days=7 + 4 + AVAILABILITY_STALE_GRACE_DAYS)]
    assert [entry.reason for entry in resolution.withheld] == ["availability_stale"]


def test_merging_keeps_one_evidence_source_per_lane_in_registration_order() -> None:
    """Two rows for one `(layer, kind, zoom)` would let a stale listing overwrite a proven index."""
    reader = ScriptedReader(
        {
            "signal": whole_ladder(SIGNAL_LANE, published=[date(2026, 8, 1)]),
            "drought": AvailabilityUnavailableError("availability_missing", "no pointer"),
        }
    )
    lanes = (SIGNAL_LANE, DROUGHT_LANE)
    listing = FakeListing()
    for tier in ZOOM_TIERS:
        listing.write_day("drought", "observed", tier, date(2026, 8, 4))
    resolution = resolve_availability_lanes(reader, lanes=lanes, policy="census_until_bootstrap", now=NOW)
    census = build_coverage(listing, lanes=resolution.census_lanes, generated_at=NOW)

    merged = merge_direct_lane_rows(lanes=lanes, resolution=resolution, census_rows=census.lanes)

    identities = [(entry.layer, entry.kind, entry.zoom) for entry in merged]
    assert len(identities) == len(set(identities)) == len(lanes) * len(ZOOM_TIERS)
    assert [entry.layer for entry in merged[: len(ZOOM_TIERS)]] == ["signal"] * len(ZOOM_TIERS)
    assert {entry.coverage_authority for entry in merged if entry.layer == "drought"} == {"census"}
    assert {entry.required_rungs for entry in merged if entry.layer == "drought"} == {()}


def test_a_pointer_is_re_read_only_once_its_ttl_has_run_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Steady state is ONE tiny pointer GET per lane per TTL, not one per cold coverage build."""
    proven = whole_ladder(SIGNAL_LANE, published=[date(2026, 8, 1)])
    calls: list[str] = []

    def fake_read(store: object, **kwargs: object) -> AvailabilityIndex:
        del store
        calls.append(str(kwargs["lane_root"]))
        return proven

    monkeypatch.setattr(availability_module, "read_latest_availability", fake_read)
    reader = AvailabilityCoverageReader(CountingStore(), pointer_ttl_seconds=POINTER_REVALIDATE_SECONDS)

    reader.read(SIGNAL_LANE, now=NOW)
    reader.read(SIGNAL_LANE, now=NOW + timedelta(seconds=POINTER_REVALIDATE_SECONDS - 1))
    assert calls == ["layer=signal/kind=observed"]

    reader.read(SIGNAL_LANE, now=NOW + timedelta(seconds=POINTER_REVALIDATE_SECONDS))
    assert len(calls) == POINTER_FETCHES_ACROSS_ONE_TTL, (
        "a pointer is MUTABLE and must be re-fetched once its TTL is spent"
    )


def test_an_immutable_generation_is_kept_by_its_key_while_the_pointer_is_always_refetched() -> None:
    """A generation key carries its own digest, so a cache hit can never be a stale hit."""
    root = lane_root(SIGNAL_LANE)
    pointer_key = availability_pointer_key(root)
    generation_key = availability_generation_key(root, DIGEST)
    inner = CountingStore(
        {
            pointer_key: StoredAvailabilityObject(payload=b"{}", etag='"1"'),
            generation_key: StoredAvailabilityObject(payload=b"parquet", etag='"2"'),
        }
    )
    cached = GenerationCachingStorage(inner=inner)

    for _attempt in range(CACHE_PROBE_ATTEMPTS):
        cached.read(pointer_key, max_bytes=1024)
        cached.read(generation_key, max_bytes=1024)

    assert inner.reads.count(pointer_key) == CACHE_PROBE_ATTEMPTS
    assert inner.reads.count(generation_key) == 1


def test_an_oversized_generation_is_served_but_never_retained() -> None:
    """`GENERATION_MAX_BYTES` allows 256 MiB; holding one of those per lane is the memory incident."""
    generation_key = availability_generation_key(lane_root(SIGNAL_LANE), DIGEST)
    inner = CountingStore({generation_key: StoredAvailabilityObject(payload=b"x" * 64, etag='"1"')})
    cached = GenerationCachingStorage(inner=inner, max_entry_bytes=8)

    assert cached.read(generation_key, max_bytes=1024) is not None
    assert cached.read(generation_key, max_bytes=1024) is not None
    assert inner.reads.count(generation_key) == OVERSIZED_GENERATION_FETCHES


def test_the_coverage_reader_refuses_to_write_through_its_cache() -> None:
    """A publish reached through this wrapper would leave the process believing a stale pointer."""
    cached = GenerationCachingStorage(inner=CountingStore())

    with pytest.raises(NotImplementedError):
        cached.put_immutable("layer=signal/kind=observed/availability/_LATEST.json", b"{}", content_type="text/plain")
    with pytest.raises(NotImplementedError):
        cached.compare_and_swap(
            "layer=signal/kind=observed/availability/_LATEST.json",
            b"{}",
            expected_etag=None,
            content_type="text/plain",
        )
