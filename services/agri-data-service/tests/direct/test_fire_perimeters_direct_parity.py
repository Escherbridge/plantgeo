"""The counted PostgreSQL-vs-Parquet receipt that gates dropping `geo.features` for this layer.

The PostgreSQL side is a session double returning row shapes; the Parquet side is a real
`ObjectStore` over the shared in-memory `RecordingBackend`, so the listing, the completion rule and
the read-back are the production ones. No network, no DuckDB.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.pipeline.direct.fire_perimeters.parity import (
    FirePerimetersParityError,
    build_fire_perimeters_parity_receipt,
)
from agri_data_service.pipeline.direct.fire_perimeters.products import FIRE_PERIMETERS_DIRECT_KIND
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_SCHEMA, FIRE_PERIMETERS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

VERSION_DAY = date(2026, 9, 6)
CAPTURED_AT = datetime(2026, 9, 6, 14, 30, tzinfo=UTC)


def _wkb(identifier: str) -> bytes:
    """A per-identifier PLACEHOLDER, deliberately not parseable WKB, and safe only here.

    `parity.py` never reads a geometry -- it compares `md5` digests -- so distinct, cheap bytes are
    a better fixture than a real polygon: they make "same identifier, different shape" a one-line
    change. The moment a test hands a row to `fill_one_lane_day`, this stops being safe, because
    that path's z9/z5/z0 `GeometrySimplification` really does parse the bytes; see
    `test_fire_perimeters_direct_adapter.py::valid_square_wkb` for what such a test must use.
    """
    return b"\x01wkb-" + identifier.encode()


def _md5(payload: bytes) -> str:
    return hashlib.md5(payload, usedforsecurity=False).hexdigest()


@dataclass(frozen=True, slots=True)
class _LayerRow:
    is_public: bool


@dataclass(frozen=True, slots=True)
class _PerimeterRow:
    unique_fire_identifier: str
    observed_day: str | None
    geometry_md5: str


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def __iter__(self) -> Any:
        return iter(self._rows)


class SessionDouble:
    """Answers the layer-gate query and then the population query, in that fixed order."""

    def __init__(self, *, is_public: bool | None, rows: list[_PerimeterRow]) -> None:
        self._answers = [
            _Result([] if is_public is None else [_LayerRow(is_public=is_public)]),
            _Result(list(rows)),
        ]
        self.rollbacks = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:  # noqa: ARG002
        return self._answers.pop(0)

    async def rollback(self) -> None:
        self.rollbacks += 1


def _postgres_row(identifier: str, observed_day: str | None = "2026-08-30") -> _PerimeterRow:
    return _PerimeterRow(
        unique_fire_identifier=identifier,
        observed_day=observed_day,
        geometry_md5=_md5(_wkb(identifier)),
    )


def _parquet_row(identifier: str, observed_day: date | None = date(2026, 8, 30)) -> dict[str, Any]:
    return {
        "feature_id": f"direct:{identifier}",
        "unique_fire_identifier": identifier,
        "snapshot_day": VERSION_DAY,
        "observed_day": observed_day,
        "incident_name": f"{identifier} Fire",
        "irwin_id": None,
        "fire_discovery_at": datetime(2026, 7, 16, 1, 7, tzinfo=UTC),
        "polygon_at": datetime(2026, 8, 30, 18, 45, tzinfo=UTC),
        "gis_acres": 1234.5,
        "fire_cause": "Natural",
        "incident_type_category": "WF",
        "poo_state": "US-OR",
        "percent_contained": 30.0,
        "severity": "moderate",
        "status": "published",
        "data_available_at": None,
        "updated_at": CAPTURED_AT,
        "geometry_wkb": _wkb(identifier),
    }


def _store_with(*rows: dict[str, Any]) -> ObjectStore:
    store = ObjectStore(RecordingBackend())
    if not rows:
        return store
    table = pa.Table.from_pylist(list(rows), schema=FIRE_PERIMETERS_SCHEMA.arrow_schema)
    store.write_partition(
        table,
        layer=FIRE_PERIMETERS_STREAM,
        kind=FIRE_PERIMETERS_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=VERSION_DAY,
    )
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=len(rows), completed_at=CAPTURED_AT, run_id="test"),
        layer=FIRE_PERIMETERS_STREAM,
        kind=FIRE_PERIMETERS_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=VERSION_DAY,
    )
    return store


@pytest.mark.asyncio
async def test_an_identical_population_achieves_parity() -> None:
    session = SessionDouble(is_public=True, rows=[_postgres_row("OR-A"), _postgres_row("OR-B")])
    store = _store_with(_parquet_row("OR-A"), _parquet_row("OR-B"))

    receipt = await build_fire_perimeters_parity_receipt(session, store)  # type: ignore[arg-type]

    assert receipt.parity_achieved is True
    assert receipt.postgres_rows == 2
    assert receipt.parquet_rows == 2
    assert receipt.parquet_version_day == VERSION_DAY.isoformat()
    assert receipt.geometry_digest_mismatches == 0


@pytest.mark.asyncio
async def test_a_perimeter_postgres_holds_and_parquet_lacks_is_a_blocker() -> None:
    """D1's exact bar: under-coverage is a blocker, not a note."""
    session = SessionDouble(is_public=True, rows=[_postgres_row("OR-A"), _postgres_row("OR-B")])
    store = _store_with(_parquet_row("OR-A"))

    receipt = await build_fire_perimeters_parity_receipt(session, store)  # type: ignore[arg-type]

    assert receipt.parity_achieved is False
    assert receipt.missing_from_parquet == ("OR-B",)
    assert receipt.to_json_dict()["missing_from_parquet_count"] == 1


