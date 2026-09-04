"""Config validation, CLI defaults and the weekly settled-Tuesday arithmetic the forward writer bounds on.

No network, no object store and no DuckDB: every test here either validates a config in isolation or
exercises the before-the-floor no-op path, which `run_drought_forward` returns from before it ever
calls `ObjectStore.from_settings()`.
"""

from __future__ import annotations

from datetime import date

import pytest

from agri_data_service.pipeline.direct.drought.forward import (
    DROUGHT_DEFAULT_MAX_DAYS,
    DROUGHT_DEFAULT_RETRY_ATTEMPTS,
    DroughtForwardConfig,
    DroughtForwardConfigError,
    _validate_config,
    parse_args,
    parser,
    run_drought_forward,
)
from agri_data_service.pipeline.direct.drought.products import newest_settled_tuesday


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
