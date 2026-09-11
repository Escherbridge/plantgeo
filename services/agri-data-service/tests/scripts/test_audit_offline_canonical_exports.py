from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from pathlib import Path

EXPECTED_LANE_COUNT = 8
EXPECTED_RUNG_OBJECT_COUNT = 2
APPLY_REFUSED_EXIT_CODE = 2

audit = load_scripts_module(
    "audit_offline_canonical_exports.py",
    "agri_data_service_test_audit_offline_canonical_exports",
)


class FakeReadOnlyStore:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def get(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def list(self, prefix: str) -> tuple[Any, ...]:
        return tuple(
            audit.ListedObject(key=key, byte_count=len(payload))
            for key, payload in sorted(self.objects.items())
            if key.startswith(prefix)
        )


def _encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fixture() -> tuple[Any, FakeReadOnlyStore]:
    source_manifest = _encoded(
        {
            "contract_version": "source/v1",
            "snapshot_id": "snapshot-1",
            "row_count": 10,
            "partition_count": 1,
            "batch_count": 1,
            "rejected_rows": 0,
            "observation_day_min": "2026-01-01",
            "observation_day_max": "2026-01-01",
        }
    )
    source_manifest_sha = _sha256(source_manifest)
    frozen_manifest = _encoded({"contract_version": "frozen/v1", "product": "test-product"})
    frozen_manifest_sha = _sha256(frozen_manifest)
    source_part = b"source parquet bytes"
    authority = audit.ExportAuthority(
        builder_file="selected_builder.py",
        product="test-product",
        lane="test-lane",
        signal_name="test_signal",
        normalized_unit="C",
        source_snapshot_id="snapshot-1",
        source_root="raw/snapshot=snapshot-1",
        source_manifest_key="raw/snapshot=snapshot-1/manifest.json",
        source_complete_key="raw/snapshot=snapshot-1/_COMPLETE",
        source_manifest_sha256=source_manifest_sha,
        source_contract_version="source/v1",
        source_key="test-source",
        support_key="test-support",
        source_part_prefix=("raw/snapshot=snapshot-1/source=test-source/product=test-product/support=test-support/"),
        expected_source_parts=1,
        expected_source_bytes=len(source_part),
        source_row_count=10,
        source_partition_count=1,
        source_batch_count=1,
        snapshot_first_day=date(2026, 1, 1),
        snapshot_last_day=date(2026, 1, 1),
        frozen_data_root="frozen/test-lane/snapshot=snapshot-1",
        frozen_manifest_sha256=frozen_manifest_sha,
        history_first_day=date(2026, 1, 1),
        history_last_day=date(2026, 1, 1),
        history_day_count=1,
        cells_per_day=10,
        rung_rows_per_day=((13, 10), (9, 10), (5, 10), (0, 1)),
    )
    bootstrap = _encoded({"schema_version": "bootstrap/v1", "row_count": 4})
    bootstrap_sha = _sha256(bootstrap)
    generation = b"immutable availability parquet"
    generation_sha = _sha256(generation)
    bootstrap_key = f"{authority.live_root}/availability/bootstrap/_BOOTSTRAPPED.json"
    generation_key = f"{authority.live_root}/availability/generation={generation_sha}/availability.parquet"
    pointer = _encoded(
        {
            "schema_version": "agri.parquet.availability.v1",
            "lane": authority.lane,
            "lane_root": authority.live_root,
            "product": authority.product,
            "nature": "observed",
            "required_rungs": [13, 9, 5, 0],
            "generation_key": generation_key,
            "generation_sha256": generation_sha,
            "generation_receipt_sha256": "a" * 64,
            "generation_bytes": len(generation),
            "rows": 4,
            "earliest_terminal_day": "2026-01-01",
            "latest_terminal_day": "2026-01-01",
            "source_ceiling": "2026-01-01",
            "prior_generation_key": None,
            "prior_generation_sha256": None,
            "created_at": "2026-01-02T00:00:00Z",
            "bootstrap_receipt_key": bootstrap_key,
            "bootstrap_receipt_sha256": bootstrap_sha,
            "verified_source_inventory_root": "b" * 64,
        }
    )
    objects = {
        authority.source_manifest_key: source_manifest,
        authority.source_complete_key: _encoded(
            {"manifest_sha256": source_manifest_sha, "row_count": 10, "partition_count": 1}
        ),
        f"{authority.source_part_prefix}year=2026/month=01/part-0.parquet": source_part,
        f"{authority.frozen_data_root}/manifest.json": frozen_manifest,
        f"{authority.frozen_data_root}/_COMPLETE": _encoded({"manifest_sha256": frozen_manifest_sha}),
        f"{authority.live_root}/availability/_LATEST.json": pointer,
        bootstrap_key: bootstrap,
        generation_key: generation,
    }
    for rung, _ in authority.rung_rows_per_day:
        prefix = f"{authority.live_root}/zoom={rung:02d}/year=2026/month=01/day=01/"
        objects[f"{prefix}part-0.parquet"] = f"z{rung} parquet".encode()
        objects[f"{prefix}_complete.json"] = _encoded({"row_count": 1})
    return authority, FakeReadOnlyStore(objects)


def test_authorities_are_derived_from_the_two_selected_builders() -> None:
    authorities = audit.load_authorities()

    assert len(authorities) == EXPECTED_LANE_COUNT
    assert {item.builder_file for item in authorities} == set(audit.AUTHORITY_FILES)
    assert {item.lane for item in authorities} == {
        "soil-field-vpd",
        "soil-temperature-0-to-7cm",
        "soil-temperature-7-to-28cm",
        "soil-temperature-28-to-100cm",
        "soil-temperature-100-to-255cm",
        "climate-field-air-temperature-mean",
        "climate-field-air-temperature-max",
        "climate-field-air-temperature-min",
    }
    assert {item.rung_rows_per_day for item in authorities} == {
        ((13, 1_470), (9, 1_470), (5, 1_470), (0, 6)),
        ((13, 397), (9, 397), (5, 397), (0, 24)),
    }


def test_complete_fake_lane_produces_a_verified_read_only_receipt() -> None:
    authority, store = _fixture()

    report = audit.audit_exports(
        store,
        authorities=(authority,),
        clock=iter((10.0, 12.5)).__next__,
        peak_rss=lambda: (1234, "fake_peak_rss"),
    )

    assert report["mode"] == "read-only"
    assert report["verdict"] == {"status": "verified", "findings": []}
    assert report["historical_phase_timings"] == {
        phase: {
            "status": "unavailable",
            "seconds": None,
            "reason": ("The selected historical builders did not record phase timing; this audit will not infer it."),
        }
        for phase in ("stage", "build", "upload")
    }
    expected_listed = [
        payload
        for key, payload in store.objects.items()
        if key.startswith(
            (
                authority.source_part_prefix,
                f"{authority.frozen_data_root}/",
                f"{authority.live_root}/",
            )
        )
    ]
    assert report["measurement"] == {
        "audit_elapsed_wall_seconds": 2.5,
        "peak_process_rss_bytes": 1234,
        "peak_process_rss_method": "fake_peak_rss",
        "list_operations": 3,
        "listed_object_count": len(expected_listed),
        "listed_object_bytes": sum(len(payload) for payload in expected_listed),
    }
    lane = report["lanes"][0]
    assert lane["live_census"]["history_complete_day_count"] == 1
    assert lane["live_census"]["history_owed_day_count"] == 0
    for rung, _ in authority.rung_rows_per_day:
        assert lane["live_census"]["rungs"][str(rung)]["listed_object_count"] == EXPECTED_RUNG_OBJECT_COUNT
        assert lane["live_census"]["rungs"][str(rung)]["listed_object_bytes"] > 0
    assert lane["index_state"]["generation_receipt"]["checksum_matches"] is True


def test_source_byte_drift_and_missing_rung_fail_the_receipt() -> None:
    authority, store = _fixture()
    authority = replace(authority, expected_source_bytes=authority.expected_source_bytes + 1)
    missing_key = f"{authority.live_root}/zoom=00/year=2026/month=01/day=01/part-0.parquet"
    del store.objects[missing_key]

    report = audit.audit_exports(
        store,
        authorities=(authority,),
        clock=iter((1.0, 2.0)).__next__,
        peak_rss=lambda: (None, "unavailable"),
    )

    assert report["verdict"]["status"] == "failed"
    assert {finding["code"] for finding in report["verdict"]["findings"]} == {
        "source_byte_count_mismatch",
        "history_ladder_incomplete",
    }


def test_only_explicit_out_can_write_and_it_never_overwrites(tmp_path: Path) -> None:
    output = tmp_path / "audit.json"

    audit._write_exclusive(output, "first")

    assert output.read_text() == "first\n"
    with pytest.raises(FileExistsError):
        audit._write_exclusive(output, "second")


def test_apply_is_refused_before_configuration(capsys: pytest.CaptureFixture[str]) -> None:
    assert audit.main(["--apply"]) == APPLY_REFUSED_EXIT_CODE
    assert "no apply or object-store mutation mode" in capsys.readouterr().err


def test_production_store_exposes_no_remote_mutation_methods() -> None:
    assert not hasattr(audit.S3ReadOnlyStore, "put")
    assert not hasattr(audit.S3ReadOnlyStore, "delete")
    assert not hasattr(audit.S3ReadOnlyStore, "overwrite")