@pytest.mark.asyncio
async def test_a_perimeter_only_parquet_holds_is_reported_rather_than_absorbed() -> None:
    session = SessionDouble(is_public=True, rows=[_postgres_row("OR-A")])
    store = _store_with(_parquet_row("OR-A"), _parquet_row("OR-Z"))

    receipt = await build_fire_perimeters_parity_receipt(session, store)  # type: ignore[arg-type]

    assert receipt.parity_achieved is False
    assert receipt.extra_in_parquet == ("OR-Z",)


@pytest.mark.asyncio
async def test_the_undated_bucket_is_counted_on_both_sides_rather_than_hidden_in_a_total() -> None:
    """The retired day export DELETED these rows; they must show at every slider date instead."""
    session = SessionDouble(is_public=True, rows=[_postgres_row("OR-A", observed_day=None)])
    store = _store_with(_parquet_row("OR-A", observed_day=None))

    receipt = await build_fire_perimeters_parity_receipt(session, store)  # type: ignore[arg-type]

    assert receipt.postgres_undated_rows == 1
    assert receipt.parquet_undated_rows == 1
    assert receipt.parity_achieved is True


@pytest.mark.asyncio
async def test_an_undated_row_that_parquet_dated_is_an_observed_day_mismatch() -> None:
    session = SessionDouble(is_public=True, rows=[_postgres_row("OR-A", observed_day=None)])
    store = _store_with(_parquet_row("OR-A", observed_day=date(2026, 8, 30)))

    receipt = await build_fire_perimeters_parity_receipt(session, store)  # type: ignore[arg-type]

    assert receipt.parity_achieved is False
    assert receipt.observed_day_mismatches[0]["unique_fire_identifier"] == "OR-A"
    assert receipt.observed_day_mismatches[0]["postgres_observed_day"] is None


@pytest.mark.asyncio
async def test_a_geometry_digest_mismatch_is_counted_but_never_gates_the_cutover() -> None:
    """Neither PostGIS nor DuckDB promises byte-identical WKB, so this is evidence to look at."""
    session = SessionDouble(is_public=True, rows=[_postgres_row("OR-A")])
    store = _store_with({**_parquet_row("OR-A"), "geometry_wkb": b"\x01a-different-serialisation"})

    receipt = await build_fire_perimeters_parity_receipt(session, store)  # type: ignore[arg-type]

    assert receipt.geometry_digest_mismatches == 1
    assert receipt.parity_achieved is True


@pytest.mark.asyncio
async def test_no_published_version_at_all_reports_every_perimeter_missing_rather_than_raising() -> None:
    session = SessionDouble(is_public=True, rows=[_postgres_row("OR-A")])

    receipt = await build_fire_perimeters_parity_receipt(session, _store_with())  # type: ignore[arg-type]

    assert receipt.parquet_version_day is None
    assert receipt.parquet_rows == 0
    assert receipt.missing_from_parquet == ("OR-A",)
    assert receipt.parity_achieved is False


@pytest.mark.asyncio
async def test_zero_postgres_rows_is_refused_rather_than_reported_as_agreement() -> None:
    """A mistargeted DSN and an already-dropped relation are opposite conclusions, not one green tick."""
    session = SessionDouble(is_public=True, rows=[])

    with pytest.raises(FirePerimetersParityError, match="refusing"):
        await build_fire_perimeters_parity_receipt(session, _store_with())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_withdrawn_layer_is_named_in_the_refusal_rather_than_read_as_empty() -> None:
    """`is_public IS FALSE` makes the population query return nothing; that is not 'the layer is gone'."""
    session = SessionDouble(is_public=False, rows=[])

    with pytest.raises(FirePerimetersParityError, match="WITHDRAWN"):
        await build_fire_perimeters_parity_receipt(session, _store_with())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_missing_layer_row_is_named_separately_from_a_withdrawn_one() -> None:
    session = SessionDouble(is_public=None, rows=[])

    with pytest.raises(FirePerimetersParityError, match=r"no geo\.layers row"):
        await build_fire_perimeters_parity_receipt(session, _store_with())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_receipt_json_carries_the_layer_gate_this_writer_cannot_see() -> None:
    """The one place a layer withdrawn from publication is still observable after the cutover."""
    session = SessionDouble(is_public=True, rows=[_postgres_row("OR-A")])
    store = _store_with(_parquet_row("OR-A"))

    payload = (await build_fire_perimeters_parity_receipt(session, store)).to_json_dict()  # type: ignore[arg-type]

    assert payload["postgres_layer_is_public"] is True
    assert payload["parity_achieved"] is True
