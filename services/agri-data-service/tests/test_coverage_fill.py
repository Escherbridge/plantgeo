"""A gap fill may only author a plan it can justify, and may only retire a day upstream really lacks.

No database and no network. The upstream probe is supplied as a value rather than performed, which
is the whole reason `decide_coverage_fill` takes one: the decision ladder, the absence rows and the
plan bytes are all exercised here, and the parts that need a real provider or a real planner belong
elsewhere.

Both directions are covered on every branch that matters: a run the provider serves becomes a plan,
a run it serves nothing for becomes a governed absence, and each cheap refusal is proven to spend no
upstream request at all.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import httpx
import pytest

from agri_data_service.execution.coverage_census import LaneCell
from agri_data_service.execution.coverage_contract import DayCoverage, DayState, SignalReconciliation
from agri_data_service.execution.coverage_fill import (
    GAP_FILL_FAMILY_TOKEN,
    GAP_FILL_NOTE_SENTINEL,
    GAP_PROBE_SCHEMA_VERSION,
    GAP_PROBE_SOURCE_VERSION,
    GAP_PROBE_TRANSFORM_VERSION,
    CoverageFillDecision,
    CoverageFillError,
    FillRefusal,
    GapProbe,
    GapTarget,
    GapVerdict,
    ProbedCell,
    ProbedParameter,
    coverage_fill_payload,
    decide_coverage_fill,
    fill_plan_stem,
    fill_window,
    gap_probe_verdict,
    gap_to_probe,
    lane_gap_targets,
    plan_signal_names,
    planned_absence,
    probe_gap_window,
    record_governed_absence,
    signal_name_for,
    signals_this_plan_can_fill,
    unprobed_refusal,
    write_fill_plan,
)
from agri_data_service.execution.historical_backfill import (
    NASA_POWER_MAX_RESPONSE_BYTES,
    HistoricalNasaBackfillPlan,
    four_calendar_years_before,
)
from agri_data_service.execution.plan_continuation import (
    ContinuationLane,
    ContinuationSource,
    frontier_probe_cells,
    load_continuation_source,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

PLANS_ROOT = Path(__file__).resolve().parent.parent / "plans"
NASA_WEATHER_FAST_PLAN = PLANS_ROOT / "nasa-power-western-na-weather-fast-20220806-20260806.json"
OPEN_METEO_VPD_PLAN = PLANS_ROOT / "open-meteo-era5-land-pnw-vpd-20220802-20260802.json"

# Every decision below is taken from an instant well past the gaps it reasons about: a run reaching
# today is the forward refresh's business, and the ladder refuses it before any probe is spent.
DECIDED_AT = datetime(2026, 8, 11, 5, 0, tzinfo=UTC)
GAP_FIRST = date(2024, 3, 14)
GAP_LAST = date(2024, 3, 16)
PROBE_CELL_COUNT = 3
# 2024-03-14..2024-03-16 inclusive, named so an assertion reads as a claim about the run.
GAP_DAY_COUNT = 3


def _source(tmp_path: Path, plan_path: Path = NASA_WEATHER_FAST_PLAN) -> ContinuationSource:
    return load_continuation_source(plan_path, local_execution_root=tmp_path)


def _reconciliation(signal_name: str, missing: Sequence[date]) -> SignalReconciliation:
    """One signal whose only interesting property is which days are missing."""
    ordered = sorted(missing)
    return SignalReconciliation(
        source_key="nasa-power-daily",
        signal_name=signal_name,
        earliest_required_day=date(2022, 8, 6),
        through_day=date(2026, 8, 6),
        days=tuple(
            DayCoverage(day=day, state=DayState.MISSING, observed_cell_count=0, expected_cell_count=397)
            for day in ordered
        ),
    )


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def _target(first: date = GAP_FIRST, last: date = GAP_LAST) -> GapTarget:
    return GapTarget(first_day=first, last_day=last, signal_names=("precipitation",))


def _probe(source: ContinuationSource, target: GapTarget, *, observed_per_parameter: int) -> GapProbe:
    cells = frontier_probe_cells(source.cells, PROBE_CELL_COUNT)
    return GapProbe(
        start_day=target.first_day,
        end_day=target.last_day,
        requested_day_count=target.day_count,
        measured_at=DECIDED_AT,
        cells=tuple(
            ProbedCell(
                cell_key=cell.cell_key,
                parameters=tuple(
                    ProbedParameter(parameter=parameter, observed_day_count=observed_per_parameter)
                    for parameter in source.parameters
                ),
            )
            for cell in cells
        ),
    )


def _lattice(source: ContinuationSource) -> dict[str, LaneCell]:
    return {
        cell.cell_key: LaneCell(
            cell_id=uuid.uuid5(uuid.NAMESPACE_URL, cell.cell_key),
            cell_key=cell.cell_key,
            latitude=cell.latitude,
            longitude=cell.longitude,
        )
        for cell in source.cells
    }


def _decide(
    source: ContinuationSource,
    signals: Sequence[SignalReconciliation],
    *,
    output_directory: Path,
    probe: GapProbe | None,
) -> CoverageFillDecision:
    return decide_coverage_fill(
        source,
        signals,
        output_directory=output_directory,
        lane_cells=_lattice(source),
        support_key="surface",
        probe=probe,
        now=DECIDED_AT,
    )


def test_missing_days_collapse_into_runs_and_name_the_signals_inside_each() -> None:
    """One fetch serves every parameter, so runs are unioned across signals and labelled with them."""
    targets = lane_gap_targets(
        (
            _reconciliation("precipitation", _days(GAP_FIRST, GAP_LAST)),
            _reconciliation("wind_speed", [GAP_LAST, date(2025, 1, 2)]),
        )
    )

    assert [(target.first_day, target.last_day, target.day_count) for target in targets] == [
        (GAP_FIRST, GAP_LAST, 3),
        (date(2025, 1, 2), date(2025, 1, 2), 1),
    ]
    assert targets[0].signal_names == ("precipitation", "wind_speed")
    assert targets[1].signal_names == ("wind_speed",)


def test_the_fill_window_is_anchored_on_the_gap_not_on_today() -> None:
    """Anchoring on today would spend quota on every day between the hole and the live edge."""
    window = fill_window(_target())

    assert window.end_date == GAP_LAST
    assert window.start_date == four_calendar_years_before(GAP_LAST)
    assert window.day_count == (window.end_date - window.start_date).days + 1


def test_a_lane_with_nothing_missing_refuses_without_a_probe(tmp_path: Path) -> None:
    """The state this verb exists to reach is not an error and costs no upstream request."""
    source = _source(tmp_path)
    signals = (_reconciliation("precipitation", ()),)

    assert gap_to_probe(source, signals, output_directory=tmp_path, now=DECIDED_AT) is None
    decision = _decide(source, signals, output_directory=tmp_path, probe=None)
    assert decision.refusal is FillRefusal.NOTHING_MISSING
    assert decision.target is None
    assert decision.missing_day_count == 0


def test_a_gap_reaching_today_is_the_forward_refresh_s_business(tmp_path: Path) -> None:
    """Filling the live edge would retire days the provider has simply not published yet."""
    source = _source(tmp_path)
    today = DECIDED_AT.date()
    signals = (_reconciliation("precipitation", _days(today - timedelta(days=2), today)),)

    live_edge = _target(today - timedelta(days=2), today)
    assert (
        unprobed_refusal(source, live_edge, output_directory=tmp_path, now=DECIDED_AT) is FillRefusal.GAP_AT_LIVE_EDGE
    )
    assert gap_to_probe(source, signals, output_directory=tmp_path, now=DECIDED_AT) is None
    decision = _decide(source, signals, output_directory=tmp_path, probe=None)
    assert decision.refusal is FillRefusal.GAP_AT_LIVE_EDGE
    assert decision.probe is None


def test_a_run_longer_than_the_frozen_window_is_refused_not_half_planned(tmp_path: Path) -> None:
    """Authoring only the tail would leave the head unplanned and looking filled on the next run."""
    source = _source(tmp_path)
    long_run = _days(date(2020, 1, 1), date(2025, 1, 1))
    signals = (_reconciliation("precipitation", long_run),)

    assert gap_to_probe(source, signals, output_directory=tmp_path, now=DECIDED_AT) is None
    decision = _decide(source, signals, output_directory=tmp_path, probe=None)
    assert decision.refusal is FillRefusal.GAP_EXCEEDS_WINDOW_SPAN
    assert decision.missing_day_count == len(long_run)


def test_an_already_authored_fill_is_refused_rather_than_rewritten(tmp_path: Path) -> None:
    """Idempotence: a second run must not stack a second lineage over the same days."""
    source = _source(tmp_path)
    signals = (_reconciliation("precipitation", _days(GAP_FIRST, GAP_LAST)),)
    existing = tmp_path / f"{fill_plan_stem(source.path.stem, fill_window(_target()))}.json"
    existing.write_text("{}\n", encoding="utf-8")

    assert gap_to_probe(source, signals, output_directory=tmp_path, now=DECIDED_AT) is None
    decision = _decide(source, signals, output_directory=tmp_path, probe=None)
    assert decision.refusal is FillRefusal.PLAN_ALREADY_AUTHORED


def test_a_fillable_gap_without_a_probe_is_a_fault_not_a_guess(tmp_path: Path) -> None:
    """The one thing this module may never do is decide a gap it did not measure."""
    source = _source(tmp_path)
    signals = (_reconciliation("precipitation", _days(GAP_FIRST, GAP_LAST)),)

    assert gap_to_probe(source, signals, output_directory=tmp_path, now=DECIDED_AT) == _target()
    with pytest.raises(CoverageFillError, match="requires a measured provider probe"):
        _decide(source, signals, output_directory=tmp_path, probe=None)


def test_a_verdict_needs_at_least_one_probed_cell() -> None:
    """Zero cells is not evidence of emptiness; it is evidence of nothing."""
    empty = GapProbe(start_day=GAP_FIRST, end_day=GAP_LAST, requested_day_count=3, measured_at=DECIDED_AT, cells=())
    with pytest.raises(CoverageFillError, match="at least one probed cell"):
        gap_probe_verdict(empty)


def test_any_value_anywhere_makes_the_run_fillable(tmp_path: Path) -> None:
    """A run partly served is SERVED; the walk itself records the per-parameter emptiness precisely."""
    source = _source(tmp_path)
    probe = _probe(source, _target(), observed_per_parameter=0)
    served = GapProbe(
        start_day=probe.start_day,
        end_day=probe.end_day,
        requested_day_count=probe.requested_day_count,
        measured_at=probe.measured_at,
        cells=(
            ProbedCell(
                cell_key=probe.cells[0].cell_key,
                parameters=(
                    ProbedParameter(parameter=source.parameters[0], observed_day_count=1),
                    *(
                        ProbedParameter(parameter=parameter, observed_day_count=0)
                        for parameter in source.parameters[1:]
                    ),
                ),
            ),
            *probe.cells[1:],
        ),
    )

    assert gap_probe_verdict(served) is GapVerdict.SERVED
    assert served.served_parameters == (source.parameters[0],)
    assert set(served.empty_parameters) == set(source.parameters[1:])
    assert gap_probe_verdict(probe) is GapVerdict.EMPTY


def test_a_served_gap_authors_a_plan_that_survives_its_own_lane_contract(tmp_path: Path) -> None:
    """The emitted bytes are re-parsed through the contract the backfill verb itself uses."""
    source = _source(tmp_path)
    signals = (_reconciliation("precipitation", _days(GAP_FIRST, GAP_LAST)),)
    decision = _decide(
        source, signals, output_directory=tmp_path, probe=_probe(source, _target(), observed_per_parameter=3)
    )

    assert decision.refusal is None
    assert decision.verdict is GapVerdict.SERVED
    assert decision.fill_window is not None
    assert decision.fill_window.end_date == GAP_LAST
    assert decision.plan_bytes is not None
    reparsed = HistoricalNasaBackfillPlan.model_validate_json(decision.plan_bytes)
    assert reparsed.nasa.window.end_date == GAP_LAST
    # Everything except the window, the key, the as-of and the note is inherited verbatim.
    assert [cell.cell_key for cell in reparsed.nasa.cells] == [cell.cell_key for cell in source.cells]
    assert reparsed.nasa.parameters == list(source.parameters)
    assert reparsed.transform_version == source.plan.transform_version
    assert reparsed.source.key == source.plan.source.key


def test_a_fill_lineage_is_named_apart_from_a_continuation_lineage(tmp_path: Path) -> None:
    """`plan_family` must read the two as different families, or a continuation supersedes a fill."""
    source = _source(tmp_path)
    decision = _decide(
        source,
        (_reconciliation("precipitation", _days(GAP_FIRST, GAP_LAST)),),
        output_directory=tmp_path,
        probe=_probe(source, _target(), observed_per_parameter=3),
    )

    assert decision.output_path is not None
    assert GAP_FILL_FAMILY_TOKEN in decision.output_path.stem
    assert decision.output_path.stem.endswith("-20200316-20240316")
    assert decision.fill_release_set_key is not None
    assert GAP_FILL_FAMILY_TOKEN in decision.fill_release_set_key


def test_the_authored_plan_carries_its_own_provenance(tmp_path: Path) -> None:
    """A person reading the artifact must be able to see which hole it closes and what it cost."""
    source = _source(tmp_path)
    decision = _decide(
        source,
        (_reconciliation("precipitation", _days(GAP_FIRST, GAP_LAST)),),
        output_directory=tmp_path,
        probe=_probe(source, _target(), observed_per_parameter=3),
    )

    assert decision.plan_bytes is not None
    description = HistoricalNasaBackfillPlan.model_validate_json(decision.plan_bytes).description
    assert description is not None
    assert GAP_FILL_NOTE_SENTINEL in description
    assert "2024-03-14..2024-03-16 (3 days)" in description
    assert "coverage-fill" in description


def test_a_written_plan_is_never_rewritten(tmp_path: Path) -> None:
    """A plan artifact is immutable; a second write is a fault, not an overwrite."""
    source = _source(tmp_path)
    decision = _decide(
        source,
        (_reconciliation("precipitation", _days(GAP_FIRST, GAP_LAST)),),
        output_directory=tmp_path,
        probe=_probe(source, _target(), observed_per_parameter=3),
    )
    written = write_fill_plan(decision)

    assert written.is_file()
    with pytest.raises(CoverageFillError, match="never rewritten"):
        write_fill_plan(decision)


def test_a_refused_fill_has_no_plan_to_write(tmp_path: Path) -> None:
    source = _source(tmp_path)
    decision = _decide(source, (_reconciliation("precipitation", ()),), output_directory=tmp_path, probe=None)

    with pytest.raises(CoverageFillError, match="no plan to write"):
        write_fill_plan(decision)


def test_an_empty_gap_becomes_a_governed_absence_over_the_whole_run(tmp_path: Path) -> None:
    """One honest gap record per probed cell and signal, spanning the run -- never one row per day."""
    source = _source(tmp_path)
    decision = _decide(
        source,
        (_reconciliation("precipitation", _days(GAP_FIRST, GAP_LAST)),),
        output_directory=tmp_path,
        probe=_probe(source, _target(), observed_per_parameter=0),
    )

    assert decision.refusal is FillRefusal.UPSTREAM_SERVES_NOTHING
    assert decision.verdict is GapVerdict.EMPTY
    assert decision.output_path is None
    absence = decision.absence
    assert absence is not None
    assert absence.row_count == PROBE_CELL_COUNT * len(source.parameters)
    assert absence.day_count == GAP_DAY_COUNT
    for row in absence.rows:
        assert row.window_start == datetime(2024, 3, 14, tzinfo=UTC)
        assert row.window_end == datetime(2024, 3, 16, tzinfo=UTC)
        assert row.expected_observation_count == GAP_DAY_COUNT
        assert row.support_key == "surface"
    assert {row.signal_name for row in absence.rows} == {
        signal_name_for(ContinuationLane.NASA, parameter) for parameter in source.parameters
    }


def test_the_absence_checksum_is_stable_so_re_probing_is_one_release(tmp_path: Path) -> None:
    """Two runs probing the same span are the same evidence and must not mint a second lineage."""
    source = _source(tmp_path)
    first = planned_absence(
        source,
        _target(),
        _probe(source, _target(), observed_per_parameter=0),
        lane_cells=_lattice(source),
        support_key="surface",
    )
    second = planned_absence(
        source,
        _target(),
        _probe(source, _target(), observed_per_parameter=0),
        lane_cells=_lattice(source),
        support_key="surface",
    )

    assert first.payload_checksum == second.payload_checksum
    other = planned_absence(
        source,
        _target(date(2024, 5, 1), date(2024, 5, 2)),
        _probe(source, _target(date(2024, 5, 1), date(2024, 5, 2)), observed_per_parameter=0),
        lane_cells=_lattice(source),
        support_key="surface",
    )
    assert other.payload_checksum != first.payload_checksum


def test_an_absence_needs_a_lattice_cell_to_attach_to(tmp_path: Path) -> None:
    """A probed cell the warehouse does not hold has nothing the audit foreign key can point at."""
    source = _source(tmp_path)
    with pytest.raises(CoverageFillError, match="not in the warehouse lattice"):
        planned_absence(
            source,
            _target(),
            _probe(source, _target(), observed_per_parameter=0),
            lane_cells={},
            support_key="surface",
        )


def test_a_provider_variable_maps_onto_its_warehouse_signal_or_is_refused() -> None:
    """An absence written under an invented signal name is unreadable by the census that must see it."""
    assert signal_name_for(ContinuationLane.NASA, "PRECTOTCORR") == "precipitation"
    assert signal_name_for(ContinuationLane.OPEN_METEO, "soil_moisture_0_to_7cm_mean") == "soil_water_content_layer_1"
    with pytest.raises(CoverageFillError, match="no declared warehouse signal"):
        signal_name_for(ContinuationLane.NASA, "WD2M")


class _FakeResult:
    """The narrow slice of SQLAlchemy's Result the absence writer uses."""

    def __init__(self, scalar: object, returned: Sequence[object] = ()) -> None:
        self._scalar = scalar
        self._returned = list(returned)

    def scalar_one_or_none(self) -> object:
        return self._scalar

    def scalars(self) -> _FakeResult:
        """Return self, because the fake already yields scalars."""
        return self

    def all(self) -> list[object]:
        return self._returned


