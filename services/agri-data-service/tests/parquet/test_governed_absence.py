"""Governed-absence markers: the frozen key, the mandatory evidence, and the two-sided refusal.

RUNBOOK §0.25.3: one absence convention for every stream, decided before any lane writes. A
marked day is covered-by-absence, never a gap — and data and absence never coexist silently.

A MARKER SETTLES ONE ZOOM TIER, NOT A DAY. "The source had nothing for this day" is asserted per
rung, because that is the only claim a listing of one rung can support; the refusal that keeps data
and absence apart is scoped the same way, and the tests below say so both directions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast

import pytest

from agri_data_service.foundation.parquet.absence import (
    ABSENCE_SCHEMA_VERSION,
    GovernedAbsence,
    GovernedAbsenceError,
)
from agri_data_service.foundation.parquet.paths import (
    ABSENCE_FILE_NAME,
    AbsenceMarkerPath,
    absence_marker_path,
    completion_marker_path,
    day_prefix,
    missing_partition_days,
    partition_day_statuses,
    partition_path,
    try_parse_absence_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.parquet.gap_fill import GAP_FILL_PARTITION_KIND, _export_one_day
from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration
from agri_data_service.pipeline.parquet.objectstore import (
    ABSENCE_CONTENT_TYPE,
    EmptyPartitionError,
    GovernedAbsenceConflictError,
    ObjectStore,
)
from agri_data_service.warehouse.parquet.schema import SIGNAL_PLANE_STREAM
from tests.parquet.test_objectstore_writer import (
    BASE_TIER,
    JULY_FOURTH,
    WHOLE_WORLD_TIER,
    RecordingBackend,
    signal_rows,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.lane_registry import LaneAdapter

#: A COARSE rung, so a first-conflict refusal would already have marked the rungs before it.
CONFLICTING_TIER = 5


def sample_absence() -> GovernedAbsence:
    return GovernedAbsence(
        reason="upstream served no data for the day",
        upstream_response="HTTP 200 from api.example.gov with an empty feature set",
        recorded_at=datetime(2026, 7, 5, 8, 30, tzinfo=UTC),
        run_id="run-0042",
    )


# --- the key ---------------------------------------------------------------------------------


def test_the_marker_lands_inside_the_day_prefix() -> None:
    key = absence_marker_path("sensors", "observed", BASE_TIER, JULY_FOURTH)

    assert key == f"{day_prefix('sensors', 'observed', BASE_TIER, JULY_FOURTH)}{ABSENCE_FILE_NAME}"
    assert key.endswith("/absent.json")


def test_a_marker_key_round_trips_through_parse() -> None:
    key = absence_marker_path("fire-detections", "forecast", BASE_TIER, JULY_FOURTH)

    parsed = try_parse_absence_marker_path(key)

    assert parsed == AbsenceMarkerPath(layer="fire-detections", kind="forecast", zoom=BASE_TIER, day=JULY_FOURTH)
    assert parsed is not None
    assert parsed.key == key


def test_markers_and_part_files_are_mutually_unparseable() -> None:
    """Classifiable by key alone: neither parser ever accepts the other's objects."""
    marker_key = absence_marker_path("sensors", "observed", BASE_TIER, JULY_FOURTH)
    part_key = partition_path("sensors", "observed", BASE_TIER, JULY_FOURTH)

    assert try_parse_partition_path(marker_key) is None
    assert try_parse_absence_marker_path(part_key) is None


def test_marker_parse_normalises_backslashes_like_the_partition_parse() -> None:
    key = absence_marker_path("sensors", "observed", BASE_TIER, JULY_FOURTH).replace("/", "\\")

    parsed = try_parse_absence_marker_path(key)

    assert parsed is not None
    assert parsed.day == JULY_FOURTH


def test_an_impossible_calendar_day_is_not_a_marker() -> None:
    assert (
        try_parse_absence_marker_path("layer=sensors/kind=observed/zoom=13/year=2026/month=02/day=31/absent.json")
        is None
    )


# --- day classification ----------------------------------------------------------------------


