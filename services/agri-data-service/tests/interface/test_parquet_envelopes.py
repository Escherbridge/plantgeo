"""The resolver produces the frozen payloads byte for byte -- all four states, from a real resolution.

Nothing here asserts a shape by hand: every expectation is a golden file from `tests/contract/`,
which the TypeScript client reads through its own zod schemas. See `tests/interface/AGENTS.md`.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from agri_data_service.interface.http import serving
from agri_data_service.interface.http.faults import HTTP_CONFLICT, HTTP_SERVICE_UNAVAILABLE, ServingRefusalError
from agri_data_service.interface.http.request_params import ReadScope
from agri_data_service.interface.http.serving import resolve_day, resolve_release, resolve_window
from agri_data_service.interface.http.wire import render_row, render_window
from tests.contract.wire_contract import WireEnvelope, WireWindow
from tests.interface.fakes import FakeListing, FakeRowReader, instant

if TYPE_CHECKING:
    from agri_data_service.interface.http.wire import ServedRow

FIXTURES = Path(__file__).resolve().parents[1] / "contract" / "fixtures"
ENVELOPE_ADAPTER = TypeAdapter(WireEnvelope)

SIGNAL_SCOPE = ReadScope(layer="signal", kind="observed", tier=13, bbox=None)
DROUGHT_SCOPE = ReadScope(layer="drought", kind="observed", tier=13, bbox=None)


def fixture(name: str) -> dict[str, object]:
    """Load one golden payload."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def published_rows(name: str) -> tuple[ServedRow, ...]:
    """The rows a golden `published` payload carries, used as the fake warehouse's contents."""
    rows = fixture(name)["rows"]
    assert isinstance(rows, list)
    return tuple(row for row in rows if isinstance(row, dict))


def test_a_published_day_serializes_to_the_frozen_payload() -> None:
    listing = FakeListing()
    part = listing.write_day("signal", "observed", 13, date(2026, 8, 6))
    reader = FakeRowReader(rows_by_key={part: published_rows("day_published.json")})

    envelope = resolve_day(listing, reader, scope=SIGNAL_SCOPE, day=date(2026, 8, 6))

    assert envelope.to_wire() == fixture("day_published.json")