class _AbsenceSession:
    """An AsyncSession stand-in that answers the release insert and records every audit row.

    `inserted_rows` is what `insert_coverage_absence ... returning 1` gives back: one value for a
    row the database actually wrote, and nothing at all for one `on conflict do nothing` skipped.
    """

    def __init__(self, release_id: object, *, inserted_rows: Sequence[object] = (1,)) -> None:
        self._release_id = release_id
        self._inserted_rows = list(inserted_rows)
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> _FakeResult:
        marker = str(statement).splitlines()[0].removeprefix("--").strip()
        bound = set(getattr(statement, "_bindparams", {}) or {})
        supplied = set(parameters or {})
        if bound != supplied:
            raise AssertionError(f"{marker} binds {sorted(bound)} but was given {sorted(supplied)}")
        self.calls.append((marker, dict(parameters or {})))
        if marker == "coverage_absence_release":
            return _FakeResult(self._release_id)
        return _FakeResult(None, self._inserted_rows)


@pytest.mark.asyncio
async def test_recording_an_absence_registers_one_release_then_one_row_per_cell_and_signal(
    tmp_path: Path,
) -> None:
    """The probe's release is the evidence; every audit row hangs on exactly that one."""
    source = _source(tmp_path)
    decision = _decide(
        source,
        (_reconciliation("precipitation", _days(GAP_FIRST, GAP_LAST)),),
        output_directory=tmp_path,
        probe=_probe(source, _target(), observed_per_parameter=0),
    )
    release_id = uuid.uuid4()
    session = _AbsenceSession(release_id)

    written = await record_governed_absence(cast("AsyncSession", session), decision, now=DECIDED_AT)

    assert decision.absence is not None
    assert written == decision.absence.row_count
    markers = [marker for marker, _ in session.calls]
    assert markers[0] == "coverage_absence_release"
    assert markers.count("coverage_absence_release") == 1
    assert markers.count("insert_coverage_absence") == decision.absence.row_count
    _, release_binds = session.calls[0]
    assert release_binds["source_key"] == "nasa-power-daily"
    assert release_binds["source_version"] == GAP_PROBE_SOURCE_VERSION
    assert release_binds["schema_version"] == GAP_PROBE_SCHEMA_VERSION
    assert release_binds["transform_version"] == GAP_PROBE_TRANSFORM_VERSION
    assert release_binds["observed_from"] == datetime(2024, 3, 14, tzinfo=UTC)
    assert release_binds["observed_to"] == datetime(2024, 3, 16, tzinfo=UTC)
    _, row_binds = session.calls[1]
    assert row_binds["source_release_id"] == release_id
    assert row_binds["expected_observation_count"] == GAP_DAY_COUNT
    details = json.loads(str(row_binds["details"]))
    assert details["recorded_by"] == "coverage-fill"
    assert details["probe"]["observed_day_total"] == 0


