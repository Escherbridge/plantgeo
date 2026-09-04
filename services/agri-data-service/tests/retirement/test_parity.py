"""Parity normalisation and the rewrite-epoch classifier -- the fire-perimeters trap, in code."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.retirement.parity import (
    PARITY_BINDINGS,
    PARQUET_REWRITE_EPOCHS,
    ParityAvailability,
    ParityReceiptError,
    RecordedLaneWriteProbe,
    RewriteEpoch,
    ShortfallClass,
    assess_shortfall,
    build_parity_section,
    normalize_parity_receipt,
    parse_epoch_timestamp,
)
from tests.retirement import clean_drought_receipt, instant, short_drought_receipt

if TYPE_CHECKING:
    from collections.abc import Mapping

_FIRE_EPOCH: Final = PARQUET_REWRITE_EPOCHS["fire-perimeters"]


def _weather_receipt(*, matched: bool) -> dict[str, object]:
    """Build a weather-observations receipt in the shape that module's `main()` prints."""
    return {
        "event": "weather_observations_parity",
        "verdict": "parity_matched" if matched else "under_coverage",
        "postgres_days": 40,
        "postgres_rows": 9_000,
        "under_covered_day_count": 0 if matched else 2,
        "under_covered_days": []
        if matched
        else [{"day": "2026-08-30", "postgres_rows": 5, "parquet_status": "missing", "parquet_rows": 0}],
    }


def _vegetation_receipt(*, row_coverage: str) -> dict[str, object]:
    """Build a vegetation receipt in the nested shape that module's `to_json_dict` renders."""
    return {
        "layer": "vegetation",
        "postgres": {"days": 1_195, "cell_day_rows": 500_000, "first_day": "2022-08-09", "last_day": "2026-09-01"},
        "parquet": {"days_with_data": 1_195, "days_absent": 0, "rows_measured": None, "days_neither_side_holds": 300},
        "findings": {
            "missing_from_parquet_count": 0,
            "missing_from_parquet_sample": [],
            "ladder_incomplete_count": 0,
            "ladder_incomplete_sample": [],
            "row_shortfall_count": 0,
            "row_shortfall_sample": [],
            "row_surplus_count": 0,
            "row_surplus_sample": [],
        },
        "verdict": {"day_coverage": "parity_ok", "row_coverage": row_coverage, "parity_achieved": True},
    }


def test_each_layer_modules_receipt_shape_is_recognised_by_structure() -> None:
    """Recognition is structural so a receipt from the wrong layer cannot be filed under this one."""
    assert normalize_parity_receipt(clean_drought_receipt()).shape == "drought"
    assert normalize_parity_receipt(_weather_receipt(matched=True)).shape == "weather_observations"
    assert normalize_parity_receipt(_vegetation_receipt(row_coverage="parity_ok")).shape == "vegetation"


def test_an_unrecognised_shape_is_refused_rather_than_defaulted() -> None:
    """A receipt this tool cannot read must never normalise into a permissive verdict."""
    with pytest.raises(ParityReceiptError, match="unrecognised parity receipt shape"):
        normalize_parity_receipt({"some": "other tool's output"})


def test_a_receipt_reporting_its_own_run_as_failed_is_refused() -> None:
    """`vegetation/parity.py` prints `{"status": "failed"}` when it could not read a side."""
    with pytest.raises(ParityReceiptError, match="parity run itself failed"):
        normalize_parity_receipt({"status": "failed", "error": "OperationalError: no such schema"})


def test_a_day_only_vegetation_receipt_is_marked_as_not_having_compared_rows() -> None:
    """Without `--count-rows` the module proves day membership only, which is not D1's bar."""
    normalized = normalize_parity_receipt(_vegetation_receipt(row_coverage="not_measured"))

    assert normalized.rows_compared is False
    assert any("not_measured" in note for note in normalized.notes)


def test_a_zero_day_baseline_is_flagged_because_covered_would_be_unearned() -> None:
    """A mistargeted DSN produces exactly this receipt: nothing read, everything 'covered'."""
    receipt: Mapping[str, object] = {**clean_drought_receipt(days=0, rows=0)}

    assert normalize_parity_receipt(receipt).baseline_empty is True