def test_a_day_over_its_row_budget_says_so_rather_than_answering_short_and_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`truncated` is the only way a caller learns the warehouse holds more than it was handed."""
    monkeypatch.setattr(serving, "DAY_ROW_BUDGET", 1)
    listing = FakeListing()
    part = listing.write_day("signal", "observed", 13, date(2026, 8, 6))
    reader = FakeRowReader(rows_by_key={part: published_rows("day_published.json")})

    envelope = resolve_day(listing, reader, scope=SIGNAL_SCOPE, day=date(2026, 8, 6))

    assert envelope.to_wire() == fixture("day_published_truncated.json")
    assert reader.reads[0].row_budget == 1, "the resolver must carry its budget into the read"


def test_a_governed_absence_serializes_with_the_evidence_the_marker_carries() -> None:
    payload = fixture("day_governed_absence.json")
    absence = payload["absence"]
    assert isinstance(absence, dict)
    listing = FakeListing()
    listing.write_day("signal", "observed", 13, date(2026, 8, 6))
    listing.write_absence(
        "signal",
        "observed",
        13,
        date(2026, 8, 9),
        reason=str(absence["reason"]),
        upstream_response=str(absence["upstream_response"]),
        recorded_at=instant(str(absence["recorded_at"])),
        run_id=str(absence["run_id"]),
    )

    envelope = resolve_day(listing, FakeRowReader(), scope=SIGNAL_SCOPE, day=date(2026, 8, 9))

    assert envelope.to_wire() == payload


def test_a_gap_day_inside_a_written_lane_serializes_as_day_not_written() -> None:
    listing = FakeListing()
    listing.write_day("signal", "observed", 13, date(2026, 8, 6))

    envelope = resolve_day(listing, FakeRowReader(), scope=SIGNAL_SCOPE, day=date(2026, 8, 11))

    assert envelope.to_wire() == fixture("day_not_written.json")


def test_a_tier_that_has_never_been_written_is_never_reported_as_a_gap() -> None:
    """A slider must not mount an axis over a tier nobody wrote; the two states are not the same claim."""
    envelope = resolve_day(FakeListing(), FakeRowReader(), scope=SIGNAL_SCOPE, day=date(2026, 8, 6))

    assert envelope.to_wire() == fixture("day_lane_never_written.json")


def test_a_carried_forward_release_is_reported_at_its_own_day() -> None:
    listing = FakeListing()
    part = listing.write_day("drought", "observed", 13, date(2026, 8, 18))
    reader = FakeRowReader(rows_by_key={part: published_rows("release_carry_forward.json")})

    envelope = resolve_release(listing, reader, scope=DROUGHT_SCOPE, as_of=date(2026, 8, 24))

    assert envelope.to_wire() == fixture("release_carry_forward.json")


def test_a_release_whose_own_day_is_a_governed_absence_reports_that_day_too() -> None:
    payload = fixture("release_governed_absence.json")
    absence = payload["absence"]
    assert isinstance(absence, dict)
    listing = FakeListing()
    listing.write_absence(
        "drought",
        "observed",
        13,
        date(2026, 8, 18),
        reason=str(absence["reason"]),
        upstream_response=str(absence["upstream_response"]),
        recorded_at=instant(str(absence["recorded_at"])),
        run_id=str(absence["run_id"]),
    )

    envelope = resolve_release(listing, FakeRowReader(), scope=DROUGHT_SCOPE, as_of=date(2026, 8, 24))

    assert envelope.to_wire() == payload


def test_a_window_states_every_day_of_its_closed_range() -> None:
    payload = fixture("window.json")
    days = payload["days"]
    assert isinstance(days, list)
    absence = days[2]["absence"]
    listing = FakeListing()
    part = listing.write_day("signal", "observed", 13, date(2026, 8, 6))
    listing.write_absence(
        "signal",
        "observed",
        13,
        date(2026, 8, 8),
        reason=str(absence["reason"]),
        upstream_response=str(absence["upstream_response"]),
        recorded_at=instant(str(absence["recorded_at"])),
        run_id=str(absence["run_id"]),
    )
    rows = tuple(row for row in days[0]["rows"] if isinstance(row, dict))
    reader = FakeRowReader(rows_by_key={part: rows})

    envelopes = resolve_window(
        listing, reader, scope=SIGNAL_SCOPE, first_day=date(2026, 8, 6), last_day=date(2026, 8, 9)
    )

    assert render_window(envelopes) == payload


def test_a_window_never_omits_a_gap_day_however_long_the_gap_runs() -> None:
    """A short array would read as 'the missing days are fine'; the resolver walks the span itself."""
    listing = FakeListing()
    listing.write_day("signal", "observed", 13, date(2026, 8, 1))
    listing.write_day("signal", "observed", 13, date(2026, 8, 20))

    envelopes = resolve_window(
        listing, FakeRowReader(), scope=SIGNAL_SCOPE, first_day=date(2026, 8, 1), last_day=date(2026, 8, 20)
    )
    stated = [envelope.to_wire()["requested_day"] for envelope in envelopes]

    assert stated == [date(2026, 8, day).isoformat() for day in range(1, 21)]
    assert WireWindow.model_validate(render_window(envelopes)).days[1].state == "day_not_written"


def test_every_resolved_envelope_satisfies_the_frozen_contract_model() -> None:
    """The pydantic table forbids unknown fields, so this fails the moment a route invents one."""
    listing = FakeListing()
    part = listing.write_day("signal", "observed", 13, date(2026, 8, 6))
    listing.write_absence(
        "signal",
        "observed",
        13,
        date(2026, 8, 8),
        reason="upstream published no scenes",
        upstream_response="HTTP 200, features: []",
        recorded_at=instant("2026-08-09T03:02:11Z"),
        run_id="parquet-drain:1a7d9c22",
    )
    reader = FakeRowReader(rows_by_key={part: published_rows("day_published.json")})
    resolved = [
        resolve_day(listing, reader, scope=SIGNAL_SCOPE, day=date(2026, 8, 6)),
        resolve_day(listing, reader, scope=SIGNAL_SCOPE, day=date(2026, 8, 8)),
        resolve_day(listing, reader, scope=SIGNAL_SCOPE, day=date(2026, 8, 11)),
        resolve_day(FakeListing(), reader, scope=SIGNAL_SCOPE, day=date(2026, 8, 6)),
    ]

    states = {ENVELOPE_ADAPTER.validate_python(envelope.to_wire()).state for envelope in resolved}

    assert states == {"published", "governed_absence", "day_not_written", "lane_never_written"}


def test_a_day_holding_half_an_export_is_refused_rather_than_called_a_gap() -> None:
    """Part files with no completion marker: serving it ships half a release, calling it a gap lies."""
    listing = FakeListing()
    listing.write_day("signal", "observed", 13, date(2026, 8, 6), complete=False)

    with pytest.raises(ServingRefusalError) as raised:
        resolve_day(listing, FakeRowReader(), scope=SIGNAL_SCOPE, day=date(2026, 8, 6))

    assert raised.value.status == HTTP_SERVICE_UNAVAILABLE
    assert raised.value.code == "partition_day_incomplete"


def test_a_day_carrying_both_a_release_and_an_absence_is_refused_rather_than_half_served() -> None:
    listing = FakeListing()
    listing.write_day("signal", "observed", 13, date(2026, 8, 6))
    listing.write_absence(
        "signal",
        "observed",
        13,
        date(2026, 8, 6),
        reason="r",
        upstream_response="u",
        recorded_at=instant("2026-08-07T00:00:00Z"),
        run_id="run",
    )

    with pytest.raises(ServingRefusalError) as raised:
        resolve_day(listing, FakeRowReader(), scope=SIGNAL_SCOPE, day=date(2026, 8, 6))

    assert raised.value.status == HTTP_CONFLICT
    assert raised.value.code == "partition_day_conflict"


def test_an_unfinished_export_does_not_become_the_newest_release() -> None:
    """A release resolution falls through to the last COMPLETED publication, never to half of one."""
    listing = FakeListing()
    complete = listing.write_day("drought", "observed", 13, date(2026, 8, 11))
    listing.write_day("drought", "observed", 13, date(2026, 8, 18), complete=False)
    reader = FakeRowReader(rows_by_key={complete: ({"area_id": 1},)})

    envelope = resolve_release(listing, reader, scope=DROUGHT_SCOPE, as_of=date(2026, 8, 24))

    assert envelope.to_wire()["served_day"] == "2026-08-11"


def test_a_release_asked_of_a_lane_with_nothing_written_says_so() -> None:
    envelope = resolve_release(FakeListing(), FakeRowReader(), scope=DROUGHT_SCOPE, as_of=date(2026, 8, 24))

    assert envelope.to_wire() == {"state": "lane_never_written", "requested_day": "2026-08-24"}


def test_a_window_budget_truncates_at_the_late_end_and_never_reports_a_late_day_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A day the scan never reached is `published` with no rows, never `day_not_written`."""
    monkeypatch.setattr(serving, "WINDOW_ROW_BUDGET", 4)
    listing = FakeListing()
    first = listing.write_day("signal", "observed", 13, date(2026, 8, 1))
    second = listing.write_day("signal", "observed", 13, date(2026, 8, 2))
    third = listing.write_day("signal", "observed", 13, date(2026, 8, 3))
    reader = FakeRowReader(
        rows_by_key={
            first: tuple({"cell_id": index} for index in range(3)),
            second: tuple({"cell_id": index} for index in range(3)),
            third: tuple({"cell_id": index} for index in range(3)),
        }
    )

    envelopes = resolve_window(
        listing, reader, scope=SIGNAL_SCOPE, first_day=date(2026, 8, 1), last_day=date(2026, 8, 3)
    )
    wire = [envelope.to_wire() for envelope in envelopes]

    assert [entry["state"] for entry in wire] == ["published", "published", "published"]
    # Three rows of day one land whole; day two is cut mid-day; day three was never reached and says
    # so with an empty row list rather than by reporting itself unwritten.
    assert [len(entry["rows"]) for entry in wire] == [3, 1, 0]
    assert [entry["truncated"] for entry in wire] == [False, True, True]


def test_a_cell_this_plane_cannot_render_fails_closed_rather_than_being_stringified() -> None:
    """`str(value)` would serve a Decimal, a list or a struct as text under a type nobody announced.

    Latent while every registered schema is scalar; `union_by_name` over a drifted object is how a
    type nobody registered arrives in a row, and a silent contract change is worse than a refusal.
    """
    with pytest.raises(ValueError, match="no agreed rendering"):
        render_row({"cell_id": "4127", "readings": [1, 2, 3]})


def test_every_scalar_the_registered_schemas_do_carry_still_renders() -> None:
    """The control for failing closed: the shapes the twelve lanes actually hold must all pass."""
    rendered = render_row(
        {
            "cell_id": "4127",
            "count": 12,
            "flagged": True,
            "value": 0.412,
            "missing": None,
            "not_a_number": float("nan"),
            "observed_at": datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
            "day": date(2026, 8, 6),
            "digest": b"\x01\xff",
        }
    )

    assert rendered == {
        "cell_id": "4127",
        "count": 12,
        "flagged": True,
        "value": 0.412,
        "missing": None,
        "not_a_number": None,
        "observed_at": "2026-08-06T12:00:00Z",
        "day": "2026-08-06",
        "digest": "01ff",
    }
