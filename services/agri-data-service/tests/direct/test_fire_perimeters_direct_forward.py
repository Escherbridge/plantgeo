"""Config validation, CLI defaults and the memoized watermark the forward turn substitutes.

No network, no object store and no DuckDB: every test here validates a config in isolation or
exercises `MemoizedDirectWatermark`, which by construction opens neither a socket nor a session.
`run_fire_perimeters_forward` itself always fetches WFIGS first -- this lane's version day is derived
from the population, not from the calendar -- so there is no before-the-floor no-op path to exercise
the way `test_drought_forward.py` has one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
from agri_data_service.pipeline.direct.fire_perimeters.forward import (
    FIRE_PERIMETERS_DEFAULT_MAX_DAYS,
    FIRE_PERIMETERS_DEFAULT_RETRY_ATTEMPTS,
    FIRE_PERIMETERS_MAX_DAYS,
    FirePerimetersForwardConfig,
    FirePerimetersForwardConfigError,
    MemoizedDirectWatermark,
    _validate_config,
    parse_args,
    parser,
)
from agri_data_service.pipeline.direct.fire_perimeters.products import (
    FIRE_PERIMETERS_DIRECT_ALL_TIERS,
    FIRE_PERIMETERS_DIRECT_KIND,
    fire_perimeters_lane_registration,
)
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER

WATERMARK = SourceWatermark(
    day=date(2026, 9, 6), instant=datetime(2026, 9, 6, 14, 30, tzinfo=UTC), basis="test watermark"
)


def _config(**overrides: object) -> FirePerimetersForwardConfig:
    base: dict[str, object] = {
        "max_days": 1,
        "time_budget_seconds": 60.0,
        "retry_attempts": 3,
        "retry_base_seconds": 1.0,
        "retry_max_seconds": 10.0,
        "contention_timeout_seconds": 30.0,
    }
    base.update(overrides)
    return FirePerimetersForwardConfig(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize("max_days", [0, 2, 5])
def test_any_max_days_but_one_is_refused_because_a_static_lane_owes_one_version(max_days: int) -> None:
    """A turn that quietly published one version under `--max-days 5` would misreport what it did."""
    with pytest.raises(FirePerimetersForwardConfigError, match="static_lookup"):
        _validate_config(_config(max_days=max_days))


def test_the_documented_operator_command_validates() -> None:
    """`python -m agri_data_service.pipeline.direct.fire_perimeters --max-days 1`."""
    config = parse_args(["--max-days", "1"])

    assert config.max_days == FIRE_PERIMETERS_MAX_DAYS


def test_retry_attempts_outside_bounds_are_refused() -> None:
    with pytest.raises(FirePerimetersForwardConfigError, match="retry-attempts"):
        _validate_config(_config(retry_attempts=0))


def test_retry_max_below_retry_base_is_refused() -> None:
    with pytest.raises(FirePerimetersForwardConfigError, match="retry-max-seconds"):
        _validate_config(_config(retry_base_seconds=10.0, retry_max_seconds=1.0))


def test_a_non_finite_time_budget_is_refused() -> None:
    with pytest.raises(FirePerimetersForwardConfigError, match="time-budget-seconds"):
        _validate_config(_config(time_budget_seconds=float("inf")))


def test_the_parser_has_no_product_flag_because_this_lane_has_exactly_one() -> None:
    built = parser()

    with pytest.raises(SystemExit):
        built.parse_args(["--product", "fire-perimeters"])


def test_default_args_parse_to_the_documented_defaults() -> None:
    config = parse_args([])

    assert config.max_days == FIRE_PERIMETERS_DEFAULT_MAX_DAYS
    assert config.retry_attempts == FIRE_PERIMETERS_DEFAULT_RETRY_ATTEMPTS
    assert config.bbox is None


def test_the_bbox_override_is_carried_onto_the_config() -> None:
    """The extent the published version claims to cover; an unconfigured one is a refusal, not a skip.

    THE TWO ARGV TOKENS ARE THE TEST. `--bbox` and its value arrive separately, exactly as a shell
    hands them over, and the value's leading `-125` is not a pure negative number -- so argparse
    reads it as another option and raises "argument --bbox: expected one argument" unless
    `parse_args` rewrites it to `--bbox=-125,...` first. Passing one pre-joined `--bbox=...` token
    here would exercise the CLI without exercising the guard, which is how four sibling writers
    shipped the same crash.
    """
    config = parse_args(["--bbox", "-125,42,-116,49"])

    assert config.bbox == "-125,42,-116,49"


@pytest.mark.asyncio
async def test_the_memoized_watermark_answers_from_the_turns_one_capture() -> None:
    """Both sides of `_fill_static_day`'s race bracket see one immutable capture, so it never loops."""
    memo = MemoizedDirectWatermark(watermark=WATERMARK)

    before = await memo(None, None, today=date(2026, 9, 6))  # type: ignore[arg-type]
    after = await memo(None, None, today=date(2026, 9, 6))  # type: ignore[arg-type]

    assert before is WATERMARK
    assert after is WATERMARK
    assert before.instant == after.instant
    assert memo.reads == 2  # noqa: PLR2004 - the race bracket reads once before and once after


def test_the_registered_lane_is_still_the_static_watermark_driven_one_this_writer_substitutes_into() -> None:
    """If this lane ever stopped being a static_lookup, the whole no-day-loop design would be wrong."""
    lane = fire_perimeters_lane_registration()

    assert lane.nature == "static_lookup"
    assert lane.publication_lag_days == 0
    assert lane.watermark is not None
    assert lane.writer_ceiling is None


def test_the_direct_tier_ladder_is_the_base_rung_plus_its_three_derived_rungs() -> None:
    assert FIRE_PERIMETERS_DIRECT_KIND == "observed"
    assert FIRE_PERIMETERS_DIRECT_ALL_TIERS[0] == LANE_BASE_ZOOM_TIER
    assert len(FIRE_PERIMETERS_DIRECT_ALL_TIERS) == 4  # noqa: PLR2004 - the rung count IS the assertion
