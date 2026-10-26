"""Parity: `wfigs._named_day` must mirror `direct.fire_perimeters.rows.publisher_named_day` exactly.

A G6 hazard: the lane's `_named_day` used to skip empty strings and accept any
`date.fromisoformat`-parseable prefix (including ISO week dates), where the legacy writer's
`publisher_named_day` stops the chain on ANY string (COALESCE semantics) and enforces a strict
`\\d{4}-\\d{2}-\\d{2}` regex first (`wfigs.py`'s `AGENTS.md` "The legacy-casting-rules divergence
(L1)"). This table drives BOTH functions over the same inputs so a future edit to either one that
reintroduces the divergence fails loudly here rather than at a G6 compare.

Every case below names only `polygonDateTime`/`fireDiscoveryDateTime` -- the two keys
`_OBSERVATION_DAY_KEYS` actually carries -- so legacy's wider four-key chain and the lane's
narrower two-key chain agree on every row here; the table isolates the two behavioural fixes this
parity test exists to pin, not the separately-documented, still-deliberate key-set narrowing.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from agri_data_service.pipeline.direct.fire_perimeters.rows import publisher_named_day
from agri_data_service.pipeline.lanes.fire_perimeters.wfigs import _named_day

PARITY_CASES: tuple[tuple[str, dict[str, Any], date | None], ...] = (
    (
        "polygon time wins over discovery time",
        {"polygonDateTime": "2026-08-30T18:45:00.000Z", "fireDiscoveryDateTime": "2026-07-16T01:07:00.000Z"},
        date(2026, 8, 30),
    ),
    (
        "a JSON-null polygon time falls through to discovery time",
        {"polygonDateTime": None, "fireDiscoveryDateTime": "2026-07-16T01:07:00.000Z"},
        date(2026, 7, 16),
    ),
    (
        "a present-but-empty timestamp stops the chain instead of falling through",
        {"polygonDateTime": "", "fireDiscoveryDateTime": "2026-07-16T01:07:00.000Z"},
        None,
    ),
    (
        "an entirely empty string prefix is refused",
        {"polygonDateTime": ""},
        None,
    ),
    (
        "no timestamp at all is undated rather than dropped",
        {"uniqueFireIdentifier": "OR-XYZ-000001"},
        None,
    ),
    (
        "an unreal calendar day is refused the way pg_input_is_valid refuses it",
        {"polygonDateTime": "2026-02-31T00:00:00.000Z"},
        None,
    ),
    (
        "a misshaped day prefix is refused by the regex",
        {"polygonDateTime": "30/08/2026"},
        None,
    ),
    (
        "the named day is the string prefix, never a converted instant",
        {"polygonDateTime": "2026-08-30T23:59:59.999Z"},
        date(2026, 8, 30),
    ),
    (
        "an ISO week date is refused by the strict regex despite date.fromisoformat accepting it",
        {"polygonDateTime": "2026-W01-1"},
        None,
    ),
)


@pytest.mark.parametrize(
    ("perimeter", "expected"),
    [case[1:] for case in PARITY_CASES],
    ids=[case[0] for case in PARITY_CASES],
)
def test_lane_named_day_matches_legacy_publisher_named_day(perimeter: dict[str, Any], expected: date | None) -> None:
    assert publisher_named_day(perimeter) == expected
    assert _named_day(perimeter) == expected