def test_the_epoch_is_checked_before_any_verdict_is_believed() -> None:
    """Fact 3: a clean receipt against an unrewritten twin is still not evidence of anything."""
    normalized = normalize_parity_receipt(clean_drought_receipt())
    probe = RecordedLaneWriteProbe(recorded={"fire-perimeters": instant(1)})

    assessment = assess_shortfall(normalized=normalized, epoch=_FIRE_EPOCH, probe=probe)

    assert assessment.classification is ShortfallClass.TWIN_NOT_REWRITTEN
    assert assessment.blocking is True


def test_a_short_lane_past_its_epoch_is_reported_as_genuine_under_coverage() -> None:
    """Once the twin has been rewritten, a shortfall is a shortfall and D1's blocker applies."""
    normalized = normalize_parity_receipt(short_drought_receipt())
    probe = RecordedLaneWriteProbe(recorded={"fire-perimeters": instant(9)})

    assessment = assess_shortfall(normalized=normalized, epoch=_FIRE_EPOCH, probe=probe)

    assert assessment.classification is ShortfallClass.GENUINE_UNDER_COVERAGE


def test_an_unknown_last_write_is_unproven_rather_than_either_answer() -> None:
    """With no evidence about the twin, a shortfall cannot be told apart from a pending rewrite."""
    normalized = normalize_parity_receipt(short_drought_receipt())

    assessment = assess_shortfall(normalized=normalized, epoch=_FIRE_EPOCH, probe=RecordedLaneWriteProbe(recorded={}))

    assert assessment.classification is ShortfallClass.TWIN_REWRITE_UNPROVEN


def test_a_lane_with_no_epoch_reads_its_receipt_directly() -> None:
    """Only a lane whose partition semantics changed pays the epoch check."""
    probe = RecordedLaneWriteProbe(recorded={})

    clean = assess_shortfall(normalized=normalize_parity_receipt(clean_drought_receipt()), epoch=None, probe=probe)
    short = assess_shortfall(normalized=normalize_parity_receipt(short_drought_receipt()), epoch=None, probe=probe)

    assert clean.classification is ShortfallClass.NONE
    assert short.classification is ShortfallClass.GENUINE_UNDER_COVERAGE


def test_no_comparison_at_all_is_unmeasured_not_under_covered() -> None:
    """Calling an uncounted lane 'short' is the same overclaim pointed the other way."""
    assessment = assess_shortfall(normalized=None, epoch=None, probe=RecordedLaneWriteProbe(recorded={}))

    assert assessment.classification is ShortfallClass.UNMEASURED
    assert assessment.blocking is True


def test_a_layer_without_a_parity_module_emits_unavailable_and_no_number() -> None:
    """Wave B ships three parity modules; the other five layers must say so rather than invent one."""
    section = build_parity_section(
        scope="sensors",
        binding=None,
        receipt_payload=None,
        receipt_source=None,
        epoch=None,
    )

    assert section.availability is ParityAvailability.UNAVAILABLE
    assert section.normalized is None
    assert "no per-layer parity module" in (section.unavailable_reason or "")


def test_a_bound_module_with_no_captured_receipt_names_the_command_to_run() -> None:
    """An operator must never have to guess which command produces the missing evidence."""
    section = build_parity_section(
        scope="drought",
        binding=PARITY_BINDINGS["drought"],
        receipt_payload=None,
        receipt_source=None,
        epoch=None,
    )

    assert section.availability is ParityAvailability.UNAVAILABLE
    assert "direct.drought.parity" in (section.unavailable_reason or "")


def test_a_naive_timestamp_is_refused_because_an_epoch_needs_an_absolute_instant() -> None:
    """A comparison against a naive local time is a comparison nobody can reproduce."""
    with pytest.raises(ParityReceiptError, match="no timezone"):
        parse_epoch_timestamp("2026-09-04T00:00:00")


def test_the_fire_perimeters_epoch_cites_the_registration_that_moved_it() -> None:
    """The refusal must carry the evidence, so nobody has to re-derive why the days are unreadable."""
    epoch: RewriteEpoch = _FIRE_EPOCH

    assert epoch.epoch_at == instant(4)
    assert "lane_registry.py" in epoch.citation
    assert "static_lookup" in epoch.reason