@pytest.mark.asyncio
async def test_a_re_applied_absence_reports_zero_written_not_the_number_offered(tmp_path: Path) -> None:
    """`on conflict do nothing` writes nothing the second time; printing the offer would be a false claim."""
    source = _source(tmp_path)
    decision = _decide(
        source,
        (_reconciliation("precipitation", _days(GAP_FIRST, GAP_LAST)),),
        output_directory=tmp_path,
        probe=_probe(source, _target(), observed_per_parameter=0),
    )
    # Every insert conflicts, so `returning 1` gives back no row at all.
    session = _AbsenceSession(uuid.uuid4(), inserted_rows=())

    written = await record_governed_absence(cast("AsyncSession", session), decision, now=DECIDED_AT)

    assert decision.absence is not None
    assert decision.absence.row_count > 0
    assert written == 0
    markers = [marker for marker, _ in session.calls]
    assert markers.count("insert_coverage_absence") == decision.absence.row_count


@pytest.mark.asyncio
async def test_an_unknown_lane_cannot_own_an_absence(tmp_path: Path) -> None:
    """A release insert that matched no data_source must fail, never write orphaned evidence."""
    source = _source(tmp_path)
    decision = _decide(
        source,
        (_reconciliation("precipitation", _days(GAP_FIRST, GAP_LAST)),),
        output_directory=tmp_path,
        probe=_probe(source, _target(), observed_per_parameter=0),
    )
    session = _AbsenceSession(None)

    with pytest.raises(CoverageFillError, match=r"no agri\.data_source row carries key"):
        await record_governed_absence(cast("AsyncSession", session), decision, now=DECIDED_AT)


