"""Config validation, CLI defaults and the weekly settled-Tuesday arithmetic the forward writer bounds on.

No network, no object store and no DuckDB: every test here either validates a config in isolation,
resolves a week selection against a literal census, or exercises the before-the-floor no-op path,
which `run_drought_forward` returns from before it ever calls `ObjectStore.from_settings()`.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, cast

import pytest

from agri_data_service.pipeline.direct.drought.adapter import DirectDroughtError
from agri_data_service.pipeline.direct.drought.forward import (
    DROUGHT_DEFAULT_MAX_DAYS,
    DROUGHT_DEFAULT_RETRY_ATTEMPTS,
    DROUGHT_DIRECT_ALL_TIERS,
    DroughtForwardConfig,
    DroughtForwardConfigError,
    _selected_release_weeks,
    _validate_config,
    _weeks_this_turn,
    parse_args,
    parser,
    run_drought_forward,
)
from agri_data_service.pipeline.direct.drought.products import drought_lane_registration, newest_settled_tuesday

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.foundation.parquet.zoom import ZoomTier


def _config(**overrides: object) -> DroughtForwardConfig:
    base = {
        "max_days": 1,
        "time_budget_seconds": 60.0,
        "retry_attempts": 3,
        "retry_base_seconds": 1.0,
        "retry_max_seconds": 10.0,
        "contention_timeout_seconds": 30.0,
    }
    base.update(overrides)
    return DroughtForwardConfig(**base)  # type: ignore[arg-type]


def test_newest_settled_tuesday_steps_back_to_the_most_recent_tuesday() -> None:
    # 2026-08-20 is a Thursday; lag 4 lands on 2026-08-16 (Sunday), whose most recent Tuesday is 08-11.
    result = newest_settled_tuesday(today=date(2026, 8, 20), publication_lag_days=4)

    assert result == date(2026, 8, 11)
    assert result.weekday() == 1


def test_newest_settled_tuesday_is_idempotent_on_a_tuesday_itself() -> None:
    result = newest_settled_tuesday(today=date(2026, 8, 18), publication_lag_days=0)

    assert result == date(2026, 8, 18)


@pytest.mark.parametrize("max_days", [0, 6])
def test_max_days_outside_bounds_is_refused(max_days: int) -> None:
    with pytest.raises(DroughtForwardConfigError, match="--max-days"):
        _validate_config(_config(max_days=max_days))


def test_retry_max_below_retry_base_is_refused() -> None:
    with pytest.raises(DroughtForwardConfigError, match="retry-max-seconds"):
        _validate_config(_config(retry_base_seconds=10.0, retry_max_seconds=1.0))


def test_a_non_finite_time_budget_is_refused() -> None:
    with pytest.raises(DroughtForwardConfigError, match="time-budget-seconds"):
        _validate_config(_config(time_budget_seconds=float("inf")))


def test_the_parser_has_no_product_flag_because_this_lane_has_exactly_one() -> None:
    """Unlike climate/soil's `--product`, drought publishes one stream; the flag would be meaningless."""
    built = parser()

    with pytest.raises(SystemExit):
        built.parse_args(["--product", "drought"])


def test_default_args_parse_to_the_documented_defaults() -> None:
    config = parse_args([])

    assert config.max_days == DROUGHT_DEFAULT_MAX_DAYS
    assert config.retry_attempts == DROUGHT_DEFAULT_RETRY_ATTEMPTS


@pytest.mark.asyncio
async def test_a_turn_entirely_before_the_lane_floor_is_a_clean_noop() -> None:
    """`weeks` is empty, and `run_drought_forward` must return before touching the object store."""
    config = _config(today=date(2000, 1, 1))

    report = await run_drought_forward(config)

    assert report["status"] == "completed"
    assert report["days_published"] == 0
    assert report["results"] == []


