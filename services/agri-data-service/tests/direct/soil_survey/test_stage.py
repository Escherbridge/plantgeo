"""SSURGO staging and the release index: guards, dry run, resumable journal, manifest last, evidence.

`test_manifest_is_last_and_retry_never_advances_admission` is the archive test rewritten for the new
stage semantics (plan section 1c): a retry resumes from the journal instead of re-preparing, and
`fakes.Storage.compare_and_swap` still fails the test if anything tries to advance admission. No
test here touches a real bucket: `fakes.Storage` stands in for every write.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final, NoReturn

import pytest
from pydantic import ValidationError

from agri_data_service.foundation.soil_survey.receipts import (
    AreaCensusEntry,
    AreaInventory,
    Blob,
    SoilSurveyError,
    digest,
    encoded,
)
from agri_data_service.foundation.soil_survey.release import (
    MAX_RELEASE_SHARDS,
    Release,
    ShardArea,
    ShardRef,
    manifest_key,
    release_key,
)
from agri_data_service.pipeline.direct.soil_survey import __main__ as soil_survey_main
from agri_data_service.pipeline.direct.soil_survey.prepare import candidate_manifest_path, prepare_candidate
from agri_data_service.pipeline.direct.soil_survey.stage import (
    STAGE_ALLOWED_VARIABLE,
    build_release,
    journal_path,
    publish_candidate,
    publish_release,
    read_journal,
    require_stage_authorised,
)
from tests.direct.soil_survey.fakes import AREA, POLYGONS, VINTAGE, Storage, completed

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.foundation.soil_survey.release import Candidate

SHARD: Final = "ID-1"
BUCKET: Final = "plantgeo-test-bucket"
OTHER_BUCKET: Final = "plantgeo-other-bucket"
PENDING_AREA: Final = "ID002"
REGION_ENVELOPE: Final = (-126.0, 41.0, -110.0, 50.0)
ALLOWED: Final = {STAGE_ALLOWED_VARIABLE: "1"}


async def _prepared(root: Path) -> tuple[Candidate, str]:
    await completed(root)
    return prepare_candidate(root, SHARD, [AREA])


def _scope(entries: list[tuple[str, str]]) -> AreaInventory:
    return AreaInventory(
        response=Blob(sha256=digest(b"{}"), byte_count=len(b"{}")),
        query_sha256=digest(b"census"),
        checked_at=datetime.now(UTC),
        areas=tuple(AreaCensusEntry(area=area, saverest=saverest) for area, saverest in entries),
        envelope=REGION_ENVELOPE,
        region="pnw",
    )


def _evidence_keys(candidate: Candidate) -> set[str]:
    keys: set[str] = set()
    for area in candidate.areas:
        keys.update(summary.response.key for summary in (area.opening, area.closing) if summary is not None)
        for page in area.pages:
            keys.update((page.response.key, page.keys_response.key))
    return keys


def _refuse(*arguments: object, **keywords: object) -> NoReturn:
    raise AssertionError(f"must not be called: {arguments} {keywords}")


async def test_manifest_is_last_and_retry_never_advances_admission(tmp_path: Path) -> None:
    candidate, sha = await _prepared(tmp_path)
    part_keys = {part.blob.key for part in candidate.parts}
    storage = Storage()
    storage.fail_manifest = True
    with pytest.raises(OSError, match="marker-last"):
        publish_candidate(tmp_path, sha, storage, bucket=BUCKET)
    assert set(storage.objects) == part_keys
    assert read_journal(tmp_path, sha, bucket=BUCKET).staged_keys == part_keys
    assert not read_journal(tmp_path, sha, bucket=BUCKET).manifest_staged

    storage.fail_manifest = False
    storage.writes.clear()
    report = publish_candidate(tmp_path, sha, storage, bucket=BUCKET)
    assert storage.writes == [report.manifest_key] == [manifest_key(sha)]
    assert (report.uploaded_objects, report.resumed_objects) == (0, len(part_keys))
    assert digest(storage.objects[report.manifest_key]) == sha == digest(encoded(candidate))
    assert read_journal(tmp_path, sha, bucket=BUCKET).manifest_staged

    assert publish_candidate(tmp_path, sha, storage, bucket=BUCKET).manifest_key == report.manifest_key
    assert not any("/releases/" in key for key in storage.objects)


def test_stage_guard_refuses_a_missing_switch_or_a_different_bucket() -> None:
    with pytest.raises(SoilSurveyError, match=STAGE_ALLOWED_VARIABLE):
        require_stage_authorised(BUCKET, configured_bucket=BUCKET, environ={})
    with pytest.raises(SoilSurveyError, match="does not equal"):
        require_stage_authorised(OTHER_BUCKET, configured_bucket=BUCKET, environ=ALLOWED)
    with pytest.raises(SoilSurveyError, match="does not equal"):
        require_stage_authorised(BUCKET, configured_bucket=None, environ=ALLOWED)
    require_stage_authorised(BUCKET, configured_bucket=BUCKET, environ=ALLOWED)


async def test_stage_cli_refuses_before_any_storage_is_built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, sha = await _prepared(tmp_path)
    monkeypatch.setattr(soil_survey_main.BotoAvailabilityStorage, "from_settings", _refuse)
    monkeypatch.setattr(soil_survey_main, "_configured_bucket", lambda: BUCKET)
    stage = ["stage", "--root", str(tmp_path), "--manifest", sha, "--apply"]
    monkeypatch.delenv(STAGE_ALLOWED_VARIABLE, raising=False)
    with pytest.raises(SoilSurveyError, match=STAGE_ALLOWED_VARIABLE):
        soil_survey_main.main([*stage, "--bucket", BUCKET])
    monkeypatch.setenv(STAGE_ALLOWED_VARIABLE, "1")
    with pytest.raises(SoilSurveyError, match="does not equal"):
        soil_survey_main.main([*stage, "--bucket", OTHER_BUCKET])
    assert not journal_path(tmp_path, sha).exists()


async def test_stage_dry_run_reports_object_count_and_bytes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    candidate, sha = await _prepared(tmp_path)
    soil_survey_main.main(["stage", "--root", str(tmp_path), "--manifest", sha, "--bucket", BUCKET])
    report = json.loads(capsys.readouterr().out)
    manifest_bytes = len(candidate_manifest_path(tmp_path, sha).read_bytes())
    assert report["outcome"] == "dry_run"
    assert report["object_count"] == len(candidate.parts) + 1
    assert report["byte_count"] == sum(part.blob.byte_count for part in candidate.parts) + manifest_bytes
    assert report["already_journaled"] == 0
    assert not journal_path(tmp_path, sha).exists()


async def test_resumed_stage_reverifies_journaled_objects_before_skipping_upload(tmp_path: Path) -> None:
    """A journaled key is re-read and re-verified against the bucket, not merely trusted (finding 4)."""
    candidate, sha = await _prepared(tmp_path)
    part_keys = [part.blob.key for part in candidate.parts]
    local_objects = sorted(path.name for path in (tmp_path / "objects").iterdir())
    storage = Storage()
    report = publish_candidate(tmp_path, sha, storage, bucket=BUCKET)
    assert storage.reads == [report.manifest_key]

    storage.reads.clear()
    storage.writes.clear()
    assert publish_candidate(tmp_path, sha, storage, bucket=BUCKET).resumed_objects == len(candidate.parts)
    assert storage.writes == [report.manifest_key]
    # Every already-journaled part is re-read before its upload is skipped, plus the manifest.
    assert storage.reads == [*part_keys, report.manifest_key]

    # A bucket object missing under the current bucket (a lifecycle rule, a recreated bucket) is
    # caught, not silently trusted: the re-verification of the first part fails, so it is
    # re-uploaded rather than skipped, even though the journal still names it as staged.
    del storage.objects[part_keys[0]]
    storage.reads.clear()
    storage.writes.clear()
    report_after_corruption = publish_candidate(tmp_path, sha, storage, bucket=BUCKET)
    assert part_keys[0] in storage.writes
    assert report_after_corruption.resumed_objects == len(candidate.parts) - 1

    journal_path(tmp_path, sha).unlink()
    storage.reads.clear()
    publish_candidate(tmp_path, sha, storage, bucket=BUCKET)
    assert storage.reads == [report.manifest_key]
    assert sorted(path.name for path in (tmp_path / "objects").iterdir()) == local_objects


async def test_raw_source_evidence_is_staged_only_with_the_flag(tmp_path: Path) -> None:
    candidate, sha = await _prepared(tmp_path)
    evidence = _evidence_keys(candidate)
    without = Storage()
    publish_candidate(tmp_path, sha, without, bucket=BUCKET)
    assert not evidence & set(without.objects)
    with_evidence = Storage()
    publish_candidate(tmp_path, sha, with_evidence, bucket=OTHER_BUCKET, include_source_evidence=True)
    assert evidence <= set(with_evidence.objects)
    assert read_journal(tmp_path, sha, bucket=OTHER_BUCKET).source_evidence_staged
    assert not read_journal(tmp_path, sha, bucket=BUCKET).source_evidence_staged


async def test_release_pins_staged_shards_and_reports_pending_areas(tmp_path: Path) -> None:
    _, sha = await _prepared(tmp_path)
    (tmp_path / "areas.json").write_bytes(encoded(_scope([(AREA, VINTAGE), (PENDING_AREA, VINTAGE)])))
    with pytest.raises(SoilSurveyError, match="not staged"):
        build_release(tmp_path, [sha], bucket=BUCKET)
    storage = Storage()
    publish_candidate(tmp_path, sha, storage, bucket=BUCKET)
    release, payload = build_release(tmp_path, [sha], bucket=BUCKET)
    assert release.pending_areas == (PENDING_AREA,)
    assert release.source_evidence == "capture_volume"
    assert release.shards[0].native_rows == len(POLYGONS)
    assert release.shards[0].manifest.sha256 == sha
    key, release_sha = publish_release(tmp_path, release, payload, storage)
    assert key == release_key(release_sha)
    assert storage.objects[key] == payload
    assert storage.writes[-1] == key


def _shard_ref(index: int) -> ShardRef:
    area = f"ID{index:03d}"
    return ShardRef(
        shard=f"ID-{index}",
        manifest=Blob(sha256=digest(area.encode()), byte_count=1),
        release_day=date(2025, 8, 27),
        captured_at=datetime(2026, 9, 1, tzinfo=UTC),
        areas=(ShardArea(area=area, saverest=VINTAGE, native_rows=1, repaired_rows=0, labelled_rows=0),),
        bbox=(0.0, 0.0, 1.0, 1.0),
    )


def test_a_release_index_over_the_shard_cap_is_refused() -> None:
    shards = tuple(_shard_ref(index) for index in range(1, MAX_RELEASE_SHARDS + 2))
    scope = _scope([(shard.areas[0].area, VINTAGE) for shard in shards])
    common = {
        "scope": scope,
        "source_evidence": "capture_volume",
        "release_day": date(2025, 8, 27),
        "captured_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    with pytest.raises(ValidationError, match="shards"):
        Release.model_validate({**common, "shards": shards, "pending_areas": ()})
    at_cap = Release.model_validate(
        {**common, "shards": shards[:MAX_RELEASE_SHARDS], "pending_areas": (shards[-1].areas[0].area,)}
    )
    assert len(at_cap.shards) == MAX_RELEASE_SHARDS