@pytest.mark.asyncio
async def test_only_an_absence_refusal_has_anything_to_record(tmp_path: Path) -> None:
    source = _source(tmp_path)
    decision = _decide(
        source,
        (_reconciliation("precipitation", _days(GAP_FIRST, GAP_LAST)),),
        output_directory=tmp_path,
        probe=_probe(source, _target(), observed_per_parameter=3),
    )
    session = _AbsenceSession(uuid.uuid4())

    with pytest.raises(CoverageFillError, match="refused as an absence"):
        await record_governed_absence(cast("AsyncSession", session), decision, now=DECIDED_AT)


def test_the_payload_reports_what_was_written_and_what_was_not(tmp_path: Path) -> None:
    """A dry run and an applied run differ only in two fields, and both must be legible."""
    source = _source(tmp_path)
    decision = _decide(
        source,
        (_reconciliation("precipitation", _days(GAP_FIRST, GAP_LAST)),),
        output_directory=tmp_path,
        probe=_probe(source, _target(), observed_per_parameter=3),
    )
    dry = coverage_fill_payload(decision, plan_written=False, absence_rows_written=0)

    assert dry["plan_written"] is False
    assert dry["refusal"] is None
    assert dry["verdict"] == str(GapVerdict.SERVED)
    assert dry["target_gap"] == {
        "first_day": "2024-03-14",
        "last_day": "2024-03-16",
        "day_count": 3,
        "signal_names": ["precipitation"],
    }
    probe_payload = dry["provider_probe"]
    assert isinstance(probe_payload, dict)
    assert probe_payload["requested_day_count"] == GAP_DAY_COUNT
    assert len(cast("list[object]", probe_payload["cells"])) == PROBE_CELL_COUNT
    assert dry["projected_observation_rows"] == len(source.cells) * len(source.parameters) * (
        (GAP_LAST - four_calendar_years_before(GAP_LAST)).days + 1
    )


