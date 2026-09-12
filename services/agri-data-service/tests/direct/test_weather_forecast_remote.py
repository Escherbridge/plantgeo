"""Remote forecast publication, durable duty, recovery, and retention contracts."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from agri_data_service.ingest.http import BoundedResponse
from agri_data_service.pipeline.direct.weather_forecast.artifacts import ForecastSample, prepare_run
from agri_data_service.pipeline.direct.weather_forecast.duties import (
    DUTY_DESCRIPTORS,
    DiscoveryBatch,
    ForecastCandidate,
    PreparedRun,
    provider_retry_after,
    read_work_state,
    run_forward_duty,
    run_repair_duty,
    run_status_duty,
    status_object_key,
    status_pointer_key,
)
from agri_data_service.pipeline.direct.weather_forecast.remote_artifacts import (
    RemotePublishResult,
    RemoteRetentionPolicy,
    canonical_bytes,
    catalog_key,
    commit_key,
    delete_retired_entry,
    load_catalog,
    manifest_key,
    parquet_key,
    publish_prepared_run,
    read_remote_run,
    receipt_key,
    rollback_remote_run,
    source_key,
)
from agri_data_service.pipeline.parquet.availability_index import StoredAvailabilityObject
from agri_data_service.pipeline.parquet.objectstore import ListedObject
from agri_data_service.warehouse.weather_forecast.contracts import (
    ForecastRun,
    ForecastSeries,
    ForecastValue,
    SpatialSupport,
)

PRODUCT = "weather-remote-test"
START = datetime(2026, 9, 10, tzinfo=UTC)
DISCOVERY_LIMIT = 4
COOLDOWN_SECONDS = 120
COOLDOWN_CHECK_SECONDS = 60


@dataclass
class MemoryStorage:
    objects: dict[str, StoredAvailabilityObject] = field(default_factory=dict)
    version: int = 0
    raise_catalog_once: bool = False

    def _write(self, key: str, payload: bytes) -> None:
        self.version += 1
        self.objects[key] = StoredAvailabilityObject(payload=payload, etag=f'"{self.version}"')

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        stored = self.objects.get(key)
        if stored is not None and len(stored.payload) > max_bytes:
            raise ValueError("oversized test object")
        return stored

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        del content_type
        existing = self.objects.get(key)
        if existing is not None and existing.payload != payload:
            raise ValueError("immutable conflict")
        if existing is None:
            self._write(key, payload)

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool:
        del content_type
        if self.raise_catalog_once and key == catalog_key(PRODUCT):
            self.raise_catalog_once = False
            raise OSError("simulated catalog interruption")
        existing = self.objects.get(key)
        if expected_etag is None and existing is not None:
            return False
        if expected_etag is not None and (existing is None or existing.etag != expected_etag):
            return False
        self._write(key, payload)
        return True


@dataclass
class DeleteBackend:
    deleted: list[str] = field(default_factory=list)

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        del key, payload, content_type

    def delete(self, key: str) -> None:
        self.deleted.append(key)

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        del prefix
        yield from ()

    def size_of(self, key: str) -> int | None:
        del key
        return None

    def get(self, key: str) -> bytes | None:
        del key
        return None


def _source(run_id: str) -> bytes:
    return canonical_bytes({"run": run_id})


def _prepare(
    root: Path,
    run_id: str,
    initialized: datetime,
    *,
    product_id: str = PRODUCT,
) -> PreparedRun:
    source = _source(run_id)
    run = ForecastRun(
        run_id=run_id,
        product_id=product_id,
        provider="fixture-provider",
        model="fixture-model",
        model_version="fixture-model-v1",
        model_init_at=initialized,
        provider_issued_at=initialized + timedelta(minutes=5),
        fetched_at=initialized + timedelta(minutes=10),
        admitted_at=initialized + timedelta(minutes=20),
        published_at=initialized + timedelta(minutes=30),
        licence="fixture only",
        source_url="https://example.org/forecast",
        source_payload_sha256=sha256(source).hexdigest(),
        support=SpatialSupport(kind="sampled_point"),
        variables=("temperature_2m",),
    )
    values = tuple(
        ForecastValue(
            run_id=run_id,
            sample_id="boise",
            longitude=-116.2,
            latitude=43.6,
            variable="temperature_2m",
            unit="degC",
            valid_at=initialized + timedelta(hours=hour),
            lead_seconds=hour * 3600,
            value=float(hour),
            status="available",
        )
        for hour in range(2)
    )
    prepare_run(
        root=root,
        series=ForecastSeries(run=run, values=values),
        samples=(ForecastSample(sample_id="boise", longitude=-116.2, latitude=43.6),),
        start=initialized,
        end=initialized + timedelta(hours=2),
        source_payload=source,
    )
    return PreparedRun(root=root, product_id=product_id, run_id=run_id)


def _candidate(run_id: str, initialized: datetime) -> ForecastCandidate:
    return ForecastCandidate(
        product_id=PRODUCT,
        run_id=run_id,
        provider="fixture-provider",
        model="fixture-model",
        model_version="fixture-model-v1",
        model_init_at=initialized,
        provider_issued_at=initialized + timedelta(minutes=5),
    )


def test_remote_objects_are_content_addressed_and_pinned_reads_survive_rollback(tmp_path: Path) -> None:
    storage = MemoryStorage()
    first_local = _prepare(tmp_path, "run-a", START)
    first = publish_prepared_run(
        storage,
        local_root=first_local.root,
        product_id=PRODUCT,
        run_id=first_local.run_id,
        published_at=START + timedelta(hours=1),
    )
    replay = publish_prepared_run(
        storage,
        local_root=first_local.root,
        product_id=PRODUCT,
        run_id=first_local.run_id,
        published_at=START + timedelta(hours=2),
    )
    assert not replay.advanced
    assert replay.entry.published_at == first.entry.published_at
    assert manifest_key(first.entry.manifest_sha256) in storage.objects
    assert receipt_key(first.entry.receipt_sha256) in storage.objects
    assert source_key(first.entry.source_sha256) in storage.objects
    assert parquet_key(first.entry.parquet_sha256) in storage.objects

    second_local = _prepare(tmp_path, "run-b", START + timedelta(hours=6))
    second = publish_prepared_run(
        storage,
        local_root=second_local.root,
        product_id=PRODUCT,
        run_id=second_local.run_id,
        published_at=START + timedelta(hours=7),
    )
    manifest, pinned, _entry = read_remote_run(storage, product_id=PRODUCT, run_id="run-a")
    assert manifest.run.run_id == pinned.run.run_id == "run-a"
    assert pinned.run.published_at == first.entry.published_at
    rolled_back = rollback_remote_run(
        storage, product_id=PRODUCT, run_id="run-a", expected_active_run_id=second.entry.run_id
    )
    assert rolled_back.active_run_id == "run-a"


def test_commit_survives_pointer_interruption_and_preserves_first_publication_time(tmp_path: Path) -> None:
    storage = MemoryStorage(raise_catalog_once=True)
    prepared = _prepare(tmp_path, "run-a", START)
    first_publication = START + timedelta(hours=1)
    with pytest.raises(OSError, match="catalog interruption"):
        publish_prepared_run(
            storage,
            local_root=prepared.root,
            product_id=PRODUCT,
            run_id=prepared.run_id,
            published_at=first_publication,
        )
    assert commit_key(PRODUCT, "run-a") in storage.objects
    result = publish_prepared_run(
        storage,
        local_root=prepared.root,
        product_id=PRODUCT,
        run_id=prepared.run_id,
        published_at=first_publication + timedelta(hours=1),
    )
    assert result.entry.published_at == first_publication


def test_catalog_retention_is_bounded_and_cleanup_never_deletes_shared_blobs(tmp_path: Path) -> None:
    storage = MemoryStorage()
    other_product = "weather-remote-other"
    other = _prepare(tmp_path / "other", "run-a", START, product_id=other_product)
    publish_prepared_run(
        storage,
        local_root=other.root,
        product_id=other.product_id,
        run_id=other.run_id,
        published_at=START + timedelta(hours=1),
    )
    results: list[RemotePublishResult] = []
    for offset, run_id in enumerate(("run-a", "run-b", "run-c")):
        initialized = START + timedelta(hours=offset * 6)
        prepared = _prepare(tmp_path, run_id, initialized)
        result = publish_prepared_run(
            storage,
            local_root=prepared.root,
            product_id=PRODUCT,
            run_id=run_id,
            published_at=initialized + timedelta(hours=1),
            retention=RemoteRetentionPolicy(max_runs=2),
        )
        results.append(result)
    retired = results[-1].retired
    _stored, catalog = load_catalog(storage, PRODUCT)
    assert [entry.run_id for entry in catalog.runs] == ["run-b", "run-c"]
    assert [entry.run_id for entry in retired] == ["run-a"]
    backend = DeleteBackend()
    removed = delete_retired_entry(storage, backend, product_id=PRODUCT, entry=retired[0], object_prefix="prefix")
    assert removed == (commit_key(PRODUCT, "run-a"),)
    assert backend.deleted == [f"prefix/{commit_key(PRODUCT, 'run-a')}"]
    assert source_key(retired[0].source_sha256) in storage.objects
    assert parquet_key(retired[0].parquet_sha256) in storage.objects
    _other_manifest, other_series, _other_entry = read_remote_run(
        storage,
        product_id=other_product,
        run_id="run-a",
    )
    assert other_series.run.product_id == other_product


def test_retry_after_is_durable_and_blocks_discovery_until_expiry(tmp_path: Path) -> None:
    storage = MemoryStorage()
    prepared = _prepare(tmp_path, "run-a", START)
    calls = {"discover": 0, "materialize": 0}

    def deferred(_after: datetime | None, _limit: int) -> DiscoveryBatch:
        calls["discover"] += 1
        return DiscoveryBatch(candidates=(), retry_after_seconds=COOLDOWN_SECONDS)

    def materialize(_candidate: ForecastCandidate) -> PreparedRun:
        calls["materialize"] += 1
        return prepared

    now = START + timedelta(hours=2)
    first = run_forward_duty(storage, product_id=PRODUCT, now=now, discover=deferred, materialize=materialize)
    blocked = run_forward_duty(
        storage,
        product_id=PRODUCT,
        now=now + timedelta(seconds=COOLDOWN_CHECK_SECONDS),
        discover=deferred,
        materialize=materialize,
    )
    assert first.status == "cooldown_recorded"
    assert blocked.status == "cooldown"
    assert calls == {"discover": 1, "materialize": 0}
    _stored, state = read_work_state(storage, product_id=PRODUCT, now=now)
    assert state.cooldown_until == now + timedelta(seconds=COOLDOWN_SECONDS)

    def discovered(_after: datetime | None, limit: int) -> DiscoveryBatch:
        calls["discover"] += 1
        assert limit == DISCOVERY_LIMIT
        return DiscoveryBatch(candidates=(_candidate("run-a", START),))

    published = run_forward_duty(
        storage,
        product_id=PRODUCT,
        now=now + timedelta(seconds=COOLDOWN_SECONDS + 1),
        discover=discovered,
        materialize=materialize,
    )
    assert published.status == "published"
    assert calls == {"discover": 2, "materialize": 1}
    assert load_catalog(storage, PRODUCT)[1].active_run_id == "run-a"


def test_shared_retry_after_field_accepts_delta_and_http_date() -> None:
    now = START
    delta = cast(BoundedResponse, SimpleNamespace(retry_after="60"))
    dated = cast(BoundedResponse, SimpleNamespace(retry_after="Thu, 10 Sep 2026 00:02:00 GMT"))
    assert provider_retry_after(delta, now=now) == COOLDOWN_CHECK_SECONDS
    assert provider_retry_after(dated, now=now) == COOLDOWN_SECONDS


def test_materialized_model_version_must_match_discovered_run(tmp_path: Path) -> None:
    storage = MemoryStorage()
    prepared = _prepare(tmp_path, "run-a", START)
    candidate = _candidate("run-a", START).model_copy(update={"model_version": "fixture-model-v2"})

    result = run_forward_duty(
        storage,
        product_id=PRODUCT,
        now=START + timedelta(hours=2),
        discover=lambda _after, _limit: DiscoveryBatch(candidates=(candidate,)),
        materialize=lambda _candidate_value: prepared,
    )

    assert result.status == "publication_deferred"
    assert result.detail == "materialized forecast does not match the exact discovered run identity"
    assert load_catalog(storage, PRODUCT)[1].active_run_id is None


def test_repair_authors_exact_work_and_status_publishes_valid_time_coverage(tmp_path: Path) -> None:
    storage = MemoryStorage()
    prepared = _prepare(tmp_path, "run-a", START)
    result = publish_prepared_run(
        storage,
        local_root=prepared.root,
        product_id=PRODUCT,
        run_id=prepared.run_id,
        published_at=START + timedelta(hours=1),
    )
    stored = storage.objects[parquet_key(result.entry.parquet_sha256)]
    storage.objects[parquet_key(result.entry.parquet_sha256)] = StoredAvailabilityObject(
        payload=b"corrupt", etag=stored.etag
    )
    repaired = run_repair_duty(storage, product_id=PRODUCT, now=START + timedelta(hours=2))
    assert repaired.authored == 1
    _work_object, work = read_work_state(storage, product_id=PRODUCT, now=START + timedelta(hours=2))
    repair = next(item for item in work.pending if item.kind == "repair")
    assert repair.candidate == _candidate("run-a", START)

    status = run_status_duty(storage, product_id=PRODUCT, now=START + timedelta(hours=3))
    replay = run_status_duty(storage, product_id=PRODUCT, now=START + timedelta(hours=3))
    assert status.status == "status_published"
    assert replay.status == "status_replayed"
    pointer = storage.objects[status_pointer_key(PRODUCT)]
    pointer_payload = json.loads(pointer.payload)
    status_payload = storage.objects[status_object_key(pointer_payload["status_sha256"])]
    published_status = json.loads(status_payload.payload)
    assert published_status["runs"][0]["model_version"] == "fixture-model-v1"
    assert published_status["runs"][0]["valid_start"] == START.isoformat().replace("+00:00", "Z")
    assert published_status["runs"][0]["valid_end"] == (START + timedelta(hours=2)).isoformat().replace(
        "+00:00", "Z"
    )
    assert published_status["pending_repair"] == 1
    assert {descriptor.name for descriptor in DUTY_DESCRIPTORS} == {
        "weather-forecast-forward",
        "weather-forecast-repair",
        "weather-forecast-status",
    }
