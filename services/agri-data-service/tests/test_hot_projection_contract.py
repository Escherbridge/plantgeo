"""Focused offline tests for the separate rolling Railway hot-projection contract."""

# ruff: noqa: PLR2004

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agri_data_service.execution.hot_projection import (
    HOT_PROJECTION_TARGET_KEY,
    HOT_PROJECTION_WINDOW_DAYS,
    HotProjectionForecastReceipt,
    HotProjectionManifest,
    HotProjectionPointer,
    HotProjectionPointerAdvance,
    HotProjectionSourceReceipt,
    apply_hot_projection_pointer_advance,
    build_hot_projection_manifest,
    prepare_hot_projection_pointer_advance,
    rolling_hot_projection_window,
)

AS_OF = datetime(2026, 7, 21, tzinfo=UTC)


def _checksum(character: str) -> str:
    return character * 64


def _source_receipt(  # noqa: PLR0913
    source_key: str,
    *,
    observed_from: datetime = AS_OF - timedelta(days=HOT_PROJECTION_WINDOW_DAYS),
    observed_until: datetime = AS_OF,
    observation_count: int = 10,
    source_checksum: str = _checksum("a"),
    receipt_checksum: str = _checksum("b"),
) -> HotProjectionSourceReceipt:
    return HotProjectionSourceReceipt(
        source_key=source_key,
        source_release_manifest_checksum=source_checksum,
        receipt_manifest_checksum=receipt_checksum,
        observed_from=observed_from,
        observed_until=observed_until,
        observation_count=observation_count,
    )


def _forecast_receipt(
    forecast_key: str = "daily-model-v1",
    *,
    issued_at: datetime = AS_OF - timedelta(hours=1),
    valid_from: datetime = AS_OF,
    valid_until: datetime = AS_OF + timedelta(days=14),
    forecast_count: int = 14,
) -> HotProjectionForecastReceipt:
    return HotProjectionForecastReceipt(
        forecast_key=forecast_key,
        forecast_manifest_checksum=_checksum("c"),
        forecast_receipt_checksum=_checksum("d"),
        issued_at=issued_at,
        valid_from=valid_from,
        valid_until=valid_until,
        forecast_count=forecast_count,
    )


def _manifest(*, as_of_time: datetime = AS_OF) -> HotProjectionManifest:
    return build_hot_projection_manifest(
        as_of_time=as_of_time,
        source_receipts=[
            _source_receipt(
                "usdm-weekly",
                observation_count=3,
                source_checksum=_checksum("e"),
                receipt_checksum=_checksum("f"),
            ),
            _source_receipt("nasa-power-daily", observation_count=10),
        ],
        forecast_receipts=[_forecast_receipt()],
    )


def test_rolling_window_is_exact_utc_half_open_year() -> None:
    window = rolling_hot_projection_window(AS_OF)

    assert window.observed_from == datetime(2025, 7, 21, tzinfo=UTC)
    assert window.observed_until == AS_OF
    assert window.observed_until - window.observed_from == timedelta(days=365)
    assert window.observed_from <= datetime(2025, 7, 21, tzinfo=UTC) < window.observed_until
    assert not (window.observed_from <= window.observed_until < window.observed_until)