def test_statuses_classify_data_absent_conflict_and_missing_chronologically() -> None:
    first = JULY_FOURTH
    keys = [
        partition_path("sensors", "observed", BASE_TIER, first),
        # `data` requires the completion marker; the part file alone would be `incomplete`.
        completion_marker_path("sensors", "observed", BASE_TIER, first),
        absence_marker_path("sensors", "observed", BASE_TIER, first + timedelta(days=1)),
        # Day 3 is the `conflict` case, which outranks completion: a release and a governed absence
        # on one day is an admin anomaly whether or not the release finished.
        partition_path("sensors", "observed", BASE_TIER, first + timedelta(days=2)),
        absence_marker_path("sensors", "observed", BASE_TIER, first + timedelta(days=2)),
    ]

    statuses = partition_day_statuses(
        layer="sensors", kind="observed", zoom=BASE_TIER, first_day=first, last_day=first + timedelta(days=3), keys=keys
    )

    assert list(statuses.items()) == [
        (first, "data"),
        (first + timedelta(days=1), "absent"),
        (first + timedelta(days=2), "conflict"),
        (first + timedelta(days=3), "missing"),
    ]


def test_statuses_ignore_other_layers_and_kinds() -> None:
    keys = [
        absence_marker_path("vegetation", "observed", BASE_TIER, JULY_FOURTH),
        absence_marker_path("sensors", "forecast", BASE_TIER, JULY_FOURTH),
    ]

    statuses = partition_day_statuses(
        layer="sensors", kind="observed", zoom=BASE_TIER, first_day=JULY_FOURTH, last_day=JULY_FOURTH, keys=keys
    )

    assert statuses == {JULY_FOURTH: "missing"}


def test_missing_partition_days_treats_a_marked_day_as_covered() -> None:
    """The §0.25.3 binding point: covered-by-absence is not a gap."""
    marked = JULY_FOURTH + timedelta(days=1)
    keys = [
        partition_path("sensors", "observed", BASE_TIER, JULY_FOURTH),
        absence_marker_path("sensors", "observed", BASE_TIER, marked),
    ]

    missing = missing_partition_days(
        layer="sensors",
        kind="observed",
        zoom=BASE_TIER,
        first_day=JULY_FOURTH,
        last_day=marked + timedelta(days=1),
        keys=keys,
    )

    assert missing == (marked + timedelta(days=1),)


# --- the evidence payload --------------------------------------------------------------------


def test_evidence_round_trips_through_json_bytes() -> None:
    absence = sample_absence()

    decoded = GovernedAbsence.from_json_bytes(absence.to_json_bytes())

    assert decoded == absence


def test_recorded_at_is_serialised_in_utc() -> None:
    eastern_noon = sample_absence().recorded_at.astimezone(timezone(timedelta(hours=-4)))
    absence = GovernedAbsence(reason="r", upstream_response="u", recorded_at=eastern_noon, run_id="run-1")

    assert b'"recorded_at": "2026-07-05T08:30:00+00:00"' in absence.to_json_bytes()


@pytest.mark.parametrize("field_name", ["reason", "upstream_response", "run_id"])
def test_blank_evidence_is_refused(field_name: str) -> None:
    values = {"reason": "r", "upstream_response": "u", "run_id": "run-1", field_name: "  "}

    with pytest.raises(GovernedAbsenceError, match=field_name):
        GovernedAbsence(recorded_at=datetime(2026, 7, 5, tzinfo=UTC), **values)


def test_a_naive_recorded_at_is_refused() -> None:
    with pytest.raises(GovernedAbsenceError, match="timezone-aware"):
        GovernedAbsence(reason="r", upstream_response="u", recorded_at=datetime(2026, 7, 5), run_id="run-1")  # noqa: DTZ001


def test_bytes_that_are_not_json_are_refused() -> None:
    with pytest.raises(GovernedAbsenceError, match="not valid UTF-8 JSON"):
        GovernedAbsence.from_json_bytes(b"PAR1\x00not json")


def test_a_wrong_schema_version_is_refused() -> None:
    payload = (
        sample_absence()
        .to_json_bytes()
        .replace(f'"schema_version": {ABSENCE_SCHEMA_VERSION}'.encode(), b'"schema_version": 99')
    )

    with pytest.raises(GovernedAbsenceError, match="schema_version"):
        GovernedAbsence.from_json_bytes(payload)