def _weeks(**overrides: object) -> tuple[date, tuple[date, ...]]:
    """Resolve the release slice one config selects, against the drought lane's own registration."""
    lane = drought_lane_registration()
    settled_through = newest_settled_tuesday(today=date(2026, 9, 17), publication_lag_days=lane.publication_lag_days)
    return _selected_release_weeks(_config(**overrides), lane=lane, settled_through=settled_through)


def test_no_target_day_still_walks_the_bounded_backlog() -> None:
    first_day, weeks = _weeks()

    assert len(weeks) > 1
    assert weeks[0] == first_day
    assert all(week.weekday() == 1 for week in weeks)


def test_a_target_day_selects_exactly_that_one_settled_release() -> None:
    # 2026-08-11 is a Tuesday inside the lane's floor..settled window.
    first_day, weeks = _weeks(target_day=date(2026, 8, 11))

    assert first_day == date(2026, 8, 11)
    assert weeks == (date(2026, 8, 11),)


@pytest.mark.parametrize(
    ("target_day", "message"),
    [
        (date(2026, 8, 12), "is not a USDM release Tuesday"),
        (date(2000, 1, 4), "is before the source-owned floor"),
        (date(2030, 1, 1), "is after the settled source ceiling"),
    ],
)
def test_an_out_of_contract_target_day_is_refused_rather_than_silently_skipped(target_day: date, message: str) -> None:
    with pytest.raises(DroughtForwardConfigError, match=message):
        _weeks(target_day=target_day)


def test_the_target_day_flag_parses_as_an_iso_date() -> None:
    config = parse_args(["--target-day", "2026-08-11"])

    assert config.target_day == date(2026, 8, 11)
    assert parse_args([]).target_day is None


def _statuses(by_day: dict[str, str]) -> dict[ZoomTier, dict[date, PartitionDayStatus]]:
    """Render one census the way `_tier_status_for_weeks` does: the same status at every rung."""
    return {
        tier: {date.fromisoformat(day): cast("PartitionDayStatus", status) for day, status in by_day.items()}
        for tier in DROUGHT_DIRECT_ALL_TIERS
    }


def test_without_a_target_the_turn_publishes_only_owed_weeks_up_to_max_days() -> None:
    weeks = (date(2026, 8, 11), date(2026, 8, 18), date(2026, 8, 25))
    statuses = _statuses({"2026-08-11": "missing", "2026-08-18": "data", "2026-08-25": "missing"})

    selected, forced = _weeks_this_turn(statuses, weeks, config=_config(max_days=1))

    assert selected == (date(2026, 8, 25),), "newest owed week first, sliced by --max-days"
    assert forced is False


def test_a_target_already_published_at_every_rung_is_still_republished() -> None:
    """The flag's main use: an operator who does not trust a release that DID land."""
    weeks = (date(2026, 8, 18),)
    statuses = _statuses({"2026-08-18": "data"})

    selected, forced = _weeks_this_turn(statuses, weeks, config=_config(target_day=date(2026, 8, 18)))

    assert selected == (date(2026, 8, 18),), "a published target must not vanish into an empty selection"
    assert forced is True


def test_a_target_that_is_genuinely_owed_is_selected_without_being_called_forced() -> None:
    weeks = (date(2026, 8, 18),)
    statuses = _statuses({"2026-08-18": "missing"})

    selected, forced = _weeks_this_turn(statuses, weeks, config=_config(target_day=date(2026, 8, 18)))

    assert selected == (date(2026, 8, 18),)
    assert forced is False


def test_forcing_a_target_does_not_skip_the_census_refusals() -> None:
    """`_pending_weeks` is where a data/absence conflict is refused; a forced turn still runs it."""
    weeks = (date(2026, 8, 18),)
    statuses = _statuses({"2026-08-18": "conflict"})

    with pytest.raises(DirectDroughtError, match="data/absence conflict"):
        _weeks_this_turn(statuses, weeks, config=_config(target_day=date(2026, 8, 18)))


def test_the_target_day_help_text_says_it_republishes() -> None:
    """`--help` is the only contract an operator reads before running the repair."""
    help_text = parser().format_help()

    assert "--target-day" in help_text
    assert "republish" in help_text
