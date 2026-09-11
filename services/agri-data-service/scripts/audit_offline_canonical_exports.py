"""Audit the eight selected canonical exports without mutating object storage.

See ``scripts/AGENTS.md`` under "Offline canonical export audit".
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib
import importlib.util
import json
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from types import ModuleType

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = SERVICE_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from agri_data_service.config import ObjectStoreCredentials, Settings  # noqa: E402
from agri_data_service.foundation.parquet.paths import (  # noqa: E402
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
)

SCHEMA_VERSION = "plantgeo.offline-canonical-export-audit.v1"
EXPECTED_LANE_COUNT = 8
AUTHORITY_FILES = (
    "build_era5_land_from_canonical_snapshot.py",
    "build_nasa_power_from_canonical_snapshot.py",
)
POINTER_FIELDS = (
    "schema_version",
    "lane",
    "lane_root",
    "product",
    "nature",
    "required_rungs",
    "generation_key",
    "generation_sha256",
    "generation_receipt_sha256",
    "generation_bytes",
    "rows",
    "earliest_terminal_day",
    "latest_terminal_day",
    "source_ceiling",
    "prior_generation_key",
    "prior_generation_sha256",
    "created_at",
    "bootstrap_receipt_key",
    "bootstrap_receipt_sha256",
    "verified_source_inventory_root",
)


@dataclass(frozen=True, slots=True)
class ListedObject:
    key: str
    byte_count: int


class ReadOnlyObjectStore(Protocol):
    def get(self, key: str) -> bytes | None: ...

    def list(self, prefix: str) -> Sequence[ListedObject]: ...


@dataclass(frozen=True, slots=True)
class ExportAuthority:
    builder_file: str
    product: str
    lane: str
    signal_name: str
    normalized_unit: str
    source_snapshot_id: str
    source_root: str
    source_manifest_key: str
    source_complete_key: str
    source_manifest_sha256: str
    source_contract_version: str
    source_key: str
    support_key: str
    source_part_prefix: str
    expected_source_parts: int
    expected_source_bytes: int
    source_row_count: int
    source_partition_count: int
    source_batch_count: int
    snapshot_first_day: date
    snapshot_last_day: date
    frozen_data_root: str
    frozen_manifest_sha256: str
    history_first_day: date
    history_last_day: date
    history_day_count: int
    cells_per_day: int
    rung_rows_per_day: tuple[tuple[int, int], ...]

    @property
    def live_root(self) -> str:
        return f"layer={self.lane}/kind=observed"


class S3ReadOnlyStore:
    """Expose only GET and LIST against the configured warehouse prefix."""

    def __init__(self, credentials: ObjectStoreCredentials, object_store_prefix: str) -> None:
        self._bucket = credentials.bucket
        self._prefix = object_store_prefix.strip("/")
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=credentials.endpoint_url,
            region_name=credentials.region,
            aws_access_key_id=credentials.access_key_id.get_secret_value(),
            aws_secret_access_key=credentials.secret_access_key.get_secret_value(),
            config=Config(retries={"max_attempts": 12, "mode": "adaptive"}),
        )

    def _key(self, relative: str) -> str:
        clean = relative.strip("/")
        return f"{self._prefix}/{clean}" if self._prefix else clean

    def get(self, key: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key(key))
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        payload = response["Body"].read()
        return payload if isinstance(payload, bytes) else bytes(payload)

    def list(self, prefix: str) -> Sequence[ListedObject]:
        full_prefix = self._key(prefix)
        token: str | None = None
        objects: list[ListedObject] = []
        while True:
            request: dict[str, object] = {"Bucket": self._bucket, "Prefix": full_prefix}
            if token is not None:
                request["ContinuationToken"] = token
            response = self._client.list_objects_v2(**request)
            for item in response.get("Contents", []):
                full_key = str(item["Key"])
                key = full_key[len(self._prefix) + 1 :] if self._prefix else full_key
                objects.append(ListedObject(key=key, byte_count=int(item["Size"])))
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token:
                return tuple(sorted(objects, key=lambda item: item.key))
            token = next_token


@dataclass(slots=True)
class _MeteredStore:
    delegate: ReadOnlyObjectStore
    list_operations: int = 0
    listed_object_count: int = 0
    listed_object_bytes: int = 0

    def get(self, key: str) -> bytes | None:
        return self.delegate.get(key)

    def list(self, prefix: str) -> Sequence[ListedObject]:
        objects = self.delegate.list(prefix)
        self.list_operations += 1
        self.listed_object_count += len(objects)
        self.listed_object_bytes += sum(item.byte_count for item in objects)
        return objects


def _load_module(file_name: str) -> ModuleType:
    path = Path(__file__).with_name(file_name)
    module_name = f"_plantgeo_offline_audit_{path.stem}"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load selected builder {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _date_value(value: object, name: str) -> date:
    if not isinstance(value, date):
        raise RuntimeError(f"selected builder {name} is not a date")
    return value


def load_authorities() -> tuple[ExportAuthority, ...]:
    """Derive the complete eight-lane contract from the two selected builders."""
    authorities: list[ExportAuthority] = []
    for file_name in AUTHORITY_FILES:
        module = _load_module(file_name)
        products = module.PRODUCTS
        if not isinstance(products, Mapping):
            raise RuntimeError(f"selected builder {file_name} has no PRODUCTS mapping")
        for product_name, spec in products.items():
            product = str(spec.product)
            if product != str(product_name):
                raise RuntimeError(f"selected builder {file_name} product table is internally inconsistent")
            source_root = str(module.SOURCE_ROOT)
            source_key = str(module.SOURCE_KEY)
            support_key = str(module.SUPPORT_KEY)
            rung_rows = module.EXPECTED_TIER_ROWS_PER_DAY
            authorities.append(
                ExportAuthority(
                    builder_file=file_name,
                    product=product,
                    lane=str(spec.lane),
                    signal_name=str(spec.signal_name),
                    normalized_unit=str(spec.normalized_unit),
                    source_snapshot_id=str(module.SOURCE_SNAPSHOT_ID),
                    source_root=source_root,
                    source_manifest_key=str(module.SOURCE_MANIFEST_KEY),
                    source_complete_key=str(module.SOURCE_COMPLETE_KEY),
                    source_manifest_sha256=str(module.SOURCE_MANIFEST_SHA256),
                    source_contract_version=str(module.SOURCE_CONTRACT_VERSION),
                    source_key=source_key,
                    support_key=support_key,
                    source_part_prefix=(f"{source_root}/source={source_key}/product={product}/support={support_key}/"),
                    expected_source_parts=int(module.EXPECTED_SOURCE_PARTS),
                    expected_source_bytes=int(spec.expected_source_bytes),
                    source_row_count=int(module.SOURCE_ROW_COUNT),
                    source_partition_count=int(module.SOURCE_PARTITION_COUNT),
                    source_batch_count=int(module.SOURCE_BATCH_COUNT),
                    snapshot_first_day=_date_value(module.SNAPSHOT_FIRST_DAY, "SNAPSHOT_FIRST_DAY"),
                    snapshot_last_day=_date_value(module.SNAPSHOT_LAST_DAY, "SNAPSHOT_LAST_DAY"),
                    frozen_data_root=str(spec.frozen_data_root),
                    frozen_manifest_sha256=str(spec.frozen_manifest_sha256),
                    history_first_day=_date_value(module.EXPECTED_FIRST_DAY, "EXPECTED_FIRST_DAY"),
                    history_last_day=_date_value(module.EXPECTED_LAST_DAY, "EXPECTED_LAST_DAY"),
                    history_day_count=int(module.EXPECTED_DAYS),
                    cells_per_day=int(module.EXPECTED_CELLS_PER_DAY),
                    rung_rows_per_day=tuple(
                        sorted(((int(rung), int(rows)) for rung, rows in rung_rows.items()), reverse=True)
                    ),
                )
            )
    if len(authorities) != EXPECTED_LANE_COUNT or len({item.lane for item in authorities}) != EXPECTED_LANE_COUNT:
        raise RuntimeError("selected builders must describe exactly eight distinct lanes")
    return tuple(authorities)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_object(payload: bytes | None) -> dict[str, object] | None:
    if payload is None:
        return None
    value = json.loads(payload)
    return value if isinstance(value, dict) else None


def _receipt(
    store: ReadOnlyObjectStore,
    key: str,
    expected_sha256: str | None = None,
    *,
    decode_json: bool = True,
) -> dict[str, object]:
    payload = store.get(key)
    observed = _sha256(payload) if payload is not None else None
    return {
        "key": key,
        "exists": payload is not None,
        "byte_count": len(payload) if payload is not None else None,
        "expected_sha256": expected_sha256,
        "observed_sha256": observed,
        "checksum_matches": expected_sha256 is None or observed == expected_sha256,
        "document": _json_object(payload) if decode_json else None,
    }


def _receipt_fields(receipt: Mapping[str, object], fields: Sequence[str]) -> dict[str, object]:
    selected = dict(receipt)
    document = receipt.get("document")
    selected["document"] = {field: document.get(field) for field in fields} if isinstance(document, Mapping) else None
    return selected


def _finding(
    findings: list[dict[str, object]],
    authority: ExportAuthority,
    code: str,
    observed: object,
    expected: object,
) -> None:
    findings.append({"lane": authority.lane, "code": code, "observed": observed, "expected": expected})


def _audit_source(
    store: ReadOnlyObjectStore,
    authority: ExportAuthority,
    findings: list[dict[str, object]],
) -> dict[str, object]:
    manifest = _receipt(store, authority.source_manifest_key, authority.source_manifest_sha256)
    completion = _receipt(store, authority.source_complete_key)
    objects = store.list(authority.source_part_prefix)
    listed_bytes = sum(item.byte_count for item in objects)
    expected_manifest = {
        "contract_version": authority.source_contract_version,
        "snapshot_id": authority.source_snapshot_id,
        "row_count": authority.source_row_count,
        "partition_count": authority.source_partition_count,
        "batch_count": authority.source_batch_count,
        "rejected_rows": 0,
        "observation_day_min": authority.snapshot_first_day.isoformat(),
        "observation_day_max": authority.snapshot_last_day.isoformat(),
    }
    actual_manifest = manifest["document"]
    drift = (
        {key: actual_manifest.get(key) for key, value in expected_manifest.items() if actual_manifest.get(key) != value}
        if isinstance(actual_manifest, Mapping)
        else expected_manifest
    )
    if not manifest["exists"] or not manifest["checksum_matches"]:
        _finding(findings, authority, "source_manifest_receipt_mismatch", manifest, "pinned receipt")
    if drift:
        _finding(findings, authority, "source_manifest_contract_drift", drift, expected_manifest)
    completion_document = completion["document"]
    completion_sha = completion_document.get("manifest_sha256") if isinstance(completion_document, Mapping) else None
    if completion_sha != authority.source_manifest_sha256:
        _finding(
            findings,
            authority,
            "source_completion_manifest_mismatch",
            completion_sha,
            authority.source_manifest_sha256,
        )
    if isinstance(completion_document, Mapping) and isinstance(actual_manifest, Mapping):
        completion_counts = {
            "row_count": completion_document.get("row_count"),
            "partition_count": completion_document.get("partition_count"),
        }
        manifest_counts = {
            "row_count": actual_manifest.get("row_count"),
            "partition_count": actual_manifest.get("partition_count"),
        }
        if completion_counts != manifest_counts:
            _finding(
                findings,
                authority,
                "source_completion_count_mismatch",
                completion_counts,
                manifest_counts,
            )
    if len(objects) != authority.expected_source_parts:
        _finding(findings, authority, "source_object_count_mismatch", len(objects), authority.expected_source_parts)
    if listed_bytes != authority.expected_source_bytes:
        _finding(findings, authority, "source_byte_count_mismatch", listed_bytes, authority.expected_source_bytes)
    return {
        "snapshot_id": authority.source_snapshot_id,
        "root": authority.source_root,
        "source_key": authority.source_key,
        "product": authority.product,
        "support_key": authority.support_key,
        "part_prefix": authority.source_part_prefix,
        "manifest": _receipt_fields(manifest, tuple(expected_manifest)),
        "completion": _receipt_fields(
            completion,
            ("manifest_key", "manifest_sha256", "row_count", "partition_count", "completed_at"),
        ),
        "manifest_expected_fields": expected_manifest,
        "manifest_drift": drift,
        "listed_object_count": len(objects),
        "listed_object_bytes": listed_bytes,
        "expected_object_count": authority.expected_source_parts,
        "expected_object_bytes": authority.expected_source_bytes,
    }


def _audit_frozen(
    store: ReadOnlyObjectStore,
    authority: ExportAuthority,
    findings: list[dict[str, object]],
) -> dict[str, object]:
    manifest_key = f"{authority.frozen_data_root}/manifest.json"
    completion_key = f"{authority.frozen_data_root}/_COMPLETE"
    manifest = _receipt(store, manifest_key, authority.frozen_manifest_sha256)
    completion = _receipt(store, completion_key)
    objects = store.list(f"{authority.frozen_data_root}/")
    listed_bytes = sum(item.byte_count for item in objects)
    if not manifest["exists"] or not manifest["checksum_matches"]:
        _finding(findings, authority, "frozen_manifest_receipt_mismatch", manifest, "pinned receipt")
    completion_document = completion["document"]
    completion_sha = completion_document.get("manifest_sha256") if isinstance(completion_document, Mapping) else None
    if completion_sha != authority.frozen_manifest_sha256:
        _finding(
            findings,
            authority,
            "frozen_completion_manifest_mismatch",
            completion_sha,
            authority.frozen_manifest_sha256,
        )
    completion_manifest_key = (
        completion_document.get("manifest_key") if isinstance(completion_document, Mapping) else None
    )
    if completion_manifest_key not in {None, manifest_key}:
        _finding(
            findings,
            authority,
            "frozen_completion_key_mismatch",
            completion_manifest_key,
            manifest_key,
        )
    return {
        "data_root": authority.frozen_data_root,
        "manifest": _receipt_fields(
            manifest,
            (
                "contract_version",
                "snapshot_id",
                "snapshot_manifest_key",
                "snapshot_manifest_sha256",
                "product",
                "source_observation_day_min",
                "source_observation_day_max",
                "totals",
            ),
        ),
        "completion": _receipt_fields(
            completion,
            ("manifest_key", "manifest_sha256", "row_count", "partition_count", "completed_at"),
        ),
        "listed_object_count": len(objects),
        "listed_object_bytes": listed_bytes,
    }


def _audit_live_census(
    store: ReadOnlyObjectStore,
    authority: ExportAuthority,
    findings: list[dict[str, object]],
) -> dict[str, object]:
    objects = store.list(f"{authority.live_root}/")
    parts: dict[tuple[date, int], list[ListedObject]] = defaultdict(list)
    completions: dict[tuple[date, int], list[ListedObject]] = defaultdict(list)
    derived_empties: dict[tuple[date, int], list[ListedObject]] = defaultdict(list)
    absences: dict[tuple[date, int], list[ListedObject]] = defaultdict(list)
    availability: list[ListedObject] = []
    unexpected: list[ListedObject] = []
    for item in objects:
        partition = try_parse_partition_path(item.key)
        if partition is not None:
            parts[(partition.day, int(partition.zoom))].append(item)
            continue
        completion = try_parse_completion_marker_path(item.key)
        if completion is not None:
            target = derived_empties if completion.derived_empty else completions
            target[(completion.day, int(completion.zoom))].append(item)
            continue
        absence = try_parse_absence_marker_path(item.key)
        if absence is not None:
            absences[(absence.day, int(absence.zoom))].append(item)
            continue
        if item.key.startswith(f"{authority.live_root}/availability/"):
            availability.append(item)
        else:
            unexpected.append(item)

    rungs = tuple(rung for rung, _ in authority.rung_rows_per_day)
    history_days = tuple(
        authority.history_first_day + timedelta(days=offset) for offset in range(authority.history_day_count)
    )
    complete_history_days = [
        day
        for day in history_days
        if all(
            len(parts[(day, rung)]) == 1
            and len(completions[(day, rung)]) == 1
            and not derived_empties[(day, rung)]
            and not absences[(day, rung)]
            for rung in rungs
        )
    ]
    complete_set = set(complete_history_days)
    owed_days = [day for day in history_days if day not in complete_set]
    if owed_days:
        _finding(findings, authority, "history_ladder_incomplete", len(owed_days), 0)
    if unexpected:
        _finding(findings, authority, "unexpected_live_objects", len(unexpected), 0)

    all_day_rungs = set(parts) | set(completions) | set(derived_empties) | set(absences)
    all_days = sorted({day for day, _ in all_day_rungs})
    per_rung: dict[str, object] = {}
    for rung, expected_rows in authority.rung_rows_per_day:
        rung_items = [item for item in objects if f"/zoom={rung:02d}/" in item.key]
        per_rung[str(rung)] = {
            "expected_rows_per_day": expected_rows,
            "part_objects": sum(len(value) for (day, zoom), value in parts.items() if zoom == rung),
            "completion_objects": sum(len(value) for (day, zoom), value in completions.items() if zoom == rung),
            "derived_empty_objects": sum(len(value) for (day, zoom), value in derived_empties.items() if zoom == rung),
            "absence_objects": sum(len(value) for (day, zoom), value in absences.items() if zoom == rung),
            "listed_object_count": len(rung_items),
            "listed_object_bytes": sum(item.byte_count for item in rung_items),
        }
    return {
        "root": authority.live_root,
        "listed_object_count": len(objects),
        "listed_object_bytes": sum(item.byte_count for item in objects),
        "earliest_listed_day": all_days[0].isoformat() if all_days else None,
        "latest_listed_day": all_days[-1].isoformat() if all_days else None,
        "history_complete_day_count": len(complete_history_days),
        "history_owed_day_count": len(owed_days),
        "history_owed_day_sample": [day.isoformat() for day in owed_days[:20]],
        "days_before_history": sum(day < authority.history_first_day for day in all_days),
        "days_after_history": sum(day > authority.history_last_day for day in all_days),
        "availability_object_count": len(availability),
        "availability_object_bytes": sum(item.byte_count for item in availability),
        "unexpected_object_count": len(unexpected),
        "unexpected_object_sample": [item.key for item in unexpected[:20]],
        "rungs": per_rung,
    }


def _audit_index(
    store: ReadOnlyObjectStore,
    authority: ExportAuthority,
    findings: list[dict[str, object]],
) -> dict[str, object]:
    pointer_key = f"{authority.live_root}/availability/_LATEST.json"
    pointer = _receipt(store, pointer_key)
    document = pointer["document"]
    fixed = {field: document.get(field) for field in POINTER_FIELDS} if isinstance(document, Mapping) else None
    expected_rungs = [rung for rung, _ in authority.rung_rows_per_day]
    if not isinstance(document, Mapping):
        _finding(findings, authority, "availability_pointer_missing_or_malformed", document, "JSON object")
    else:
        expected_identity = {
            "lane": authority.lane,
            "lane_root": authority.live_root,
            "required_rungs": expected_rungs,
        }
        observed_rungs = document.get("required_rungs")
        normalized_observed_rungs = sorted(observed_rungs) if isinstance(observed_rungs, list) else observed_rungs
        observed_identity = {
            "lane": document.get("lane"),
            "lane_root": document.get("lane_root"),
            "required_rungs": normalized_observed_rungs,
        }
        normalized_expected_identity = {
            **expected_identity,
            "required_rungs": sorted(expected_rungs),
        }
        drift = {
            key: observed_identity.get(key)
            for key, value in normalized_expected_identity.items()
            if observed_identity.get(key) != value
        }
        if drift:
            _finding(
                findings,
                authority,
                "availability_pointer_identity_drift",
                drift,
                normalized_expected_identity,
            )
        pointer_rows = document.get("rows")
        observed_rows = pointer_rows if isinstance(pointer_rows, int) and not isinstance(pointer_rows, bool) else 0
        if observed_rows < authority.history_day_count * len(expected_rungs):
            _finding(
                findings,
                authority,
                "availability_pointer_history_shortfall",
                document.get("rows"),
                f">={authority.history_day_count * len(expected_rungs)}",
            )
    bootstrap_key = (
        str(document.get("bootstrap_receipt_key"))
        if isinstance(document, Mapping) and document.get("bootstrap_receipt_key")
        else f"{authority.live_root}/availability/bootstrap/_BOOTSTRAPPED.json"
    )
    bootstrap_sha = (
        str(document.get("bootstrap_receipt_sha256"))
        if isinstance(document, Mapping) and document.get("bootstrap_receipt_sha256")
        else None
    )
    bootstrap = _receipt(store, bootstrap_key, bootstrap_sha)
    if not bootstrap["exists"] or not bootstrap["checksum_matches"]:
        _finding(findings, authority, "availability_bootstrap_receipt_mismatch", bootstrap, "pointer receipt")
    generation_key = str(document.get("generation_key")) if isinstance(document, Mapping) else ""
    generation_sha = str(document.get("generation_sha256")) if isinstance(document, Mapping) else None
    generation = _receipt(store, generation_key, generation_sha, decode_json=False) if generation_key else None
    if generation is None or not generation["exists"] or not generation["checksum_matches"]:
        _finding(findings, authority, "availability_generation_receipt_mismatch", generation, "pointer receipt")
    elif isinstance(document, Mapping) and generation["byte_count"] != document.get("generation_bytes"):
        _finding(
            findings,
            authority,
            "availability_generation_byte_count_mismatch",
            generation["byte_count"],
            document.get("generation_bytes"),
        )
    return {
        "pointer": {**pointer, "fields": fixed},
        "bootstrap_receipt": bootstrap,
        "generation_receipt": generation,
    }


def _peak_process_rss_bytes() -> tuple[int | None, str]:
    if sys.platform == "win32":

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("page_fault_count", ctypes.c_ulong),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        measured = ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
        if measured:
            return int(counters.peak_working_set_size), "windows_peak_working_set"
        return None, "unavailable"
    try:
        resource_module = importlib.import_module("resource")
    except ModuleNotFoundError:
        return None, "unavailable"
    peak = int(resource_module.getrusage(resource_module.RUSAGE_SELF).ru_maxrss)
    return (peak if sys.platform == "darwin" else peak * 1024), "posix_ru_maxrss"


def audit_exports(
    store: ReadOnlyObjectStore,
    *,
    authorities: Sequence[ExportAuthority] | None = None,
    clock: Callable[[], float] = time.perf_counter,
    peak_rss: Callable[[], tuple[int | None, str]] = _peak_process_rss_bytes,
) -> dict[str, object]:
    selected = tuple(authorities) if authorities is not None else load_authorities()
    started = clock()
    metered = _MeteredStore(store)
    findings: list[dict[str, object]] = []
    lanes = [
        {
            "authority": asdict(authority),
            "source": _audit_source(metered, authority, findings),
            "frozen": _audit_frozen(metered, authority, findings),
            "history": {
                "first_day": authority.history_first_day.isoformat(),
                "last_day": authority.history_last_day.isoformat(),
                "day_count": authority.history_day_count,
                "cells_per_day": authority.cells_per_day,
                "rung_rows_per_day": dict(authority.rung_rows_per_day),
            },
            "live_census": _audit_live_census(metered, authority, findings),
            "index_state": _audit_index(metered, authority, findings),
        }
        for authority in selected
    ]
    rss_bytes, rss_method = peak_rss()
    elapsed = clock() - started
    unavailable_reason = "The selected historical builders did not record phase timing; this audit will not infer it."
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "read-only",
        "authority_builders": list(AUTHORITY_FILES),
        "historical_phase_timings": {
            phase: {"status": "unavailable", "seconds": None, "reason": unavailable_reason}
            for phase in ("stage", "build", "upload")
        },
        "measurement": {
            "audit_elapsed_wall_seconds": elapsed,
            "peak_process_rss_bytes": rss_bytes,
            "peak_process_rss_method": rss_method,
            "list_operations": metered.list_operations,
            "listed_object_count": metered.listed_object_count,
            "listed_object_bytes": metered.listed_object_bytes,
        },
        "lane_count": len(lanes),
        "lanes": lanes,
        "verdict": {"status": "verified" if not findings else "failed", "findings": findings},
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=SERVICE_ROOT / ".env")
    parser.add_argument("--out", type=Path, help="exclusively create this local JSON receipt")
    return parser


def _write_exclusive(path: Path, payload: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--apply" in arguments:
        print("error: this auditor has no apply or object-store mutation mode", file=sys.stderr)
        return 2
    parsed = _parser().parse_args(arguments)
    try:
        configured = Settings(_env_file=parsed.env_file)  # type: ignore[call-arg]
        store = S3ReadOnlyStore(configured.require_object_store(), configured.object_store_prefix)
        report = audit_exports(store)
        rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
        if parsed.out is not None:
            _write_exclusive(parsed.out, rendered)
        print(rendered)
        verdict = report["verdict"]
        return 0 if isinstance(verdict, Mapping) and verdict.get("status") == "verified" else 1
    except (OSError, RuntimeError, ValueError, ClientError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
