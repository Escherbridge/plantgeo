"""The S18 registration mirror: every row equals its lane TOML, and a row is registered, served and censused.

Three seams, highest first:

- the REAL `lanes/` tree against the SHIPPED rows (`config_stream_mirror_violations`): live the moment
  `w3-water-gauges` or `p4-contract-freeze` appends a row and its TOML, with no edit here;
- a fresh interpreter whose mirror holds one fixture row before anything imports the registry, so
  the import-time snapshots (`authorized_serving._LANES`, the calendar floor) are the real ones;
- the pure splice (`registrations_with_config_streams`) for the refusal text and the calendar floor.

The shipped row tuple stays empty in Phase 1; every fixture row lives here.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import textwrap
from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING, Final

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.parquet.calendar import CALENDAR_STREAM
from agri_data_service.foundation.region import load_region
from agri_data_service.pipeline.parquet.config_stream_registrations import ConfigStreamRow
from agri_data_service.pipeline.parquet.lane_registry import (
    LaneRegistryError,
    config_stream_mirror_violations,
    registrations_with_config_streams,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from tests.lane_config.builders import REAL_LANES_DIRECTORY, SERVICE_ROOT, settled_soil_lane, write_lane_tree
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

#: A config lane shaped like today's soil lane (`tests/lane_config/builders.py::settled_soil_lane`).
FIXTURE_LANE_ID: Final = "mirror-fixture-forward"
FIXTURE_STREAM: Final = f"{FIXTURE_LANE_ID}-vpd"
FIXTURE_FLOOR: Final = date(2022, 8, 2)
FIXTURE_BASIS: Final = "first day of the reviewed ERA5-Land archive plan"
FIXTURE_ROW: Final = ConfigStreamRow(
    slug=FIXTURE_STREAM,
    lane_id=FIXTURE_LANE_ID,
    strategy="soil.open_meteo_era5_land",
    nature="daily_series",
    history_floor=FIXTURE_FLOOR,
    publication_lag_days=5,
    floor_basis=FIXTURE_BASIS,
)

#: The deepest literal floor today, and the lane that sets it (`fire-detections`, MODIS_SP).
LITERAL_CALENDAR_FLOOR: Final = date(2000, 11, 1)
#: A mirror row older than every literal, as `water-gauges-daily` (1990-09-30) will be.
OLDER_THAN_EVERY_LITERAL: Final = date(1990, 9, 30)

#: A literally registered legacy stream and its registered writer floor, for the literal-parity cases.
LEGACY_STREAM: Final = "soil-field-vpd"
LEGACY_WRITER_FLOOR: Final = date(2026, 8, 3)
LEGACY_COMPLETE_FLOOR: Final = date(2022, 4, 30)


def _mirror_violations(tmp_path: Path, lanes: Sequence[Mapping[str, object]], rows: Sequence[ConfigStreamRow]) -> str:
    """Load a built `lanes/` tree for the pilot region and join every mirror violation into one string."""
    config = load_lane_configs(write_lane_tree(tmp_path, lanes), load_region("pnw"))
    assert dict(config.quarantined) == {}
    return " | ".join(config_stream_mirror_violations(config, rows=rows))


def _legacy_soil_lane(**stream: object) -> dict[str, object]:
    """A legacy-executor lane TOML naming one literally registered soil stream."""
    return settled_soil_lane(
        "legacy-soil-fixture-forward",
        streams=[{"slug": LEGACY_STREAM, "floor_basis": "the literal registration's citation", **stream}],
    )


# --- the real tree against the shipped rows ------------------------------------------------------


def test_every_shipped_row_equals_its_lane_toml_and_every_toml_stream_has_one_registration() -> None:
    """S18/FR-25 over what ships: no row drifts from its TOML, and no `[[streams]]` slug lacks a registration."""
    landed = load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw"))

    assert config_stream_mirror_violations(landed) == ()


# --- the checker names every way a row and its TOML can disagree ---------------------------------


def test_a_row_equal_to_its_toml_entry_is_clean_and_a_floorless_stream_inherits_the_lane_floor(
    tmp_path: Path,
) -> None:
    """Includes the `water-gauges-daily` shape: `[days] floor` only, the row spelling both floors (spec §7a)."""
    lane_floor = date(2021, 1, 1)
    inherited = settled_soil_lane(
        FIXTURE_LANE_ID,
        days={"floor": lane_floor},
        streams=[{"slug": FIXTURE_STREAM, "floor_basis": FIXTURE_BASIS}],
    )
    both_floors_spelled = replace(FIXTURE_ROW, history_floor=lane_floor, complete_history_floor=lane_floor)

    assert _mirror_violations(tmp_path / "exact", [settled_soil_lane(FIXTURE_LANE_ID)], [FIXTURE_ROW]) == ""
    assert (
        _mirror_violations(tmp_path / "inherits", [inherited], [replace(FIXTURE_ROW, history_floor=lane_floor)]) == ""
    )
    assert _mirror_violations(tmp_path / "spelled", [inherited], [both_floors_spelled]) == ""


@pytest.mark.parametrize(
    ("drifted", "field"),
    [
        pytest.param(replace(FIXTURE_ROW, history_floor=date(2022, 8, 3)), "history_floor", id="floor"),
        pytest.param(
            replace(FIXTURE_ROW, complete_history_floor=date(2022, 9, 1)), "complete_history_floor", id="complete-floor"
        ),
        pytest.param(replace(FIXTURE_ROW, publication_lag_days=6), "publication_lag_days", id="lag"),
        pytest.param(replace(FIXTURE_ROW, floor_basis="a different citation"), "floor_basis", id="basis"),
        pytest.param(replace(FIXTURE_ROW, strategy="soil.another_source"), "strategy", id="strategy"),
        pytest.param(replace(FIXTURE_ROW, nature="release_series"), "nature", id="nature"),
    ],
)
def test_a_row_that_drifts_from_its_toml_is_named_with_the_field_and_the_expected_row(
    tmp_path: Path, drifted: ConfigStreamRow, field: str
) -> None:
    """The failure names the field and prints the row the TOML implies, so the fix is a paste."""
    violations = _mirror_violations(tmp_path, [settled_soil_lane(FIXTURE_LANE_ID)], [drifted])

    assert f"mirror row {FIXTURE_STREAM!r} differs from lanes/{FIXTURE_LANE_ID}.toml in {field};" in violations
    assert repr(FIXTURE_ROW) in violations


@pytest.mark.parametrize(
    ("lanes", "rows", "fragment"),
    [
        pytest.param(
            [settled_soil_lane(FIXTURE_LANE_ID)],
            [],
            f"declares stream {FIXTURE_STREAM!r}, which has no registration",
            id="toml-stream-without-a-row",
        ),
        pytest.param(
            [],
            [FIXTURE_ROW],
            f"lane {FIXTURE_LANE_ID!r}, which has no lane file",
            id="row-without-a-lane-file",
        ),
        pytest.param(
            [settled_soil_lane(FIXTURE_LANE_ID, streams=[])],
            [FIXTURE_ROW],
            "which declares no such [[streams]] entry",
            id="row-its-lane-does-not-declare",
        ),
        pytest.param(
            [_legacy_soil_lane()],
            [replace(FIXTURE_ROW, slug=LEGACY_STREAM, lane_id="legacy-soil-fixture-forward")],
            f"stream(s) {LEGACY_STREAM} would be registered twice",
            id="row-repeating-a-literal-registration",
        ),
        pytest.param(
            [settled_soil_lane(FIXTURE_LANE_ID, nature="static_lookup")],
            [replace(FIXTURE_ROW, nature="static_lookup")],
            "is a static_lookup; a version-stamped stream keys to a source watermark",
            id="static-lookup-row",
        ),
        pytest.param(
            [_legacy_soil_lane(history_floor=date(2026, 8, 1))],
            [],
            f"stream {LEGACY_STREAM!r} contradicts its literal registration: history_floor",
            id="toml-contradicting-a-literal-floor",
        ),
        pytest.param(
            [
                settled_soil_lane(
                    "legacy-soil-fixture-forward",
                    days={"publication_lag_days": 9},
                    streams=[{"slug": LEGACY_STREAM, "floor_basis": "citation"}],
                )
            ],
            [],
            "publication_lag_days 9 vs registered 5",
            id="toml-contradicting-a-literal-lag",
        ),
    ],
)
def test_the_checker_names_each_unmirrored_orphaned_or_contradicted_stream(
    tmp_path: Path, lanes: Sequence[Mapping[str, object]], rows: Sequence[ConfigStreamRow], fragment: str
) -> None:
    assert fragment in _mirror_violations(tmp_path, lanes, rows)


def test_a_toml_repeating_a_literal_registrations_facts_needs_no_row(tmp_path: Path) -> None:
    """A legacy lane's TOML names its literal stream; agreeing floors and lag are one registration, not two.

    The two floors are declared in separate trees because `LaneStream` currently refuses a complete
    floor older than the writer floor, which is exactly the relation every literal override has.
    """
    writer_floor = _legacy_soil_lane(history_floor=LEGACY_WRITER_FLOOR)
    complete_floor = _legacy_soil_lane(complete_history_floor=LEGACY_COMPLETE_FLOOR)

    assert _mirror_violations(tmp_path / "writer", [writer_floor], []) == ""
    assert _mirror_violations(tmp_path / "complete", [complete_floor], []) == ""


# --- the splice: registration, refusal, calendar --------------------------------------------------


@pytest.mark.asyncio
async def test_a_row_joins_the_registry_refusing_generic_exports_in_the_runners_name() -> None:
    """The runner writes a config stream; a generic export is refused and told which lane and strategy do."""
    registry = {registration.slug: registration for registration in registrations_with_config_streams([FIXTURE_ROW])}
    registration = registry[FIXTURE_STREAM]

    assert (registration.nature, registration.history_floor, registration.publication_lag_days) == (
        FIXTURE_ROW.nature,
        FIXTURE_FLOOR,
        FIXTURE_ROW.publication_lag_days,
    )
    assert registration.claimed_history_floor == FIXTURE_FLOOR
    assert registration.floor_basis == FIXTURE_BASIS
    with pytest.raises(LaneRegistryError) as refusal:
        await registration.adapter(None, None, day=date(2026, 9, 1), run_id="generic")  # type: ignore[arg-type]
    message = str(refusal.value)
    assert f"python -m agri_data_service.pipeline.runner --lane {FIXTURE_LANE_ID} --mode forward" in message
    assert "agri_data_service.pipeline.lanes.soil.open_meteo_era5_land" in message
    assert "pipeline.direct." not in message


@pytest.mark.asyncio
async def test_an_older_row_moves_the_calendar_floor_names_its_lane_and_the_next_version_carries_it() -> None:
    """FR-25/A19: the calendar floor is derived over the mirror too, and its text names who set it."""
    older = replace(FIXTURE_ROW, history_floor=OLDER_THAN_EVERY_LITERAL)
    literal_only = {entry.slug: entry for entry in registrations_with_config_streams([])}[CALENDAR_STREAM]
    spliced = {entry.slug: entry for entry in registrations_with_config_streams([older])}[CALENDAR_STREAM]

    assert literal_only.history_floor == LITERAL_CALENDAR_FLOOR
    assert "set by fire-detections --" in literal_only.floor_basis
    assert spliced.history_floor == OLDER_THAN_EVERY_LITERAL
    assert f"set by {FIXTURE_STREAM} (config lane {FIXTURE_LANE_ID}) --" in spliced.floor_basis
    assert "fire-detections" not in spliced.floor_basis

    backend = RecordingBackend()
    await spliced.adapter(None, ObjectStore(backend), day=date(2026, 9, 1), run_id="test")  # type: ignore[arg-type]
    written = [
        pq.read_table(io.BytesIO(payload)).column("calendar_day").to_pylist() for payload in backend.objects.values()
    ]
    assert min(day for part in written for day in part) == OLDER_THAN_EVERY_LITERAL


# --- a fixture row, served and censused through the real import-time snapshots -------------------

#: Runs in a fresh interpreter: the mirror is patched BEFORE `lane_registry` is imported, so the
#: fixture row reaches `authorized_serving._LANES` and the calendar floor exactly as a shipped row would.
#: The lane loader's file reader is made to refuse first, so the import also proves no TOML is read.
_FRESH_INTERPRETER_PROBE: Final = textwrap.dedent(
    """
    import json
    import sys
    from datetime import date
    from types import SimpleNamespace

    from agri_data_service.foundation.lane_config import loader
    from agri_data_service.pipeline.parquet import config_stream_registrations as mirror

    assert "agri_data_service.pipeline.parquet.lane_registry" not in sys.modules

    def refuse_import_time_toml(path, _model):
        raise AssertionError(f"{path.name} was parsed while the registry and serving imported (S18)")

    loader._read_model = refuse_import_time_toml
    mirror.CONFIG_STREAM_ROWS = (
        mirror.ConfigStreamRow(
            slug="mirror-fixture-daily", lane_id="mirror-fixture-forward", strategy="soil.open_meteo_era5_land",
            nature="daily_series", history_floor=date(1990, 9, 30), publication_lag_days=5,
            floor_basis="fixture citation",
        ),
    )

    from agri_data_service.foundation.canonical import sha256_digest
    from agri_data_service.foundation.parquet.paths import completion_marker_path, partition_path
    from agri_data_service.parquet_ops import authorized_serving as serving
    from agri_data_service.parquet_ops import faults
    from agri_data_service.parquet_ops.coverage import registered_census_lanes
    from agri_data_service.parquet_ops.request_params import ReadScope
    from agri_data_service.pipeline.parquet.availability_index import EvidenceReceipt
    from agri_data_service.pipeline.parquet.lane_registry import CALENDAR_HISTORY_FLOOR
    from tests.parquet_ops.fakes import FakeListing, FakeRowReader

    day = date(2026, 8, 6)
    part = partition_path("mirror-fixture-daily", "observed", 13, day)
    completion = completion_marker_path("mirror-fixture-daily", "observed", 13, day)
    objects = {part: b"receipt-bound parquet bytes", completion: b"receipt-bound completion"}
    row = SimpleNamespace(
        day=day, rung=13, terminal_state="published",
        terminal_receipt=EvidenceReceipt(key=part + ".terminal.json", sha256="1" * 64),
        data_receipts=(EvidenceReceipt(key=part, sha256=sha256_digest(objects[part])),),
        completion_receipt=EvidenceReceipt(key=completion, sha256=sha256_digest(objects[completion])),
    )
    index = SimpleNamespace(
        rows=(row,), pointer=SimpleNamespace(identity=SimpleNamespace(), generation_bytes=1, rows=1)
    )
    serving.read_availability_pointer = lambda *_args, **_kwargs: index.pointer
    serving.read_latest_availability = lambda *_args, **_kwargs: index

    class ReadOnlyStore:
        def read(self, key, *, max_bytes):
            payload = objects.get(key)
            return None if payload is None else SimpleNamespace(payload=payload, etag="test", version_id=None)

    def served_rows(layer):
        try:
            answered = serving.resolve_authorized_day(
                serving.AuthorizedServingReader(ReadOnlyStore()),
                FakeListing(keys=set(objects)),
                FakeRowReader(rows_by_key={part: ({"cell_id": "fixture"},)}),
                scope=ReadScope(layer=layer, kind="observed", tier=13, bbox=None),
                day=day,
            )
        except faults.ServingRefusalError as refusal:
            return str(refusal)
        return answered.to_wire()["rows"]

    print(json.dumps({
        "served": served_rows("mirror-fixture-daily"),
        "unregistered": served_rows("mirror-fixture-unregistered"),
        "censused": "mirror-fixture-daily" in {lane.layer for lane in registered_census_lanes()},
        "calendar_floor": CALENDAR_HISTORY_FLOOR.isoformat(),
    }))
    """
)
#: A cold interpreter imports the whole serving stack; this machine has measured ~6 s for it.
_FRESH_INTERPRETER_TIMEOUT_SECONDS: Final = 180


def test_a_fixture_row_is_served_censused_and_moves_the_calendar_through_the_import_time_snapshots() -> None:
    """N2: `_LANES` and the calendar floor snapshot the registry at import, and that import reads no lane TOML."""
    completed = subprocess.run(
        (sys.executable, "-c", _FRESH_INTERPRETER_PROBE),
        cwd=SERVICE_ROOT,
        capture_output=True,
        text=True,
        timeout=_FRESH_INTERPRETER_TIMEOUT_SECONDS,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    outcome = json.loads(completed.stdout.strip().splitlines()[-1])

    assert outcome["served"] == [{"cell_id": "fixture"}]
    assert "the lane is not registered" in outcome["unregistered"]
    assert outcome["censused"] is True
    assert outcome["calendar_floor"] == OLDER_THAN_EVERY_LITERAL.isoformat()