def test_the_open_meteo_lane_travels_the_same_ladder(tmp_path: Path) -> None:
    """The fill is lane-agnostic: the second lane's plan must clone and re-validate identically."""
    source = _source(tmp_path, OPEN_METEO_VPD_PLAN)
    probe = GapProbe(
        start_day=GAP_FIRST,
        end_day=GAP_LAST,
        requested_day_count=3,
        measured_at=DECIDED_AT,
        cells=tuple(
            ProbedCell(
                cell_key=cell.cell_key,
                parameters=tuple(
                    ProbedParameter(parameter=parameter, observed_day_count=3) for parameter in source.parameters
                ),
            )
            for cell in frontier_probe_cells(source.cells, PROBE_CELL_COUNT)
        ),
    )
    decision = decide_coverage_fill(
        source,
        (_reconciliation("vapor_pressure_deficit", _days(GAP_FIRST, GAP_LAST)),),
        output_directory=tmp_path,
        lane_cells=_lattice(source),
        support_key="era5-land-0.1deg",
        probe=probe,
        now=DECIDED_AT,
    )

    assert decision.lane is ContinuationLane.OPEN_METEO
    assert decision.refusal is None
    assert decision.plan_bytes is not None
    assert decision.fill_window is not None
    assert decision.fill_window.end_date == GAP_LAST


