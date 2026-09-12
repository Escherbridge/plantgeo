"""Config validation, and the two no-network/no-republish paths: unconfigured bbox, and a version the
object store already covers.

`fetch_watersheds_snapshot` and `ObjectStore.from_settings` are monkeypatched shut everywhere except
the currency-check test, which exercises `resolve_static_lane` for real against a store the test
seeds directly -- no test here opens a socket or a real object-store connection.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

import agri_data_service.pipeline.direct.watersheds.forward as forward_module
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
from agri_data_service.pipeline.direct.watersheds.adapter import WATERSHEDS_DIRECT_KIND
from agri_data_service.pipeline.direct.watersheds.forward import (
    WATERSHEDS_MAX_DAYS,
    WatershedsForwardConfig,
    WatershedsForwardConfigError,
    _validate_config,
    parse_args,
    parser,
    run_watersheds_forward,
)
from agri_data_service.pipeline.direct.watersheds.source import WatershedsSnapshotSource
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_SCHEMA, WATERSHEDS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

BBOX = "-125,42,-111,49"


def _config(**overrides: object) -> WatershedsForwardConfig:
    base: dict[str, object] = {"max_days": WATERSHEDS_MAX_DAYS, "bbox": None, "run_id": "test-run", "today": None}
    base.update(overrides)
    return WatershedsForwardConfig(**base)  # type: ignore[arg-type]


def test_max_days_other_than_one_is_refused() -> None:
    with pytest.raises(WatershedsForwardConfigError, match="--max-days"):
        _validate_config(_config(max_days=2))


def test_the_parser_has_no_product_flag_because_this_lane_has_exactly_one() -> None:
    """Unlike climate/soil's `--product`, watersheds publishes one stream; the flag would be meaningless."""
    built = parser()

    with pytest.raises(SystemExit):
        built.parse_args(["--product", "watersheds"])


def test_default_args_parse_to_max_days_one() -> None:
    config = parse_args([])

    assert config.max_days == WATERSHEDS_MAX_DAYS


def test_a_bbox_whose_first_ordinate_is_negative_survives_two_argv_tokens() -> None:
    """THE TWO ARGV TOKENS ARE THE TEST, and every western-US bbox starts with a negative longitude.

    `--bbox` and its value arrive separately, exactly as a shell hands them over, and the value's
    leading `-125` is not a pure negative number -- so argparse reads it as another option and
    raises "argument --bbox: expected one argument" unless `parse_args` rewrites it to
    `--bbox=-125,...` first. Passing one pre-joined `--bbox=...` token here would exercise the CLI
    without exercising the guard, which is how four sibling writers shipped the same crash.
    """
    config = parse_args(["--bbox", "-125,42,-116,49"])

    assert config.bbox == "-125,42,-116,49"


@pytest.mark.asyncio
async def test_an_unconfigured_bbox_is_a_clean_noop_that_opens_no_object_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DO NOT DELETE. Mirrors the before-any-network no-op `ingest/watersheds.py`'s own ingestion
    job, and `pipeline/direct/drought/forward.py`'s before-the-floor no-op, both return."""
    monkeypatch.setattr(forward_module, "resolve_bounded_bbox", lambda override: None)  # noqa: ARG005

    def _fail_from_settings(*args: object, **kwargs: object) -> ObjectStore:  # noqa: ARG001 - never called, by assertion
        raise AssertionError("ObjectStore.from_settings() must never be called when INGEST_BBOX is unconfigured")

    monkeypatch.setattr(ObjectStore, "from_settings", staticmethod(_fail_from_settings))

    report = await run_watersheds_forward(_config())

    assert report["status"] == "completed"
    assert report["published"] is False
    assert "INGEST_BBOX" in str(report["detail"])


def _minimal_table(huc12: str, *, release_day: date) -> pa.Table:
    row = {
        "huc12": huc12,
        "name": None,
        "areasqkm": None,
        "tohuc": None,
        "states": None,
        "hutype": None,
        "source": "USGS NHDPlus HR WBDHU12",
        "observed_at": None,
        "data_available_at": None,
        "release_day": release_day,
        "feature_id": f"direct:{huc12}",
        "geom": b"\x00\x01",
    }
    return pa.Table.from_pylist([row], schema=WATERSHEDS_SCHEMA.arrow_schema)


@pytest.mark.asyncio
async def test_a_version_the_store_already_holds_is_reported_current_not_republished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_day = date(2026, 8, 7)
    export_instant = datetime(2026, 8, 8, tzinfo=UTC)
    watermark = SourceWatermark(day=version_day, basis="test", instant=datetime(2026, 8, 7, tzinfo=UTC))

    async def fake_fetch(*, bbox: str) -> WatershedsSnapshotSource:
        return WatershedsSnapshotSource(
            bbox=bbox, accepted=(), rejected_count=0, fetched_at=datetime.now(UTC), watermark=watermark
        )

    backend = RecordingBackend(stamps_puts_at=export_instant)
    store = ObjectStore(backend)
    store.write_partition(
        _minimal_table("170900011201", release_day=version_day),
        layer=WATERSHEDS_STREAM,
        kind=WATERSHEDS_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=version_day,
    )
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=1, completed_at=export_instant, run_id="setup"),
        layer=WATERSHEDS_STREAM,
        kind=WATERSHEDS_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=version_day,
    )

    monkeypatch.setattr(forward_module, "resolve_bounded_bbox", lambda override: BBOX)  # noqa: ARG005
    monkeypatch.setattr(forward_module, "fetch_watersheds_snapshot", fake_fetch)
    monkeypatch.setattr(ObjectStore, "from_settings", staticmethod(lambda source=None: store))  # noqa: ARG005 - keeps from_settings' own signature

    report = await run_watersheds_forward(_config())

    assert report["published"] is False
    assert report["state"] == "current"
