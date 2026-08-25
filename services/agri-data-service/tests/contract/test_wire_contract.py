"""Freezes `/api/v1/parquet`: the fixtures, the shapes, and agreement with the TypeScript client.

Rationale: see `AGENTS.md` in this directory.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter, ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterator

from tests.contract.wire_contract import (
    WIRE_BASE_PATH,
    WIRE_PARAMS,
    WIRE_ROUTES,
    WIRE_STATES,
    WireCoverage,
    WireEnvelope,
    WireWindow,
)

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[4]
TS_CLIENT = REPO_ROOT / "src" / "lib" / "server" / "services" / "parquet-plane-client.ts"

ENVELOPE_ADAPTER = TypeAdapter(WireEnvelope)

#: fixture file -> the model it must satisfy.
ENVELOPE_FIXTURES = [
    "day_published.json",
    "day_published_truncated.json",
    "day_governed_absence.json",
    "day_not_written.json",
    "day_lane_never_written.json",
    "release_carry_forward.json",
    "release_governed_absence.json",
]


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ENVELOPE_FIXTURES)
def test_every_envelope_fixture_round_trips(name: str) -> None:
    """Parse then re-serialize must return the fixture unchanged -- no field silently added or dropped."""
    raw = load(name)
    assert ENVELOPE_ADAPTER.dump_python(ENVELOPE_ADAPTER.validate_python(raw), mode="json") == raw


def test_window_fixture_round_trips() -> None:
    raw = load("window.json")
    assert WireWindow.model_validate(raw).model_dump(mode="json") == raw


def test_coverage_fixture_round_trips() -> None:
    raw = load("coverage.json")
    assert WireCoverage.model_validate(raw).model_dump(mode="json", by_alias=True) == raw


def test_all_four_states_have_a_fixture() -> None:
    """A state with no golden payload is a state nobody has agreed on."""
    covered = {load(name)["state"] for name in ENVELOPE_FIXTURES}
    covered |= {day["state"] for day in load("window.json")["days"]}
    assert covered == set(WIRE_STATES)


def test_an_unannounced_field_is_a_contract_break() -> None:
    """`extra="forbid"` is the freeze; without it a server could add fields nobody agreed to."""
    with pytest.raises(ValidationError):
        ENVELOPE_ADAPTER.validate_python({**load("day_published.json"), "row_count": 2})


def test_the_window_states_every_day_in_its_closed_range() -> None:
    """WIRE assumption 6: a short array would read as 'the missing days are fine'."""
    days = [day["requested_day"] for day in load("window.json")["days"]]
    assert days == sorted(days), "window days must ascend"
    assert len(days) == len(set(days)), "a day may not appear twice"
    first, last = days[0], days[-1]
    expected = _calendar_range(first, last)
    assert days == expected, f"window {first}..{last} omitted {sorted(set(expected) - set(days))}"


def test_a_carried_forward_release_is_reported_at_its_own_day() -> None:
    """The named-day rule: a weekly release must never be dressed up as fresher than it is."""
    for name in ("release_carry_forward.json", "release_governed_absence.json"):
        raw = load(name)
        assert raw["served_day"] < raw["requested_day"], f"{name} must exercise carry-forward"


def test_no_fixture_carries_a_timezone_bearing_day() -> None:
    """Days are ISO string prefixes. A `T` or a `Z` in one is how 6,279 rows once moved."""
    for path in sorted(FIXTURES.glob("*.json")):
        for key, value in _walk(json.loads(path.read_text(encoding="utf-8"))):
            if key.endswith("_day") and isinstance(value, str):
                assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), f"{path.name}: {key}={value!r}"


def test_a_never_written_lane_reports_null_bounds() -> None:
    """`soil-survey` has 238,986 source rows and 0 written; the census must say so, not guess a day."""
    lanes = {lane["layer"]: lane for lane in load("coverage.json")["lanes"]}
    assert lanes["soil-survey"]["earliest_day"] is None
    assert lanes["soil-survey"]["latest_day"] is None


def test_the_typescript_client_still_agrees_with_this_contract() -> None:
    """The freeze is only real if drift fails on BOTH sides. This is the Python side of that."""
    source = TS_CLIENT.read_text(encoding="utf-8")
    block = re.search(r"const WIRE = \{(.*?)\n\} as const;", source, re.S)
    assert block, "the WIRE block moved or was renamed in parquet-plane-client.ts"
    body = block.group(1)

    base = re.search(r'basePath:\s*"([^"]+)"', body)
    assert base, "the WIRE `basePath` moved or was renamed"
    assert base.group(1) == WIRE_BASE_PATH

    assert _ts_pairs(body, "routes") == WIRE_ROUTES
    assert _ts_pairs(body, "params") == WIRE_PARAMS


def _ts_pairs(body: str, section: str) -> dict[str, str]:
    block = re.search(rf"{section}:\s*\{{(.*?)\}},", body, re.S)
    assert block, f"the WIRE `{section}` section moved or was renamed"
    return dict(re.findall(r'(\w+):\s*"([^"]+)"', block.group(1)))


def _walk(node: object, key: str = "") -> Iterator[tuple[str, object]]:
    if isinstance(node, dict):
        for child_key, child in node.items():
            yield from _walk(child, child_key)
    elif isinstance(node, list):
        for child in node:
            yield from _walk(child, key)
    else:
        yield key, node


def _calendar_range(first: str, last: str) -> list[str]:
    start = date.fromisoformat(first)
    end = date.fromisoformat(last)
    out, cursor = [], start
    while cursor <= end:
        out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out