# --- The provider probe -------------------------------------------------------------
#
# `httpx.MockTransport` and no network, matching tests/test_historical_backfill.py. This half decides
# SERVED vs EMPTY, and EMPTY writes a PERMANENT governed absence, so the sentinel filter and the
# refusal paths below are the difference between retiring a day upstream truly cannot serve and
# retiring one it padded with fill values.

NASA_FILL_SENTINEL = -999.0
PROBE_PARAMETER_COUNT = 7
OPEN_METEO_SERVED_DAYS = 2
HTTP_TOO_MANY_REQUESTS = 429


def _nasa_payload(series_by_parameter: Mapping[str, Mapping[str, object]]) -> bytes:
    return json.dumps({"properties": {"parameter": dict(series_by_parameter)}}).encode("utf-8")


def _nasa_transport(payload: bytes, *, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "start=20240314" in str(request.url)
        assert "end=20240316" in str(request.url)
        return httpx.Response(status, content=payload, headers={"Content-Type": "application/json"})

    return httpx.MockTransport(handler)


def _nasa_series(source: ContinuationSource, values: Sequence[object]) -> dict[str, dict[str, object]]:
    days = ("20240314", "20240315", "20240316")
    return {parameter: dict(zip(days, values, strict=True)) for parameter in source.parameters}


@pytest.mark.asyncio
async def test_a_nasa_probe_counts_only_real_values_and_the_run_reads_as_served(tmp_path: Path) -> None:
    """One real value anywhere in the run makes it a fillable hole, not an absence."""
    source = _source(tmp_path)
    payload = _nasa_payload(_nasa_series(source, (NASA_FILL_SENTINEL, 4.25, None)))
    async with httpx.AsyncClient(transport=_nasa_transport(payload)) as client:
        probe = await probe_gap_window(source, _target(), now=DECIDED_AT, client=client)

    assert len(probe.cells) == PROBE_CELL_COUNT
    assert probe.requested_day_count == GAP_DAY_COUNT
    assert probe.measured_at == DECIDED_AT
    # One real value per parameter per cell: the -999 fill and the null are both missingness.
    assert probe.observed_day_total == PROBE_CELL_COUNT * PROBE_PARAMETER_COUNT
    assert len(probe.served_parameters) == PROBE_PARAMETER_COUNT
    assert probe.empty_parameters == ()
    assert gap_probe_verdict(probe) is GapVerdict.SERVED


@pytest.mark.asyncio
async def test_a_nasa_run_padded_entirely_with_fill_values_reads_as_empty(tmp_path: Path) -> None:
    """The whole point of the sentinel filter: counting raw keys would call this fully served."""
    source = _source(tmp_path)
    filled = (NASA_FILL_SENTINEL, NASA_FILL_SENTINEL, NASA_FILL_SENTINEL)
    async with httpx.AsyncClient(transport=_nasa_transport(_nasa_payload(_nasa_series(source, filled)))) as client:
        probe = await probe_gap_window(source, _target(), now=DECIDED_AT, client=client)

    assert probe.observed_day_total == 0
    assert probe.served_parameters == ()
    assert len(probe.empty_parameters) == PROBE_PARAMETER_COUNT
    assert gap_probe_verdict(probe) is GapVerdict.EMPTY


@pytest.mark.asyncio
async def test_a_nasa_response_missing_a_requested_parameter_is_a_fault_not_an_absence(
    tmp_path: Path,
) -> None:
    """A short answer must never be read as evidence the provider has nothing."""
    source = _source(tmp_path)
    partial = _nasa_series(source, (1.0, 2.0, 3.0))
    partial.pop("T2M")
    async with httpx.AsyncClient(transport=_nasa_transport(_nasa_payload(partial))) as client:
        with pytest.raises(CoverageFillError, match="omits requested parameter T2M"):
            await probe_gap_window(source, _target(), now=DECIDED_AT, client=client)


@pytest.mark.asyncio
async def test_an_unparseable_nasa_payload_is_refused_rather_than_counted_as_zero(tmp_path: Path) -> None:
    source = _source(tmp_path)
    async with httpx.AsyncClient(transport=_nasa_transport(b"<html>gateway timeout</html>")) as client:
        with pytest.raises(CoverageFillError, match="returned an unusable payload"):
            await probe_gap_window(source, _target(), now=DECIDED_AT, client=client)


@pytest.mark.asyncio
async def test_a_provider_error_status_stops_the_probe_instead_of_reading_as_empty(tmp_path: Path) -> None:
    """A 429 is our quota problem, not evidence about the provider's coverage."""
    source = _source(tmp_path)
    transport = _nasa_transport(b'{"detail": "rate limited"}', status=HTTP_TOO_MANY_REQUESTS)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await probe_gap_window(source, _target(), now=DECIDED_AT, client=client)


@pytest.mark.asyncio
async def test_an_oversized_nasa_payload_is_refused_before_it_is_parsed(tmp_path: Path) -> None:
    source = _source(tmp_path)
    oversized = b"x" * (NASA_POWER_MAX_RESPONSE_BYTES + 1)
    async with httpx.AsyncClient(transport=_nasa_transport(oversized)) as client:
        with pytest.raises(CoverageFillError, match="exceeds"):
            await probe_gap_window(source, _target(), now=DECIDED_AT, client=client)


def _open_meteo_transport(body: object) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "start_date=2024-03-14" in str(request.url)
        assert "end_date=2024-03-16" in str(request.url)
        return httpx.Response(
            200, content=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_an_open_meteo_probe_counts_non_null_days_per_parameter(tmp_path: Path) -> None:
    source = _source(tmp_path, OPEN_METEO_VPD_PLAN)
    body = {
        "daily": {
            "time": ["2024-03-14", "2024-03-15", "2024-03-16"],
            "vapour_pressure_deficit_max": [1.4, None, 1.9],
        }
    }
    async with httpx.AsyncClient(transport=_open_meteo_transport(body)) as client:
        probe = await probe_gap_window(source, _target(), now=DECIDED_AT, client=client)

    assert probe.observed_day_total == PROBE_CELL_COUNT * OPEN_METEO_SERVED_DAYS
    assert probe.served_parameters == ("vapour_pressure_deficit_max",)
    assert gap_probe_verdict(probe) is GapVerdict.SERVED


@pytest.mark.asyncio
async def test_an_open_meteo_run_of_all_nulls_reads_as_empty(tmp_path: Path) -> None:
    source = _source(tmp_path, OPEN_METEO_VPD_PLAN)
    body = {
        "daily": {
            "time": ["2024-03-14", "2024-03-15", "2024-03-16"],
            "vapour_pressure_deficit_max": [None, None, None],
        }
    }
    async with httpx.AsyncClient(transport=_open_meteo_transport(body)) as client:
        probe = await probe_gap_window(source, _target(), now=DECIDED_AT, client=client)

    assert probe.observed_day_total == 0
    assert probe.empty_parameters == ("vapour_pressure_deficit_max",)
    assert gap_probe_verdict(probe) is GapVerdict.EMPTY


@pytest.mark.asyncio
async def test_an_open_meteo_response_without_its_daily_block_is_refused(tmp_path: Path) -> None:
    source = _source(tmp_path, OPEN_METEO_VPD_PLAN)
    async with httpx.AsyncClient(transport=_open_meteo_transport({"latitude": 44.0})) as client:
        with pytest.raises(CoverageFillError, match="missing its daily block"):
            await probe_gap_window(source, _target(), now=DECIDED_AT, client=client)


@pytest.mark.asyncio
async def test_an_open_meteo_series_shorter_than_its_time_axis_is_refused(tmp_path: Path) -> None:
    """A short series would otherwise silently under-count and manufacture an absence."""
    source = _source(tmp_path, OPEN_METEO_VPD_PLAN)
    body = {
        "daily": {
            "time": ["2024-03-14", "2024-03-15", "2024-03-16"],
            "vapour_pressure_deficit_max": [1.4],
        }
    }
    async with httpx.AsyncClient(transport=_open_meteo_transport(body)) as client:
        with pytest.raises(CoverageFillError, match="omits requested parameter"):
            await probe_gap_window(source, _target(), now=DECIDED_AT, client=client)


# --- One plan fills only its own signals ---------------------------------------------


def test_a_plan_only_targets_holes_in_the_signals_it_actually_fetches(tmp_path: Path) -> None:
    """The weather plan carries no soil parameter, so a soil hole is not its to close."""
    source = _source(tmp_path)
    lane_census = (
        _reconciliation("precipitation", _days(GAP_FIRST, GAP_LAST)),
        _reconciliation("soil_wetness_profile", _days(date(2023, 1, 1), date(2023, 1, 5))),
    )

    fillable = signals_this_plan_can_fill(source, lane_census)

    assert plan_signal_names(source) == frozenset(
        {
            "precipitation",
            "relative_humidity",
            "air_temperature_mean",
            "dew_point_temperature",
            "air_temperature_max",
            "air_temperature_min",
            "wind_speed",
        }
    )
    assert [signal.signal_name for signal in fillable] == ["precipitation"]
    # Without the narrowing the older soil hole would win oldest-first and be "filled" by a plan
    # that fetches no soil parameter -- the walk succeeds, the hole survives, the census still
    # reports it, and the next run authors the same useless plan again.
    assert lane_gap_targets(fillable)[0].first_day == GAP_FIRST
    assert lane_gap_targets(lane_census)[0].first_day == date(2023, 1, 1)