def test_window_rejects_partial_days_and_naive_times() -> None:
    with pytest.raises(ValueError, match="exact UTC day boundary"):
        rolling_hot_projection_window(datetime(2026, 7, 21, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="timezone"):
        rolling_hot_projection_window(datetime(2026, 7, 21))  # noqa: DTZ001
    assert (
        rolling_hot_projection_window(datetime(2026, 7, 20, 18, tzinfo=timezone(timedelta(hours=-6)))).observed_until
        == AS_OF
    )
    with pytest.raises(ValueError, match="exact UTC day boundary"):
        rolling_hot_projection_window(datetime(2026, 7, 20, 17, tzinfo=timezone(timedelta(hours=-6))))


def test_manifest_binds_sorted_source_and_forecast_receipts_with_counts() -> None:
    manifest = _manifest()

    assert manifest.target_key == HOT_PROJECTION_TARGET_KEY
    assert [receipt.source_key for receipt in manifest.source_receipts] == ["nasa-power-daily", "usdm-weekly"]
    assert manifest.total_observation_count == 13
    assert manifest.total_forecast_count == 14
    assert manifest.window.observed_until == AS_OF


def test_projection_manifest_is_idempotent_and_receipt_checksum_bound() -> None:
    first = _manifest()
    second = _manifest()
    assert first.manifest_checksum == second.manifest_checksum

    payload = first.model_dump(mode="json")
    payload["source_receipts"][0]["receipt_manifest_checksum"] = _checksum("9")
    with pytest.raises(ValidationError, match="manifest_checksum"):
        HotProjectionManifest.model_validate(payload)


def test_projection_rejects_source_window_outside_the_exact_rolling_year() -> None:
    with pytest.raises(ValidationError, match="exact rolling hot-projection window"):
        build_hot_projection_manifest(
            as_of_time=AS_OF,
            source_receipts=[_source_receipt("nasa-power-daily", observed_from=AS_OF - timedelta(days=364))],
            forecast_receipts=[_forecast_receipt()],
        )


def test_projection_rejects_forecasts_before_or_far_beyond_the_hot_boundary() -> None:
    with pytest.raises(ValidationError, match="forecasts must start"):
        build_hot_projection_manifest(
            as_of_time=AS_OF,
            source_receipts=[_source_receipt("nasa-power-daily")],
            forecast_receipts=[_forecast_receipt(valid_from=AS_OF - timedelta(days=1))],
        )
    with pytest.raises(ValidationError, match="forecasts must start"):
        build_hot_projection_manifest(
            as_of_time=AS_OF,
            source_receipts=[_source_receipt("nasa-power-daily")],
            forecast_receipts=[_forecast_receipt(valid_from=AS_OF + timedelta(days=1))],
        )
    with pytest.raises(ValidationError, match="forecasts must start"):
        build_hot_projection_manifest(
            as_of_time=AS_OF,
            source_receipts=[_source_receipt("nasa-power-daily")],
            forecast_receipts=[_forecast_receipt(valid_until=AS_OF + timedelta(days=366))],
        )


def test_hot_contract_has_no_full_history_root_or_spool_dependency() -> None:
    module_path = Path(__import__("agri_data_service.execution.hot_projection", fromlist=["__file__"]).__file__)
    source = module_path.read_text(encoding="utf-8")

    assert "historical_promotion" not in source
    assert "HistoricalReleaseSetRoot" not in source
    assert "HistoricalPromotionSpool" not in source
    assert "httpx" not in source
    assert "sqlalchemy" not in source


def test_pointer_compare_and_swap_is_guarded_and_idempotent() -> None:
    manifest = _manifest()
    advance = prepare_hot_projection_pointer_advance(None, manifest)
    assert advance is not None
    assert advance.expected_generation == 0
    assert advance.expected_manifest_checksum is None

    first = apply_hot_projection_pointer_advance(None, advance, manifest)
    assert first.generation == 1
    assert first.manifest_checksum == manifest.manifest_checksum
    assert apply_hot_projection_pointer_advance(first, advance, manifest) == first
    assert prepare_hot_projection_pointer_advance(first, manifest) is None


def test_pointer_rejects_stale_or_wrong_manifest_compare_and_swap() -> None:
    first_manifest = _manifest()
    first_advance = prepare_hot_projection_pointer_advance(None, first_manifest)
    assert first_advance is not None
    first_pointer = apply_hot_projection_pointer_advance(None, first_advance, first_manifest)

    next_as_of = AS_OF + timedelta(days=1)
    next_manifest = build_hot_projection_manifest(
        as_of_time=next_as_of,
        source_receipts=[
            _source_receipt(
                "nasa-power-daily",
                observed_from=next_as_of - timedelta(days=HOT_PROJECTION_WINDOW_DAYS),
                observed_until=next_as_of,
            )
        ],
        forecast_receipts=[
            _forecast_receipt(
                issued_at=next_as_of - timedelta(hours=1),
                valid_from=next_as_of,
                valid_until=next_as_of + timedelta(days=14),
            )
        ],
    )
    next_advance = prepare_hot_projection_pointer_advance(first_pointer, next_manifest)
    assert next_advance is not None

    stale_pointer = HotProjectionPointer(
        generation=2,
        manifest_checksum=_checksum("8"),
        observed_until=AS_OF,
    )
    with pytest.raises(ValueError, match="compare-and-swap guard"):
        apply_hot_projection_pointer_advance(stale_pointer, next_advance, next_manifest)

    wrong_manifest_advance = HotProjectionPointerAdvance(
        expected_generation=first_pointer.generation,
        expected_manifest_checksum=first_pointer.manifest_checksum,
        next_generation=first_pointer.generation + 1,
        next_manifest_checksum=_checksum("7"),
    )
    with pytest.raises(ValueError, match="target the supplied"):
        apply_hot_projection_pointer_advance(first_pointer, wrong_manifest_advance, next_manifest)


def test_pointer_rejects_a_manifest_that_regresses_the_serving_window() -> None:
    first_manifest = _manifest()
    first_advance = prepare_hot_projection_pointer_advance(None, first_manifest)
    assert first_advance is not None
    first_pointer = apply_hot_projection_pointer_advance(None, first_advance, first_manifest)
    newer_manifest = build_hot_projection_manifest(
        as_of_time=AS_OF + timedelta(days=1),
        source_receipts=[
            _source_receipt(
                "nasa-power-daily",
                observed_from=AS_OF - timedelta(days=HOT_PROJECTION_WINDOW_DAYS - 1),
                observed_until=AS_OF + timedelta(days=1),
            )
        ],
        forecast_receipts=[
            _forecast_receipt(
                issued_at=AS_OF + timedelta(hours=23),
                valid_from=AS_OF + timedelta(days=1),
                valid_until=AS_OF + timedelta(days=15),
            )
        ],
    )
    newer_advance = prepare_hot_projection_pointer_advance(first_pointer, newer_manifest)
    assert newer_advance is not None
    newer_pointer = apply_hot_projection_pointer_advance(first_pointer, newer_advance, newer_manifest)

    with pytest.raises(ValueError, match="must not regress"):
        prepare_hot_projection_pointer_advance(newer_pointer, first_manifest)
