"""The harness the `/api/v1/ml` route tests share: a mirrored bucket, a real reader, a fake request.

Every read under test goes through the real `ObjectStore`, the real DuckDB session and the real
partition grammar. Only the transport is faked: the handlers are awaited directly, because the
Sanic test client needs `sanic-testing`, which is not a dependency of this service.
"""

from __future__ import annotations

import json as json_module
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Final, cast

import duckdb
import polars as pl
import pyarrow as pa
from test_forecast_lane_bootstrap import MirroredObjectStoreBackend

from plantgeo_ml_service.pipeline.availability_publisher import InMemoryPointerStore, reset_conditional_put_support
from plantgeo_ml_service.pipeline.duckdb_session import DuckDbSession
from plantgeo_ml_service.pipeline.forecast_lane_bootstrap import (
    availability_config_for,
    bootstrap_forecast_lane,
    publish_forecast_day,
    terminal_rows_for_day,
    write_run_receipt,
)
from plantgeo_ml_service.pipeline.object_store import ObjectStore
from plantgeo_ml_service.warehouse.streams import SIGNAL_STREAM, stream_schema

if TYPE_CHECKING:
    from pathlib import Path

    from sanic import Request
    from sanic.response import HTTPResponse

    from plantgeo_ml_service.pipeline.forecast_lane_bootstrap import ForecastDayReceipt

MOMENT: Final = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)

#: One hex digest, for a fixture that must name a receipt it does not otherwise care about.
FIXTURE_SHA: Final = "a" * 64


@dataclass(frozen=True, slots=True)
class ServingHarness:
    """A bucket on disk, its pointer store, and a factory for fresh bounded sessions over it."""

    root: Path
    store: ObjectStore
    pointers: InMemoryPointerStore

    def session(self) -> DuckDbSession:
        """Open one session over the mirrored directory; a route closes the session it is handed."""
        return DuckDbSession(connection=duckdb.connect(), bucket_uri=self.root.as_posix())

    @property
    def objects(self) -> dict[str, bytes]:
        """Return every object the bucket currently holds, by key."""
        backend = self.store.backend
        assert isinstance(backend, MirroredObjectStoreBackend)
        return backend.inner.objects


def build_serving(root: Path) -> ServingHarness:
    """Build a bucket rooted at `root`, mirrored to disk so DuckDB can read its partitions back."""
    reset_conditional_put_support()
    return ServingHarness(
        root=root,
        store=ObjectStore(backend=MirroredObjectStoreBackend(root=root)),
        pointers=InMemoryPointerStore(),
    )


def mount(harness: ServingHarness, monkeypatch: Any) -> None:
    """Point the blueprint's two seams at this harness, so no route opens a network client.

    `read_lane_availability` reads the pointer through `store.read_object`, not through the
    `PointerStore` a fixture published with: in production both name the same bucket
    (`BotoPointerStore` is handed the same S3 client as the backend), but this harness's
    `InMemoryPointerStore` is a separate dict, so a pointer a fixture published before calling
    `mount()` would otherwise be invisible to the route. Mirroring every currently-published
    pointer into the backend here reproduces the production coupling without changing it.
    """
    from plantgeo_ml_service.planes import routes  # noqa: PLC0415 - lazy import keeps this a test-only seam

    # The facade, not the store: this is what a deployed route holds, so it is what a test exercises.
    monkeypatch.setattr(routes, "open_store", harness.store.read_only)
    monkeypatch.setattr(routes, "open_serving_session", harness.session)
    for key, stored in harness.pointers.pointers.items():
        harness.store.backend.put(key, stored.payload, content_type="application/json")


def publish_lane(harness: ServingHarness, receipt: ForecastDayReceipt, *, layer: str, source_ceiling: date) -> None:
    """Advance one lane's availability pointer onto a day already written, for a lane with no ladder.

    `write_and_publish_forecast_rows` walks a whole horizon keyed on `observed_day`; a lane that
    partitions by `valid_day` (fire-risk) cannot use it, and every serving read now judges its day
    against the pointer, so a fixture that writes a partition and stops publishes nothing.
    """
    source_receipt = write_run_receipt(harness.store, {"lane": layer}, layer=layer, forecast_run_id=FIXTURE_SHA)
    rows = terminal_rows_for_day(
        receipt, layer=layer, source_receipt=source_receipt, source_ceiling=source_ceiling, published_at=MOMENT
    )
    bootstrap = bootstrap_forecast_lane(
        harness.store,
        rows,
        layer=layer,
        source_receipt=source_receipt,
        source_ceiling=source_ceiling,
        created_at=MOMENT,
    )
    publish_forecast_day(
        harness.store,
        harness.pointers,
        rows,
        layer=layer,
        config=availability_config_for(
            layer=layer,
            source_receipt=source_receipt,
            source_ceiling=source_ceiling,
            bootstrap_receipt=bootstrap,
        ),
        created_at=MOMENT,
    )


def request_with(**parameters: str) -> Request:
    """Return the only part of a Sanic request these handlers read: its multi-valued query string."""
    return cast("Request", SimpleNamespace(args={name: [value] for name, value in parameters.items()}))


def body_of(response: HTTPResponse) -> dict[str, Any]:
    """Return one Sanic JSON response's decoded body."""
    decoded = json_module.loads(bytes(response.body or b"").decode("utf-8"))
    assert isinstance(decoded, dict)
    return decoded


def signal_forecast_frame(  # noqa: PLR0913 - one keyword per fixture-row field is the contract
    *,
    issued_on: date,
    days: tuple[date, ...],
    cell_id: str,
    longitude: float,
    latitude: float,
    quantiles: tuple[float, ...] = (0.1, 0.5, 0.9),
) -> pl.DataFrame:
    """Build one cell's signal forecast rows: one row per published day and quantile."""
    schema = stream_schema(SIGNAL_STREAM, "forecast")
    rows = [
        {
            "support_key": "surface",
            "signal_name": "air_temperature_mean",
            "normalized_unit": "C",
            "cell_id": cell_id,
            "observed_day": day,
            "normalized_value": 12.0 + horizon + quantile,
            "observation_count": 0,
            "newest_observed_at": MOMENT,
            "coverage_fraction": None,
            "allowed_client_exposure": True,
            "cell_longitude": longitude,
            "cell_latitude": latitude,
            "forecast_run_id": FIXTURE_SHA,
            "random_seed": 7,
            "ensemble_size": 20,
            "horizon_days": horizon,
            "issued_on": issued_on,
            "quantile": quantile,
        }
        for horizon, day in enumerate(days, start=1)
        for quantile in quantiles
    ]
    return pl.from_arrow(pa.Table.from_pylist(rows, schema=schema.arrow_schema))  # type: ignore[return-value]


__all__ = [
    "FIXTURE_SHA",
    "MOMENT",
    "ServingHarness",
    "body_of",
    "build_serving",
    "mount",
    "publish_lane",
    "request_with",
    "signal_forecast_frame",
]
