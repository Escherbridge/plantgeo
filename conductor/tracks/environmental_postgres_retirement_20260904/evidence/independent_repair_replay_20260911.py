"""Reproduce reviewed local repair artifacts without network or production operations."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def mtbs(root: Path, output: Path) -> dict[str, object]:
    from agri_data_service.pipeline.direct.burn_severity.older_recovery import (
        prepare_older_capture,
        verify_current_preservation,
    )

    research = root / ".omc/research"
    saved = research / "mtbs-older-prepared-20260911-v1"
    prepared = output / "mtbs-older-replayed"
    manifest_sha = "254c92c3d6a1ec243431300a8f7f4fb2125deed92cb4ca347ab53cb737ca4f72"
    expected_sha = "58dbbbd0415cee8b33cd839bdfcefd90a857923b5cd97154990373d0eff003a4"
    prepare_older_capture(research / "mtbs-older-capture-20260911-v2", manifest_sha, prepared)
    saved_bytes = (saved / "preparation.json").read_bytes()
    require(sha256(saved_bytes) == expected_sha, "saved older preparation pin differs")
    require((prepared / "preparation.json").read_bytes() == saved_bytes, "older replay receipt differs")
    saved_names = {path.name for path in (saved / "blobs").iterdir()}
    replay_names = {path.name for path in (prepared / "blobs").iterdir()}
    require(saved_names == replay_names, "older replay blob population differs")
    byte_count = 0
    for name in sorted(saved_names):
        expected = (saved / "blobs" / name).read_bytes()
        actual = (prepared / "blobs" / name).read_bytes()
        require(sha256(expected) == name and actual == expected, "older replay blob differs")
        byte_count += len(actual)
    receipt = json.loads(saved_bytes)
    previous = research / "preserved-repair-inputs-20260911"
    current = verify_current_preservation(
        previous / "mtbs-current-capture-20260910-v3",
        previous / "mtbs-current-prepared-20260910-v3",
        "4690af4629e617ffbde3f5e6ae99be3eb4c1a536fe47a931d7f0a8456c8f6468",
    )
    require(current["descriptor"]["source_row_count"] == 747, "current rollback row population differs")
    require(
        current["preparation_sha256"] == "e50bd78582b4e2bb6ce45ba21188d67309edca25d1d3dc7e6d3a71e49e980406",
        "current rollback preparation pin differs",
    )
    return {
        "status": "pass",
        "older_preparation_sha256": expected_sha,
        "older_full_blob_count": len(saved_names),
        "older_full_blob_bytes": byte_count,
        "older_fire_ids_sha256": receipt["fire_ids_sha256"],
        "older_rungs": receipt["rungs"],
        "current_preservation": current,
        "comparison": "reproduce from pinned complete source, compare all preparation and blob bytes",
        "production_state_verified": False,
    }


def signal(root: Path, _output: Path) -> dict[str, object]:
    from agri_data_service.foundation.parquet.completion import PartitionCompletion
    from agri_data_service.foundation.parquet.paths import completion_marker_path
    from agri_data_service.pipeline.parquet.signal_candidate_admission import EXPECTED_ROWS, RUNGS
    from agri_data_service.pipeline.parquet.signal_coordinate_correction import build_signal_repair

    request_path = root / ".omc/research/signal-coordinate-offline-request-20260911.json"
    request_bytes = request_path.read_bytes()
    request_sha = "7b9949661c1529650b90e2ea93bc216c27a1312ef6b4ce79af9c463512f5416e"
    require(sha256(request_bytes) == request_sha, "saved offline signal request pin differs")
    document = json.loads(request_bytes)
    archive = root / ".omc/research/preserved-repair-inputs-20260911/signal-coordinate-artifacts-20260910.tar.gz"
    repair = build_signal_repair(
        archive, bucket=document["bucket"], completed_at=datetime.fromisoformat(document["completed_at"])
    )
    require(repair.request == request_bytes, "signal ordinary writer request bytes differ")
    rows = {str(rung): 0 for rung in RUNGS}
    part_count = {str(rung): 0 for rung in RUNGS}
    multipart_days = {str(rung): [] for rung in RUNGS}
    physical_objects = physical_bytes = 0
    for day in repair.days:
        physical_objects += len(day.objects)
        physical_bytes += sum(len(payload) for payload in day.objects.values())
        for rung in RUNGS:
            marker = PartitionCompletion.from_json_bytes(
                day.objects[completion_marker_path("signal", "observed", rung, day.day)]
            )
            rows[str(rung)] += marker.row_count
            part_count[str(rung)] += marker.part_count
            require(len(marker.parts) == marker.part_count, "signal part digest population differs")
            if marker.part_count > 1:
                multipart_days[str(rung)].append(day.day.isoformat())
            for part in marker.parts:
                payload = day.objects[part.relative_path]
                require(
                    len(payload) == part.byte_count and sha256(payload) == part.sha256,
                    "signal ordinary part complete digest differs",
                )
    require(rows == EXPECTED_ROWS, "signal logical row totals differ")
    return {
        "status": "pass", "request_sha256": request_sha, "days": len(repair.days),
        "first_day": repair.days[0].day.isoformat(), "last_day": repair.days[-1].day.isoformat(),
        "logical_rows": rows, "parts": part_count, "multipart_days": multipart_days,
        "physical_objects": physical_objects, "physical_bytes": physical_bytes,
        "comparison": "full pinned archive validation, exact ordinary request reproduction, every part digest",
        "production_state_verified": False,
    }


def soil(root: Path, _output: Path) -> dict[str, object]:
    from rasterio.io import MemoryFile

    from agri_data_service.pipeline.static_soil.point import open_verified_soil_bundle, soil_point

    research = root / ".omc/research"
    evidence_bytes = (research / "static-soil-valid-pixel-inspection-20260911.json").read_bytes()
    evidence_sha = "72bb00084e1642af2f3099234ebfdaab9e5b34fa605024ac0e4cdb30db8d8d23"
    require(sha256(evidence_bytes) == evidence_sha, "saved soil pixel evidence pin differs")
    evidence = json.loads(evidence_bytes)
    bundle = open_verified_soil_bundle(
        research / "static-soil-candidate-20260911", expected_manifest_sha256=evidence["candidate_sha256"]
    )
    verified = []
    for sample in evidence["candidates"]:
        answer = soil_point(
            bundle, longitude=sample["longitude"], latitude=sample["latitude"],
            selected_day=date.fromisoformat(sample["point_answer"]["selected_day"]),
        )
        serializable = json.loads(json.dumps(asdict(answer), default=str))
        require(serializable == sample["point_answer"], "soil point answer differs")
        for asset, cog, value in zip(bundle.manifest.assets, bundle.cogs, answer.values, strict=True):
            with MemoryFile(cog) as memory, memory.open() as dataset:
                raw = int(dataset.read(1, window=((value.row, value.row + 1), (value.column, value.column + 1)))[0, 0])
            require(raw == value.raw_value and raw / asset.scale_divisor == value.value, "soil raw pixel/scaled value differs")
        verified.append(serializable)
    return {
        "status": "pass", "candidate_sha256": bundle.manifest_sha256,
        "saved_pixel_evidence_sha256": evidence_sha,
        "cog_count": len(bundle.cogs), "full_hashed_cog_bytes": sum(map(len, bundle.cogs)),
        "verified_samples": verified, "raw_pixels_compared": len(verified) * len(bundle.cogs),
        "comparison": "complete COG hashes, saved answers and independent base-pixel numeric reads",
        "production_state_verified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": "independent-repair-replay/v1", "started_at": datetime.now(UTC).isoformat(), "checks": {}}
    for name, check in (("mtbs", mtbs), ("signal", signal), ("soil", soil)):
        started = time.monotonic()
        try:
            result = check(arguments.root.resolve(), arguments.output)
        except Exception as error:
            result = {"status": "fail", "error_type": type(error).__name__, "error": str(error)}
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        report["checks"][name] = result
        print(json.dumps({"check": name, "status": result["status"], "duration_seconds": result["duration_seconds"]}), flush=True)
    report["finished_at"] = datetime.now(UTC).isoformat()
    (arguments.output / "replay.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if all(result["status"] == "pass" for result in report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
