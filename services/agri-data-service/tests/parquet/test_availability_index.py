from __future__ import annotations

import io
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from click.testing import CliRunner

import agri_data_service.interface.cli.data as data_cli_module
from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.interface.cli.data import data
from agri_data_service.pipeline.parquet.availability_index import (
    EVIDENCE_OBJECT_MAX_BYTES,
    GENERATION_MAX_BYTES,
    POINTER_MAX_BYTES,
    AvailabilityChecksumError,
    AvailabilityConflictError,
    AvailabilityIdentity,
    AvailabilityMalformedError,
    AvailabilityPointer,
    AvailabilityRow,
    AvailabilityUnavailableError,
    BootstrapInventoryEvidence,
    BootstrapRequest,
    BotoAvailabilityStorage,
    EvidenceReceipt,
    SourceEvidence,
    StoredAvailabilityObject,
    TerminalEvidence,
    availability_lane_identity,
    availability_pointer_key,
    build_bootstrap_inventory_evidence,
    build_source_evidence,
    build_terminal_evidence,
    compute_verified_source_inventory_root,
    load_bootstrap_request,
    read_latest_availability,
)
from agri_data_service.pipeline.parquet.availability_index import (
    _bootstrap_availability_owned as bootstrap_availability,
)
from agri_data_service.warehouse.schemas.availability_index import (
    AVAILABILITY_INDEX_SCHEMA,
    AVAILABILITY_REQUIRED_RUNGS,
    AVAILABILITY_SCHEMA_VERSION,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_LANE_ROOT = "layer=test-lane/kind=observed"
_CREATED_AT = datetime(2026, 9, 1, 12, tzinfo=UTC)
_ABSENCE_REASON = "verified_source_empty"


@dataclass
class MemoryAvailabilityStorage:
    objects: dict[str, StoredAvailabilityObject] = field(default_factory=dict)
    _version: int = 0

    def seed(self, key: str, payload: bytes) -> EvidenceReceipt:
        self._version += 1
        self.objects[key] = StoredAvailabilityObject(
            payload=payload,
            etag=f'"{self._version}"',
            version_id=f"version-{self._version}",
        )
        return EvidenceReceipt(key=key, sha256=sha256_digest(payload))

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        stored = self.objects.get(key)
        if stored is not None and len(stored.payload) > max_bytes:
            raise AvailabilityUnavailableError("availability_oversized", key)
        return stored

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        del content_type
        existing = self.objects.get(key)
        if existing is not None and existing.payload != payload:
            raise RuntimeError("immutable conflict")
        if existing is None:
            self.seed(key, payload)

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool:
        del content_type
        existing = self.objects.get(key)
        if expected_etag is None and existing is not None:
            return False
        if expected_etag is not None and (existing is None or existing.etag != expected_etag):
            return False
        self.seed(key, payload)
        return True


def test_schema_freezes_native_receipt_list_and_canonical_rungs() -> None:
    data_receipts = AVAILABILITY_INDEX_SCHEMA.field("data_receipts")
    expected = (
        ("lane", pa.string(), False),
        ("product", pa.string(), False),
        ("nature", pa.string(), False),
        ("day", pa.date32(), False),
        ("rung", pa.int16(), False),
        ("terminal_state", pa.string(), False),
        ("row_count", pa.int64(), False),
        ("source_receipt_key", pa.string(), False),
        ("source_receipt_sha256", pa.string(), False),
        ("terminal_receipt_key", pa.string(), False),
        ("terminal_receipt_sha256", pa.string(), False),
        (
            "data_receipts",
            pa.list_(
                pa.field(
                    "item",
                    pa.struct(
                        [
                            pa.field("key", pa.string(), nullable=False),
                            pa.field("sha256", pa.string(), nullable=False),
                        ]
                    ),
                    nullable=False,
                )
            ),
            False,
        ),
        ("completion_receipt_key", pa.string(), True),
        ("completion_receipt_sha256", pa.string(), True),
        ("absence_reason", pa.string(), True),
        ("source_ceiling", pa.date32(), False),
        ("published_at", pa.timestamp("us", tz="UTC"), False),
    )

    assert AVAILABILITY_SCHEMA_VERSION == "1"
    assert AVAILABILITY_REQUIRED_RUNGS == (0, 5, 9, 13)
    assert tuple((field.name, field.type, field.nullable) for field in AVAILABILITY_INDEX_SCHEMA) == expected
    assert data_receipts.nullable is False
    assert pa.types.is_list(data_receipts.type)
    assert data_receipts.type.value_field.nullable is False
    assert pa.types.is_struct(data_receipts.type.value_type)
    assert [field.name for field in data_receipts.type.value_type] == ["key", "sha256"]
    assert all(field.nullable is False for field in data_receipts.type.value_type)


def test_governed_absence_is_selectable_only_as_a_complete_ladder() -> None:
    store = MemoryAvailabilityStorage()
    manifest = store.seed("evidence/manifest.json", b"manifest")
    identity = _identity(manifest)
    day = date(2026, 8, 30)
    bootstrap_input = build_bootstrap_inventory_evidence(
        BootstrapInventoryEvidence(identity=identity, source_ceiling=day, object_receipts=(manifest,))
    )
    store.seed(bootstrap_input.receipt.key, bootstrap_input.payload)
    source_object = store.seed("source/empty-response.bin", b"source-empty")
    source_evidence = build_source_evidence(
        SourceEvidence(
            identity=identity,
            day=day,
            source_ceiling=day,
            object_receipts=(source_object,),
        )
    )
    source = store.seed(source_evidence.receipt.key, source_evidence.payload)
    reason = _ABSENCE_REASON
    rows = tuple(
        AvailabilityRow(
            lane=identity.lane,
            product=identity.product,
            nature=identity.nature,
            day=day,
            rung=rung,
            terminal_state="governed_absence",
            row_count=0,
            source_receipt=source,
            terminal_receipt=_seed_absence_terminal(
                store,
                identity=identity,
                source=source,
                day=day,
                rung=rung,
            ),
            data_receipts=(),
            completion_receipt=None,
            absence_reason=reason,
            source_ceiling=day,
            published_at=_CREATED_AT - timedelta(hours=1),
        )
        for rung in AVAILABILITY_REQUIRED_RUNGS
    )
    bootstrap_availability(
        store,
        BootstrapRequest(
            identity=identity,
            source_ceiling=day,
            created_at=_CREATED_AT,
            input_receipts=(bootstrap_input.receipt,),
            rows=rows,
            input_sha256="a" * 64,
        ),
    )

    index = read_latest_availability(
        store,
        lane_root=_LANE_ROOT,
        expected_lane="test-lane",
        expected_product="test-product",
        expected_nature="daily_series",
        expected_required_rungs=AVAILABILITY_REQUIRED_RUNGS,
        required_source_ceiling=day,
    )

    assert index.selectable_days() == (day,)
    assert {row.terminal_state for row in index.rows} == {"governed_absence"}


def test_reads_fail_closed_on_missing_stale_malformed_and_checksum_invalid_evidence() -> None:
    missing = MemoryAvailabilityStorage()
    with pytest.raises(AvailabilityUnavailableError) as missing_error:
        read_latest_availability(missing, lane_root=_LANE_ROOT)
    assert missing_error.value.code == "availability_missing"

    store, pointer = _published_bootstrap()
    with pytest.raises(AvailabilityUnavailableError) as stale_error:
        read_latest_availability(
            store,
            lane_root=_LANE_ROOT,
            required_source_ceiling=pointer.source_ceiling + timedelta(days=1),
        )
    assert stale_error.value.code == "availability_stale"

    pointer_key = availability_pointer_key(_LANE_ROOT)
    original_pointer = store.objects[pointer_key]
    store.seed(pointer_key, b"{}")
    with pytest.raises(AvailabilityMalformedError):
        read_latest_availability(store, lane_root=_LANE_ROOT)

    store.objects[pointer_key] = original_pointer
    generation = store.objects[pointer.generation_key]
    store.seed(pointer.generation_key, b"!" + generation.payload[1:])
    with pytest.raises(AvailabilityChecksumError):
        read_latest_availability(store, lane_root=_LANE_ROOT)


def test_reader_rejects_pointer_parquet_cross_binding_mutation() -> None:
    store, _ = _published_bootstrap()
    pointer_key = availability_pointer_key(_LANE_ROOT)
    document = json.loads(store.objects[pointer_key].payload)
    document["product"] = "other-product"
    store.seed(pointer_key, canonical_json(document).encode())

    with pytest.raises(AvailabilityUnavailableError) as error:
        read_latest_availability(store, lane_root=_LANE_ROOT)

    assert error.value.code == "availability_stale"


def test_lane_identity_decomposes_a_lane_root_and_refuses_anything_else() -> None:
    """The public inverse of `objectstore.availability_lane_root`, so a reader need not respell it."""
    assert availability_lane_identity(_LANE_ROOT) == ("test-lane", "observed")

    with pytest.raises(ValueError, match="lane_root must be exactly"):
        availability_lane_identity("layer=test-lane/kind=hourly")


def test_exact_json_loader_rejects_duplicate_object_keys(tmp_path: Path) -> None:
    payload = b'{"schema_version":"first","schema_version":"second"}'
    input_path = tmp_path / "duplicate.json"
    input_path.write_bytes(payload)

    with pytest.raises(ValueError, match="duplicate key"):
        load_bootstrap_request(
            input_path,
            expected_sha256=sha256_digest(payload),
            expected_row_count=1,
        )


def test_boto_adapter_uses_conditional_put_headers() -> None:
    client = RecordingS3Client()
    storage = BotoAvailabilityStorage(bucket="bucket", client=client, prefix="sandbox/")

    storage.put_immutable("object.json", b"immutable", content_type="application/json")
    created = storage.compare_and_swap(
        "pointer.json",
        b"first",
        expected_etag=None,
        content_type="application/json",
    )
    advanced = storage.compare_and_swap(
        "pointer.json",
        b"second",
        expected_etag='"etag-1"',
        content_type="application/json",
    )

    assert created is True
    assert advanced is True
    assert client.puts[0]["IfNoneMatch"] == "*"
    assert client.puts[1]["IfNoneMatch"] == "*"
    assert client.puts[2]["IfMatch"] == '"etag-1"'
    assert all(call["Key"].startswith("sandbox/") for call in client.puts)


def test_boto_immutable_retries_conditional_request_conflict_then_creates() -> None:
    client = ScriptedS3Client(put_results=["ConditionalRequestConflict", None], visible=[None])
    storage = BotoAvailabilityStorage(bucket="bucket", client=client)

    storage.put_immutable("object.json", b"immutable", content_type="application/json")

    expected_puts = 2
    assert len(client.puts) == expected_puts
    assert client.gets == 1


def test_boto_immutable_retries_visible_409_then_adopts_only_after_412() -> None:
    client = ScriptedS3Client(
        put_results=["409", "412"],
        visible=[b"immutable", b"immutable"],
    )
    storage = BotoAvailabilityStorage(bucket="bucket", client=client)

    storage.put_immutable("object.json", b"immutable", content_type="application/json")

    expected_attempts = 2
    assert len(client.puts) == expected_attempts
    assert client.gets == expected_attempts


def test_boto_immutable_exhausts_bounded_conditional_request_conflicts() -> None:
    client = ScriptedS3Client(
        put_results=["409", "409", "409"],
        visible=[b"immutable", b"immutable", b"immutable"],
    )
    storage = BotoAvailabilityStorage(bucket="bucket", client=client)

    with pytest.raises(AvailabilityConflictError, match="bounded retries"):
        storage.put_immutable("object.json", b"immutable", content_type="application/json")

    expected_attempts = 3
    assert len(client.puts) == expected_attempts
    assert client.gets == expected_attempts


@pytest.mark.parametrize("code", ["412", "PreconditionFailed"])
def test_boto_immutable_adopts_only_identical_bytes_after_precondition(code: str) -> None:
    client = ScriptedS3Client(put_results=[code], visible=[b"immutable"])
    storage = BotoAvailabilityStorage(bucket="bucket", client=client)

    storage.put_immutable("object.json", b"immutable", content_type="application/json")

    assert len(client.puts) == 1


def test_boto_immutable_rejects_different_bytes_after_precondition() -> None:
    client = ScriptedS3Client(put_results=["412"], visible=[b"different"])
    storage = BotoAvailabilityStorage(bucket="bucket", client=client)

    with pytest.raises(AvailabilityConflictError, match="different bytes"):
        storage.put_immutable("object.json", b"immutable", content_type="application/json")


@pytest.mark.parametrize("code", ["409", "ConditionalRequestConflict", "412", "PreconditionFailed"])
def test_boto_pointer_cas_conflicts_return_false(code: str) -> None:
    client = ScriptedS3Client(put_results=[code])
    storage = BotoAvailabilityStorage(bucket="bucket", client=client)

    assert (
        storage.compare_and_swap(
            "pointer.json",
            b"pointer",
            expected_etag='"etag-1"',
            content_type="application/json",
        )
        is False
    )
    assert client.gets == 0


@pytest.mark.parametrize(
    ("key", "max_bytes"),
    [
        ("availability/_LATEST.json", POINTER_MAX_BYTES),
        ("availability/generation.parquet", GENERATION_MAX_BYTES),
        ("source/object.bin", EVIDENCE_OBJECT_MAX_BYTES),
    ],
)
def test_boto_read_rejects_oversized_content_length_before_body_read(
    key: str,
    max_bytes: int,
) -> None:
    body = BytesBody(b"small")
    client = ReadResponseClient(
        response={
            "Body": body,
            "ContentLength": max_bytes + 1,
            "ETag": '"etag"',
        }
    )
    storage = BotoAvailabilityStorage(bucket="bucket", client=client)

    with pytest.raises(AvailabilityUnavailableError) as error:
        storage.read(key, max_bytes=max_bytes)

    assert error.value.code == "availability_oversized"
    assert body.read_calls == 0
    assert body.closed is True


@pytest.mark.parametrize(
    ("payload", "content_length"),
    [(b"lying body", 4), (b"short", 8)],
)
def test_boto_read_rejects_lying_or_short_body(payload: bytes, content_length: int) -> None:
    body = BytesBody(payload)
    client = ReadResponseClient(
        response={
            "Body": body,
            "ContentLength": content_length,
            "ETag": '"etag"',
        }
    )
    storage = BotoAvailabilityStorage(bucket="bucket", client=client)

    with pytest.raises(AvailabilityMalformedError, match="body length"):
        storage.read("object.json", max_bytes=32)

    assert body.closed is True


def test_boto_read_retains_etag_and_optional_version_id() -> None:
    body = BytesBody(b"bounded")
    client = ReadResponseClient(
        response={
            "Body": body,
            "ContentLength": len(body.payload),
            "ETag": '"etag"',
            "VersionId": "version-7",
        }
    )
    storage = BotoAvailabilityStorage(bucket="bucket", client=client)

    stored = storage.read("object.json", max_bytes=len(body.payload))

    assert stored == StoredAvailabilityObject(
        payload=body.payload,
        etag='"etag"',
        version_id="version-7",
    )
    assert body.closed is True


def _write_bootstrap_cli_input(tmp_path: Path) -> tuple[Path, bytes, int]:
    manifest = EvidenceReceipt(key="evidence/manifest.json", sha256=sha256_digest(b"manifest"))
    identity = _identity(manifest)
    day = date(2026, 8, 30)
    bootstrap_input = build_bootstrap_inventory_evidence(
        BootstrapInventoryEvidence(identity=identity, source_ceiling=day, object_receipts=(manifest,))
    )
    rows = _offline_published_rows(identity, day)
    document = {
        "created_at": "2026-09-01T12:00:00.000000Z",
        "input_receipts": [bootstrap_input.receipt.to_wire()],
        "lane": identity.lane,
        "lane_root": identity.lane_root,
        "nature": identity.nature,
        "product": identity.product,
        "required_rungs": list(identity.required_rungs),
        "rows": rows,
        "schema_version": "availability-bootstrap-input-v1",
        "source_ceiling": day.isoformat(),
        "verified_source_inventory_root": identity.verified_source_inventory_root,
    }
    payload = canonical_json(document).encode()
    input_path = tmp_path / "bootstrap.json"
    input_path.write_bytes(payload)
    return input_path, payload, len(rows)


def test_bootstrap_cli_is_offline_by_default(tmp_path: Path) -> None:
    input_path, payload, row_count = _write_bootstrap_cli_input(tmp_path)

    result = CliRunner().invoke(
        data,
        [
            "availability-bootstrap",
            "--input",
            str(input_path),
            "--input-sha256",
            sha256_digest(payload),
            "--expected-row-count",
            str(row_count),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["dry_run"] is True
    assert report["apply"] is False
    assert "pointer" not in report


def test_bootstrap_cli_apply_requires_loader_database_before_object_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path, payload, row_count = _write_bootstrap_cli_input(tmp_path)
    monkeypatch.setattr(data_cli_module.settings, "local_source_loader_database_url", None)
    monkeypatch.setattr(data_cli_module.settings, "database_url", None)

    def refuse_store(_cls: type[BotoAvailabilityStorage]) -> BotoAvailabilityStorage:
        raise AssertionError("object store must not be constructed before loader DB validation")

    monkeypatch.setattr(BotoAvailabilityStorage, "from_settings", classmethod(refuse_store))
    result = CliRunner().invoke(
        data,
        [
            "availability-bootstrap",
            "--input",
            str(input_path),
            "--input-sha256",
            sha256_digest(payload),
            "--expected-row-count",
            str(row_count),
            "--apply",
        ],
    )

    assert result.exit_code == 1
    assert "LOCAL_SOURCE_LOADER_DATABASE_URL" in result.output


def test_bootstrap_cli_apply_uses_loader_session_and_guarded_public_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path, payload, row_count = _write_bootstrap_cli_input(tmp_path)
    session = object()
    store = object()
    calls: list[tuple[object, object, BootstrapRequest]] = []
    database_url = "postgresql+asyncpg://user:password@loader.example:5432/plantgeo"

    @asynccontextmanager
    async def loader_session(resolved_database_url: str) -> AsyncIterator[object]:
        assert resolved_database_url == database_url
        yield session

    async def guarded_apply(
        held_session: object,
        held_store: object,
        request: BootstrapRequest,
    ) -> object:
        calls.append((held_session, held_store, request))
        return SimpleNamespace(pointer=SimpleNamespace(to_wire=dict), advanced=True, attempts=1)

    monkeypatch.setattr(
        data_cli_module.settings,
        "local_source_loader_database_url",
        database_url,
    )
    monkeypatch.setattr(data_cli_module, "local_source_loader_session", loader_session)

    def store_from_settings(_cls: type[BotoAvailabilityStorage]) -> object:
        return store

    monkeypatch.setattr(data_cli_module.BotoAvailabilityStorage, "from_settings", classmethod(store_from_settings))
    monkeypatch.setattr(data_cli_module, "bootstrap_availability", guarded_apply)
    result = CliRunner().invoke(
        data,
        [
            "availability-bootstrap",
            "--input",
            str(input_path),
            "--input-sha256",
            sha256_digest(payload),
            "--expected-row-count",
            str(row_count),
            "--apply",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0][0:2] == (session, store)


def test_unlocked_availability_cores_have_no_production_callers() -> None:
    service_root = Path(__file__).parents[2]
    source_root = service_root / "src" / "agri_data_service"
    owner = source_root / "pipeline" / "parquet" / "availability_index.py"
    production_files = (*source_root.rglob("*.py"), *(service_root / "scripts").rglob("*.py"))
    private_names = (
        "_bootstrap_availability_owned",
        "_publish_availability_owned",
        "_rollback_availability_owned",
    )
    offenders = {
        str(path.relative_to(service_root)): tuple(name for name in private_names if name in path.read_text())
        for path in production_files
        if path != owner and any(name in path.read_text() for name in private_names)
    }

    assert offenders == {}


def test_publish_cli_is_offline_by_default(tmp_path: Path) -> None:
    manifest = EvidenceReceipt(key="evidence/manifest.json", sha256=sha256_digest(b"manifest"))
    identity = _identity(manifest)
    day = date(2026, 8, 31)
    rows = _offline_published_rows(identity, day)
    document = {
        "bootstrap_receipt_key": f"{_LANE_ROOT}/availability/bootstrap/receipt={'b' * 64}.json",
        "bootstrap_receipt_sha256": "b" * 64,
        "created_at": "2026-09-01T12:00:00.000000Z",
        "lane": identity.lane,
        "lane_root": identity.lane_root,
        "nature": identity.nature,
        "product": identity.product,
        "required_rungs": list(identity.required_rungs),
        "rows": rows,
        "schema_version": "availability-publication-input-v1",
        "source_ceiling": day.isoformat(),
        "verified_source_inventory_root": identity.verified_source_inventory_root,
    }
    payload = canonical_json(document).encode()
    input_path = tmp_path / "publication.json"
    input_path.write_bytes(payload)

    result = CliRunner().invoke(
        data,
        [
            "availability-publish",
            "--input",
            str(input_path),
            "--input-sha256",
            sha256_digest(payload),
            "--expected-row-count",
            str(len(rows)),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["dry_run"] is True
    assert report["apply"] is False
    assert "pointer" not in report


# --- The published-empty rung: a derived rung that generalised every base row away ---------------

_BASE_RUNG = AVAILABILITY_REQUIRED_RUNGS[-1]
_EMPTY_RUNG = AVAILABILITY_REQUIRED_RUNGS[0]
_EMPTY_DAY = date(2026, 8, 30)


def _empty_rung_row(rung: int) -> AvailabilityRow:
    """One published row holding no rows at all, bound to a receipt rather than to data."""
    return AvailabilityRow(
        lane="test-lane",
        product="test-product",
        nature="daily_series",
        day=_EMPTY_DAY,
        rung=rung,
        terminal_state="published",
        row_count=0,
        source_receipt=EvidenceReceipt(key="source/typed.json", sha256="a" * 64),
        terminal_receipt=EvidenceReceipt(key="terminal/typed.json", sha256="b" * 64),
        data_receipts=(),
        completion_receipt=EvidenceReceipt(key=_completion_key(_EMPTY_DAY, rung), sha256="c" * 64),
        absence_reason=None,
        source_ceiling=_EMPTY_DAY,
        published_at=_CREATED_AT - timedelta(hours=1),
    )


def test_a_derived_rung_may_publish_zero_rows_when_it_binds_a_completion_receipt() -> None:
    """The day is published and holds rows; THIS rung of it kept none of them.

    A governed absence would claim the SOURCE had nothing, which is false -- and would also mix
    terminal states inside one day, which `_validate_generation_day` refuses and `selectable_days`
    rests on, so the day would become unselectable at EVERY rung rather than merely unindexed.
    """
    row = _empty_rung_row(_EMPTY_RUNG)

    assert (row.row_count, row.data_receipts) == (0, ())
    assert row.completion_receipt is not None


def test_the_base_rung_may_never_publish_zero_rows() -> None:
    """An empty BASE rung is a governed absence; a second vocabulary for it would record one state twice."""
    with pytest.raises(ValueError, match="positive row_count"):
        _empty_rung_row(_BASE_RUNG)


def test_a_published_row_without_a_completion_receipt_is_refused() -> None:
    """The receipt is the whole claim: without it an empty rung is a rung nobody wrote."""
    row = _empty_rung_row(_EMPTY_RUNG)

    with pytest.raises(ValueError, match="requires a completion receipt"):
        AvailabilityRow(
            lane=row.lane,
            product=row.product,
            nature=row.nature,
            day=row.day,
            rung=row.rung,
            terminal_state="published",
            row_count=0,
            source_receipt=row.source_receipt,
            terminal_receipt=row.terminal_receipt,
            data_receipts=(),
            completion_receipt=None,
            absence_reason=None,
            source_ceiling=row.source_ceiling,
            published_at=row.published_at,
        )


def test_terminal_evidence_admits_the_same_empty_rung_and_refuses_the_same_base_one() -> None:
    """The evidence and the row must agree about what is sayable, or one refuses what the other wrote."""
    identity = _identity(EvidenceReceipt(key="evidence/manifest.json", sha256="d" * 64))

    def evidence(rung: int) -> TerminalEvidence:
        return TerminalEvidence(
            identity=identity,
            day=_EMPTY_DAY,
            rung=rung,
            terminal_state="published",
            row_count=0,
            source_ceiling=_EMPTY_DAY,
            published_at=_CREATED_AT - timedelta(hours=1),
            source_receipt=EvidenceReceipt(key="source/typed.json", sha256="a" * 64),
            data_receipts=(),
            completion_receipt=EvidenceReceipt(key=_completion_key(_EMPTY_DAY, rung), sha256="c" * 64),
            absence_receipt=None,
            absence_reason=None,
        )

    assert evidence(_EMPTY_RUNG).row_count == 0
    with pytest.raises(ValueError, match="positive row_count"):
        evidence(_BASE_RUNG)


def test_a_bootstrap_verifies_a_published_empty_rung_against_its_zero_part_marker() -> None:
    """END TO END through the real verification: the marker's own bytes are re-read and re-hashed.

    `_verify_completion_object` compares the receipt's counts to the evidence AND refuses a marker
    that does not say the same thing about emptiness as the row binding it.
    """
    store = MemoryAvailabilityStorage()
    manifest = store.seed("evidence/manifest.json", b"manifest")
    identity = _identity(manifest)
    bootstrap_input = build_bootstrap_inventory_evidence(
        BootstrapInventoryEvidence(identity=identity, source_ceiling=_EMPTY_DAY, object_receipts=(manifest,))
    )
    store.seed(bootstrap_input.receipt.key, bootstrap_input.payload)
    source_object = store.seed("source/response.bin", b"source")
    source_evidence = build_source_evidence(
        SourceEvidence(
            identity=identity,
            day=_EMPTY_DAY,
            source_ceiling=_EMPTY_DAY,
            object_receipts=(source_object,),
        )
    )
    source = store.seed(source_evidence.receipt.key, source_evidence.payload)
    rows = tuple(
        _empty_published_rung(store, identity=identity, source=source, day=_EMPTY_DAY, rung=rung)
        if rung == _EMPTY_RUNG
        else _published_row(store, identity=identity, source=source, day=_EMPTY_DAY, rung=rung)
        for rung in AVAILABILITY_REQUIRED_RUNGS
    )

    pointer = bootstrap_availability(
        store,
        BootstrapRequest(
            identity=identity,
            source_ceiling=_EMPTY_DAY,
            created_at=_CREATED_AT,
            input_receipts=(bootstrap_input.receipt,),
            rows=rows,
            input_sha256="a" * 64,
        ),
    ).pointer

    index = read_latest_availability(store, lane_root=_LANE_ROOT)
    assert pointer.generation_key == index.pointer.generation_key
    assert index.selectable_days() == (_EMPTY_DAY,), "an emptied rung must not cost the day its whole ladder"


def _empty_published_rung(
    store: MemoryAvailabilityStorage,
    *,
    identity: AvailabilityIdentity,
    source: EvidenceReceipt,
    day: date,
    rung: int,
) -> AvailabilityRow:
    """Seed one rung holding NO parts and a derived-empty receipt, as `_retract_tier` leaves it."""
    published_at = _CREATED_AT - timedelta(hours=1)
    completion = store.seed(
        _completion_key(day, rung),
        PartitionCompletion(
            part_count=0,
            row_count=0,
            completed_at=published_at,
            run_id=f"test-z{rung}",
            derived_empty=True,
        ).to_json_bytes(),
    )
    terminal = build_terminal_evidence(
        TerminalEvidence(
            identity=identity,
            day=day,
            rung=rung,
            terminal_state="published",
            row_count=0,
            source_ceiling=day,
            published_at=published_at,
            source_receipt=source,
            data_receipts=(),
            completion_receipt=completion,
            absence_receipt=None,
            absence_reason=None,
        )
    )
    store.seed(terminal.receipt.key, terminal.payload)
    return AvailabilityRow(
        lane=identity.lane,
        product=identity.product,
        nature=identity.nature,
        day=day,
        rung=rung,
        terminal_state="published",
        row_count=0,
        source_receipt=source,
        terminal_receipt=terminal.receipt,
        data_receipts=(),
        completion_receipt=completion,
        absence_reason=None,
        source_ceiling=day,
        published_at=published_at,
    )


def _identity(manifest: EvidenceReceipt) -> AvailabilityIdentity:
    return AvailabilityIdentity(
        lane_root=_LANE_ROOT,
        lane="test-lane",
        product="test-product",
        nature="daily_series",
        required_rungs=AVAILABILITY_REQUIRED_RUNGS,
        verified_source_inventory_root=compute_verified_source_inventory_root((manifest,)),
    )


def _offline_published_rows(identity: AvailabilityIdentity, day: date) -> list[dict[str, object]]:
    source_object = EvidenceReceipt(key="source/response.bin", sha256=sha256_digest(b"source"))
    source = build_source_evidence(
        SourceEvidence(
            identity=identity,
            day=day,
            source_ceiling=day,
            object_receipts=(source_object,),
        )
    ).receipt
    rows: list[dict[str, object]] = []
    for rung in AVAILABILITY_REQUIRED_RUNGS:
        data_payload = _parquet_payload(1)
        data = EvidenceReceipt(key=_partition_key(day, rung), sha256=sha256_digest(data_payload))
        completion_payload = PartitionCompletion(
            part_count=1,
            row_count=1,
            completed_at=_CREATED_AT - timedelta(hours=1),
            run_id=f"offline-z{rung}",
        ).to_json_bytes()
        completion = EvidenceReceipt(
            key=_completion_key(day, rung),
            sha256=sha256_digest(completion_payload),
        )
        terminal = build_terminal_evidence(
            TerminalEvidence(
                identity=identity,
                day=day,
                rung=rung,
                terminal_state="published",
                row_count=1,
                source_ceiling=day,
                published_at=_CREATED_AT - timedelta(hours=1),
                source_receipt=source,
                data_receipts=(data,),
                completion_receipt=completion,
                absence_receipt=None,
                absence_reason=None,
            )
        ).receipt
        rows.append(
            AvailabilityRow(
                lane=identity.lane,
                product=identity.product,
                nature=identity.nature,
                day=day,
                rung=rung,
                terminal_state="published",
                row_count=1,
                source_receipt=source,
                terminal_receipt=terminal,
                data_receipts=(data,),
                completion_receipt=completion,
                absence_reason=None,
                source_ceiling=day,
                published_at=_CREATED_AT - timedelta(hours=1),
            ).to_wire()
        )
    return rows


def _published_row(
    store: MemoryAvailabilityStorage,
    *,
    identity: AvailabilityIdentity,
    source: EvidenceReceipt,
    day: date,
    rung: int,
) -> AvailabilityRow:
    published_at = _CREATED_AT - timedelta(hours=1)
    data = store.seed(_partition_key(day, rung), _parquet_payload(1))
    completion = store.seed(
        _completion_key(day, rung),
        PartitionCompletion(
            part_count=1,
            row_count=1,
            completed_at=published_at,
            run_id=f"test-z{rung}",
        ).to_json_bytes(),
    )
    terminal = build_terminal_evidence(
        TerminalEvidence(
            identity=identity,
            day=day,
            rung=rung,
            terminal_state="published",
            row_count=1,
            source_ceiling=day,
            published_at=published_at,
            source_receipt=source,
            data_receipts=(data,),
            completion_receipt=completion,
            absence_receipt=None,
            absence_reason=None,
        )
    )
    store.seed(terminal.receipt.key, terminal.payload)
    return AvailabilityRow(
        lane=identity.lane,
        product=identity.product,
        nature=identity.nature,
        day=day,
        rung=rung,
        terminal_state="published",
        row_count=1,
        source_receipt=source,
        terminal_receipt=terminal.receipt,
        data_receipts=(data,),
        completion_receipt=completion,
        absence_reason=None,
        source_ceiling=day,
        published_at=published_at,
    )


def _seed_absence_terminal(
    store: MemoryAvailabilityStorage,
    *,
    identity: AvailabilityIdentity,
    source: EvidenceReceipt,
    day: date,
    rung: int,
) -> EvidenceReceipt:
    absence = store.seed(
        _absence_key(day, rung),
        GovernedAbsence(
            reason=_ABSENCE_REASON,
            upstream_response="verified empty test response",
            recorded_at=_CREATED_AT - timedelta(hours=1),
            run_id=f"test-z{rung}",
        ).to_json_bytes(),
    )
    terminal = build_terminal_evidence(
        TerminalEvidence(
            identity=identity,
            day=day,
            rung=rung,
            terminal_state="governed_absence",
            row_count=0,
            source_ceiling=day,
            published_at=_CREATED_AT - timedelta(hours=1),
            source_receipt=source,
            data_receipts=(),
            completion_receipt=None,
            absence_receipt=absence,
            absence_reason=_ABSENCE_REASON,
        )
    )
    store.seed(terminal.receipt.key, terminal.payload)
    return terminal.receipt


def _partition_prefix(day: date, rung: int) -> str:
    return f"{_LANE_ROOT}/zoom={rung:02d}/year={day.year:04d}/month={day.month:02d}/day={day.day:02d}"


def _partition_key(day: date, rung: int) -> str:
    return f"{_partition_prefix(day, rung)}/part-0.parquet"


def _completion_key(day: date, rung: int) -> str:
    return f"{_partition_prefix(day, rung)}/_complete.json"


def _absence_key(day: date, rung: int) -> str:
    return f"{_partition_prefix(day, rung)}/absent.json"


def _parquet_payload(row_count: int) -> bytes:
    sink = io.BytesIO()
    pq.write_table(pa.table({"value": list(range(row_count))}), sink)
    return sink.getvalue()


def _published_bootstrap() -> tuple[MemoryAvailabilityStorage, AvailabilityPointer]:
    store = MemoryAvailabilityStorage()
    manifest = store.seed("evidence/manifest.json", b"manifest")
    identity = _identity(manifest)
    day = date(2026, 8, 30)
    bootstrap_input = build_bootstrap_inventory_evidence(
        BootstrapInventoryEvidence(identity=identity, source_ceiling=day, object_receipts=(manifest,))
    )
    store.seed(bootstrap_input.receipt.key, bootstrap_input.payload)
    source_object = store.seed("source/response.bin", b"source")
    source_evidence = build_source_evidence(
        SourceEvidence(
            identity=identity,
            day=day,
            source_ceiling=day,
            object_receipts=(source_object,),
        )
    )
    source = store.seed(source_evidence.receipt.key, source_evidence.payload)
    rows = tuple(
        _published_row(store, identity=identity, source=source, day=day, rung=rung)
        for rung in AVAILABILITY_REQUIRED_RUNGS
    )
    pointer = bootstrap_availability(
        store,
        BootstrapRequest(
            identity=identity,
            source_ceiling=day,
            created_at=_CREATED_AT,
            input_receipts=(bootstrap_input.receipt,),
            rows=rows,
            input_sha256="a" * 64,
        ),
    ).pointer
    return store, pointer


@dataclass
class RecordingS3Client:
    puts: list[dict[str, object]] = field(default_factory=list)

    def get_object(self, **kwargs: object) -> dict[str, object]:
        raise AssertionError(f"unexpected get_object: {kwargs}")

    def put_object(self, **kwargs: object) -> object:
        self.puts.append(dict(kwargs))
        return {}


@dataclass
class BytesBody:
    payload: bytes
    closed: bool = False
    read_calls: int = 0

    def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        return self.payload if size < 0 else self.payload[:size]

    def close(self) -> None:
        self.closed = True


@dataclass
class ScriptedS3Client:
    put_results: list[str | None]
    visible: list[bytes | None] = field(default_factory=list)
    puts: list[dict[str, object]] = field(default_factory=list)
    gets: int = 0

    def get_object(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        self.gets += 1
        if not self.visible:
            raise AssertionError("unexpected get_object")
        payload = self.visible.pop(0)
        if payload is None:
            raise _client_error("NoSuchKey", operation="GetObject")
        return {
            "Body": BytesBody(payload),
            "ContentLength": len(payload),
            "ETag": '"visible-etag"',
            "VersionId": "visible-version",
        }

    def put_object(self, **kwargs: object) -> object:
        self.puts.append(dict(kwargs))
        if not self.put_results:
            raise AssertionError("unexpected put_object")
        result = self.put_results.pop(0)
        if result is not None:
            raise _client_error(result, operation="PutObject")
        return {}


def _client_error(code: str, *, operation: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "scripted conflict"}},
        operation,
    )


@dataclass
class ReadResponseClient:
    response: dict[str, object]

    def get_object(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        return self.response

    def put_object(self, **kwargs: object) -> object:
        raise AssertionError(f"unexpected put_object: {kwargs}")