def test_missing_evidence_fields_are_named() -> None:
    with pytest.raises(GovernedAbsenceError, match="reason"):
        GovernedAbsence.from_json_bytes(b'{"schema_version": 1}')


# --- the store -------------------------------------------------------------------------------


def test_write_absence_lands_at_the_frozen_key_with_json_content_type() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    absence = sample_absence()

    receipt = store.write_absence(absence, layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)

    expected_key = absence_marker_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH)
    assert receipt.key == expected_key
    assert receipt.relative_path == expected_key
    assert receipt.kind == "observed"
    # Which rung this marker settles. Without it a receipt claims the day, and the day has four.
    assert receipt.zoom == BASE_TIER
    assert receipt.day == JULY_FOURTH
    assert backend.content_types[expected_key] == ABSENCE_CONTENT_TYPE
    assert GovernedAbsence.from_json_bytes(backend.objects[expected_key]) == absence
    assert receipt.byte_count == len(backend.objects[expected_key])


def test_write_absence_refuses_a_day_that_already_holds_data() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)

    with pytest.raises(GovernedAbsenceConflictError, match="already holds data"):
        store.write_absence(
            sample_absence(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH
        )


def test_write_partition_refuses_a_day_marked_absent() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_absence(sample_absence(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)

    with pytest.raises(GovernedAbsenceConflictError, match="manual admin action"):
        store.write_partition(
            signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH
        )
    assert store.absence_exists(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH) is True
    assert store.partition_exists(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH) is False


def test_explicit_retraction_authorizes_absence_to_data_correction() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_absence(sample_absence(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)

    store.clear_absence_marker(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH)
    receipt = store.write_partition(
        signal_rows(),
        layer=SIGNAL_PLANE_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        day=JULY_FOURTH,
    )

    assert store.absence_exists(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH) is False
    assert store.partition_exists(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH) is True
    assert receipt.row_count == signal_rows().num_rows


def test_the_listing_feeds_markers_to_gap_detection() -> None:
    """`list_partition_keys` output must let `missing_partition_days` see the absence."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    marked = JULY_FOURTH + timedelta(days=1)
    store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)
    store.write_absence(sample_absence(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=marked)

    keys = store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", BASE_TIER)
    missing = missing_partition_days(
        layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, first_day=JULY_FOURTH, last_day=marked, keys=keys
    )

    assert absence_marker_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, marked) in keys
    assert missing == ()


def test_a_marker_at_one_tier_does_not_cover_that_day_at_another() -> None:
    """A coarse rung's governed absence is not an answer about the base rung, and must not close its gap.

    Reading it as one would stop the driver ever attempting the day at the tier that is genuinely
    missing -- a marker is terminal, so the gap would never be revisited.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_absence(
        sample_absence(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=WHOLE_WORLD_TIER, day=JULY_FOURTH
    )

    coarse_keys = store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", WHOLE_WORLD_TIER)
    base_keys = store.list_partition_keys(SIGNAL_PLANE_STREAM, "observed", BASE_TIER)

    assert coarse_keys == (absence_marker_path(SIGNAL_PLANE_STREAM, "observed", WHOLE_WORLD_TIER, JULY_FOURTH),)
    assert base_keys == ()
    assert (
        missing_partition_days(
            layer=SIGNAL_PLANE_STREAM,
            kind="observed",
            zoom=WHOLE_WORLD_TIER,
            first_day=JULY_FOURTH,
            last_day=JULY_FOURTH,
            keys=coarse_keys,
        )
        == ()
    )
    # The same listing, censused at the base tier, still reports the real gap.
    assert missing_partition_days(
        layer=SIGNAL_PLANE_STREAM,
        kind="observed",
        zoom=BASE_TIER,
        first_day=JULY_FOURTH,
        last_day=JULY_FOURTH,
        keys=coarse_keys,
    ) == (JULY_FOURTH,)
    assert store.absence_exists(SIGNAL_PLANE_STREAM, "observed", WHOLE_WORLD_TIER, JULY_FOURTH) is True
    assert store.absence_exists(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH) is False


def test_the_two_sided_refusal_is_scoped_to_the_tier_being_written() -> None:
    """Data at one rung must not block a marker at another: they are separate claims about separate rungs.

    Whether the four tiers of one published day AGREE is the derivation step's invariant, not the
    writer's -- the tiers live under four disjoint prefixes, so policing them here would cost four
    listings per marker and still race. What the writer owes is that no ONE rung ever holds both.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(signal_rows(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)

    receipt = store.write_absence(
        sample_absence(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=WHOLE_WORLD_TIER, day=JULY_FOURTH
    )

    assert receipt.zoom == WHOLE_WORLD_TIER
    assert receipt.relative_path == absence_marker_path(SIGNAL_PLANE_STREAM, "observed", WHOLE_WORLD_TIER, JULY_FOURTH)
    # And at the tier that DOES hold data, the refusal still stands.
    with pytest.raises(GovernedAbsenceConflictError, match="already holds data"):
        store.write_absence(
            sample_absence(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH
        )


def test_absence_exists_answers_without_downloading() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)

    assert store.absence_exists(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH) is False
    store.write_absence(sample_absence(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH)
    assert store.absence_exists(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH) is True


def test_the_store_prefix_wraps_the_marker_too() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend, prefix="sandbox")

    receipt = store.write_absence(
        sample_absence(), layer=SIGNAL_PLANE_STREAM, kind="observed", zoom=BASE_TIER, day=JULY_FOURTH
    )

    relative = absence_marker_path(SIGNAL_PLANE_STREAM, "observed", BASE_TIER, JULY_FOURTH)
    assert receipt.key == f"sandbox/{relative}"
    assert receipt.relative_path == relative
    assert list(backend.objects) == [f"sandbox/{relative}"]


# --- the ladder is refused whole, or written whole ---------------------------------------------


class _EmptyLane:
    """A lane adapter whose export finds nothing, which is what opens the absence ladder."""

    async def __call__(self, *_args: object, **_kwargs: object) -> object:
        raise EmptyPartitionError("the fixture export returned 0 rows")


class _RollbackSession:
    """The only session behaviour `_govern_absent_day`'s caller needs: a rollback that succeeds."""

    async def execute(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _absence_lane() -> LaneRegistration:
    return LaneRegistration(
        slug=SIGNAL_PLANE_STREAM,
        adapter=cast("LaneAdapter", _EmptyLane()),
        history_floor=JULY_FOURTH,
        publication_lag_days=0,
        nature="daily_series",
        floor_basis="test fixture for the governed-absence ladder",
        watermark=None,
    )


@pytest.mark.asyncio
async def test_a_rung_holding_parts_refuses_the_whole_ladder_before_any_marker_is_written() -> None:
    """Coarse-first-and-refuse-on-conflict left z0 and z5 governed-absent while z13 served its rows.

    That day is base-complete, so `build_gap_census` never selects it again: the lie is stable and
    invisible. The ladder is therefore pre-checked at every rung and refused as a whole.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    # ONE rung holds data. It is not the censused base rung, so a first-conflict refusal would have
    # already written the coarser markers by the time it was found.
    store.write_partition(
        signal_rows(),
        layer=SIGNAL_PLANE_STREAM,
        kind=GAP_FILL_PARTITION_KIND,
        zoom=CONFLICTING_TIER,
        day=JULY_FOURTH,
    )

    outcome, _parts, _rows, marked_bytes, detail = await _export_one_day(
        cast("AsyncSession", _RollbackSession()),
        store,
        _absence_lane(),
        day=JULY_FOURTH,
        run_id="governed-absence-ladder",
        now=lambda: datetime(2026, 7, 5, tzinfo=UTC),
    )

    assert outcome == "blocked"
    assert marked_bytes == 0
    assert detail is not None
    assert f"z{CONFLICTING_TIER}" in detail, "the note must name the rung an admin has to look at"
    written_markers = [key for key in backend.objects if key.endswith(ABSENCE_FILE_NAME)]
    assert written_markers == [], "a refused ladder writes no marker at ANY rung, coarse ones included"
